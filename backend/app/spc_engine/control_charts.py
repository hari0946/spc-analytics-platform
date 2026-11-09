"""Control chart engine (Phase 11). Pure functions: no FastAPI, no Snowflake.

Charts: XBAR (with R or S), R, S, I (individuals), MR, P, NP, C, U, EWMA, CUSUM.
Every function returns a ChartResult-shaped dict:
  {"chart", "n_points", "points": [...], "cl": [...], "ucl": [...], "lcl": [...], "sigma": [...]?,
   "out_of_control": [indices], "params": {...}}
`cl/ucl/lcl` are per-point lists (constant for most charts, varying for P/U with variable n and for EWMA).

Constants (d2, d3, c4 and derived A2, A3, D3, D4, B3, B4) are computed from their definitions, then cross-checked
against the published tables in the unit tests. Invalid configurations raise ChartConfigError.
"""
import math
from functools import lru_cache

import numpy as np
from scipy import integrate, stats

MIN_N, MAX_N = 2, 25


class ChartConfigError(ValueError):
    """The data / parameters cannot produce a valid chart."""


# ------------------------------------------------------------------------------------------ constants
@lru_cache(maxsize=None)
def d2(n: int) -> float:
    """E[range of n standard normal values]."""
    f = lambda x: 1 - stats.norm.cdf(x) ** n - (1 - stats.norm.cdf(x)) ** n
    return integrate.quad(f, -np.inf, np.inf)[0]


_D3 = {2: 0.852502, 3: 0.888368, 4: 0.879808, 5: 0.864082, 6: 0.84804, 7: 0.833205, 8: 0.819831, 9: 0.807834,
       10: 0.797051, 11: 0.787315, 12: 0.778478, 13: 0.770416, 14: 0.763023, 15: 0.756211, 16: 0.749908, 17: 0.744052,
       18: 0.738591, 19: 0.733481, 20: 0.728686, 21: 0.724173, 22: 0.719915, 23: 0.715887, 24: 0.712068, 25: 0.708441}


def d3(n: int) -> float:
    """Std deviation of the range of n standard normal values (precomputed with `d3_numeric`; the double integral
    takes seconds per size, too slow for request time). Tests check these against the integral and published tables."""
    if n not in _D3:
        raise ChartConfigError(f"Subgroup size must be between {MIN_N} and {MAX_N} (got {n})")
    return _D3[n]


@lru_cache(maxsize=None)
def d3_numeric(n: int) -> float:
    def integrand(y, x):  # joint density of (min=x, max=y) times (y-x)^2
        return n * (n - 1) * (y - x) ** 2 * stats.norm.pdf(x) * stats.norm.pdf(y) * (stats.norm.cdf(y) - stats.norm.cdf(x)) ** (n - 2)
    e2 = integrate.dblquad(integrand, -9, 9, lambda x: x, lambda x: 9, epsabs=1e-10, epsrel=1e-10)[0]
    return math.sqrt(e2 - d2(n) ** 2)


@lru_cache(maxsize=None)
def c4(n: int) -> float:
    return math.sqrt(2 / (n - 1)) * math.exp(math.lgamma(n / 2) - math.lgamma((n - 1) / 2))


def _check_n(n: int) -> None:
    if not (MIN_N <= n <= MAX_N):
        raise ChartConfigError(f"Subgroup size must be between {MIN_N} and {MAX_N} (got {n})")


def factors(n: int) -> dict:
    _check_n(n)
    D2, D3, C4 = d2(n), d3(n), c4(n)
    s = math.sqrt(1 - C4 ** 2)
    return {"d2": D2, "d3": D3, "c4": C4, "A2": 3 / (D2 * math.sqrt(n)), "A3": 3 / (C4 * math.sqrt(n)),
            "D3": max(0.0, 1 - 3 * D3 / D2), "D4": 1 + 3 * D3 / D2,
            "B3": max(0.0, 1 - 3 * s / C4), "B4": 1 + 3 * s / C4}


# ------------------------------------------------------------------------------------------ helpers
def _num(values, name: str) -> np.ndarray:
    try:
        a = np.asarray([float(v) for v in values], dtype=float)
    except (TypeError, ValueError):
        raise ChartConfigError(f"{name} must be numeric")
    if a.size and not np.all(np.isfinite(a)):
        raise ChartConfigError(f"{name} contains missing or non-finite values; clean the data first")
    return a


def _subgroups(groups) -> np.ndarray:
    rows = [list(g) for g in groups]
    if len(rows) < 2:
        raise ChartConfigError("At least 2 subgroups are required")
    sizes = {len(r) for r in rows}
    if len(sizes) != 1:
        raise ChartConfigError(f"Subgroups must all have the same size for this chart (found sizes {sorted(sizes)})")
    n = sizes.pop()
    _check_n(n)
    return np.vstack([_num(r, "subgroup values") for r in rows])


def _result(chart, points, cl, ucl, lcl, params, extra=None) -> dict:
    k = len(points)
    cl, ucl, lcl = (np.broadcast_to(np.asarray(v, dtype=float), (k,)).tolist() for v in (cl, ucl, lcl))
    pts = [float(p) for p in points]
    ooc = [i for i, p in enumerate(pts) if p > ucl[i] + 1e-12 or p < lcl[i] - 1e-12]
    out = {"chart": chart, "n_points": k, "points": pts, "cl": cl, "ucl": ucl, "lcl": lcl, "out_of_control": ooc,
           "params": params}
    if extra:
        out.update(extra)
    return out


def make_subgroups(values, size: int) -> tuple[list[list[float]], int]:
    """Sequential subgroups of `size` readings (time order). Returns (subgroups, readings dropped from the incomplete tail)."""
    _check_n(size)
    v = list(values)
    k = len(v) // size
    return [v[i * size:(i + 1) * size] for i in range(k)], len(v) - k * size


# ------------------------------------------------------------------------------------------ variables charts
def xbar_chart(groups, method: str = "R") -> dict:
    """X-bar chart. method 'R' uses Rbar (limits xbarbar +- A2*Rbar); 'S' uses Sbar (xbarbar +- A3*Sbar)."""
    if method not in ("R", "S"):
        raise ChartConfigError("method must be 'R' or 'S'")
    x = _subgroups(groups)
    n = x.shape[1]
    f = factors(n)
    means = x.mean(axis=1)
    grand = means.mean()
    if method == "R":
        spread = (x.max(axis=1) - x.min(axis=1)).mean()
        half = f["A2"] * spread
    else:
        spread = x.std(axis=1, ddof=1).mean()
        half = f["A3"] * spread
    return _result("XBAR", means, grand, grand + half, grand - half,
                   {"method": method, "subgroup_size": n, "grand_mean": grand, "spread_bar": spread,
                    "sigma_hat": spread / (f["d2"] if method == "R" else f["c4"])})


def r_chart(groups) -> dict:
    x = _subgroups(groups)
    n = x.shape[1]
    f = factors(n)
    r = x.max(axis=1) - x.min(axis=1)
    rbar = r.mean()
    return _result("R", r, rbar, f["D4"] * rbar, f["D3"] * rbar, {"subgroup_size": n, "rbar": rbar})


def s_chart(groups) -> dict:
    x = _subgroups(groups)
    n = x.shape[1]
    f = factors(n)
    s = x.std(axis=1, ddof=1)
    sbar = s.mean()
    return _result("S", s, sbar, f["B4"] * sbar, f["B3"] * sbar, {"subgroup_size": n, "sbar": sbar})


def imr_chart(values) -> dict:
    """Individuals and Moving Range (span 2). Returns {'I': chart, 'MR': chart}. Sigma = MRbar / d2(2)."""
    x = _num(values, "values")
    if x.size < 3:
        raise ChartConfigError("At least 3 individual values are required")
    mr = np.abs(np.diff(x))
    mrbar = mr.mean()
    D2 = d2(2)
    mean = x.mean()
    i = _result("I", x, mean, mean + 3 * mrbar / D2, mean - 3 * mrbar / D2,
                {"mean": mean, "mrbar": mrbar, "sigma_hat": mrbar / D2})
    m = _result("MR", mr, mrbar, factors(2)["D4"] * mrbar, 0.0, {"mrbar": mrbar, "span": 2})
    return {"I": i, "MR": m}


# ------------------------------------------------------------------------------------------ attribute charts
def _counts(nonconf, sizes=None, name="counts"):
    d = _num(nonconf, name)
    if d.size < 2:
        raise ChartConfigError("At least 2 samples are required")
    if np.any(d < 0) or np.any(d != np.floor(d)):
        raise ChartConfigError(f"{name} must be non-negative whole numbers")
    if sizes is None:
        return d, None
    n = _num(sizes, "sample sizes")
    if n.size != d.size:
        raise ChartConfigError("counts and sample sizes must have the same length")
    if np.any(n <= 0) or np.any(n != np.floor(n)):
        raise ChartConfigError("sample sizes must be positive whole numbers")
    return d, n


def p_chart(defectives, sample_sizes) -> dict:
    d, n = _counts(defectives, sample_sizes, "defectives")
    if np.any(d > n):
        raise ChartConfigError("defectives cannot exceed the sample size")
    pbar = d.sum() / n.sum()
    se = np.sqrt(pbar * (1 - pbar) / n)
    return _result("P", d / n, pbar, np.minimum(1.0, pbar + 3 * se), np.maximum(0.0, pbar - 3 * se),
                   {"pbar": pbar, "variable_limits": bool(np.ptp(n) > 0)})


def np_chart(defectives, sample_size) -> dict:
    """NP chart needs a CONSTANT sample size (an int, or a list with identical values)."""
    sizes = [sample_size] * len(list(defectives)) if np.isscalar(sample_size) else list(sample_size)
    d, n = _counts(defectives, sizes, "defectives")
    if np.ptp(n) > 0:
        raise ChartConfigError("NP chart requires a constant sample size; use a P chart for varying sizes")
    if np.any(d > n):
        raise ChartConfigError("defectives cannot exceed the sample size")
    n0 = n[0]
    pbar = d.sum() / n.sum()
    half = 3 * math.sqrt(n0 * pbar * (1 - pbar))
    return _result("NP", d, n0 * pbar, min(n0, n0 * pbar + half), max(0.0, n0 * pbar - half),
                   {"pbar": pbar, "sample_size": int(n0)})


def c_chart(defects) -> dict:
    """C chart: defects per constant-size inspection unit."""
    c, _ = _counts(defects, None, "defects")
    cbar = c.mean()
    return _result("C", c, cbar, cbar + 3 * math.sqrt(cbar), max(0.0, cbar - 3 * math.sqrt(cbar)), {"cbar": cbar})


def u_chart(defects, units) -> dict:
    """U chart: defects per unit with varying inspection-unit counts."""
    c, n = _counts(defects, units, "defects")
    ubar = c.sum() / n.sum()
    se = np.sqrt(ubar / n)
    return _result("U", c / n, ubar, ubar + 3 * se, np.maximum(0.0, ubar - 3 * se),
                   {"ubar": ubar, "variable_limits": bool(np.ptp(n) > 0)})


# ------------------------------------------------------------------------------------------ memory charts
def _sigma_from_mr(x: np.ndarray) -> float:
    if x.size < 3:
        raise ChartConfigError("At least 3 values are needed to estimate sigma")
    s = np.abs(np.diff(x)).mean() / d2(2)
    if s <= 0:
        raise ChartConfigError("Cannot estimate sigma: the data are constant")
    return float(s)


def ewma_chart(values, lam: float = 0.2, L: float = 3.0, target: float | None = None, sigma: float | None = None) -> dict:
    """EWMA: z_i = lam*x_i + (1-lam)*z_{i-1}, z_0 = target. Limits: target +- L*sigma*sqrt(lam/(2-lam)*(1-(1-lam)^(2i)))."""
    x = _num(values, "values")
    if not (0 < lam <= 1):
        raise ChartConfigError("lambda must be in (0, 1]")
    if L <= 0:
        raise ChartConfigError("L must be positive")
    mu0 = float(x.mean()) if target is None else float(target)
    sig = _sigma_from_mr(x) if sigma is None else float(sigma)
    if sig <= 0:
        raise ChartConfigError("sigma must be positive")
    z = np.empty_like(x)
    prev = mu0
    for i, v in enumerate(x):
        prev = lam * v + (1 - lam) * prev
        z[i] = prev
    i = np.arange(1, x.size + 1)
    w = L * sig * np.sqrt(lam / (2 - lam) * (1 - (1 - lam) ** (2 * i)))
    return _result("EWMA", z, mu0, mu0 + w, mu0 - w, {"lambda": lam, "L": L, "target": mu0, "sigma": sig})


def cusum_chart(values, k: float = 0.5, h: float = 5.0, target: float | None = None, sigma: float | None = None) -> dict:
    """Tabular CUSUM: C+_i = max(0, x_i-(mu0+K)+C+_{i-1}); C-_i = max(0, (mu0-K)-x_i+C-_{i-1}); K=k*sigma, H=h*sigma.
    `points` = C+, `lower_points` = -C-; signal when C+ > H or C- > H."""
    x = _num(values, "values")
    if k < 0 or h <= 0:
        raise ChartConfigError("k must be >= 0 and h must be > 0")
    mu0 = float(x.mean()) if target is None else float(target)
    sig = _sigma_from_mr(x) if sigma is None else float(sigma)
    if sig <= 0:
        raise ChartConfigError("sigma must be positive")
    K, H = k * sig, h * sig
    cp, cm = np.zeros_like(x), np.zeros_like(x)
    pp = pm = 0.0
    for i, v in enumerate(x):
        pp = max(0.0, v - (mu0 + K) + pp)
        pm = max(0.0, (mu0 - K) - v + pm)
        cp[i], cm[i] = pp, pm
    n = x.size
    ooc = [i for i in range(n) if cp[i] > H or cm[i] > H]
    return {"chart": "CUSUM", "n_points": n, "points": cp.tolist(), "lower_points": (-cm).tolist(),
            "cl": [0.0] * n, "ucl": [H] * n, "lcl": [-H] * n, "out_of_control": ooc,
            "params": {"k": k, "h": h, "target": mu0, "sigma": sig, "K": K, "H": H}}


CHART_TYPES = ["XBAR_R", "XBAR_S", "R", "S", "IMR", "P", "NP", "C", "U", "EWMA", "CUSUM"]
