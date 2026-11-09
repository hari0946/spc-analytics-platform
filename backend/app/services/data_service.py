"""Read access to GOLD historical data with filtering. Everything is computed in Snowflake; only the selected
page / aggregate is transferred to Python."""
from decimal import Decimal

from app.core import snowflake as sf
from app.core.exceptions import ConflictError, NotFoundError
from app.schemas.common import PageParams
from app.schemas.data import (FilterOptions, OptionValue, ParameterSummary, ProductionRow, ProductionSummary,
                              SensorRow, SensorSummary)
from app.services import pipeline_sql as q
from app.services.filters import COLUMNS, TIME_COLUMN, DataFilters, build_where

UPLOADS = f"{q.DB}.METADATA.DATASET_UPLOADS"
TABLE = {"sensor": q.GOLD_SENSOR, "production": q.GOLD_PROD}
DATASET_TYPE = {"sensor": "SENSOR", "production": "PRODUCTION"}
OPTION_LIMIT = 1000
SENSOR_ORDER = "MEASURED_AT, UPLOAD_ID, PLANT_CODE, LINE_CODE, ASSET_CODE, TAG_NAME, PARAMETER"      # unique per row
PRODUCTION_ORDER = "EVENT_TIME, UPLOAD_ID, PLANT_CODE, LINE_CODE, ASSET_CODE, BATCH_ID, PRODUCT"


def _f(v):
    return float(v) if isinstance(v, Decimal) else v


def check_datasets(f: DataFilters, dataset: str) -> None:
    """Explicitly requested uploads must exist, be fully processed and be of the right kind."""
    if not f.upload_ids:
        return
    ph = ",".join(["%s"] * len(f.upload_ids))
    found = {r[0]: (r[1], r[2]) for r in sf.execute(
        f"SELECT UPLOAD_ID, STATUS, DATASET_TYPE FROM {UPLOADS} WHERE UPLOAD_ID IN ({ph})", f.upload_ids)}
    missing = [i for i in f.upload_ids if i not in found]
    if missing:
        raise NotFoundError("Dataset(s) not found", {"upload_ids": missing})
    pending = [i for i, (s, _) in found.items() if s != "COMPLETED"]
    if pending:
        raise ConflictError("Dataset(s) not processed yet; run processing first", {"upload_ids": pending})
    wrong = [i for i, (_, t) in found.items() if t != DATASET_TYPE[dataset]]
    if wrong:
        raise ConflictError(f"Dataset(s) do not contain {dataset} data", {"upload_ids": wrong})


def options(f: DataFilters, dataset: str) -> FilterOptions:
    check_datasets(f, dataset)
    table, arms, params = TABLE[dataset], [], []
    dims = {**COLUMNS[dataset], "datasets": "UPLOAD_ID"}
    for name, col in dims.items():
        where, p = build_where(f, dataset, UPLOADS, exclude=name)
        arms.append(f"(SELECT '{name}' AS K, {col}::VARCHAR AS V, COUNT(*) AS N FROM {table} {where} "
                    f"AND {col} IS NOT NULL GROUP BY {col} ORDER BY {col} LIMIT {OPTION_LIMIT})")
        params += p
    result: dict[str, list[OptionValue]] = {n: [] for n in dims}
    for k, v, n in sf.execute(" UNION ALL ".join(arms), params):
        result[k].append(OptionValue(value=v, count=n))
    for vals in result.values():
        vals.sort(key=lambda o: o.value)
    where, p = build_where(f, dataset, UPLOADS, exclude="dates")
    tcol = TIME_COLUMN[dataset]
    first, last = sf.execute(f"SELECT MIN({tcol}), MAX({tcol}) FROM {table} {where}", p)[0]
    return FilterOptions(plants=result["plants"], lines=result["lines"], assets=result["assets"],
                         tags=result.get("tags", []), parameters=result.get("parameters", []),
                         batches=result["batches"], datasets=result["datasets"], first=first, last=last)


def sensor_rows(f: DataFilters, p: PageParams) -> tuple[list[SensorRow], int]:
    check_datasets(f, "sensor")
    where, params = build_where(f, "sensor", UPLOADS)
    total = sf.execute(f"SELECT COUNT(*) FROM {q.GOLD_SENSOR} {where}", params)[0][0]
    rows = sf.execute(
        f"SELECT UPLOAD_ID, PLANT_CODE, LINE_CODE, ASSET_CODE, TAG_NAME, PARAMETER, MEASURED_AT, VALUE, BATCH_ID, SUBGROUP_ID "
        f"FROM {q.GOLD_SENSOR} {where} ORDER BY {SENSOR_ORDER} LIMIT %s OFFSET %s", [*params, p.page_size, p.offset])
    return [SensorRow(upload_id=r[0], plant_code=r[1], line_code=r[2], asset_code=r[3], tag_name=r[4], parameter=r[5],
                      measured_at=r[6], value=r[7], batch_id=r[8], subgroup_id=r[9]) for r in rows], total


def production_rows(f: DataFilters, p: PageParams) -> tuple[list[ProductionRow], int]:
    check_datasets(f, "production")
    where, params = build_where(f, "production", UPLOADS)
    total = sf.execute(f"SELECT COUNT(*) FROM {q.GOLD_PROD} {where}", params)[0][0]
    rows = sf.execute(
        f"SELECT UPLOAD_ID, PLANT_CODE, LINE_CODE, ASSET_CODE, BATCH_ID, PRODUCT, EVENT_TIME, UNITS_PRODUCED, SAMPLE_SIZE, "
        f"DEFECTIVE_COUNT, DEFECT_COUNT FROM {q.GOLD_PROD} {where} ORDER BY {PRODUCTION_ORDER} LIMIT %s OFFSET %s",
        [*params, p.page_size, p.offset])
    return [ProductionRow(upload_id=r[0], plant_code=r[1], line_code=r[2], asset_code=r[3], batch_id=r[4], product=r[5],
                          event_time=r[6], units_produced=r[7], sample_size=r[8], defective_count=r[9],
                          defect_count=r[10]) for r in rows], total


def sensor_summary(f: DataFilters) -> SensorSummary:
    check_datasets(f, "sensor")
    where, params = build_where(f, "sensor", UPLOADS)
    per = sf.execute(f"SELECT PARAMETER, COUNT(*), MIN(VALUE), MAX(VALUE), AVG(VALUE), MIN(MEASURED_AT), MAX(MEASURED_AT), "
                     f"COUNT(DISTINCT UPLOAD_ID) FROM {q.GOLD_SENSOR} {where} GROUP BY PARAMETER ORDER BY PARAMETER", params)
    tot = sf.execute(f"SELECT COUNT(*), MIN(MEASURED_AT), MAX(MEASURED_AT), COUNT(DISTINCT UPLOAD_ID) "
                     f"FROM {q.GOLD_SENSOR} {where}", params)[0]
    return SensorSummary(
        total_rows=tot[0], first=tot[1], last=tot[2], datasets=tot[3],
        parameters=[ParameterSummary(parameter=r[0], n=r[1], min=_f(r[2]), max=_f(r[3]), mean=_f(r[4])) for r in per])


def production_summary(f: DataFilters) -> ProductionSummary:
    check_datasets(f, "production")
    where, params = build_where(f, "production", UPLOADS)
    r = sf.execute(f"SELECT COUNT(*), MIN(EVENT_TIME), MAX(EVENT_TIME), COUNT(DISTINCT UPLOAD_ID), SUM(UNITS_PRODUCED), "
                   f"SUM(SAMPLE_SIZE), SUM(DEFECTIVE_COUNT), SUM(DEFECT_COUNT), COUNT(DISTINCT BATCH_ID) "
                   f"FROM {q.GOLD_PROD} {where}", params)[0]
    return ProductionSummary(total_rows=r[0], first=r[1], last=r[2], datasets=r[3], units_produced=_f(r[4]),
                             sample_size=_f(r[5]), defective_count=_f(r[6]), defect_count=_f(r[7]), batches=r[8])


# ---------------------------------------------------------------- time series for charts (Phase 9)
MAX_SERIES = 40
_KEY = "PLANT_CODE, LINE_CODE, ASSET_CODE, TAG_NAME, PARAMETER"


def sensor_series(f: DataFilters, max_points: int):
    """One series per (plant, line, asset, tag, parameter). Series longer than max_points are aggregated in Snowflake
    into <= max_points+1 equal time buckets (avg/min/max/n), so extremes stay visible and nothing is sampled away."""
    from app.core.exceptions import UnprocessableError
    from app.schemas.data import Series, SeriesPoint, SeriesResponse

    check_datasets(f, "sensor")
    where, params = build_where(f, "sensor", UPLOADS)
    n_series = sf.execute(f"SELECT COUNT(DISTINCT HASH({_KEY})) FROM {q.GOLD_SENSOR} {where}", params)[0][0]
    if n_series > MAX_SERIES:
        raise UnprocessableError(f"The selection contains {n_series} series (limit {MAX_SERIES}); narrow the filters",
                                 {"series": n_series, "limit": MAX_SERIES})
    cols = ", ".join(f"f.{c.strip()}" for c in _KEY.split(","))
    sql = f"""
WITH f AS (SELECT {_KEY}, MEASURED_AT AS T, VALUE AS V, HASH({_KEY}) AS SK FROM {q.GOLD_SENSOR} {where}),
     c AS (SELECT SK, COUNT(*) AS N, MIN(T) AS T0,
                  GREATEST(1, CEIL(DATEDIFF('second', MIN(T), MAX(T)) / %s)) AS W FROM f GROUP BY SK)
SELECT {cols}, f.T, f.V, f.V, f.V, 1, c.N, 0 FROM f JOIN c ON f.SK = c.SK WHERE c.N <= %s
UNION ALL
SELECT {cols}, DATEADD('second', FLOOR(DATEDIFF('second', c.T0, f.T) / c.W) * c.W, c.T0),
       AVG(f.V), MIN(f.V), MAX(f.V), COUNT(*), c.N, c.W
FROM f JOIN c ON f.SK = c.SK WHERE c.N > %s
GROUP BY {cols}, c.T0, c.W, c.N, FLOOR(DATEDIFF('second', c.T0, f.T) / c.W)
ORDER BY 1, 2, 3, 4, 5, 6"""
    rows = sf.execute(sql, [*params, max_points, max_points, max_points])
    out: dict[tuple, Series] = {}
    for r in rows:
        key = tuple(r[:5])
        s = out.get(key)
        if s is None:
            s = out[key] = Series(plant_code=r[0], line_code=r[1], asset_code=r[2], tag_name=r[3], parameter=r[4],
                                  n_total=r[10], aggregated=int(r[11]) > 0, bucket_seconds=int(r[11]) or None, points=[])
        s.points.append(SeriesPoint(t=r[5], v=_f(r[6]), min=_f(r[7]), max=_f(r[8]), n=r[9]))
    return SeriesResponse(max_points=max_points, series=list(out.values()))
