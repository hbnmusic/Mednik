"""Biostatistical normalizers: Cohen's d, NNT/NNH, risk differences, pooling, sample size.

Conventions
-----------
* Cohen's d is returned *signed so that positive = favourable to the treatment arm*
  (callers pass ``lower_is_better`` for symptom scales such as PANSS / MADRS).
* Risk differences are treatment − comparator, as proportions (0..1).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from scipy import stats as sps

Z975 = float(sps.norm.ppf(0.975))


def z_for_ci(ci_pct: float = 95.0) -> float:
    return float(sps.norm.ppf(0.5 + ci_pct / 200.0))


def sd_from_se(se: float | None, n: int | None) -> float | None:
    if se is None or n is None or n <= 0:
        return None
    return float(se) * math.sqrt(n)


def pooled_sd(sd1: float, sd2: float, n1: int, n2: int) -> float:
    return math.sqrt(((n1 - 1) * sd1 ** 2 + (n2 - 1) * sd2 ** 2) / max(1, (n1 + n2 - 2)))


def hedges_correction(n1: int, n2: int) -> float:
    df = n1 + n2 - 2
    return 1 - 3 / (4 * df - 1) if df > 1 else 1.0


def d_variance(d: float, n1: int, n2: int) -> float:
    return (n1 + n2) / (n1 * n2) + d ** 2 / (2 * (n1 + n2))


def cohens_d_from_ci(diff: float, ci_low: float, ci_high: float, n1: int, n2: int, *,
                     ci_pct: float = 95.0, lower_is_better: bool = True) -> tuple[float, float] | None:
    """Standardized effect from an adjusted mean difference + its CI (e.g. LS-mean difference from MMRM).

    SE(diff) = (U − L) / (2·z);  SD_pooled ≈ SE(diff) / sqrt(1/n1 + 1/n2);  d = diff / SD_pooled.
    """
    if None in (diff, ci_low, ci_high, n1, n2) or n1 <= 1 or n2 <= 1:
        return None
    width = abs(ci_high - ci_low)
    if width <= 0:
        return None
    se = width / (2 * z_for_ci(ci_pct))
    sdp = se / math.sqrt(1 / n1 + 1 / n2)
    if sdp <= 0:
        return None
    d = diff / sdp
    d = -d if lower_is_better else d
    return d, d_variance(d, n1, n2)


def cohens_d_from_means(m_t: float, m_c: float, sd_t: float, sd_c: float, n_t: int, n_c: int, *,
                        lower_is_better: bool = True) -> tuple[float, float] | None:
    if None in (m_t, m_c, sd_t, sd_c, n_t, n_c) or n_t <= 1 or n_c <= 1:
        return None
    sdp = pooled_sd(sd_t, sd_c, n_t, n_c)
    if sdp <= 0:
        return None
    d = (m_t - m_c) / sdp
    d = -d if lower_is_better else d
    return d, d_variance(d, n_t, n_c)


def cohens_d_from_p(diff_sign: float, p: float, n1: int, n2: int) -> tuple[float, float] | None:
    """Back-calculate |d| from a two-sided p-value (t approximation). Used only when no CI/SD is reported."""
    if p is None or not (0 < p < 1) or n1 <= 1 or n2 <= 1:
        return None
    df = n1 + n2 - 2
    t = float(sps.t.isf(p / 2, df))
    d = t * math.sqrt(1 / n1 + 1 / n2) * (1 if diff_sign >= 0 else -1)
    return d, d_variance(d, n1, n2)


def risk_difference(x_t: int, n_t: int, x_c: int, n_c: int) -> dict[str, float | None]:
    if not n_t or not n_c:
        return {"p_t": None, "p_c": None, "rd": None, "rd_lo": None, "rd_hi": None, "nnt": None}
    p_t, p_c = x_t / n_t, x_c / n_c
    rd = p_t - p_c
    se = math.sqrt(p_t * (1 - p_t) / n_t + p_c * (1 - p_c) / n_c)
    return {"p_t": p_t, "p_c": p_c, "rd": rd, "rd_lo": rd - Z975 * se, "rd_hi": rd + Z975 * se, "nnt": nnt_from_rd(rd)}


def nnt_from_rd(rd: float | None) -> float | None:
    """NNT (benefit) or NNH (harm) = 1/|RD|, rounded up as is conventional."""
    if rd is None or abs(rd) < 1e-9:
        return None
    return float(math.ceil(1 / abs(rd)))


def pool_inverse_variance(effects: Iterable[tuple[float, float]]) -> dict[str, float] | None:
    """Fixed-effect inverse-variance pool of (estimate, variance) pairs."""
    pairs = [(e, v) for e, v in effects if e is not None and v and v > 0]
    if not pairs:
        return None
    w = [1 / v for _, v in pairs]
    est = sum(wi * e for wi, (e, _) in zip(w, pairs)) / sum(w)
    se = math.sqrt(1 / sum(w))
    q = sum(wi * (e - est) ** 2 for wi, (e, _) in zip(w, pairs))
    k = len(pairs)
    i2 = max(0.0, (q - (k - 1)) / q) if q > 0 and k > 1 else 0.0
    return {"estimate": est, "se": se, "lo": est - Z975 * se, "hi": est + Z975 * se, "k": k, "i2": i2}


def pool_risk_differences(items: Sequence[tuple[float, int, int]]) -> dict[str, float] | None:
    """Sample-size-weighted pooled risk difference from (rd, n_t, n_c)."""
    good = [(rd, nt, nc) for rd, nt, nc in items if rd is not None and nt and nc]
    if not good:
        return None
    w = [(nt * nc) / (nt + nc) for _, nt, nc in good]
    rd = sum(wi * r for wi, (r, _, _) in zip(w, good)) / sum(w)
    return {"estimate": rd, "k": len(good), "n_total": sum(nt + nc for _, nt, nc in good)}


def log_hr_from_ci(hr: float, lo: float, hi: float) -> tuple[float, float] | None:
    if not hr or not lo or not hi or hr <= 0 or lo <= 0 or hi <= 0:
        return None
    se = (math.log(hi) - math.log(lo)) / (2 * Z975)
    return math.log(hr), se ** 2


# ---------------------------------------------------------------------------
# Sample size (Adjacency C)
# ---------------------------------------------------------------------------


def n_per_arm_continuous(d: float, alpha: float = 0.05, power: float = 0.80, ratio: float = 1.0) -> int | None:
    """Two-sided two-sample comparison of means (normal approximation + t-correction)."""
    d = abs(d)
    if d <= 0:
        return None
    za = float(sps.norm.ppf(1 - alpha / 2))
    zb = float(sps.norm.ppf(power))
    n1 = (1 + 1 / ratio) * (za + zb) ** 2 / d ** 2
    n1 += za ** 2 / 4  # small-sample (t) correction
    return int(math.ceil(n1))


def n_per_arm_proportions(p1: float, p2: float, alpha: float = 0.05, power: float = 0.80) -> int | None:
    if p1 is None or p2 is None or abs(p1 - p2) < 1e-6:
        return None
    za = float(sps.norm.ppf(1 - alpha / 2))
    zb = float(sps.norm.ppf(power))
    pbar = (p1 + p2) / 2
    num = (za * math.sqrt(2 * pbar * (1 - pbar)) + zb * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2
    return int(math.ceil(num / (p1 - p2) ** 2))


def inflate_for_dropout(n: int | None, dropout: float) -> int | None:
    if n is None:
        return None
    dropout = min(max(dropout, 0.0), 0.6)
    return int(math.ceil(n / (1 - dropout)))


def power_for_n(d: float, n_per_arm: int, alpha: float = 0.05) -> float:
    za = float(sps.norm.ppf(1 - alpha / 2))
    ncp = abs(d) * math.sqrt(n_per_arm / 2)
    return float(sps.norm.cdf(ncp - za))
