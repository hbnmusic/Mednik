"""Literature ingestion (spec §3.3): PubMed E-utilities + Europe PMC, MeSH publication-type tagging."""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

from lxml import etree
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clients import EuropePmcClient, PubMedClient
from ..models import ClinicalStudy, Document, DocumentLink, ExtractedEndpoint
from ..taxonomy import SCALE_BY_KEY, classify_indication, classify_scale, text_mentions_asset
from ..textutil import clean_ws, parse_p_value, sha256_text
from . import stats
from .tiering import EvidenceContext, evidence_tier, validity_level
from .trials_ingest import column_for

log = logging.getLogger("mednik.literature")

PUBTYPE_TAGS: list[tuple[str, str]] = [
    (r"^clinical trial,\s*phase iii$", "PHASE3"),
    (r"^clinical trial,\s*phase ii$", "PHASE2"),
    (r"^clinical trial,\s*phase iv$", "PHASE4"),
    (r"^clinical trial,\s*phase i$", "PHASE1"),
    (r"^randomized controlled trial$", "RCT"),
    (r"^controlled clinical trial$", "CONTROLLED_TRIAL"),
    (r"^clinical trial$", "CLINICAL_TRIAL"),
    (r"^network meta-analysis$", "NMA"),
    (r"^meta-analysis$", "META_ANALYSIS"),
    (r"^systematic review$", "SYSTEMATIC_REVIEW"),
    (r"^observational study$", "OBSERVATIONAL"),
    (r"^practice guideline$", "GUIDELINE"),
    (r"^guideline$", "GUIDELINE"),
    (r"^consensus development conference", "CONSENSUS"),
    (r"^case reports?$", "CASE_REPORT"),
    (r"^review$", "REVIEW"),
    (r"^comparative study$", "COMPARATIVE"),
    (r"^multicenter study$", "MULTICENTER"),
]
TITLE_TAGS: list[tuple[str, str]] = [
    (r"post[-\s]?hoc", "POST_HOC"),
    (r"pooled\s+analys", "POOLED"),
    (r"network\s+meta-?analys", "NMA"),
    (r"meta-?analys", "META_ANALYSIS"),
    (r"discrete[-\s]choice|conjoint|best[-\s]worst", "DCE"),
    (r"\bsurvey\b|questionnaire", "SURVEY"),
    (r"guideline|consensus|recommendation|algorithm|canmat|wfsbp|\bnice\b|\bapa\b|va/dod", "GUIDELINE_LIKE"),
    (r"real[-\s]world|retrospective|claims\s+database|cohort|registry", "RWE"),
    (r"open[-\s]label", "OPEN_LABEL"),
    (r"case\s+(report|series)", "CASE_SERIES"),
    (r"biomarker|\bpet\b|receptor\s+occupancy|\bmri\b|eeg", "BIOMARKER"),
]


def evidence_tags(pub_types: list[str], title: str | None, abstract: str | None) -> list[str]:
    tags: list[str] = []
    for pt in pub_types:
        p = pt.strip().lower()
        for pat, tag in PUBTYPE_TAGS:
            if re.search(pat, p) and tag not in tags:
                tags.append(tag)
    blob = f"{title or ''} {(abstract or '')[:1500]}".lower()
    for pat, tag in TITLE_TAGS:
        if re.search(pat, blob) and tag not in tags:
            tags.append(tag)
    return tags


def _text(el) -> str:
    return clean_ws(" ".join(el.itertext())) if el is not None else ""


def parse_pubmed_xml(xml: str) -> list[dict[str, Any]]:
    parser = etree.XMLParser(recover=True, huge_tree=True, resolve_entities=False, no_network=True)
    root = etree.fromstring(xml.encode("utf-8"), parser)
    if root is None:
        return []
    out = []
    for art in root.iter("PubmedArticle"):
        mc = art.find("MedlineCitation")
        if mc is None:
            continue
        pmid = _text(mc.find("PMID"))
        a = mc.find("Article")
        title = _text(a.find("ArticleTitle")) if a is not None else ""
        abs_parts = []
        if a is not None and a.find("Abstract") is not None:
            for at in a.find("Abstract").findall("AbstractText"):
                lab = at.get("Label")
                t = _text(at)
                abs_parts.append(f"{lab}: {t}" if lab else t)
        journal = _text(a.find("Journal/Title")) if a is not None else ""
        year = None
        for path in ("Journal/JournalIssue/PubDate/Year", "Journal/JournalIssue/PubDate/MedlineDate", "ArticleDate/Year"):
            y = _text(a.find(path)) if a is not None else ""
            m = re.search(r"(19|20)\d{2}", y)
            if m:
                year = int(m.group(0))
                break
        authors, affs = [], []
        for au in (a.findall("AuthorList/Author") if a is not None else []):
            ln, ini = _text(au.find("LastName")), _text(au.find("Initials"))
            if ln:
                authors.append(f"{ln} {ini}".strip())
            for af in au.findall("AffiliationInfo/Affiliation"):
                affs.append(_text(af))
        pts = [_text(p) for p in (a.findall("PublicationTypeList/PublicationType") if a is not None else [])]
        mesh = [_text(m.find("DescriptorName")) for m in mc.findall("MeshHeadingList/MeshHeading")]
        nct = set()
        for db in (a.findall("DataBankList/DataBank") if a is not None else []):
            if "clinicaltrials" in _text(db.find("DataBankName")).lower():
                for acc in db.findall("AccessionNumberList/AccessionNumber"):
                    nct.add(_text(acc))
        abstract = "\n".join(abs_parts)
        nct |= set(re.findall(r"NCT\d{8}", abstract))
        doi = pmcid = None
        for aid in art.findall("PubmedData/ArticleIdList/ArticleId"):
            if aid.get("IdType") == "doi":
                doi = _text(aid)
            if aid.get("IdType") == "pmc":
                pmcid = _text(aid)
        grants = " ".join(_text(g.find("Agency")) for g in (a.findall("GrantList/Grant") if a is not None else []))
        out.append({"pmid": pmid, "title": title, "abstract": abstract, "journal": journal, "year": year,
                    "authors": ", ".join(authors[:12]) + (" et al." if len(authors) > 12 else ""), "affiliations": " ; ".join(dict.fromkeys(affs))[:4000],
                    "grants": grants, "pub_types": pts, "mesh": mesh, "nct_ids": sorted(nct), "doi": doi, "pmcid": pmcid})
    return out


def parse_epmc_result(r: dict[str, Any]) -> dict[str, Any]:
    pts = ((r.get("pubTypeList") or {}).get("pubType")) or []
    mesh = [m.get("descriptorName") for m in ((r.get("meshHeadingList") or {}).get("meshHeading") or []) if m.get("descriptorName")]
    abstract = clean_ws(re.sub(r"<[^>]+>", " ", r.get("abstractText") or ""))
    return {
        "pmid": r.get("pmid"), "pmcid": r.get("pmcid"), "doi": r.get("doi"), "title": clean_ws(re.sub(r"<[^>]+>", " ", r.get("title") or "")),
        "abstract": abstract, "journal": ((r.get("journalInfo") or {}).get("journal") or {}).get("title"),
        "year": int(r["pubYear"]) if str(r.get("pubYear") or "").isdigit() else None, "authors": r.get("authorString"),
        "affiliations": r.get("affiliation") or "", "pub_types": pts, "mesh": mesh, "is_oa": r.get("isOpenAccess") == "Y",
        "nct_ids": sorted(set(re.findall(r"NCT\d{8}", abstract))), "source": r.get("source"), "epmc_id": r.get("id"),
    }


def upsert_document(session: Session, rec: dict[str, Any], source_type: str) -> Document:
    sid = rec.get("pmid") or rec.get("pmcid") or rec.get("epmc_id") or sha256_text(rec.get("title") or "")
    key = sha256_text(f"{source_type if not rec.get('pmid') else 'pmid'}:{sid}")
    doc = session.execute(select(Document).where(Document.sha256 == key)).scalar_one_or_none()
    tags = evidence_tags(rec.get("pub_types") or [], rec.get("title"), rec.get("abstract"))
    if doc is None:
        doc = Document(sha256=key, source_type=source_type, source_id=str(sid), pmid=rec.get("pmid"))
        session.add(doc)
    doc.pmcid = doc.pmcid or rec.get("pmcid")
    doc.doi = doc.doi or rec.get("doi")
    doc.title = rec.get("title") or doc.title
    doc.journal = rec.get("journal") or doc.journal
    doc.year = rec.get("year") or doc.year
    doc.authors = rec.get("authors") or doc.authors
    doc.affiliations = (rec.get("affiliations") or "") + (" ; GRANTS: " + rec["grants"] if rec.get("grants") else "") or doc.affiliations
    if rec.get("abstract") and len(rec["abstract"]) > len(doc.abstract or ""):
        doc.abstract = rec["abstract"]
    doc.pub_types_json = sorted(set((doc.pub_types_json or []) + (rec.get("pub_types") or [])))
    doc.evidence_tags_json = sorted(set((doc.evidence_tags_json or []) + tags))
    doc.mesh_json = sorted(set((doc.mesh_json or []) + [m for m in rec.get("mesh") or [] if m]))
    doc.linked_nct_ids_json = sorted(set((doc.linked_nct_ids_json or []) + (rec.get("nct_ids") or [])))
    if rec.get("is_oa") is not None:
        doc.is_open_access = bool(rec["is_oa"]) or bool(doc.is_open_access)
    session.flush()
    return doc


def link(session: Session, doc: Document, role: str, asset_id: int | None = None, indication_id: int | None = None) -> None:
    exists = session.execute(select(DocumentLink).where(
        DocumentLink.document_id == doc.id, DocumentLink.asset_id.is_(asset_id) if asset_id is None else DocumentLink.asset_id == asset_id,
        DocumentLink.indication_id.is_(indication_id) if indication_id is None else DocumentLink.indication_id == indication_id,
        DocumentLink.role == role)).scalar_one_or_none()
    if exists is None:
        session.add(DocumentLink(document_id=doc.id, asset_id=asset_id, indication_id=indication_id, role=role))


async def search_corpus(pubmed_term: str, epmc_query: str, *, max_pubmed: int, max_epmc: int, force_refresh: bool = False,
                        say: Callable[[str], None] = log.info) -> list[tuple[str, dict[str, Any]]]:
    """Run both literature searches and return [(source_type, record)] (PubMed records first; EPMC fills gaps)."""
    pm, ep = PubMedClient(), EuropePmcClient()
    out: list[tuple[str, dict[str, Any]]] = []
    seen: set[str] = set()
    try:
        ids = await pm.esearch(pubmed_term, retmax=max_pubmed, force_refresh=force_refresh)
        for xml in await pm.efetch_xml(ids, force_refresh=force_refresh):
            for rec in parse_pubmed_xml(xml):
                seen.add(rec["pmid"])
                out.append(("pubmed", rec))
        say(f"PubMed: {len(ids)} hits for {pubmed_term[:90]}…")
    except Exception as exc:  # noqa: BLE001
        say(f"PubMed error: {exc}")
    try:
        res = await ep.search(epmc_query, max_results=max_epmc, force_refresh=force_refresh)
        added = 0
        for r in res:
            rec = parse_epmc_result(r)
            if rec.get("pmid") and rec["pmid"] in seen:
                out.append(("epmc_enrich", rec))  # OA flag / PMCID enrichment only
                continue
            if not rec.get("abstract"):
                continue
            out.append(("epmc", rec))
            added += 1
        say(f"Europe PMC: {len(res)} hits ({added} not in PubMed set)")
    except Exception as exc:  # noqa: BLE001
        say(f"Europe PMC error: {exc}")
    return out


def store_corpus(session: Session, corpus: list[tuple[str, dict[str, Any]]], role: str, asset_id: int | None, indication_id: int | None) -> list[Document]:
    docs: dict[int, Document] = {}
    for src, rec in corpus:
        st = "pubmed" if src in ("pubmed", "epmc_enrich") and rec.get("pmid") else "epmc"
        doc = upsert_document(session, rec, st)
        link(session, doc, role, asset_id, indication_id)
        docs[doc.id] = doc
    session.flush()
    return list(docs.values())


def pubmed_asset_term(asset_terms: list[str], indication_name: str | None) -> str:
    a = " OR ".join(f'"{t}"[tiab]' for t in asset_terms)
    return f"({a})" + (f' AND ("{indication_name}"[tiab] OR "{indication_name}"[mh])' if indication_name else "")


def epmc_asset_query(asset_terms: list[str], indication_name: str | None) -> str:
    a = " OR ".join(f'"{t}"' for t in asset_terms)
    return f"({a})" + (f' AND "{indication_name}"' if indication_name else "")


def doc_sponsor_class(doc: Document, company: str | None, trial_sponsors: dict[str, str]) -> str:
    for nct in doc.linked_nct_ids_json or []:
        if nct in trial_sponsors:
            return trial_sponsors[nct]
    blob = f"{doc.affiliations or ''} {doc.abstract or ''}"
    if company:
        tok = re.split(r"[\s,]+", company.strip())[0]
        if len(tok) > 3 and re.search(rf"\b{re.escape(tok)}\b", blob, re.I):
            return "INDUSTRY"
    if re.search(r"(funded|sponsored|supported)\s+by\s+[A-Z][\w\-&. ]{2,40}(inc|ltd|llc|plc|pharma|therapeutics|sciences|gmbh|s\.a\.)", blob, re.I):
        return "INDUSTRY"
    return "ACADEMIC_IIS"


def persist_literature_endpoints(session: Session, asset_id: int, indication_id: int | None, items: list[dict[str, Any]],
                                 docs_by_sid: dict[str, Document], sponsor_by_doc: dict[int, str]) -> int:
    n = 0
    studies: dict[str, ClinicalStudy] = {}
    for it in items:
        doc = docs_by_sid.get(it["source_id"])
        if doc is None:
            continue
        sk = classify_scale(it.get("endpoint_scale_name"), it.get("verbatim_quote")) or None
        if not sk:
            continue
        tags = set(doc.evidence_tags_json or [])
        if it["source_id"] not in studies:
            existing = session.execute(select(ClinicalStudy).where(
                ClinicalStudy.asset_id == asset_id, ClinicalStudy.source == doc.source_type, ClinicalStudy.source_ref == doc.source_id)).scalar_one_or_none()
            if existing is None:
                phase = 4 if "PHASE4" in tags else 3 if "PHASE3" in tags else 2 if "PHASE2" in tags else 1 if "PHASE1" in tags else None
                rct = bool(tags & {"RCT", "CONTROLLED_TRIAL"})
                ind_key = classify_indication(doc.title, doc.abstract)
                n_match = re.search(r"\b[Nn]\s*=\s*(\d{2,5})|(\d{2,5})\s+(?:patients|participants|subjects|adults)\b", doc.abstract or "")
                existing = ClinicalStudy(
                    asset_id=asset_id, indication_id=indication_id, source=doc.source_type, source_ref=doc.source_id,
                    pmid=doc.pmid, nct_id=(doc.linked_nct_ids_json or [None])[0], title=doc.title,
                    sponsor_class=sponsor_by_doc.get(doc.id, "ACADEMIC_IIS"), phase=f"PHASE{phase}" if phase else None, phase_max=phase,
                    study_type="OBSERVATIONAL" if ("OBSERVATIONAL" in tags or "RWE" in tags) else "INTERVENTIONAL",
                    allocation="RANDOMIZED" if rct else None, design_archetype=next(iter(sorted(tags & {"META_ANALYSIS", "NMA", "POST_HOC", "POOLED", "RWE", "OPEN_LABEL", "CASE_SERIES"})), None),
                    sample_size=int(next(g for g in n_match.groups() if g)) if n_match else None,
                    indication_key=ind_key, population="adult", linked_pmids_json=[doc.pmid] if doc.pmid else None,
                )
                session.add(existing)
                session.flush()
            studies[it["source_id"]] = existing
        cs = studies[it["source_id"]]
        tags = set(doc.evidence_tags_json or [])
        sdef = SCALE_BY_KEY[sk]
        etype = it.get("effect_size_type") or "LS_MEAN_DIFF"
        val = it.get("value")
        d = dvar = rd = rt = rc = None
        lo, hi = it.get("ci_95_lower"), it.get("ci_95_upper")
        n_t, n_c = it.get("n_treatment"), it.get("n_comparator")
        if etype == "COHENS_D" and val is not None:
            # papers usually report the magnitude of benefit; sign conventions vary, so take |d| only when the
            # quoted sentence itself states superiority / improvement.
            benefit = re.search(r"superior|greater\s+(improvement|reduction)|improv|significant(ly)?\s+(reduc|improv|separat)", it["verbatim_quote"], re.I)
            d = abs(val) if benefit else val
            dvar = stats.d_variance(d, n_t, n_c) if n_t and n_c else None
        elif etype == "LS_MEAN_DIFF" and val is not None and lo is not None and hi is not None and n_t and n_c:
            r = stats.cohens_d_from_ci(val, lo, hi, n_t, n_c, lower_is_better=sdef.lower_is_better)
            if r:
                d, dvar = r
        if etype == "INCIDENCE_PCT" and val is not None and it.get("comparator_value") is not None:
            rt, rc = val / 100, it["comparator_value"] / 100
            rd = rt - rc
        p, ptxt = parse_p_value(it.get("p_value"))
        significant = (p is not None and p < 0.05) or (lo is not None and hi is not None and (lo > 0 or hi < 0) and etype in ("LS_MEAN_DIFF", "RD"))
        if etype in ("OR", "HR") and lo is not None and hi is not None:
            significant = lo > 1 or hi < 1
        favorable = None
        if etype == "LS_MEAN_DIFF" and val is not None:
            favorable = (val < 0) if sdef.lower_is_better else (val > 0)
        elif etype == "COHENS_D" and d is not None:
            favorable = d > 0
        elif etype in ("NNT",):
            favorable = True
        elif etype in ("NNH",):
            favorable = False
        elif rd is not None:
            favorable = rd > 0 if sdef.kind == "binary_efficacy" else rd <= 0.02
        ctx = EvidenceContext(source=doc.source_type, sponsor_class=cs.sponsor_class, phase_max=cs.phase_max,
                              randomized=cs.allocation == "RANDOMIZED", study_type=cs.study_type, sample_size=cs.sample_size,
                              is_primary=it.get("is_primary"), is_post_hoc=bool(it.get("is_post_hoc")) or bool(tags & {"POST_HOC", "POOLED"}),
                              significant=significant, favorable=favorable, is_meta_analysis=bool(tags & {"META_ANALYSIS", "NMA"}),
                              is_case_series=bool(tags & {"CASE_REPORT", "CASE_SERIES"}), is_biomarker="BIOMARKER" in tags)
        tier, _ = evidence_tier(ctx)
        lvl, _ = validity_level(ctx)
        tp = it.get("timepoint_weeks")
        session.add(ExtractedEndpoint(
            study_id=cs.id, asset_id=asset_id, indication_id=indication_id, mvd_key=sdef.mvd_key, endpoint_scale_name=sk,
            endpoint_label=it.get("endpoint_scale_name"), timepoint_weeks=tp, arm_name=it.get("arm_name"), comparator_arm=it.get("comparator_arm"),
            effect_size_type=etype, value=val, ci_95_lower=lo, ci_95_upper=hi, p_value=p, p_value_text=ptxt,
            is_multiplicity_controlled=None, is_primary=it.get("is_primary"), is_post_hoc=ctx.is_post_hoc, n_treatment=n_t, n_comparator=n_c,
            cohens_d=d, cohens_d_var=dvar, risk_treatment=rt, risk_comparator=rc, risk_difference=rd,
            nnt_nnh=val if etype in ("NNT", "NNH") else stats.nnt_from_rd(rd), favorable=favorable, significant=significant,
            column_key=column_for(cs.indication_key, cs.population, False, tp or cs.duration_weeks), verbatim_quote=it["verbatim_quote"],
            source_id=it["source_id"], source_type=doc.source_type, extraction_method=it.get("_method", "llm"),
            evidence_tier=tier, validity_level=lvl,
        ))
        cs.evidence_tier = tier if cs.evidence_tier is None else min(cs.evidence_tier, tier, key=["1A", "1B", "2", "3"].index)
        cs.validity_level = max(cs.validity_level or 0, lvl)
        n += 1
    session.flush()
    return n


def doc_text(doc: Document, max_chars: int = 20000) -> str:
    head = f"TITLE: {doc.title or ''}\n"
    body = doc.full_text or doc.abstract or ""
    return (head + body)[:max_chars]


def asset_doc_is_extractable(doc: Document, terms: list[str]) -> bool:
    tags = set(doc.evidence_tags_json or [])
    if tags & {"GUIDELINE", "CASE_REPORT"} and not tags & {"RCT", "PHASE3", "PHASE2"}:
        return False
    if "REVIEW" in tags and not tags & {"META_ANALYSIS", "NMA", "SYSTEMATIC_REVIEW"}:
        return False
    return text_mentions_asset(f"{doc.title} {doc.abstract}", terms) and bool(doc.abstract)
