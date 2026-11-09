"""BRONZE -> SILVER -> GOLD orchestration, validation report and rejection access.

Policy (documented in the report): nothing is imputed or invented. Rows with a missing/invalid timestamp or value are
rejected with a reason; exact duplicates are dropped; conflicting duplicates keep the first row by source order.
Every input record ends up either in SILVER or in SILVER.REJECTED_RECORDS, and the report proves it (reconciliation).
"""
import json
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal

from app.core import snowflake as sf
from app.core.exceptions import ConflictError, NotFoundError
from app.schemas.common import PageParams
from app.schemas.processing import ProcessOptions, RejectionOut
from app.services import audit_service
from app.services import pipeline_sql as q

logger = logging.getLogger(__name__)
U = f"{q.DB}.METADATA.DATASET_UPLOADS"
RUNNABLE = ("LOADED_BRONZE", "COMPLETED", "PROCESS_FAILED")


def _num(v):
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    return v


def _one(sql: str, uid: str):
    return _num(sf.execute(sql, {"uid": uid})[0][0])


def _iso(v) -> str | None:
    return v.isoformat(sep=" ") if v is not None else None


def _upload_row(upload_id: str) -> tuple:
    rows = sf.execute(f"SELECT DATASET_TYPE, STATUS, COLUMN_MAPPING, ROWS_LOADED FROM {U} WHERE UPLOAD_ID = %s",
                      (upload_id,))
    if not rows:
        raise NotFoundError(f"Upload '{upload_id}' not found")
    return rows[0]


def clear_derived(upload_id: str) -> None:
    for sql, p in q.clear_statements(upload_id):
        sf.execute(sql, p)


# ------------------------------------------------------------------ start / run
def start_processing(upload_id: str, actor, ip) -> None:
    """Claim the upload (atomic status change). Raises 404/409; the work itself runs in run_processing."""
    dtype, status, mapping, _ = _upload_row(upload_id)
    if status == "PROCESSING":
        raise ConflictError("This upload is already being processed")
    if status not in RUNNABLE:
        raise ConflictError(f"Only uploads loaded into BRONZE can be processed (current status: {status})")
    claimed = sf.execute(f"UPDATE {U} SET STATUS = 'PROCESSING', ERROR_MESSAGE = NULL WHERE UPLOAD_ID = %s "
                         "AND STATUS IN ('LOADED_BRONZE', 'COMPLETED', 'PROCESS_FAILED')", (upload_id,))
    if not claimed or claimed[0][0] != 1:
        raise ConflictError("This upload is already being processed")


def run_processing(upload_id: str, options: ProcessOptions, actor_id: str, actor_name: str, ip: str | None) -> None:
    """Never raises: failures are recorded on the upload (status PROCESS_FAILED) and in the audit log."""
    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    try:
        dtype, _, mapping_json, rows_loaded = _upload_row(upload_id)
        mapping = json.loads(mapping_json)
        fmt = options.timestamp_format
        clear_derived(upload_id)                       # idempotent: re-processing replaces earlier output
        if dtype == "SENSOR":
            stmts = q.sensor_silver(mapping, fmt, upload_id) + q.sensor_gold(upload_id)
        else:
            stmts = q.production_silver(mapping, fmt, upload_id) + q.production_gold(upload_id)
        # silver (clean + rejects) must both be built before gold reads the clean table
        for sql, params in stmts:
            sf.execute(sql, params)
        report = build_report(upload_id, dtype, mapping, options, rows_loaded, started,
                              round(time.perf_counter() - t0, 2))
        sf.execute(f"UPDATE {U} SET STATUS = 'COMPLETED', PROCESSING_REPORT = PARSE_JSON(%s), COMPLETED_AT = CURRENT_TIMESTAMP(), "
                   "ERROR_MESSAGE = NULL WHERE UPLOAD_ID = %s", (json.dumps(report), upload_id))
        audit_service.log("UPLOAD_PROCESSED", actor_user_id=actor_id, actor_username=actor_name, entity_type="UPLOAD",
                          entity_id=upload_id, ip=ip, details={"silver_rows": report["counts"]["silver_rows"],
                                                               "rejected": report["counts"]["rejected_rows"],
                                                               "checks_passed": report["all_checks_passed"]})
    except Exception as e:  # noqa: BLE001
        logger.exception("Processing failed for %s", upload_id)
        try:
            clear_derived(upload_id)
        except Exception:  # noqa: BLE001
            logger.exception("Could not clear derived rows after failure")
        sf.execute(f"UPDATE {U} SET STATUS = 'PROCESS_FAILED', ERROR_MESSAGE = %s WHERE UPLOAD_ID = %s",
                   (f"Processing failed: {type(e).__name__}: {e}"[:500], upload_id))
        audit_service.log("UPLOAD_PROCESS_FAILED", actor_user_id=actor_id, actor_username=actor_name,
                          entity_type="UPLOAD", entity_id=upload_id, success=False, ip=ip,
                          details={"error": f"{type(e).__name__}: {e}"[:300]})


# ------------------------------------------------------------------ validation report
def build_report(upload_id: str, dtype: str, mapping: dict, options: ProcessOptions, rows_loaded: int,
                 started: datetime, seconds: float) -> dict:
    bronze = q.BRONZE_SENSOR if dtype == "SENSOR" else q.BRONZE_PROD
    silver = q.CLEAN if dtype == "SENSOR" else q.VALIDATED
    gold = q.GOLD_SENSOR if dtype == "SENSOR" else q.GOLD_PROD
    bronze_rows = _one(f"SELECT COUNT(*) FROM {bronze} WHERE UPLOAD_ID = %(uid)s", upload_id)
    silver_rows = _one(f"SELECT COUNT(*) FROM {silver} WHERE UPLOAD_ID = %(uid)s", upload_id)
    gold_rows = _one(f"SELECT COUNT(*) FROM {gold} WHERE UPLOAD_ID = %(uid)s", upload_id)
    reasons = {r[0]: _num(r[1]) for r in sf.execute(
        f"SELECT REASON, COUNT(*) FROM {q.REJECTED} WHERE UPLOAD_ID = %(uid)s GROUP BY 1 ORDER BY 2 DESC, 1", {"uid": upload_id})}
    rejected = sum(reasons.values())
    n_cols = len(mapping.get("value_columns") or []) or 1
    input_units = bronze_rows * n_cols if dtype == "SENSOR" else bronze_rows
    ts_col = "MEASURED_AT" if dtype == "SENSOR" else "EVENT_TIME"
    key = ("PLANT_CODE, LINE_CODE, ASSET_CODE, TAG_NAME, PARAMETER, MEASURED_AT" if dtype == "SENSOR"
           else "PLANT_CODE, LINE_CODE, ASSET_CODE, BATCH_ID, PRODUCT, EVENT_TIME")

    checks = [
        _check("reconciliation", input_units == silver_rows + rejected,
               f"{input_units} input {'measurements' if dtype == 'SENSOR' else 'records'} = {silver_rows} accepted + {rejected} rejected"),
        _check("no_duplicate_keys_in_silver",
               _one(f"SELECT COUNT(*) FROM (SELECT 1 FROM {silver} WHERE UPLOAD_ID = %(uid)s GROUP BY {key} HAVING COUNT(*) > 1)",
                    upload_id) == 0, "business key is unique within SILVER"),
        _check("timestamps_in_range",
               _one(f"SELECT COUNT(*) FROM {silver} WHERE UPLOAD_ID = %(uid)s AND ({ts_col} < '1970-01-01'::TIMESTAMP_NTZ "
                    f"OR {ts_col} > DATEADD(day, 1, SYSDATE()))", upload_id) == 0, "no timestamp before 1970 or in the future"),
        _check("gold_matches_silver", gold_rows == silver_rows, f"{gold_rows} GOLD rows vs {silver_rows} SILVER rows"),
        _check("bronze_matches_upload", bronze_rows == rows_loaded, f"{bronze_rows} BRONZE rows vs {rows_loaded} loaded"),
    ]
    summary: dict = {}
    if dtype == "SENSOR":
        agg = _one(f"SELECT COALESCE(SUM(N_POINTS), 0) FROM {q.GOLD_PROCESS} WHERE UPLOAD_ID = %(uid)s", upload_id)
        checks.append(_check("hourly_aggregates_cover_gold", agg == gold_rows, f"{agg} aggregated points vs {gold_rows} GOLD rows"))
        summary["parameters"] = [
            {"parameter": r[0], "n": _num(r[1]), "min": _num(r[2]), "max": _num(r[3]), "mean": round(float(r[4]), 6),
             "first": _iso(r[5]), "last": _iso(r[6])}
            for r in sf.execute(
                f"SELECT PARAMETER, COUNT(*), MIN(VALUE), MAX(VALUE), AVG(VALUE), MIN(MEASURED_AT), MAX(MEASURED_AT) "
                f"FROM {q.GOLD_SENSOR} WHERE UPLOAD_ID = %(uid)s GROUP BY 1 ORDER BY 1", {"uid": upload_id})]
        summary["assets"] = [r[0] for r in sf.execute(
            f"SELECT DISTINCT ASSET_CODE FROM {q.GOLD_SENSOR} WHERE UPLOAD_ID = %(uid)s AND ASSET_CODE IS NOT NULL ORDER BY 1",
            {"uid": upload_id})]
        r = sf.execute(f"SELECT MIN(MEASURED_AT), MAX(MEASURED_AT) FROM {q.GOLD_SENSOR} WHERE UPLOAD_ID = %(uid)s", {"uid": upload_id})[0]
    else:
        checks.append(_check("defectives_within_sample",
                             _one(f"SELECT COUNT(*) FROM {q.VALIDATED} WHERE UPLOAD_ID = %(uid)s AND DEFECTIVE_COUNT > SAMPLE_SIZE",
                                  upload_id) == 0, "defective count never exceeds sample size"))
        t = sf.execute(f"SELECT SUM(UNITS_PRODUCED), SUM(SAMPLE_SIZE), SUM(DEFECTIVE_COUNT), SUM(DEFECT_COUNT), "
                       f"COUNT(DISTINCT BATCH_ID) FROM {q.GOLD_PROD} WHERE UPLOAD_ID = %(uid)s", {"uid": upload_id})[0]
        summary["totals"] = {"units_produced": _num(t[0]), "sample_size": _num(t[1]), "defective_count": _num(t[2]),
                             "defect_count": _num(t[3]), "batches": _num(t[4])}
        r = sf.execute(f"SELECT MIN(EVENT_TIME), MAX(EVENT_TIME) FROM {q.GOLD_PROD} WHERE UPLOAD_ID = %(uid)s", {"uid": upload_id})[0]
    sample = [{"source_row": _num(x[0]), "parameter": x[1], "reason": x[2]} for x in sf.execute(
        f"SELECT SOURCE_ROW, PARAMETER, REASON FROM {q.REJECTED} WHERE UPLOAD_ID = %(uid)s ORDER BY SOURCE_ROW, PARAMETER LIMIT 20",
        {"uid": upload_id})]
    return {
        "dataset_type": dtype,
        "options": options.model_dump(),
        "policy": ("No values are imputed. Rows with missing or invalid timestamps/values are rejected with a reason; "
                   "exact duplicates are dropped; conflicting duplicates keep the first row by source order."),
        "started_at": started.isoformat(), "duration_seconds": seconds,
        "layout": ("wide" if mapping.get("value_columns") else "long") if dtype == "SENSOR" else "production",
        "counts": {"bronze_rows": bronze_rows, "input_units": input_units, "silver_rows": silver_rows,
                   "gold_rows": gold_rows, "rejected_rows": rejected, "rejected_by_reason": reasons,
                   "duplicates_removed": reasons.get("DUPLICATE", 0) + reasons.get("CONFLICTING_DUPLICATE", 0)},
        "time_range": {"first": _iso(r[0]), "last": _iso(r[1])},
        "summary": summary,
        "checks": checks,
        "all_checks_passed": all(c["passed"] for c in checks),
        "rejection_sample": sample,
    }


def _check(name: str, passed: bool, detail: str) -> dict:
    return {"name": name, "passed": bool(passed), "detail": detail}


# ------------------------------------------------------------------ read
def get_report(upload_id: str) -> dict:
    rows = sf.execute(f"SELECT PROCESSING_REPORT, STATUS FROM {U} WHERE UPLOAD_ID = %s", (upload_id,))
    if not rows:
        raise NotFoundError(f"Upload '{upload_id}' not found")
    if not rows[0][0]:
        raise NotFoundError(f"No processing report yet (upload status: {rows[0][1]})")
    return json.loads(rows[0][0])


def list_rejections(upload_id: str, reason: str | None, p: PageParams) -> tuple[list[RejectionOut], int]:
    _upload_row(upload_id)
    where, args = "WHERE UPLOAD_ID = %s", [upload_id]
    if reason:
        where += " AND REASON = %s"
        args.append(reason.upper())
    total = sf.execute(f"SELECT COUNT(*) FROM {q.REJECTED} {where}", args)[0][0]
    rows = sf.execute(f"SELECT SOURCE_ROW, PARAMETER, REASON, RAW_RECORD FROM {q.REJECTED} {where} "
                      "ORDER BY SOURCE_ROW, PARAMETER LIMIT %s OFFSET %s", [*args, p.page_size, p.offset])
    return [RejectionOut(source_row=r[0], parameter=r[1], reason=r[2], raw_record=json.loads(r[3]) if r[3] else None)
            for r in rows], total
