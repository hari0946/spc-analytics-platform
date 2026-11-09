"""Overview dashboard (Phase 16): connects the completed analytics modules for the selected historical data.
Everything shown is computed from Snowflake GOLD data by the same services the analysis API uses."""
from dataclasses import replace

from app.core.exceptions import UnprocessableError
from app.services import analysis_service as an
from app.services import data_service
from app.services.filters import DataFilters
from app.spc_engine import control_charts as cc
from app.spc_engine import rules as rl
from app.spc_engine import statistics as st

MAX_PARAMETERS = 8


def _parameter_card(f: DataFilters, parameter: str) -> dict:
    r = an.load_readings(f, parameter)
    s = st.descriptive(r.values)
    card = {"parameter": parameter, "n": s["n"], "mean": s["mean"], "std": s["std"], "min": s["min"], "max": s["max"],
            "out_of_control_points": 0, "violations": 0, "high_severity": 0, "rules": {}, "status": "NOT_ENOUGH_DATA",
            "first": an._iso(r.times[0]), "last": an._iso(r.times[-1]), "note": None}
    if r.series_count > 1:
        card["note"] = f"combines {r.series_count} sensors/assets"
    try:
        chart = cc.imr_chart(r.values)["I"]
        sigma = [(u - m) / 3 for u, m in zip(chart["ucl"], chart["cl"])]
        if min(sigma) <= 0:
            raise cc.ChartConfigError("constant data")
        viol = rl.detect(chart["points"], chart["cl"], sigma, "BOTH")
    except (cc.ChartConfigError, rl.RuleConfigError) as e:
        card["note"] = (card["note"] + "; " if card["note"] else "") + f"no control chart: {e}"
        return card
    summary = rl.summarize(viol)
    card.update(out_of_control_points=len(chart["out_of_control"]), violations=summary["total"],
                high_severity=sum(1 for v in viol if v["severity"] == "HIGH"), rules=summary["by_rule"],
                status="OUT_OF_CONTROL" if chart["out_of_control"] else ("ATTENTION" if viol else "IN_CONTROL"))
    return card


def _split(f: DataFilters) -> tuple[DataFilters | None, DataFilters | None]:
    """Selected datasets may be a mix of sensor and production uploads; split the selection by dataset type."""
    if not f.upload_ids:
        return f, f
    from app.core import snowflake as sf
    from app.core.exceptions import ConflictError, NotFoundError
    ph = ",".join(["%s"] * len(f.upload_ids))
    found = {r[0]: (r[1], r[2]) for r in sf.execute(
        f"SELECT UPLOAD_ID, STATUS, DATASET_TYPE FROM {data_service.UPLOADS} WHERE UPLOAD_ID IN ({ph})", f.upload_ids)}
    missing = [i for i in f.upload_ids if i not in found]
    if missing:
        raise NotFoundError("Dataset(s) not found", {"upload_ids": missing})
    pending = [i for i, (st_, _) in found.items() if st_ != "COMPLETED"]
    if pending:
        raise ConflictError("Dataset(s) not processed yet; run processing first", {"upload_ids": pending})
    sensor = [i for i, (_, t) in found.items() if t == "SENSOR"]
    prod = [i for i, (_, t) in found.items() if t == "PRODUCTION"]
    return (replace(f, upload_ids=sensor) if sensor else None), (replace(f, upload_ids=prod, tags=[], parameters=[]) if prod else None)


def build(f: DataFilters) -> dict:
    sensor_f, prod_f = _split(f)
    summary = data_service.sensor_summary(sensor_f) if sensor_f else None
    params = [p.parameter for p in summary.parameters][:MAX_PARAMETERS] if summary else []
    cards = [_parameter_card(sensor_f, p) for p in params]
    notes = []
    if summary and len(summary.parameters) > MAX_PARAMETERS:
        notes.append(f"Showing the first {MAX_PARAMETERS} of {len(summary.parameters)} parameters; filter to see others")

    quality = None
    if prod_f is not None:
        prod_f = replace(prod_f, tags=[], parameters=[])
    try:
        if prod_f is not None:
            ps = data_service.production_summary(prod_f)
            if ps.total_rows:
                quality = {"samples": ps.total_rows, "batches": ps.batches, "units_produced": ps.units_produced,
                           "sample_size": ps.sample_size, "defective_count": ps.defective_count, "defect_count": ps.defect_count,
                           "defect_rate": (ps.defective_count / ps.sample_size) if ps.sample_size and ps.defective_count is not None else None,
                           "first": ps.first, "last": ps.last, "p_chart_out_of_control": None}
                try:
                    c = an.build_charts(prod_f, None, an.ChartOptions(chart="P"))[0]["P"]
                    quality["p_chart_out_of_control"] = len(c["out_of_control"])
                except UnprocessableError as e:
                    notes.append(f"P chart not available: {e.message}")
    except UnprocessableError:
        pass  # filters that do not apply to production data simply leave the quality block empty

    if not cards and not quality:
        status, reason = "NO_DATA", "No historical data matches the selection"
    elif any(c["status"] == "OUT_OF_CONTROL" for c in cards):
        bad = [c["parameter"] for c in cards if c["status"] == "OUT_OF_CONTROL"]
        status, reason = "OUT_OF_CONTROL", f"Points beyond the control limits: {', '.join(bad)}"
    elif any(c["status"] == "ATTENTION" for c in cards) or (quality and quality["p_chart_out_of_control"]):
        status, reason = "ATTENTION", "Run-rule violations or out-of-control quality samples were found"
    else:
        status, reason = "IN_CONTROL", "No control-limit or run-rule violations in the selection"
    total_viol = sum(c["violations"] for c in cards)
    return {
        "status": status, "status_reason": reason,
        "kpis": {"readings": summary.total_rows if summary else 0, "datasets": summary.datasets if summary else 0,
                 "parameters": len(summary.parameters) if summary else 0,
                 "first": summary.first if summary else None, "last": summary.last if summary else None,
                 "violations_total": total_viol, "out_of_control_points": sum(c["out_of_control_points"] for c in cards),
                 "parameters_in_control": sum(1 for c in cards if c["status"] == "IN_CONTROL"),
                 "parameters_needing_attention": sum(1 for c in cards if c["status"] in ("ATTENTION", "OUT_OF_CONTROL"))},
        "parameters": cards, "quality": quality, "notes": notes,
        "method": "Individuals (I-MR) chart per parameter with Western Electric and Nelson rules; P chart for quality samples.",
    }
