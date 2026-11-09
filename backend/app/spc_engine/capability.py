"""Process capability (Phase 14). Pure functions.

Definitions (mu = sample mean)
  sigma_within  : short-term. Subgroups given -> Rbar/d2(n); otherwise individuals -> MRbar/d2(2)  (time-ordered data)
  sigma_overall : long-term, sample standard deviation (n-1)
  CPU = (USL-mu)/(3 sigma_within)   CPL = (mu-LSL)/(3 sigma_within)   Cpk = min(CPU, CPL)   Cp = (USL-LSL)/(6 sigma_within)
  PPU/PPL/Ppk/Pp: same with sigma_overall.
  PPM: observed (count outside spec / n * 1e6) and expected under a normal model (overall and within sigma).
  Yield %: observed (share of readings inside spec) and expected (normal model, overall sigma).
One-sided specifications give only the matching CPU/CPL (Cp/Pp are undefined). Nothing is invented: undefined indices
are None with an explanation in `notes`. When normality is rejected, percentile-based Pp/Ppk (ISO 22514) are added.
"""
import math

import numpy as np
from scipy import stats

from app.spc_engine import control_charts as cc
from app.spc_engine.statistics import clean

MIN_N = 2
RECOMMENDED_N = 30
ALPHA = 0.05


class CapabilityConfigError(ValueError):
    pass


def _spec(v, name):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise CapabilityConfigError(f"{name} must be a number")
    if not math.isfinite(f):
        raise CapabilityConfigError(f"{name} must be finite")
    return f


def validate_specs(usl=None, lsl=None, target=None) -> tuple[float | None, float | None, float | None]:
    usl, lsl, target = _spec(usl, "USL"), _spec(lsl, "LSL"), _spec(target, "Target")
    if usl is None and lsl is None:
        raise CapabilityConfigError("At least one specification limit (USL or LSL) is required")
    if usl is not None and lsl is not None and usl <= lsl:
        raise CapabilityConfigError(f"USL ({usl}) must be greater than LSL ({lsl})")
    if target is not None:
        if (usl is not None and target > usl) or (lsl is not None and target < lsl):
            raise CapabilityConfigError("Target must lie within the specification limits")
    return usl, lsl, target


def _phi(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def sigma_within(x: np.ndarray, subgroups=None) -> tuple[float | None, str]:
    if subgroups is not None:
        g = np.asarray([list(map(float, s)) for s in subgroups], dtype=float)
        if g.ndim != 2 or g.shape[0] < 2:
            raise CapabilityConfigError("At least 2 equal-sized subgroups are required")
        n = g.shape[1]
        if not (cc.MIN_N <= n <= cc.MAX_N):
            raise CapabilityConfigError(f"Subgroup size must be between {cc.MIN_N} and {cc.MAX_N}")
        rbar = float((g.max(axis=1) - g.min(axis=1)).mean())
        return rbar / cc.d2(n), f"Rbar/d2 (subgroup size {n})"
    if x.size < 3:
        return None, "MRbar/d2"
    return float(np.abs(np.diff(x)).mean() / cc.d2(2)), "MRbar/d2 (moving range of consecutive readings)"


def _percentile_method(x, usl, lsl):
    p00135, p50, p99865 = np.percentile(x, [0.135, 50, 99.865])
    out = {"p0.135": float(p00135), "p50": float(p50), "p99.865": float(p99865)}
    spread = p99865 - p00135
    out["pp"] = float((usl - lsl) / spread) if usl is not None and lsl is not None and spread > 0 else None
    ppu = (usl - p50) / (p99865 - p50) if usl is not None and p99865 > p50 else None
    ppl = (p50 - lsl) / (p50 - p00135) if lsl is not None and p50 > p00135 else None
    cands = [v for v in (ppu, ppl) if v is not None]
    out["ppu"], out["ppl"] = (None if ppu is None else float(ppu)), (None if ppl is None else float(ppl))
    out["ppk"] = float(min(cands)) if cands else None
    return out


def analyze(values, usl=None, lsl=None, target=None, subgroups=None, sigma_within_override: float | None = None) -> dict:
    usl, lsl, target = validate_specs(usl, lsl, target)
    x, n_missing = clean(values)
    n = int(x.size)
    if n < MIN_N:
        raise CapabilityConfigError(f"Insufficient data: at least {MIN_N} valid readings are required (got {n})")
    notes, warnings = [], []
    if n < RECOMMENDED_N:
        warnings.append(f"Small sample (n={n} < {RECOMMENDED_N}): capability indices are unreliable")
    mu, s_overall = float(x.mean()), float(x.std(ddof=1))
    s_within, within_method = (float(sigma_within_override), "user supplied") if sigma_within_override else sigma_within(x, subgroups)
    if s_within is not None and s_within <= 0:
        s_within = None
    res = {"n": n, "n_missing": n_missing, "mean": mu, "usl": usl, "lsl": lsl, "target": target,
           "sigma_overall": s_overall, "sigma_within": s_within, "sigma_within_method": within_method,
           "sufficient_sample": n >= RECOMMENDED_N,
           "cp": None, "cpk": None, "cpu": None, "cpl": None, "pp": None, "ppk": None, "ppu": None, "ppl": None,
           "ppm_observed_below": None, "ppm_observed_above": None, "ppm_observed_total": None,
           "ppm_expected_overall": None, "ppm_expected_within": None, "yield_observed": None, "yield_expected": None,
           "cpm": None, "normality": None, "non_normal": False, "percentile_method": None,
           "notes": notes, "warnings": warnings}

    # observed performance (needs no distribution assumption)
    below = int((x < lsl).sum()) if lsl is not None else 0
    above = int((x > usl).sum()) if usl is not None else 0
    res["ppm_observed_below"] = below / n * 1e6 if lsl is not None else None
    res["ppm_observed_above"] = above / n * 1e6 if usl is not None else None
    res["ppm_observed_total"] = (below + above) / n * 1e6
    res["yield_observed"] = (1 - (below + above) / n) * 100

    if s_overall <= 0:
        notes.append("All readings are identical: sigma is 0, so capability indices are undefined")
        inside = (usl is None or mu <= usl) and (lsl is None or mu >= lsl)
        res["yield_expected"] = 100.0 if inside else 0.0
        return res

    def indices(sigma):
        cpu = (usl - mu) / (3 * sigma) if usl is not None else None
        cpl = (mu - lsl) / (3 * sigma) if lsl is not None else None
        cands = [v for v in (cpu, cpl) if v is not None]
        cp = (usl - lsl) / (6 * sigma) if usl is not None and lsl is not None else None
        return cp, min(cands), cpu, cpl

    res["pp"], res["ppk"], res["ppu"], res["ppl"] = indices(s_overall)
    if s_within:
        res["cp"], res["cpk"], res["cpu"], res["cpl"] = indices(s_within)
    else:
        notes.append("Within-subgroup sigma could not be estimated (too few readings or no variation between consecutive readings)")
    if target is not None and usl is not None and lsl is not None:
        res["cpm"] = (usl - lsl) / (6 * math.sqrt(s_overall ** 2 + (mu - target) ** 2))

    def expected_ppm(sigma):
        lo = _phi((lsl - mu) / sigma) if lsl is not None else 0.0
        hi = 1 - _phi((usl - mu) / sigma) if usl is not None else 0.0
        return (lo + hi) * 1e6

    res["ppm_expected_overall"] = expected_ppm(s_overall)
    res["ppm_expected_within"] = expected_ppm(s_within) if s_within else None
    res["yield_expected"] = 100 - res["ppm_expected_overall"] / 1e4
    if usl is None or lsl is None:
        notes.append("One-sided specification: Cp and Pp are not defined")

    # normality & non-parametric alternative
    if n >= 3 and np.ptp(x) > 0:
        if n <= 5000:
            w, p = stats.shapiro(x)
            test = "Shapiro-Wilk"
        else:
            w, p = stats.normaltest(x)
            test = "D'Agostino K2"
        res["normality"] = {"test": test, "statistic": float(w), "p_value": float(p), "alpha": ALPHA, "normal": bool(p >= ALPHA)}
        if p < ALPHA:
            res["non_normal"] = True
            warnings.append(f"Data are not normally distributed ({test} p={p:.4g}); indices based on a normal model may mislead. "
                            "Percentile-method Pp/Ppk are provided.")
            res["percentile_method"] = _percentile_method(x, usl, lsl)
    return res
