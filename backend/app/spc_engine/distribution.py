"""Distribution analysis (Phase 15): histogram, box plot, normality tests, probability plot, distribution fitting."""
import math
import warnings

import numpy as np
from scipy import stats

from app.spc_engine.statistics import clean

ALPHA = 0.05
MAX_BINS = 200
MAX_PLOT_POINTS = 1000


class DistributionConfigError(ValueError):
    pass


def _data(values, min_n: int, what: str) -> np.ndarray:
    x, _ = clean(values)
    if x.size < min_n:
        raise DistributionConfigError(f"{what} needs at least {min_n} valid readings (got {x.size})")
    return x


def histogram(values, bins: int | str | None = None) -> dict:
    x = _data(values, 1, "Histogram")
    if x.min() == x.max():
        edges = np.array([x[0] - 0.5, x[0] + 0.5])
        counts = np.array([x.size])
    else:
        if bins is None:
            q75, q25 = np.percentile(x, [75, 25])
            method = "fd" if q75 > q25 else "sturges"
            edges = np.histogram_bin_edges(x, bins=method)
            if len(edges) - 1 > MAX_BINS:
                edges = np.histogram_bin_edges(x, bins=MAX_BINS)
        else:
            if isinstance(bins, int) and not (1 <= bins <= MAX_BINS):
                raise DistributionConfigError(f"bins must be between 1 and {MAX_BINS}")
            edges = np.histogram_bin_edges(x, bins=bins)
        counts, edges = np.histogram(x, bins=edges)
    width = np.diff(edges)
    mu, sd = float(x.mean()), float(x.std(ddof=1)) if x.size > 1 else 0.0
    centers = (edges[:-1] + edges[1:]) / 2
    normal_curve = (stats.norm.pdf(centers, mu, sd) * x.size * width).tolist() if sd > 0 else None   # expected counts per bin
    return {"n": int(x.size), "bin_edges": edges.tolist(), "counts": counts.astype(int).tolist(),
            "density": (counts / (x.size * width)).tolist(), "bin_centers": centers.tolist(),
            "normal_expected_counts": normal_curve}


def boxplot(values, whisker: float = 1.5) -> dict:
    """Tukey box plot: whiskers reach the most extreme values within whisker*IQR of the quartiles."""
    x = _data(values, 1, "Box plot")
    q1, med, q3 = (float(v) for v in np.percentile(x, [25, 50, 75]))
    iqr = q3 - q1
    lo_fence, hi_fence = q1 - whisker * iqr, q3 + whisker * iqr
    inside = x[(x >= lo_fence) & (x <= hi_fence)]
    outliers = np.sort(x[(x < lo_fence) | (x > hi_fence)])
    return {"n": int(x.size), "q1": q1, "median": med, "q3": q3, "iqr": iqr, "mean": float(x.mean()),
            "whisker_low": float(inside.min()), "whisker_high": float(inside.max()),
            "lower_fence": lo_fence, "upper_fence": hi_fence, "outliers": outliers.tolist(), "n_outliers": int(outliers.size)}


def normality(values, alpha: float = ALPHA) -> dict:
    x = _data(values, 3, "Normality test")
    n = int(x.size)
    if np.ptp(x) == 0:
        return {"n": n, "alpha": alpha, "tests": [], "normal": None, "conclusion": "Undefined: all values are identical"}
    tests = []
    if n <= 5000:
        w, p = stats.shapiro(x)
        tests.append({"test": "Shapiro-Wilk", "statistic": float(w), "p_value": float(p), "normal": bool(p >= alpha)})
    else:
        tests.append({"test": "Shapiro-Wilk", "statistic": None, "p_value": None, "normal": None,
                      "note": "not used above 5000 readings (p-values become unreliable)"})
    if n >= 8:
        k2, p = stats.normaltest(x)
        tests.append({"test": "D'Agostino K2", "statistic": float(k2), "p_value": float(p), "normal": bool(p >= alpha)})
    jb, p = stats.jarque_bera(x)
    tests.append({"test": "Jarque-Bera", "statistic": float(jb), "p_value": float(p), "normal": bool(p >= alpha)})
    ad = stats.anderson(x, dist="norm")
    crit = dict(zip([float(s) for s in ad.significance_level], [float(c) for c in ad.critical_values]))
    c5 = crit.get(5.0)
    tests.append({"test": "Anderson-Darling", "statistic": float(ad.statistic), "p_value": None,
                  "critical_values": crit, "normal": bool(ad.statistic < c5) if c5 is not None else None})
    primary = tests[0] if tests[0]["normal"] is not None else tests[1]
    return {"n": n, "alpha": alpha, "tests": tests, "primary_test": primary["test"], "normal": primary["normal"],
            "conclusion": (f"{primary['test']} p={primary['p_value']:.4g}: " +
                           ("no evidence against normality" if primary["normal"] else "data are not normally distributed"))}


def probability_plot(values) -> dict:
    """Normal probability plot: ordered data against theoretical normal quantiles, with the least-squares line."""
    x = _data(values, 3, "Probability plot")
    (osm, osr), (slope, intercept, r) = stats.probplot(x, dist="norm")
    idx = np.arange(len(osm))
    sampled = False
    if len(osm) > MAX_PLOT_POINTS:
        idx = np.unique(np.linspace(0, len(osm) - 1, MAX_PLOT_POINTS).astype(int))
        sampled = True
    return {"n": int(x.size), "theoretical_quantiles": osm[idx].tolist(), "ordered_values": osr[idx].tolist(),
            "slope": float(slope), "intercept": float(intercept), "r": float(r), "r_squared": float(r * r),
            "sampled_for_display": sampled}


_CANDIDATES = {
    # name: (scipy dist, needs positive data, fit kwargs, number of free parameters)
    "norm": (stats.norm, False, {}, 2),
    "lognorm": (stats.lognorm, True, {"floc": 0}, 2),
    "weibull_min": (stats.weibull_min, True, {"floc": 0}, 2),
    "gamma": (stats.gamma, True, {"floc": 0}, 2),
    "expon": (stats.expon, True, {"floc": 0}, 1),
    "uniform": (stats.uniform, False, {}, 2),
}


def fit_distributions(values) -> dict:
    """MLE fit of several candidates, ranked by AIC. Positive-support families are skipped when data are not all > 0.
    KS p-values use parameters estimated from the same data, so they are optimistic (approximate)."""
    x = _data(values, 8, "Distribution fitting")
    if np.ptp(x) == 0:
        raise DistributionConfigError("Distribution fitting is undefined: all values are identical")
    n = x.size
    positive = bool((x > 0).all())
    fits, skipped = [], []
    for name, (dist, needs_pos, kw, k) in _CANDIDATES.items():
        if needs_pos and not positive:
            skipped.append({"distribution": name, "reason": "requires all values > 0"})
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                params = dist.fit(x, **kw)
                ll = float(np.sum(dist.logpdf(x, *params)))
                ks = stats.kstest(x, dist.cdf, args=params)
        except Exception as e:  # noqa: BLE001 - a failing family must not break the analysis
            skipped.append({"distribution": name, "reason": f"fit failed ({type(e).__name__})"})
            continue
        if not math.isfinite(ll):
            skipped.append({"distribution": name, "reason": "likelihood is not finite"})
            continue
        fits.append({"distribution": name, "parameters": [float(p) for p in params], "n_free_parameters": k,
                     "log_likelihood": ll, "aic": 2 * k - 2 * ll, "bic": k * math.log(n) - 2 * ll,
                     "ks_statistic": float(ks.statistic), "ks_p_value": float(ks.pvalue)})
    fits.sort(key=lambda f: f["aic"])
    for rank, f in enumerate(fits, 1):
        f["rank"] = rank
    return {"n": int(n), "best": fits[0]["distribution"] if fits else None, "fits": fits, "skipped": skipped,
            "note": "Ranked by AIC (lower is better). KS p-values are approximate because parameters were fitted to the same data."}


def analyze(values, bins=None) -> dict:
    x = _data(values, 3, "Distribution analysis")
    out = {"n": int(x.size), "histogram": histogram(x, bins), "boxplot": boxplot(x), "normality": normality(x),
           "probability_plot": probability_plot(x), "fit": None, "notes": []}
    try:
        out["fit"] = fit_distributions(x)
    except DistributionConfigError as e:
        out["notes"].append(str(e))
    return out
