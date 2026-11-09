"""Persist analysis jobs and results in Snowflake (METADATA.ANALYSIS_HISTORY + SPC_RESULTS.*) and read them back.
Runs as a background task after the API response, so requests stay fast."""
import json
import logging
from datetime import datetime

from app.core import snowflake as sf
from app.core.exceptions import NotFoundError
from app.schemas.common import PageParams

logger = logging.getLogger(__name__)
DB = "SPC_PLATFORM"
HIST = f"{DB}.METADATA.ANALYSIS_HISTORY"
R = f"{DB}.SPC_RESULTS"
MAX_JSON_BYTES = 4_000_000
MAX_VIOLATION_ROWS = 10_000
HEAVY_KEYS = ("charts", "violations", "distribution")


def _dump(obj) -> str:
    return json.dumps(obj, default=str, allow_nan=False)


def _ts(s: str | None):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S") if s else None


def _many(table: str, cols: list[str], rows: list[tuple]) -> None:
    if not rows:
        return
    sql = f"INSERT INTO {R}.{table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
    with sf.cursor() as cur:
        for i in range(0, len(rows), 1000):
            cur.executemany(sql, rows[i:i + 1000])


def _typed(aid: str, kind: str, res: dict) -> None:
    p = res.get("parameter")
    if kind == "STATISTICS":
        s = res["statistics"]
        sf.execute(
            f"INSERT INTO {R}.STATISTICAL_RESULTS (ANALYSIS_ID, PARAMETER, N_COUNT, MEAN_VALUE, MEDIAN_VALUE, MODE_VALUE, MIN_VALUE, "
            "MAX_VALUE, RANGE_VALUE, VARIANCE_VALUE, STDDEV_VALUE, SKEWNESS, KURTOSIS, PERCENTILES) "
            "SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, PARSE_JSON(%s)",
            (aid, p, s["n"], s["mean"], s["median"], s["mode"], s["min"], s["max"], s["range"], s["variance"], s["std"],
             s["skewness"], s["kurtosis"], _dump({**s["percentiles"], "q1": s["q1"], "q2": s["q2"], "q3": s["q3"], "iqr": s["iqr"]})))
    elif kind in ("CONTROL_CHART", "CONTROL_LIMITS"):
        spec = res.get("spec") or {}
        rows = []
        for name, c in res["charts"].items():
            if not c.get("ucl"):
                continue
            rows.append((aid, p or res.get("chart"), name, c["ucl"][-1], c["cl"][-1], c["lcl"][-1], spec.get("usl"), spec.get("lsl"),
                         spec.get("target"), c.get("params", {}).get("subgroup_size")))
        _many("CONTROL_LIMITS", ["ANALYSIS_ID", "PARAMETER", "CHART_TYPE", "UCL", "CL", "LCL", "USL", "LSL", "TARGET", "SUBGROUP_SIZE"], rows)
    elif kind == "VIOLATIONS":
        rows = [(aid, v["parameter"], v["violation_type"], v["rule_set"], f'{v["rule_id"]}: {v["name"]}', _ts(v.get("timestamp")),
                 v["observed_value"], v["severity"], v["point_index"]) for v in res["violations"][:MAX_VIOLATION_ROWS]]
        _many("SPC_VIOLATIONS", ["ANALYSIS_ID", "PARAMETER", "VIOLATION_TYPE", "RULE_SET", "RULE_NAME", "OCCURRED_AT",
                                 "OBSERVED_VALUE", "SEVERITY", "POINT_INDEX"], rows)
    elif kind == "CAPABILITY":
        c = res["capability"]
        sf.execute(
            f"INSERT INTO {R}.CAPABILITY_RESULTS (ANALYSIS_ID, PARAMETER, CP, CPK, PP, PPK, CPU, CPL, PPM_TOTAL, YIELD_PCT, USL, LSL, TARGET, SAMPLE_SIZE) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (aid, p, c["cp"], c["cpk"], c["pp"], c["ppk"], c["cpu"], c["cpl"], c["ppm_observed_total"], c["yield_observed"],
             c["usl"], c["lsl"], c["target"], c["n"]))
    elif kind == "DISTRIBUTION":
        d = res["distribution"]
        shap = next((t for t in d["normality"]["tests"] if t["test"] == "Shapiro-Wilk" and t["statistic"] is not None), None) \
            or (d["normality"]["tests"][0] if d["normality"]["tests"] else {})
        best = d["fit"]["best"] if d.get("fit") else None
        sf.execute(
            f"INSERT INTO {R}.DISTRIBUTION_RESULTS (ANALYSIS_ID, PARAMETER, NORMALITY_TEST, TEST_STATISTIC, P_VALUE, IS_NORMAL, BEST_FIT_DIST, FIT_DETAILS) "
            "SELECT %s,%s,%s,%s,%s,%s,%s, PARSE_JSON(%s)",
            (aid, p, shap.get("test"), shap.get("statistic"), shap.get("p_value"), d["normality"]["normal"], best,
             _dump({"fit": d.get("fit"), "boxplot": d["boxplot"], "histogram": d["histogram"]})))


def persist(aid: str, kind: str, result: dict, params: dict, user_id: str | None) -> None:
    """Record the job (RUNNING -> COMPLETED/FAILED), typed result rows and the full JSON. Never raises."""
    uploads = result.get("upload_ids") or []
    try:
        sf.execute(f"INSERT INTO {HIST} (ANALYSIS_ID, UPLOAD_ID, ANALYSIS_TYPE, REQUEST_PARAMS, STATUS, REQUESTED_BY, STARTED_AT) "
                   "SELECT %s, %s, %s, PARSE_JSON(%s), 'RUNNING', %s, CURRENT_TIMESTAMP()",
                   (aid, uploads[0] if uploads else None, kind, _dump(params), user_id))
    except Exception:  # noqa: BLE001
        logger.exception("Could not record analysis %s", aid)
        return
    try:
        _typed(aid, kind, result)
        payload = _dump(result)
        if len(payload) > MAX_JSON_BYTES:
            payload = _dump({**{k: v for k, v in result.items() if k not in HEAVY_KEYS}, "truncated": True,
                             "note": "full chart/violation/distribution detail exceeded the storage limit"})
        sf.execute(f"INSERT INTO {R}.ANALYSIS_RESULTS (ANALYSIS_ID, RESULT_TYPE, RESULT_JSON) SELECT %s, %s, PARSE_JSON(%s)",
                   (aid, kind, payload))
        sf.execute(f"UPDATE {HIST} SET STATUS = 'COMPLETED', COMPLETED_AT = CURRENT_TIMESTAMP() WHERE ANALYSIS_ID = %s", (aid,))
    except Exception as e:  # noqa: BLE001
        logger.exception("Persisting analysis %s failed", aid)
        try:
            sf.execute(f"UPDATE {HIST} SET STATUS = 'FAILED', COMPLETED_AT = CURRENT_TIMESTAMP(), ERROR_MESSAGE = %s WHERE ANALYSIS_ID = %s",
                       (f"{type(e).__name__}: {e}"[:500], aid))
        except Exception:  # noqa: BLE001
            logger.exception("Could not mark analysis %s as failed", aid)


def list_history(p: PageParams, user, kind: str | None) -> tuple[list[dict], int]:
    where, args = [], []
    if "*" not in user.permissions:
        where.append("REQUESTED_BY = %s"); args.append(user.user_id)
    if kind:
        where.append("ANALYSIS_TYPE = %s"); args.append(kind.upper())
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    total = sf.execute(f"SELECT COUNT(*) FROM {HIST} {clause}", args)[0][0]
    rows = sf.execute(f"SELECT ANALYSIS_ID, UPLOAD_ID, ANALYSIS_TYPE, REQUEST_PARAMS, STATUS, REQUESTED_BY, ERROR_MESSAGE, "
                      f"CREATED_AT, STARTED_AT, COMPLETED_AT FROM {HIST} {clause} ORDER BY CREATED_AT DESC LIMIT %s OFFSET %s",
                      [*args, p.page_size, p.offset])
    return [{"analysis_id": r[0], "upload_id": r[1], "kind": r[2], "params": json.loads(r[3]) if r[3] else None, "status": r[4],
             "requested_by": r[5], "error_message": r[6], "created_at": r[7], "started_at": r[8], "completed_at": r[9]}
            for r in rows], total


def get_result(aid: str, user) -> dict:
    rows = sf.execute(f"SELECT h.STATUS, h.REQUESTED_BY, h.ANALYSIS_TYPE, h.ERROR_MESSAGE, r.RESULT_JSON FROM {HIST} h "
                      f"LEFT JOIN {R}.ANALYSIS_RESULTS r ON r.ANALYSIS_ID = h.ANALYSIS_ID WHERE h.ANALYSIS_ID = %s", (aid,))
    if not rows or ("*" not in user.permissions and rows[0][1] != user.user_id):
        raise NotFoundError(f"Analysis '{aid}' not found")
    s, _, kind, err, js = rows[0]
    return {"analysis_id": aid, "kind": kind, "status": s, "error_message": err, "result": json.loads(js) if js else None}
