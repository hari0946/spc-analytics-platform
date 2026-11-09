"""Analysis orchestration: load the selected historical readings from GOLD (one SQL query), run the pure SPC engines,
and shape JSON results. Persistence of results lives in analysis_store.py."""
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from app.core import snowflake as sf
from app.core.exceptions import UnprocessableError
from app.services import data_service
from app.services import pipeline_sql as q
from app.services.filters import DataFilters, build_where
from app.spc_engine import capability as cap
from app.spc_engine import control_charts as cc
from app.spc_engine import distribution as dist
from app.spc_engine import rules as rl
from app.spc_engine import statistics as st

MAX_READINGS = 200_000
VARIABLE_CHARTS = ["XBAR_R", "XBAR_S", "R", "S", "IMR", "EWMA", "CUSUM"]
ATTRIBUTE_CHARTS = ["P", "NP", "C", "U"]
ChartName = Literal["XBAR_R", "XBAR_S", "R", "S", "IMR", "EWMA", "CUSUM", "P", "NP", "C", "U"]
UPLOADS = data_service.UPLOADS


class ChartOptions(BaseModel):
    """Chart / analysis settings given as query parameters."""
    chart: ChartName = "IMR"
    subgroup_size: int | None = Field(None, ge=1, le=100, description="sequential subgroups of this many readings (2-25)")
    subgroup_by: Literal["size", "batch"] = "size"
    lam: float = Field(0.2, gt=0, le=1, description="EWMA lambda")
    L: float = Field(3.0, gt=0, le=10, description="EWMA limit width in sigmas")
    k: float = Field(0.5, ge=0, le=5, description="CUSUM allowance in sigmas")
    h: float = Field(5.0, gt=0, le=20, description="CUSUM decision interval in sigmas")
    target: float | None = None
    sigma: float | None = Field(None, gt=0)
    usl: float | None = None
    lsl: float | None = None
    rule_set: Literal["WESTERN_ELECTRIC", "NELSON", "BOTH"] = "BOTH"
    bins: int | None = Field(None, ge=1, le=200)


@dataclass
class Readings:
    times: list
    values: list[float]
    subgroup_ids: list
    batches: list
    series_count: int
    upload_ids: list[str]


def _iso(t) -> str:
    return t.strftime("%Y-%m-%d %H:%M:%S") if t is not None else None


def _engine_errors(fn):
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except (cc.ChartConfigError, rl.RuleConfigError, cap.CapabilityConfigError, dist.DistributionConfigError) as e:
            raise UnprocessableError(str(e))
    return wrapper


# ---------------------------------------------------------------------------------------------- data access
def load_readings(f: DataFilters, parameter: str) -> Readings:
    if not parameter or not parameter.strip():
        raise UnprocessableError("'parameter' is required")
    f = DataFilters(**{**f.__dict__, "parameters": [parameter.strip().upper()]})
    data_service.check_datasets(f, "sensor")
    where, params = build_where(f, "sensor", UPLOADS)
    rows = sf.execute(
        f"SELECT MEASURED_AT, VALUE, SUBGROUP_ID, BATCH_ID, UPLOAD_ID, HASH(PLANT_CODE, LINE_CODE, ASSET_CODE, TAG_NAME) "
        f"FROM {q.GOLD_SENSOR} {where} ORDER BY {data_service.SENSOR_ORDER} LIMIT {MAX_READINGS + 1}", params)
    if len(rows) > MAX_READINGS:
        raise UnprocessableError(f"The selection has more than {MAX_READINGS:,} readings; narrow the filters or date range")
    return Readings(times=[r[0] for r in rows], values=[float(r[1]) for r in rows], subgroup_ids=[r[2] for r in rows],
                    batches=[r[3] for r in rows], series_count=len({r[5] for r in rows}),
                    upload_ids=sorted({r[4] for r in rows}))


def load_production(f: DataFilters) -> dict:
    data_service.check_datasets(f, "production")
    where, params = build_where(f, "production", UPLOADS)
    rows = sf.execute(
        f"SELECT EVENT_TIME, SAMPLE_SIZE, DEFECTIVE_COUNT, DEFECT_COUNT, UNITS_PRODUCED, UPLOAD_ID FROM {q.GOLD_PROD} {where} "
        f"ORDER BY {data_service.PRODUCTION_ORDER} LIMIT {MAX_READINGS + 1}", params)
    if len(rows) > MAX_READINGS:
        raise UnprocessableError(f"The selection has more than {MAX_READINGS:,} samples; narrow the filters")
    return {"rows": rows, "upload_ids": sorted({r[5] for r in rows})}


# ---------------------------------------------------------------------------------------------- charts
def _group_readings(r: Readings, o: ChartOptions):
    """Return (subgroups, subgroup labels (time of first reading), notes)."""
    notes = []
    if o.subgroup_by == "batch":
        if any(s is None for s in r.subgroup_ids):
            raise UnprocessableError("Batch subgroups need a batch column mapped in the data (some readings have no batch)")
        groups: dict = {}
        order = []
        for t, v, s in zip(r.times, r.values, r.subgroup_ids):
            if s not in groups:
                groups[s] = []
                order.append(s)
            groups[s].append((t, v))
        subs = [[v for _, v in groups[s]] for s in order]
        labels = [_iso(groups[s][0][0]) for s in order]
        return subs, labels, notes
    n = o.subgroup_size
    if n is None:
        raise UnprocessableError("'subgroup_size' (2-25) is required for this chart")
    try:
        subs, dropped = cc.make_subgroups(r.values, n)
    except cc.ChartConfigError as e:
        raise UnprocessableError(str(e))
    if dropped:
        notes.append(f"{dropped} trailing reading(s) did not fill a subgroup of {n} and were not used")
    return subs, [_iso(r.times[i * n]) for i in range(len(subs))], notes


def _attach(chart: dict, labels: list[str | None]) -> dict:
    chart["labels"] = labels
    return chart


@_engine_errors
def build_charts(f: DataFilters, parameter: str | None, o: ChartOptions):
    """Returns (charts dict {name: chart}, primary chart name, notes, upload_ids, n_readings, series_count)."""
    notes: list[str] = []
    chart = o.chart
    if chart in ATTRIBUTE_CHARTS:
        prod = load_production(f)
        rows = prod["rows"]
        if chart in ("P", "NP"):
            usable = [r for r in rows if r[1] is not None and r[2] is not None]
            need = "sample_size and defective_count"
        else:
            unit_ok = [r for r in rows if r[3] is not None]
            usable = [r for r in unit_ok if (chart == "C" or r[1] is not None)]
            need = "defect_count" + (" and sample_size" if chart == "U" else "")
        if len(usable) < len(rows):
            notes.append(f"{len(rows) - len(usable)} sample(s) lack {need} and were skipped (nothing is imputed)")
        labels = [_iso(r[0]) for r in usable]
        if chart == "P":
            res = cc.p_chart([r[2] for r in usable], [r[1] for r in usable])
        elif chart == "NP":
            res = cc.np_chart([r[2] for r in usable], [r[1] for r in usable])
        elif chart == "C":
            res = cc.c_chart([r[3] for r in usable])
        else:
            res = cc.u_chart([r[3] for r in usable], [r[1] for r in usable])
        return {chart: _attach(res, labels)}, chart, notes, prod["upload_ids"], len(usable), 1

    r = load_readings(f, parameter)
    if not r.values:
        raise UnprocessableError("No historical readings match the selection")
    if r.series_count > 1:
        notes.append(f"The selection combines {r.series_count} sensors/assets; narrow the filters to chart a single process")
    t_labels = [_iso(t) for t in r.times]
    if chart in ("XBAR_R", "XBAR_S", "R", "S"):
        subs, labels, gnotes = _group_readings(r, o)
        notes += gnotes
        charts = {}
        if chart in ("XBAR_R", "XBAR_S"):
            charts["XBAR"] = _attach(cc.xbar_chart(subs, "R" if chart == "XBAR_R" else "S"), labels)
            charts["R" if chart == "XBAR_R" else "S"] = _attach(cc.r_chart(subs) if chart == "XBAR_R" else cc.s_chart(subs), labels)
            return charts, "XBAR", notes, r.upload_ids, len(r.values), r.series_count
        charts[chart] = _attach(cc.r_chart(subs) if chart == "R" else cc.s_chart(subs), labels)
        return charts, chart, notes, r.upload_ids, len(r.values), r.series_count
    if chart == "IMR":
        out = cc.imr_chart(r.values)
        return ({"I": _attach(out["I"], t_labels), "MR": _attach(out["MR"], t_labels[1:])}, "I", notes, r.upload_ids,
                len(r.values), r.series_count)
    if chart == "EWMA":
        res = cc.ewma_chart(r.values, o.lam, o.L, o.target, o.sigma)
    else:
        res = cc.cusum_chart(r.values, o.k, o.h, o.target, o.sigma)
    return {chart: _attach(res, t_labels)}, chart, notes, r.upload_ids, len(r.values), r.series_count


# ---------------------------------------------------------------------------------------------- analyses
def statistics_analysis(f: DataFilters, parameter: str) -> dict:
    r = load_readings(f, parameter)
    res = st.descriptive(r.values)
    notes = res["notes"] + ([f"The selection combines {r.series_count} sensors/assets"] if r.series_count > 1 else [])
    return {"kind": "STATISTICS", "parameter": parameter.upper(), "upload_ids": r.upload_ids, "statistics": res,
            "first": _iso(r.times[0]) if r.times else None, "last": _iso(r.times[-1]) if r.times else None,
            "notes": notes}


def control_chart_analysis(f: DataFilters, parameter: str | None, o: ChartOptions) -> dict:
    charts, primary, notes, uploads, n, series = build_charts(f, parameter, o)
    return {"kind": "CONTROL_CHART", "parameter": (parameter or "").upper() or None, "chart": o.chart, "primary": primary,
            "charts": charts, "n_readings": n, "upload_ids": uploads, "notes": notes,
            "out_of_control": {k: c["out_of_control"] for k, c in charts.items()}}


@_engine_errors
def limits_analysis(f: DataFilters, parameter: str | None, o: ChartOptions) -> dict:
    """Phase 12: control limits (UCL/CL/LCL) with spec limits (USL/LSL) and target against the historical values."""
    usl = lsl = target = None
    if o.usl is not None or o.lsl is not None:
        usl, lsl, target = cap.validate_specs(o.usl, o.lsl, o.target)
    elif o.target is not None and o.chart not in ("EWMA", "CUSUM"):
        target = o.target
    charts, primary, notes, uploads, n, series = build_charts(f, parameter, o)
    c = charts[primary]
    pts = c["points"]
    k = len(pts)
    out_ctrl = len(c["out_of_control"])
    a: dict = {"n_points": k, "points_out_of_control": out_ctrl, "pct_out_of_control": out_ctrl / k * 100 if k else None}
    individual = primary in ("I", "XBAR") and o.chart not in ("EWMA", "CUSUM")
    if usl is not None or lsl is not None:
        # spec limits apply to individual readings; for X-bar charts they are compared with subgroup means as a screen only
        below = sum(1 for p in pts if lsl is not None and p < lsl)
        above = sum(1 for p in pts if usl is not None and p > usl)
        a.update(points_below_lsl=below if lsl is not None else None, points_above_usl=above if usl is not None else None,
                 pct_out_of_spec=(below + above) / k * 100 if k else None)
        if individual:
            lo, hi = c["lcl"][-1], c["ucl"][-1]
            a["control_limits_within_spec"] = bool((lsl is None or lo >= lsl) and (usl is None or hi <= usl))
            if not a["control_limits_within_spec"]:
                notes.append("The natural process limits extend beyond the specification limits")
            if primary == "XBAR":
                notes.append("X-bar limits describe subgroup means; compare individual readings with the specification limits via capability")
    if target is not None and k:
        a["mean_minus_target"] = sum(pts) / k - target
    return {"kind": "CONTROL_LIMITS", "parameter": (parameter or "").upper() or None, "chart": o.chart, "primary": primary,
            "limits": {name: {"ucl": ch["ucl"][-1] if ch["ucl"] else None, "cl": ch["cl"][-1] if ch["cl"] else None,
                              "lcl": ch["lcl"][-1] if ch["lcl"] else None,
                              "variable": len(set(map(float, ch["ucl"]))) > 1} for name, ch in charts.items()},
            "spec": {"usl": usl, "lsl": lsl, "target": target}, "assessment": a, "charts": charts,
            "n_readings": n, "upload_ids": uploads, "notes": notes}


@_engine_errors
def violations_analysis(f: DataFilters, parameter: str | None, o: ChartOptions) -> dict:
    charts, primary, notes, uploads, n, series = build_charts(f, parameter, o)
    c = charts[primary]
    pname = (parameter or "").upper() or o.chart
    if o.chart == "CUSUM":
        viol = [{"rule_set": "CUSUM", "rule_id": "CUSUM", "name": "CUSUM signal", "description": "cumulative sum beyond the decision interval H",
                 "violation_type": "SHIFT", "severity": "HIGH", "point_index": i, "start_index": i,
                 "observed_value": c["points"][i], "z_score": None, "timestamp": c["labels"][i]} for i in c["out_of_control"]]
        notes.append("Run rules do not apply to CUSUM; its own decision interval is used")
    else:
        sigma = [(u - m) / 3 for u, m in zip(c["ucl"], c["cl"])]
        viol = rl.detect(c["points"], c["cl"], sigma, o.rule_set, timestamps=c["labels"])
        if min(sigma) <= 0:
            raise UnprocessableError("Control limits are degenerate (zero width); rules cannot be evaluated")
    for v in viol:
        v["parameter"] = pname
        v["chart"] = primary
    return {"kind": "VIOLATIONS", "parameter": pname, "chart": o.chart, "primary": primary, "rule_set": o.rule_set,
            "violations": viol, "summary": rl.summarize(viol), "n_points": c["n_points"], "upload_ids": uploads, "notes": notes}


@_engine_errors
def capability_analysis(f: DataFilters, parameter: str, o: ChartOptions) -> dict:
    r = load_readings(f, parameter)
    subgroups = None
    notes = []
    if o.subgroup_size and o.subgroup_size >= 2:
        subgroups, dropped = cc.make_subgroups(r.values, o.subgroup_size)
        if dropped:
            notes.append(f"{dropped} trailing reading(s) did not fill a subgroup and were not used for the within-subgroup sigma")
    if r.series_count > 1:
        notes.append(f"The selection combines {r.series_count} sensors/assets")
    res = cap.analyze(r.values, o.usl, o.lsl, o.target, subgroups=subgroups, sigma_within_override=o.sigma)
    return {"kind": "CAPABILITY", "parameter": parameter.upper(), "capability": res, "upload_ids": r.upload_ids,
            "notes": notes + res["notes"]}


@_engine_errors
def distribution_analysis(f: DataFilters, parameter: str, o: ChartOptions) -> dict:
    r = load_readings(f, parameter)
    res = dist.analyze(r.values, o.bins)
    notes = res.pop("notes")
    if r.series_count > 1:
        notes.append(f"The selection combines {r.series_count} sensors/assets")
    return {"kind": "DISTRIBUTION", "parameter": parameter.upper(), "distribution": res, "upload_ids": r.upload_ids, "notes": notes}
