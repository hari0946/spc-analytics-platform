"""AI Assistant (Phase 17): natural-language questions, process summary and insights.

Grounding rules
- Every number in an answer is read from an analysis computed on the selected Snowflake data in this very request
  (the same services the API uses); `evidence` returns those raw numbers.
- The assistant does not guess: if a question needs data it does not have (e.g. specification limits), it asks for them;
  if the question is outside what the data can show (causes, forecasts, advice), it says so.
- No external language model is used, so there is nothing that can invent a statistic.
"""
import re

from app.core.exceptions import UnprocessableError
from app.services import analysis_service as an
from app.services import dashboard_service as dash
from app.services import data_service
from app.services.filters import DataFilters
from app.spc_engine import statistics as st

CAPABILITIES = [
    "Summarise the process ('give me a summary', 'how is the process doing?')",
    "Report statistics ('mean and standard deviation of temperature', 'what is the median pressure?')",
    "List violations ('any out-of-control points?', 'are there rule violations for flow?')",
    "Compute capability when you give limits ('cpk of temperature with usl 15 and lsl 7')",
    "Describe the distribution ('is temperature normally distributed?', 'any outliers in speed?')",
    "Describe the data ('how many readings are there?', 'which parameters and date range?')",
    "Report quality ('what is the defect rate?')",
]
UNSUPPORTED = re.compile(r"\b(why|cause|causes|caused|root cause|predict|forecast|will |going to|should (i|we)|recommend|fix|improve|how (can|do) (i|we)|best|compare to|benchmark)\b")
STAT_WORDS = {
    "mean": ("mean", "average"), "median": ("median",), "mode": ("mode",), "std": ("std", "standard deviation", "stdev", "sigma"),
    "variance": ("variance",), "min": ("minimum", " min", "lowest", "smallest"), "max": ("maximum", " max", "highest", "largest"),
    "range": ("range", "spread"), "skewness": ("skew",), "kurtosis": ("kurt",),
    "q1": ("quartile", "q1"), "q3": ("quartile", "q3"), "p95": ("95th", "p95"), "p99": ("99th", "p99"), "p5": ("5th percentile", "p5"),
    "n": ("how many readings", "count of readings"),
}
LABEL = {"mean": "mean", "median": "median", "mode": "mode", "std": "standard deviation", "variance": "variance", "min": "minimum",
         "max": "maximum", "range": "range", "skewness": "skewness", "kurtosis": "excess kurtosis", "q1": "Q1", "q3": "Q3",
         "p95": "95th percentile", "p99": "99th percentile", "p5": "5th percentile"}


def fmt(x) -> str:
    if x is None:
        return "n/a"
    if isinstance(x, int) or (isinstance(x, float) and x.is_integer() and abs(x) < 1e9):
        return f"{int(x):,}"
    return f"{x:.4g}" if abs(x) < 1e5 else f"{x:,.0f}"


def _num(q: str, name: str):
    m = re.search(rf"\b{name}\b\s*(?:=|:|of|at|is)?\s*(-?\d+(?:\.\d+)?)", q)
    return float(m.group(1)) if m else None


def _params_in(q: str, available: list[str]) -> list[str]:
    return [p for p in available if re.search(rf"(?<![a-z0-9]){re.escape(p.lower())}(?![a-z0-9])", q)]


def _detect_intent(q: str) -> str:
    if UNSUPPORTED.search(q):
        return "UNSUPPORTED"
    if re.search(r"\b(help|what can you|what do you do|capabilit)", q):
        return "HELP"
    if re.search(r"defect|reject|scrap|nonconform|quality|first.pass", q):
        return "QUALITY"
    if re.search(r"\b(cpk|cp|ppk|pp|cpu|cpl|ppm)\b|capab|yield", q):
        return "CAPABILITY"
    if re.search(r"violat|out.of.control|alarm|\brules?\b|unstable|\bin control\b|stable|western electric|nelson|control limit", q):
        return "VIOLATIONS"
    if re.search(r"normal|distribution|histogram|outlier|box ?plot", q):
        return "DISTRIBUTION"
    if re.search(r"mean|average|median|mode\b|std|standard deviation|variance|minimum|maximum|\bmin\b|\bmax\b|range|spread|skew|kurt|percentile|quartile|statistic", q):
        return "STATISTICS"
    if re.search(r"how many|count|date range|time range|from when|since when|which (parameters|assets|datasets)|what (parameters|assets|datasets)|available|period", q):
        return "SCOPE"
    if re.search(r"summar|overview|status|how (is|are)|doing|overall|report", q):
        return "SUMMARY"
    return "UNKNOWN"


def _split_scope(f: DataFilters):
    return dash._split(f)


def ask(question: str, f: DataFilters) -> dict:
    q = " " + question.strip().lower() + " "
    intent = _detect_intent(q)
    base = {"question": question, "intent": intent, "supported": True, "evidence": {}, "used": [], "needs": None}
    if intent == "HELP":
        return {**base, "answer": "I answer questions about your uploaded historical data using real analysis results. I can:\n- "
                + "\n- ".join(CAPABILITIES)}
    if intent == "UNSUPPORTED":
        return {**base, "supported": False,
                "answer": "I can only report what the selected historical data and its SPC analyses show. I can't explain causes, "
                          "forecast, or recommend actions, because the data alone does not support those conclusions. "
                          "Ask me for statistics, violations, capability, distribution or a summary."}
    if intent == "UNKNOWN":
        return {**base, "supported": False,
                "answer": "I'm not sure what you're asking, and I won't guess. Try one of:\n- " + "\n- ".join(CAPABILITIES[:5])}

    sensor_f, prod_f = _split_scope(f)
    if intent == "QUALITY":
        return _quality(base, prod_f)

    summary = data_service.sensor_summary(sensor_f) if sensor_f else None
    available = [p.parameter for p in summary.parameters] if summary else []
    if intent == "SCOPE":
        return _scope(base, summary, prod_f)
    if intent == "SUMMARY":
        return _summary(base, f)
    if not summary or summary.total_rows == 0:
        return {**base, "supported": True, "answer": "There is no sensor data matching the current selection, so there is nothing to analyse.",
                "evidence": {"readings": 0}}

    mentioned = _params_in(q, available)
    targets = mentioned or (available if len(available) == 1 else [])
    if not targets:
        return {**base, "needs": "parameter", "answer": f"Which parameter do you mean? Available: {', '.join(available)}.",
                "evidence": {"available_parameters": available}}
    handler = {"STATISTICS": _statistics, "VIOLATIONS": _violations, "CAPABILITY": _capability, "DISTRIBUTION": _distribution}[intent]
    return handler(base, q, sensor_f, targets)


# ------------------------------------------------------------------------------------------------ intents
def _statistics(base, q, f, targets):
    wanted = [k for k, words in STAT_WORDS.items() if any(w in q for w in words) and k != "n"]
    lines, ev = [], {}
    for p in targets:
        r = an.statistics_analysis(f, p)
        s = r["statistics"]
        ev[p] = {k: s.get(k) for k in ("n", "mean", "median", "mode", "std", "variance", "min", "max", "range", "skewness", "kurtosis", "q1", "q3")}
        ev[p]["percentiles"] = s["percentiles"]
        keys = wanted or ["mean", "median", "std", "min", "max"]
        parts = []
        for k in keys:
            v = s["percentiles"]["p95"] if k == "p95" else s["percentiles"]["p99"] if k == "p99" else s["percentiles"]["p5"] if k == "p5" else s.get(k)
            parts.append(f"{LABEL[k]} {fmt(v)}" if v is not None else f"{LABEL[k]} is not defined for this data")
        lines.append(f"{p} (n={s['n']}, {r['first']} to {r['last']}): " + ", ".join(parts) + ".")
        lines += [f"  Note: {n}" for n in r["notes"] if "no mode" in n or "constant" in n or "multimodal" in n]
    return {**base, "answer": "\n".join(lines), "evidence": ev, "used": ["statistics"]}


def _violations(base, q, f, targets):
    lines, ev = [], {}
    for p in targets:
        r = an.violations_analysis(f, p, an.ChartOptions(chart="IMR"))
        v = r["violations"]
        ooc = [x for x in v if x["violation_type"] == "OUT_OF_CONTROL"]
        ev[p] = {"violations": len(v), "summary": r["summary"], "first_out_of_control": ooc[0] if ooc else None}
        if not v:
            lines.append(f"{p}: no Western Electric or Nelson rule violations in {r['n_points']} readings (individuals chart).")
            continue
        head = f"{p}: {len(v)} rule violation(s) across {r['summary']['points_flagged']} point(s) in {r['n_points']} readings."
        if ooc:
            pts = sorted({(x["timestamp"], x["observed_value"]) for x in ooc})[:3]
            head += " Beyond the control limits: " + "; ".join(f"{fmt(val)} at {t}" for t, val in pts) + "."
        by_type = ", ".join(f"{k.lower().replace('_', ' ')} {n}" for k, n in r["summary"]["by_type"].items())
        lines.append(head + f" By type: {by_type}.")
    return {**base, "answer": "\n".join(lines), "evidence": ev, "used": ["violations"]}


def _capability(base, q, f, targets):
    usl, lsl, target = _num(q, "usl"), _num(q, "lsl"), _num(q, "target")
    if usl is None and lsl is None:
        return {**base, "needs": "specification_limits", "evidence": {},
                "answer": "Capability needs specification limits, which are not stored with the data. Tell me USL and/or LSL, "
                          "e.g. 'cpk of temperature with usl 15 and lsl 7'."}
    lines, ev = [], {}
    for p in targets:
        try:
            r = an.capability_analysis(f, p, an.ChartOptions(usl=usl, lsl=lsl, target=target))
        except UnprocessableError as e:
            lines.append(f"{p}: {e.message}")
            continue
        c = r["capability"]
        ev[p] = {k: c[k] for k in ("n", "cp", "cpk", "pp", "ppk", "cpu", "cpl", "ppm_observed_total", "yield_observed",
                                   "ppm_expected_overall", "usl", "lsl", "target")}
        txt = (f"{p} (n={c['n']}, USL {fmt(usl)}, LSL {fmt(lsl)}): Cpk {fmt(c['cpk'])}, Ppk {fmt(c['ppk'])}"
               + (f", Cp {fmt(c['cp'])}, Pp {fmt(c['pp'])}" if c["cp"] is not None else "")
               + f". Observed PPM {fmt(c['ppm_observed_total'])}, observed yield {fmt(c['yield_observed'])}%.")
        for w in c["warnings"]:
            txt += f" Warning: {w.rstrip('.')}."
        lines.append(txt)
    return {**base, "answer": "\n".join(lines), "evidence": ev, "used": ["capability"]}


def _distribution(base, q, f, targets):
    lines, ev = [], {}
    for p in targets:
        try:
            r = an.distribution_analysis(f, p, an.ChartOptions())["distribution"]
        except UnprocessableError as e:
            lines.append(f"{p}: {e.message}")
            continue
        nrm, box = r["normality"], r["boxplot"]
        ev[p] = {"normal": nrm["normal"], "conclusion": nrm["conclusion"], "n_outliers": box["n_outliers"],
                 "outliers": box["outliers"][:10], "best_fit": r["fit"]["best"] if r["fit"] else None}
        txt = f"{p} (n={r['n']}): {nrm['conclusion']}. {box['n_outliers']} outlier(s) by the 1.5×IQR rule"
        if box["outliers"]:
            txt += " (" + ", ".join(fmt(o) for o in box["outliers"][:5]) + ("…" if box["n_outliers"] > 5 else "") + ")"
        txt += f". Best-fitting candidate distribution by AIC: {r['fit']['best']}." if r["fit"] else "."
        lines.append(txt)
    return {**base, "answer": "\n".join(lines), "evidence": ev, "used": ["distribution"]}


def _scope(base, summary, prod_f):
    parts, ev = [], {}
    if summary and summary.total_rows:
        params = ", ".join(f"{p.parameter} ({p.n})" for p in summary.parameters)
        parts.append(f"{summary.total_rows:,} sensor readings from {summary.datasets} dataset(s), "
                     f"{summary.first:%Y-%m-%d %H:%M} to {summary.last:%Y-%m-%d %H:%M}. Parameters: {params}.")
        ev["sensor"] = {"readings": summary.total_rows, "datasets": summary.datasets, "parameters": {p.parameter: p.n for p in summary.parameters}}
    if prod_f is not None:
        ps = data_service.production_summary(prod_f)
        if ps.total_rows:
            parts.append(f"{ps.total_rows:,} production/quality samples across {ps.batches} batch(es), {ps.first:%Y-%m-%d} to {ps.last:%Y-%m-%d}.")
            ev["production"] = {"samples": ps.total_rows, "batches": ps.batches}
    if not parts:
        parts = ["No historical data matches the current selection."]
    return {**base, "answer": " ".join(parts), "evidence": ev, "used": ["summary"]}


def _quality(base, prod_f):
    if prod_f is None:
        return {**base, "answer": "No production/quality dataset is part of the current selection, so I have no defect data to report.",
                "evidence": {}}
    ps = data_service.production_summary(prod_f)
    if not ps.total_rows:
        return {**base, "answer": "No production/quality samples match the current selection.", "evidence": {}}
    ev = {"samples": ps.total_rows, "sample_size": ps.sample_size, "defective_count": ps.defective_count, "defect_count": ps.defect_count}
    txt = f"{ps.total_rows} samples ({fmt(ps.sample_size)} units inspected)"
    if ps.defective_count is not None and ps.sample_size:
        ev["defect_rate"] = ps.defective_count / ps.sample_size
        txt += f": {fmt(ps.defective_count)} defective units, a defect rate of {ev['defect_rate'] * 100:.3g}% (first-pass yield {(1 - ev['defect_rate']) * 100:.4g}%)"
    if ps.defect_count is not None:
        txt += f"; {fmt(ps.defect_count)} total defects"
    try:
        c = an.build_charts(prod_f, None, an.ChartOptions(chart="P"))[0]["P"]
        ev["p_chart_out_of_control"] = len(c["out_of_control"])
        txt += f". The P chart has {len(c['out_of_control'])} sample(s) beyond its control limits"
        if c["out_of_control"]:
            txt += " (" + ", ".join(c["labels"][i][:10] for i in c["out_of_control"][:5]) + ")"
    except UnprocessableError:
        pass
    return {**base, "answer": txt + ".", "evidence": ev, "used": ["quality", "control_chart"]}


def _summary(base, f):
    d = dash.build(f)
    return {**base, "answer": summary_text(d), "evidence": {"status": d["status"], "kpis": d["kpis"]}, "used": ["dashboard"]}


def summary_text(d: dict) -> str:
    if d["status"] == "NO_DATA":
        return "No historical data matches the current selection."
    k = d["kpis"]
    lines = [f"Overall status: {d['status'].replace('_', ' ')} — {d['status_reason']}."]
    if k["readings"]:
        lines.append(f"{k['readings']:,} readings across {k['parameters']} parameter(s) from {k['first']:%Y-%m-%d %H:%M} to {k['last']:%Y-%m-%d %H:%M}; "
                     f"{k['violations_total']} rule violation(s), {k['out_of_control_points']} point(s) beyond control limits.")
    for c in d["parameters"]:
        lines.append(f"- {c['parameter']}: {c['status'].replace('_', ' ').lower()}, n={c['n']}, mean {fmt(c['mean'])}, std {fmt(c['std'])}, "
                     f"{c['violations']} violation(s).")
    q = d["quality"]
    if q:
        rate = f", defect rate {q['defect_rate'] * 100:.3g}%" if q["defect_rate"] is not None else ""
        lines.append(f"Quality: {q['samples']} samples{rate}.")
    return "\n".join(lines)


def insights(f: DataFilters) -> dict:
    """Plain-language findings, each backed by the numbers it was derived from."""
    d = dash.build(f)
    out = []
    for c in d["parameters"]:
        ev = {k: c[k] for k in ("parameter", "n", "out_of_control_points", "violations", "rules")}
        if c["status"] == "OUT_OF_CONTROL":
            out.append({"severity": "HIGH", "text": f"{c['parameter']} has {c['out_of_control_points']} point(s) beyond its control limits.", "evidence": ev})
        if c["rules"].get("N2") or c["rules"].get("WE4"):
            out.append({"severity": "MEDIUM", "text": f"{c['parameter']} shows a sustained run on one side of its centre line (possible shift).", "evidence": ev})
        if c["rules"].get("N3"):
            out.append({"severity": "MEDIUM", "text": f"{c['parameter']} shows a steady upward or downward trend over 6 or more consecutive points.", "evidence": ev})
        if c["status"] == "IN_CONTROL":
            out.append({"severity": "INFO", "text": f"{c['parameter']} shows no control-limit or run-rule violations over {c['n']} readings.", "evidence": ev})
        if c["status"] == "NOT_ENOUGH_DATA":
            out.append({"severity": "INFO", "text": f"{c['parameter']} has too little data for a control chart ({c['note'] or ''}).".replace(" ()", ""), "evidence": ev})
    q = d["quality"]
    if q and q["p_chart_out_of_control"]:
        out.append({"severity": "HIGH", "text": f"{q['p_chart_out_of_control']} quality sample(s) exceed the P-chart upper control limit.", "evidence": q})
    if q and q["defect_rate"] is not None:
        out.append({"severity": "INFO", "text": f"Overall defect rate is {q['defect_rate'] * 100:.3g}% over {q['samples']} samples.", "evidence": q})
    order = {"HIGH": 0, "MEDIUM": 1, "INFO": 2}
    out.sort(key=lambda i: order[i["severity"]])
    return {"status": d["status"], "insights": out, "summary": summary_text(d), "notes": d["notes"]}
