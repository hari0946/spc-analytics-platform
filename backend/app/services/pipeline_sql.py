"""SQL builders for BRONZE -> SILVER -> GOLD. All heavy lifting runs inside Snowflake.

Safety: user-controlled values (source column names, timestamp format, upload id) are only ever passed as
bound parameters (%(name)s) - never concatenated into the SQL text. Table names are constants.
"""
import json

DB = "SPC_PLATFORM"
BRONZE_SENSOR = f"{DB}.BRONZE.RAW_SENSOR_DATA"
BRONZE_PROD = f"{DB}.BRONZE.RAW_PRODUCTION_DATA"
CLEAN = f"{DB}.SILVER.CLEAN_SENSOR_DATA"
VALIDATED = f"{DB}.SILVER.VALIDATED_PRODUCTION_DATA"
REJECTED = f"{DB}.SILVER.REJECTED_RECORDS"
GOLD_SENSOR = f"{DB}.GOLD.GOLD_SENSOR_DATA"
GOLD_PROD = f"{DB}.GOLD.GOLD_PRODUCTION_DATA"
GOLD_PROCESS = f"{DB}.GOLD.GOLD_PROCESS_DATA"
DERIVED_TABLES = [CLEAN, VALIDATED, REJECTED, GOLD_SENSOR, GOLD_PROD, GOLD_PROCESS]

# Text that means "no value" in industrial exports
MISSING = "('', 'NA', 'N/A', '#N/A', 'NULL', 'NONE', 'NAN', '-', '--', '?')"
NON_FINITE = "('NaN', 'inf', '-inf', 'Infinity', '-Infinity')"
COUNT_KEYS = ["units_produced", "sample_size", "defective_count", "defect_count"]

Statement = tuple[str, dict]


def _is_missing(col: str) -> str:
    return f"({col} IS NULL OR UPPER(TRIM({col})) IN {MISSING})"


def _code(mapping: dict, key: str, params: dict) -> str:
    """Optional text column (plant/line/asset/...): trimmed, empty -> NULL; NULL when not mapped."""
    if key in mapping:
        params[f"c_{key}"] = mapping[key]
        return f"NULLIF(TRIM(GET(b.RAW_RECORD, %(c_{key})s)::STRING), '')"
    return "NULL::VARCHAR"


def _timestamp_parts(mapping: dict, fmt: str | None, params: dict) -> tuple[str, str]:
    params["c_timestamp"] = mapping["timestamp"]
    if fmt:
        params["ts_fmt"] = fmt
        return "TRY_TO_TIMESTAMP_NTZ(TRIM(TS_RAW), %(ts_fmt)s)", "TS_RAW"
    return "TRY_TO_TIMESTAMP_NTZ(TRIM(TS_RAW))", "TS_RAW"


def _ts_checks(ts_col: str) -> str:
    return (
        f"WHEN {_is_missing('TS_RAW')} THEN 'MISSING_TIMESTAMP' "
        f"WHEN {ts_col} IS NULL THEN 'INVALID_TIMESTAMP' "
        f"WHEN {ts_col} < '1970-01-01'::TIMESTAMP_NTZ OR {ts_col} > DATEADD(day, 1, SYSDATE()) THEN 'TIMESTAMP_OUT_OF_RANGE' "
    )


# ------------------------------------------------------------------------------------ SENSOR
def _sensor_cte(mapping: dict, fmt: str | None, upload_id: str) -> tuple[str, dict]:
    p: dict = {"uid": upload_id}
    ts_expr, _ = _timestamp_parts(mapping, fmt, p)
    if mapping.get("value_columns"):  # wide layout: one measurement per listed column
        p["cols"] = json.dumps(mapping["value_columns"])
        frm = f"FROM {BRONZE_SENSOR} b, LATERAL FLATTEN(input => PARSE_JSON(%(cols)s)) f"
        param_expr, val_expr = "f.value::STRING", "GET(b.RAW_RECORD, f.value::STRING)::STRING"
    else:  # long layout
        p["c_parameter"], p["c_value"] = mapping["parameter"], mapping["value"]
        frm = f"FROM {BRONZE_SENSOR} b"
        param_expr = "GET(b.RAW_RECORD, %(c_parameter)s)::STRING"
        val_expr = "GET(b.RAW_RECORD, %(c_value)s)::STRING"
    part = "PLANT_CODE, LINE_CODE, ASSET_CODE, TAG_NAME, PARAMETER, MEASURED_AT"
    cte = f"""
WITH exploded AS (
    SELECT b.SOURCE_ROW, b.RAW_RECORD,
           {_code(mapping, 'plant', p)} AS PLANT_CODE, {_code(mapping, 'line', p)} AS LINE_CODE,
           {_code(mapping, 'asset', p)} AS ASSET_CODE, {_code(mapping, 'tag', p)} AS TAG_NAME,
           {_code(mapping, 'batch', p)} AS BATCH_ID,
           GET(b.RAW_RECORD, %(c_timestamp)s)::STRING AS TS_RAW,
           {param_expr} AS PARAM_RAW, {val_expr} AS VAL_RAW
    {frm}
    WHERE b.UPLOAD_ID = %(uid)s
), typed AS (
    SELECT *, UPPER(TRIM(PARAM_RAW)) AS PARAMETER, {ts_expr} AS MEASURED_AT, TRY_TO_DOUBLE(TRIM(VAL_RAW)) AS VALUE
    FROM exploded
), checked AS (
    SELECT *, CASE {_ts_checks('MEASURED_AT')}
        WHEN {_is_missing('PARAM_RAW')} THEN 'MISSING_PARAMETER'
        WHEN {_is_missing('VAL_RAW')} THEN 'MISSING_VALUE'
        WHEN VALUE IS NULL OR VALUE::STRING IN {NON_FINITE} THEN 'INVALID_VALUE'
    END AS REJECT_REASON
    FROM typed
), ranked AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY {part} ORDER BY SOURCE_ROW) AS RN,
              FIRST_VALUE(VALUE) OVER (PARTITION BY {part} ORDER BY SOURCE_ROW) AS FIRST_VAL
    FROM checked WHERE REJECT_REASON IS NULL
)"""
    return cte, p


def sensor_silver(mapping: dict, fmt: str | None, upload_id: str) -> list[Statement]:
    cte, p = _sensor_cte(mapping, fmt, upload_id)
    clean = (f"INSERT INTO {CLEAN} (UPLOAD_ID, SOURCE_ROW, PLANT_CODE, LINE_CODE, ASSET_CODE, TAG_NAME, PARAMETER, "
             f"MEASURED_AT, VALUE, BATCH_ID) {cte} "
             "SELECT %(uid)s, SOURCE_ROW, PLANT_CODE, LINE_CODE, ASSET_CODE, TAG_NAME, PARAMETER, MEASURED_AT, VALUE, BATCH_ID "
             "FROM ranked WHERE RN = 1")
    rejects = (f"INSERT INTO {REJECTED} (UPLOAD_ID, SOURCE_ROW, PARAMETER, REASON, RAW_RECORD) {cte} "
               "SELECT %(uid)s, SOURCE_ROW, PARAMETER, REJECT_REASON, RAW_RECORD FROM checked WHERE REJECT_REASON IS NOT NULL "
               "UNION ALL "
               "SELECT %(uid)s, SOURCE_ROW, PARAMETER, IFF(VALUE = FIRST_VAL, 'DUPLICATE', 'CONFLICTING_DUPLICATE'), RAW_RECORD "
               "FROM ranked WHERE RN > 1")
    return [(clean, p), (rejects, p)]


def sensor_gold(upload_id: str) -> list[Statement]:
    p = {"uid": upload_id}
    sensor = f"""INSERT INTO {GOLD_SENSOR}
        (UPLOAD_ID, PLANT_CODE, LINE_CODE, ASSET_CODE, TAG_NAME, PARAMETER, MEASURED_AT, VALUE, BATCH_ID, SUBGROUP_ID)
    WITH s AS (SELECT * FROM {CLEAN} WHERE UPLOAD_ID = %(uid)s),
         bt AS (SELECT PARAMETER, BATCH_ID, MIN(MEASURED_AT) AS FIRST_AT FROM s WHERE BATCH_ID IS NOT NULL GROUP BY 1, 2),
         sg AS (SELECT PARAMETER, BATCH_ID,
                       DENSE_RANK() OVER (PARTITION BY PARAMETER ORDER BY FIRST_AT, BATCH_ID) AS SUBGROUP_ID FROM bt)
    SELECT s.UPLOAD_ID, s.PLANT_CODE, s.LINE_CODE, s.ASSET_CODE, s.TAG_NAME, s.PARAMETER, s.MEASURED_AT, s.VALUE,
           s.BATCH_ID, sg.SUBGROUP_ID
    FROM s LEFT JOIN sg ON s.PARAMETER = sg.PARAMETER AND s.BATCH_ID = sg.BATCH_ID
    ORDER BY s.PARAMETER, s.MEASURED_AT"""
    process = f"""INSERT INTO {GOLD_PROCESS}
        (UPLOAD_ID, ASSET_CODE, PARAMETER, BUCKET_START, BUCKET_UNIT, N_POINTS, MEAN_VALUE, MIN_VALUE, MAX_VALUE, STDDEV_VALUE)
    SELECT UPLOAD_ID, ASSET_CODE, PARAMETER, DATE_TRUNC('HOUR', MEASURED_AT), 'HOUR', COUNT(*), AVG(VALUE),
           MIN(VALUE), MAX(VALUE), STDDEV(VALUE)
    FROM {GOLD_SENSOR} WHERE UPLOAD_ID = %(uid)s
    GROUP BY UPLOAD_ID, ASSET_CODE, PARAMETER, DATE_TRUNC('HOUR', MEASURED_AT)"""
    return [(sensor, p), (process, p)]


# ------------------------------------------------------------------------------------ PRODUCTION
def _production_cte(mapping: dict, fmt: str | None, upload_id: str) -> tuple[str, dict]:
    p: dict = {"uid": upload_id}
    ts_expr, _ = _timestamp_parts(mapping, fmt, p)
    mapped = [k for k in COUNT_KEYS if k in mapping]
    raw_cols = []
    for k in COUNT_KEYS:
        if k in mapping:
            p[f"c_{k}"] = mapping[k]
            raw_cols.append(f"GET(b.RAW_RECORD, %(c_{k})s)::STRING AS {k.upper()}_RAW")
        else:
            raw_cols.append(f"NULL::STRING AS {k.upper()}_RAW")
    num_cols = ", ".join(f"TRY_TO_DOUBLE(TRIM({k.upper()}_RAW)) AS {k.upper()}_NUM" for k in COUNT_KEYS)

    def bad(k: str) -> str:
        n, r = f"{k.upper()}_NUM", f"{k.upper()}_RAW"
        return (f"(NOT {_is_missing(r)} AND ({n} IS NULL OR {n} < 0 OR {n} <> FLOOR({n}) OR {n}::STRING IN {NON_FINITE}))")

    branches = "".join(f"WHEN {bad(k)} THEN 'INVALID_COUNT' " for k in mapped)
    all_missing = " AND ".join(_is_missing(f"{k.upper()}_RAW") for k in mapped)
    extra = ""
    if "sample_size" in mapped:
        extra += "WHEN SAMPLE_SIZE_NUM = 0 THEN 'INVALID_COUNT' "
    if "sample_size" in mapped and "defective_count" in mapped:
        extra += "WHEN DEFECTIVE_COUNT_NUM > SAMPLE_SIZE_NUM THEN 'INCONSISTENT_COUNTS' "
    part = "PLANT_CODE, LINE_CODE, ASSET_CODE, BATCH_ID, PRODUCT, MEASURED_AT"
    measures = "HASH(UNITS_PRODUCED_NUM, SAMPLE_SIZE_NUM, DEFECTIVE_COUNT_NUM, DEFECT_COUNT_NUM)"
    prod = _code(mapping, "product", p)
    cte = f"""
WITH exploded AS (
    SELECT b.SOURCE_ROW, b.RAW_RECORD,
           {_code(mapping, 'plant', p)} AS PLANT_CODE, {_code(mapping, 'line', p)} AS LINE_CODE,
           {_code(mapping, 'asset', p)} AS ASSET_CODE, {_code(mapping, 'batch', p)} AS BATCH_ID, {prod} AS PRODUCT,
           GET(b.RAW_RECORD, %(c_timestamp)s)::STRING AS TS_RAW, {', '.join(raw_cols)}
    FROM {BRONZE_PROD} b WHERE b.UPLOAD_ID = %(uid)s
), typed AS (
    SELECT *, {ts_expr} AS MEASURED_AT, {num_cols} FROM exploded
), checked AS (
    SELECT *, CASE {_ts_checks('MEASURED_AT')}{branches}
        WHEN {all_missing} THEN 'MISSING_VALUE' {extra}
    END AS REJECT_REASON
    FROM typed
), ranked AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY {part} ORDER BY SOURCE_ROW) AS RN,
              FIRST_VALUE({measures}) OVER (PARTITION BY {part} ORDER BY SOURCE_ROW) AS FIRST_HASH,
              {measures} AS THIS_HASH
    FROM checked WHERE REJECT_REASON IS NULL
)"""
    return cte, p


def production_silver(mapping: dict, fmt: str | None, upload_id: str) -> list[Statement]:
    cte, p = _production_cte(mapping, fmt, upload_id)
    valid = (f"INSERT INTO {VALIDATED} (UPLOAD_ID, SOURCE_ROW, PLANT_CODE, LINE_CODE, ASSET_CODE, BATCH_ID, PRODUCT, "
             f"EVENT_TIME, UNITS_PRODUCED, SAMPLE_SIZE, DEFECTIVE_COUNT, DEFECT_COUNT) {cte} "
             "SELECT %(uid)s, SOURCE_ROW, PLANT_CODE, LINE_CODE, ASSET_CODE, BATCH_ID, PRODUCT, MEASURED_AT, "
             "UNITS_PRODUCED_NUM::NUMBER(38,0), SAMPLE_SIZE_NUM::NUMBER(38,0), DEFECTIVE_COUNT_NUM::NUMBER(38,0), "
             "DEFECT_COUNT_NUM::NUMBER(38,0) FROM ranked WHERE RN = 1")
    rejects = (f"INSERT INTO {REJECTED} (UPLOAD_ID, SOURCE_ROW, PARAMETER, REASON, RAW_RECORD) {cte} "
               "SELECT %(uid)s, SOURCE_ROW, NULL, REJECT_REASON, RAW_RECORD FROM checked WHERE REJECT_REASON IS NOT NULL "
               "UNION ALL "
               "SELECT %(uid)s, SOURCE_ROW, NULL, IFF(THIS_HASH = FIRST_HASH, 'DUPLICATE', 'CONFLICTING_DUPLICATE'), RAW_RECORD "
               "FROM ranked WHERE RN > 1")
    return [(valid, p), (rejects, p)]


def production_gold(upload_id: str) -> list[Statement]:
    p = {"uid": upload_id}
    sql = (f"INSERT INTO {GOLD_PROD} (UPLOAD_ID, PLANT_CODE, LINE_CODE, ASSET_CODE, BATCH_ID, PRODUCT, EVENT_TIME, "
           "UNITS_PRODUCED, SAMPLE_SIZE, DEFECTIVE_COUNT, DEFECT_COUNT) "
           "SELECT UPLOAD_ID, PLANT_CODE, LINE_CODE, ASSET_CODE, BATCH_ID, PRODUCT, EVENT_TIME, UNITS_PRODUCED, "
           f"SAMPLE_SIZE, DEFECTIVE_COUNT, DEFECT_COUNT FROM {VALIDATED} WHERE UPLOAD_ID = %(uid)s "
           "ORDER BY EVENT_TIME")
    return [(sql, p)]


def clear_statements(upload_id: str) -> list[Statement]:
    return [(f"DELETE FROM {t} WHERE UPLOAD_ID = %(uid)s", {"uid": upload_id}) for t in DERIVED_TABLES]
