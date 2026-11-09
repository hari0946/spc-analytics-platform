"""Western Electric and Nelson run rules (Phase 13). Pure functions.

Zones are measured in sigma units from the centre line: z = (x - cl) / sigma, where sigma is per point
(sigma_i = (ucl_i - cl_i) / 3), so charts with varying limits (P, U, EWMA) work too.
"beyond" means strictly greater; a point exactly on a boundary is NOT beyond it. A point exactly on the centre
line is on neither side, so it breaks "same side" runs.

A violation is reported at the index of the point that COMPLETES the pattern ("point_index"); while a pattern keeps
holding, each further point is reported as well (sliding window). `start_index` is the first point of the pattern.

Classification (violation_type): OUT_OF_CONTROL (beyond 3 sigma), SHIFT (long run on one side), TREND (monotonic
run), and PATTERN (zone, alternating, stratification and mixture patterns).
"""
from dataclasses import asdict, dataclass

import numpy as np

WESTERN_ELECTRIC = "WESTERN_ELECTRIC"
NELSON = "NELSON"


class RuleConfigError(ValueError):
    pass


@dataclass(frozen=True)
class RuleInfo:
    rule_set: str
    rule_id: str
    name: str
    description: str
    violation_type: str
    severity: str


RULES = {
    "WE1": RuleInfo(WESTERN_ELECTRIC, "WE1", "Beyond 3 sigma", "1 point beyond 3 sigma", "OUT_OF_CONTROL", "HIGH"),
    "WE2": RuleInfo(WESTERN_ELECTRIC, "WE2", "2 of 3 beyond 2 sigma", "2 of 3 consecutive points beyond 2 sigma on the same side", "PATTERN", "MEDIUM"),
    "WE3": RuleInfo(WESTERN_ELECTRIC, "WE3", "4 of 5 beyond 1 sigma", "4 of 5 consecutive points beyond 1 sigma on the same side", "PATTERN", "MEDIUM"),
    "WE4": RuleInfo(WESTERN_ELECTRIC, "WE4", "8 on one side", "8 consecutive points on the same side of the centre line", "SHIFT", "MEDIUM"),
    "N1": RuleInfo(NELSON, "N1", "Beyond 3 sigma", "1 point beyond 3 sigma", "OUT_OF_CONTROL", "HIGH"),
    "N2": RuleInfo(NELSON, "N2", "9 on one side", "9 consecutive points on the same side of the centre line", "SHIFT", "MEDIUM"),
    "N3": RuleInfo(NELSON, "N3", "6 trending", "6 consecutive points steadily increasing or decreasing", "TREND", "MEDIUM"),
    "N4": RuleInfo(NELSON, "N4", "14 alternating", "14 consecutive points alternating up and down", "PATTERN", "LOW"),
    "N5": RuleInfo(NELSON, "N5", "2 of 3 beyond 2 sigma", "2 of 3 consecutive points beyond 2 sigma on the same side", "PATTERN", "MEDIUM"),
    "N6": RuleInfo(NELSON, "N6", "4 of 5 beyond 1 sigma", "4 of 5 consecutive points beyond 1 sigma on the same side", "PATTERN", "LOW"),
    "N7": RuleInfo(NELSON, "N7", "15 within 1 sigma", "15 consecutive points within 1 sigma of the centre line (stratification)", "PATTERN", "LOW"),
    "N8": RuleInfo(NELSON, "N8", "8 beyond 1 sigma", "8 consecutive points beyond 1 sigma on either side, none within (mixture)", "PATTERN", "LOW"),
}
RULE_SETS = {WESTERN_ELECTRIC: ["WE1", "WE2", "WE3", "WE4"], NELSON: ["N1", "N2", "N3", "N4", "N5", "N6", "N7", "N8"]}


def _zscores(points, cl, sigma) -> np.ndarray:
    x = np.asarray(points, dtype=float)
    c = np.broadcast_to(np.asarray(cl, dtype=float), x.shape)
    s = np.broadcast_to(np.asarray(sigma, dtype=float), x.shape)
    if x.ndim != 1:
        raise RuleConfigError("points must be one-dimensional")
    if not (np.all(np.isfinite(x)) and np.all(np.isfinite(c)) and np.all(np.isfinite(s))):
        raise RuleConfigError("points, centre line and sigma must be finite")
    if np.any(s <= 0):
        raise RuleConfigError("sigma must be positive (the control limits are degenerate)")
    return (x - c) / s


def _find(rule_id: str, z: np.ndarray, x: np.ndarray) -> list[tuple[int, int]]:
    """Return (start_index, point_index) for every window where the rule holds."""
    n = len(z)
    out: list[tuple[int, int]] = []
    side = np.sign(z)
    if rule_id in ("WE1", "N1"):
        out = [(i, i) for i in range(n) if abs(z[i]) > 3]
    elif rule_id in ("WE4", "N2"):
        k = 8 if rule_id == "WE4" else 9
        run = 0
        for i in range(n):
            run = run + 1 if i and side[i] != 0 and side[i] == side[i - 1] else (1 if side[i] != 0 else 0)
            if run >= k:
                out.append((i - k + 1, i))
    elif rule_id in ("WE2", "N5", "WE3", "N6"):
        thr, m, w = (2, 2, 3) if rule_id in ("WE2", "N5") else (1, 4, 5)
        for i in range(w - 1, n):
            for s in (1, -1):
                beyond = [j for j in range(i - w + 1, i + 1) if z[j] * s > thr]
                if len(beyond) >= m and (i in beyond):
                    out.append((beyond[0], i))
                    break
    elif rule_id == "N3":
        k = 6
        for i in range(k - 1, n):
            diffs = np.diff(x[i - k + 1:i + 1])      # monotonic in the plotted values
            if np.all(diffs > 0) or np.all(diffs < 0):
                out.append((i - k + 1, i))
    elif rule_id == "N4":
        k = 14
        for i in range(k - 1, n):
            diffs = np.diff(x[i - k + 1:i + 1])
            if np.all(diffs[:-1] * diffs[1:] < 0):
                out.append((i - k + 1, i))
    elif rule_id == "N7":
        k = 15
        for i in range(k - 1, n):
            if np.all(np.abs(z[i - k + 1:i + 1]) < 1):
                out.append((i - k + 1, i))
    elif rule_id == "N8":
        k = 8
        for i in range(k - 1, n):
            if np.all(np.abs(z[i - k + 1:i + 1]) > 1):
                out.append((i - k + 1, i))
    return out


def detect(points, cl, sigma, rule_set: str | list[str] = "BOTH", values=None, timestamps=None) -> list[dict]:
    """Run the selected rules. `rule_set`: 'WESTERN_ELECTRIC', 'NELSON', 'BOTH' or a list of rule ids (e.g. ['N1','N3']).
    `values` (observed values to report; defaults to points) and `timestamps` are optional per-point lists."""
    if rule_set == "BOTH":
        ids = RULE_SETS[WESTERN_ELECTRIC] + RULE_SETS[NELSON]
    elif isinstance(rule_set, str) and rule_set in RULE_SETS:
        ids = RULE_SETS[rule_set]
    elif isinstance(rule_set, (list, tuple)):
        unknown = [r for r in rule_set if r not in RULES]
        if unknown:
            raise RuleConfigError(f"Unknown rule(s): {', '.join(map(str, unknown))}")
        ids = list(rule_set)
    else:
        raise RuleConfigError(f"Unknown rule set '{rule_set}'")
    z = _zscores(points, cl, sigma)
    xs = np.asarray(points, dtype=float)
    n = len(z)
    if values is not None and len(values) != n:
        raise RuleConfigError("values must have the same length as points")
    if timestamps is not None and len(timestamps) != n:
        raise RuleConfigError("timestamps must have the same length as points")
    obs = list(points) if values is None else list(values)
    result = []
    for rid in ids:
        info = RULES[rid]
        for start, idx in _find(rid, z, xs):
            result.append({**asdict(info), "point_index": idx, "start_index": start,
                           "observed_value": float(obs[idx]), "z_score": round(float(z[idx]), 6),
                           "timestamp": timestamps[idx] if timestamps is not None else None})
    result.sort(key=lambda v: (v["point_index"], v["rule_id"]))
    return result


def summarize(violations: list[dict]) -> dict:
    by_rule: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for v in violations:
        by_rule[v["rule_id"]] = by_rule.get(v["rule_id"], 0) + 1
        by_type[v["violation_type"]] = by_type.get(v["violation_type"], 0) + 1
    return {"total": len(violations), "by_rule": by_rule, "by_type": by_type,
            "points_flagged": len({v["point_index"] for v in violations})}
