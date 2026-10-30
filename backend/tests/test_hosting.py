"""Hosted-deployment pieces: password sign-in, cache housekeeping, interrupted-job recovery."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from mednik.api import auth
from mednik.api.main import app
from mednik.config import get_settings
from mednik.db import aux_scope, normalize_url
from mednik.models import ApiCache, IngestJob
from mednik.services.maintenance import mark_interrupted_jobs, purge_api_cache


def test_token_roundtrip_and_tamper():
    t = auth.make_token("pw", now=1_000)
    assert auth.valid_token("pw", t, now=2_000)
    assert not auth.valid_token("other", t, now=2_000)  # password change signs everyone out
    exp, sig = t.split(".")
    assert not auth.valid_token("pw", f"{int(exp) + 1}.{sig}", now=2_000)
    assert not auth.valid_token("pw", t, now=1_000 + auth.SESSION_DAYS * 86400 + 1)
    assert not auth.valid_token("pw", None) and not auth.valid_token("pw", "garbage")


def test_safe_next_blocks_open_redirects():
    assert auth._safe_next("/matrix?x=1") == "/matrix?x=1"
    for bad in ("//evil.example", "https://evil.example", "/\\evil.example", None, ""):
        assert auth._safe_next(bad) == "/"


@pytest.fixture
def password(monkeypatch):
    st = get_settings()
    monkeypatch.setattr(st, "app_password", "correct horse")
    auth._failures.clear()

    async def _no_sleep(_):
        return None

    monkeypatch.setattr(auth.asyncio, "sleep", _no_sleep)
    yield "correct horse"
    auth._failures.clear()


def test_login_flow(password):
    with TestClient(app) as c:
        assert c.get("/healthz").json() == {"status": "ok"}
        assert c.get("/api/v1/meta").status_code == 401
        r = c.get("/", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login")
        assert "Password" in c.get("/login").text
        bad = c.post("/login", data={"password": "nope", "next": "/"}, follow_redirects=False)
        assert bad.status_code == 401 and "Incorrect password" in bad.text
        ok = c.post("/login", data={"password": password, "next": "//evil.example"}, follow_redirects=False)
        assert ok.status_code == 303 and ok.headers["location"] == "/"
        assert auth.COOKIE in ok.cookies
        assert c.get("/api/v1/meta").status_code == 200
        c.get("/logout")
        assert c.get("/api/v1/meta").status_code == 401


def test_login_rate_limit(password):
    with TestClient(app) as c:
        for _ in range(auth.MAX_FAILURES):
            assert c.post("/login", data={"password": "x"}).status_code == 401
        r = c.post("/login", data={"password": password})
        assert r.status_code == 429  # locked out even with the right password until the window passes


def test_no_password_means_open():
    assert not get_settings().app_password
    with TestClient(app) as c:
        assert c.get("/api/v1/meta").status_code == 200


def test_cache_purge_keeps_llm_and_respects_cap(monkeypatch):
    st = get_settings()
    now = datetime.now(timezone.utc)
    old = now - timedelta(hours=st.cache_ttl_hours + 1)
    with aux_scope() as s:
        s.query(ApiCache).delete()
        s.add_all([
            ApiCache(cache_key="old-api", source="pubmed", url="u", status_code=200, body="x" * 10, fetched_at=old),
            ApiCache(cache_key="old-llm", source="llm:gemini", url="m", status_code=200, body="{}", fetched_at=old),
            ApiCache(cache_key="new-a", source="ctgov", url="u", status_code=200, body="a" * 700_000, fetched_at=now - timedelta(hours=2)),
            ApiCache(cache_key="new-b", source="ctgov", url="u", status_code=200, body="b" * 700_000, fetched_at=now - timedelta(hours=1)),
        ])
    monkeypatch.setattr(st, "cache_max_mb", 1)
    out = purge_api_cache(now)
    assert out == {"expired": 1, "over_cap": 1}
    with aux_scope() as s:
        assert sorted(r.cache_key for r in s.query(ApiCache).all()) == ["new-b", "old-llm"]
        s.query(ApiCache).delete()


def test_interrupted_jobs_are_closed():
    with aux_scope() as s:
        s.add(IngestJob(id="job-stuck", status="running", request_json={}))
        s.add(IngestJob(id="job-done", status="done", request_json={}))
    assert mark_interrupted_jobs() >= 1
    with aux_scope() as s:
        assert s.get(IngestJob, "job-stuck").status == "error"
        assert "restarted" in s.get(IngestJob, "job-stuck").error
        assert s.get(IngestJob, "job-done").status == "done"


def test_normalize_url():
    assert normalize_url("postgres://u:p@h:5432/db") == "postgresql+psycopg://u:p@h:5432/db"
    assert normalize_url("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert normalize_url("sqlite:///x.db") == "sqlite:///x.db"


# ---- LLM usage accounting and quota-aware retries ----------------------------------------------------------------


def test_retry_delay_honours_google_hint_and_backs_off():
    from mednik.services.llm.base import retry_delay

    quota = '429 {"error":{"status":"RESOURCE_EXHAUSTED","details":[{"retryDelay":"23s"}]}}'
    assert retry_delay(quota, 0) == 24.0
    assert retry_delay("429 rate limited", 0) == 5.0 and retry_delay("429 rate limited", 3) == 40.0
    assert retry_delay("429 rate limited", 6) == 60.0  # capped
    assert retry_delay("503 unavailable", 2) == 8.0  # ordinary server errors keep the short back-off


def test_llm_usage_counts_calls_cache_hits_and_failures(monkeypatch):
    import asyncio

    from mednik.config import Settings
    from mednik.services.extraction import extract_guideline_recs
    from mednik.services.llm import base as llm_base

    class Fake(llm_base.LlmProvider):
        name = "fake"
        n = 0

        @property
        def model(self):
            return "fake-1"

        async def _call(self, system, prompt, schema, schema_name):
            Fake.n += 1
            if "DOC_B" in prompt:
                raise llm_base.LlmError("400 bad request")  # not retryable → counted as a failed extraction
            return {"recommendations": [], "guideline_body": None, "publication_year": None}

    async def instant(_):
        return None

    monkeypatch.setattr(llm_base.asyncio, "sleep", instant)
    prov = Fake(Settings(llm_max_concurrency=2))
    docs = [("a", "DOC_A text"), ("b", "DOC_B text")]
    llm_base.reset_usage()
    asyncio.run(extract_guideline_recs(docs, {"x": ["x"]}, "Schizophrenia", provider=prov))
    u = llm_base.usage_snapshot()
    assert (u["calls"], u["failed"], u["cache_hits"]) == (1, 1, 0)
    asyncio.run(extract_guideline_recs(docs[:1], {"x": ["x"]}, "Schizophrenia", provider=prov))  # same prompt → cached
    u = llm_base.usage_snapshot()
    assert u["cache_hits"] == 1 and u["calls"] == 1 and Fake.n == 2
    assert u["approx_input_tokens"] > 0


def test_daily_quota_stops_the_sync_without_waiting(monkeypatch):
    import asyncio

    from mednik.config import Settings
    from mednik.services.extraction import extract_guideline_recs
    from mednik.services.llm import base as llm_base

    class Fake(llm_base.LlmProvider):
        name = "fake"
        n = 0

        @property
        def model(self):
            return "fake-2"

        async def _call(self, system, prompt, schema, schema_name):
            Fake.n += 1
            raise llm_base.LlmError('429 {"error":{"message":"Generate content requests per day limit exceeded on Free Tier"}}')

    slept: list[float] = []

    async def record(d):
        slept.append(d)

    monkeypatch.setattr(llm_base.asyncio, "sleep", record)
    prov = Fake(Settings(llm_max_concurrency=1))
    llm_base.reset_usage()
    docs = [(f"d{i}", f"unique doc {i}") for i in range(6)]
    asyncio.run(extract_guideline_recs(docs, {"x": ["x"]}, "Schizophrenia", provider=prov))
    u = llm_base.usage_snapshot()
    assert Fake.n == 1 and u["failed"] == 6 and u["daily_quota_exhausted"] and not slept  # one attempt, no back-off
    llm_base.reset_usage()
    assert not llm_base.usage_snapshot()["daily_quota_exhausted"]  # a new sync tries again


def test_gemini_requests_are_paced(monkeypatch):
    import asyncio

    from mednik.config import Settings
    from mednik.services.llm import base as llm_base
    from mednik.services.llm.gemini import GeminiProvider

    waits: list[float] = []

    async def record(d):
        waits.append(round(d, 1))

    monkeypatch.setattr(llm_base.asyncio, "sleep", record)
    prov = GeminiProvider(Settings(llm_requests_per_minute=6, gemini_api_key="k"))

    async def go():
        for _ in range(3):
            await prov._pace()

    asyncio.run(go())
    assert waits == [10.0, 20.0]  # 6/min → one request every 10 s (sleep is mocked, so waits accumulate)


# ---- memory: compressed cache bodies and page-at-a-time registry reads ----------------------------------------------


def test_cache_body_compression_roundtrip():
    from mednik.clients.base import decode_cache_body, encode_cache_body

    small = '{"a": 1}'
    assert encode_cache_body(small) == small and decode_cache_body(small) == small  # short bodies stay readable
    big = '{"studies": [' + ",".join('{"nctId": "NCT%08d", "text": "' % i + "lorem ipsum " * 20 + '"}' for i in range(500)) + "]}"
    enc = encode_cache_body(big)
    assert len(enc) < len(big) / 3 and enc != big
    assert decode_cache_body(enc) == big


def test_registry_pages_are_yielded_one_at_a_time_and_trimmed(monkeypatch):
    import asyncio

    from mednik.clients import ClinicalTrialsClient

    pages = [{"studies": [{"i": k} for k in range(40)], "nextPageToken": "t1"},
             {"studies": [{"i": k} for k in range(40, 80)], "nextPageToken": "t2"},
             {"studies": [{"i": k} for k in range(80, 120)]}]
    calls: list[dict] = []

    async def fake_get_json(self, path, params, force_refresh=False):
        calls.append(dict(params))
        return pages[len(calls) - 1]

    monkeypatch.setattr(ClinicalTrialsClient, "get_json", fake_get_json)

    async def go():
        sizes = []
        async for page in ClinicalTrialsClient().iter_study_pages(intervention_terms=["x"], max_studies=100):
            sizes.append(len(page))
        return sizes

    assert asyncio.run(go()) == [40, 40, 20]  # stops at max_studies, last page trimmed
    assert calls[0]["pageSize"] == get_settings().ctgov_page_size and calls[1]["pageToken"] == "t1"
    calls.clear()
    allp = asyncio.run(ClinicalTrialsClient().search_studies(intervention_terms=["x"], max_studies=100))
    assert len(allp) == 100


def test_rss_mb_reports_on_linux():
    from mednik.services.maintenance import rss_mb

    v = rss_mb()
    assert v is None or v > 10
