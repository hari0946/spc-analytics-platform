"""Descriptive statistics (Phase 10). Pure functions: no FastAPI, no Snowflake.

Conventions
- Missing values (None / NaN / inf) are removed and counted in `n_missing`.
- variance / std are SAMPLE statistics (n-1); population versions are given separately.
- skewness is the adjusted Fisher-Pearson coefficient (G1); kurtosis is EXCESS kurtosis (G2), both bias-corrected
  (the same definitions as Excel SKEW/KURT and scipy bias=False). Undefined cases return None, never a made-up number.
- percentiles use linear interpolation between order statistics (numpy default / Excel PERCENTILE.INC).
- mode: the most frequent value; ties -> all tied values are in `modes`, `mode` is the smallest; if every value is
  unique there is no mode (None).
"""
import math
from collections import Counter
from typing import Iterable

import numpy as np
from scipy import stats as sps

PERCENTILES = (1, 5, 10, 25, 50, 75, 90, 95, 99)


def clean(values: Iterable) -> tuple[np.ndarray, int]:
    """Return (finite float array, number of removed missing/non-finite values)."""
    raw = list(values)
    out = []
    for v in raw:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            out.append(f)
    return np.asarray(out, dtype=float), len(raw) - len(out)


def _round(x: float | None, nd: int = 12) -> float | None:
    return None if x is None else round(float(x), nd)


def descriptive(values: Iterable) -> dict:
    x, n_missing = clean(values)
    n = int(x.size)
    res: dict = {"n": n, "n_missing": n_missing, "mean": None, "median": None, "mode": None, "modes": [],
                 "min": None, "max": None, "range": None, "variance": None, "std": None, "pop_variance": None,
                 "pop_std": None, "sem": None, "skewness": None, "kurtosis": None, "cv": None,
                 "percentiles": {f"p{p}": None for p in PERCENTILES}, "q1": None, "q2": None, "q3": None, "iqr": None,
                 "notes": []}
    if n == 0:
        res["notes"].append("no data")
        return res
    res["mean"] = _round(x.mean())
    res["median"] = _round(np.median(x))
    res["min"], res["max"] = _round(x.min()), _round(x.max())
    res["range"] = _round(x.max() - x.min())
    counts = Counter(x.tolist())
    top = max(counts.values())
    if top > 1:
        modes = sorted(v for v, c in counts.items() if c == top)
        res["modes"], res["mode"] = modes, modes[0]
        if len(modes) > 1:
            res["notes"].append(f"multimodal: {len(modes)} values share the highest frequency ({top})")
    else:
        res["notes"].append("no mode: all values are unique")
    res["pop_variance"] = _round(x.var(ddof=0))
    res["pop_std"] = _round(x.std(ddof=0))
    pct = np.percentile(x, PERCENTILES)
    res["percentiles"] = {f"p{p}": _round(v) for p, v in zip(PERCENTILES, pct)}
    q1, q2, q3 = np.percentile(x, [25, 50, 75])
    res.update(q1=_round(q1), q2=_round(q2), q3=_round(q3), iqr=_round(q3 - q1))
    if n >= 2:
        res["variance"] = _round(x.var(ddof=1))
        res["std"] = _round(x.std(ddof=1))
        res["sem"] = _round(x.std(ddof=1) / math.sqrt(n))
        if x.mean() != 0 and x.std(ddof=1) > 0:
            res["cv"] = _round(x.std(ddof=1) / abs(x.mean()))
    else:
        res["notes"].append("variance and standard deviation need at least 2 values")
    constant = bool(np.ptp(x) == 0)
    if constant and n >= 2:
        res["notes"].append("constant data: skewness and kurtosis are undefined")
    else:
        if n >= 3:
            res["skewness"] = _round(sps.skew(x, bias=False))
        else:
            res["notes"].append("skewness needs at least 3 values")
        if n >= 4:
            res["kurtosis"] = _round(sps.kurtosis(x, fisher=True, bias=False))
        else:
            res["notes"].append("kurtosis needs at least 4 values")
    return res
