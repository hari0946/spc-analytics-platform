"""Historical-data filters: parsing/validation and the parameterized WHERE-clause builder (no I/O).

Semantics
- Each list filter (plant, line, asset, tag, parameter, batch, upload_id) matches ANY of the given values;
  different filters are combined with AND.
- Codes (plant/line/asset/tag/batch) match exactly as stored; `parameter` is case-insensitive (stored upper-case).
- `start` / `end` are inclusive. A date-only value means the whole day (start 00:00:00, end 23:59:59.999999).
  Timestamps in the data have no time zone; a value with a UTC offset is converted to UTC first.
- Values are only ever bound parameters; column names come from the fixed maps below.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone

from app.core.exceptions import UnprocessableError

UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
MAX_VALUES = 50
MAX_LEN = 100

# filter name -> column, per dataset
SENSOR_COLUMNS = {"plants": "PLANT_CODE", "lines": "LINE_CODE", "assets": "ASSET_CODE", "tags": "TAG_NAME",
                  "parameters": "PARAMETER", "batches": "BATCH_ID"}
PRODUCTION_COLUMNS = {"plants": "PLANT_CODE", "lines": "LINE_CODE", "assets": "ASSET_CODE", "batches": "BATCH_ID"}
TIME_COLUMN = {"sensor": "MEASURED_AT", "production": "EVENT_TIME"}
COLUMNS = {"sensor": SENSOR_COLUMNS, "production": PRODUCTION_COLUMNS}


@dataclass
class DataFilters:
    upload_ids: list[str] = field(default_factory=list)
    plants: list[str] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    parameters: list[str] = field(default_factory=list)
    batches: list[str] = field(default_factory=list)
    start: datetime | None = None
    end: datetime | None = None


def parse_bound(value: str | None, name: str, is_end: bool) -> datetime | None:
    if value is None or value.strip() == "":
        return None
    v = value.strip()
    try:
        if len(v) == 10:  # date only
            d = datetime.strptime(v, "%Y-%m-%d")
            return datetime.combine(d.date(), time.max) if is_end else d
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        raise UnprocessableError(f"'{name}' must be an ISO date or datetime (e.g. 2024-03-01 or 2024-03-01T08:30:00)")
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _clean_list(values: list[str] | None, name: str, upper: bool = False) -> list[str]:
    out: list[str] = []
    for v in values or []:
        v = v.strip()
        if not v:
            continue
        if len(v) > MAX_LEN:
            raise UnprocessableError(f"'{name}' values must be at most {MAX_LEN} characters")
        out.append(v.upper() if upper else v)
    if len(out) > MAX_VALUES:
        raise UnprocessableError(f"At most {MAX_VALUES} values are allowed for '{name}'")
    return list(dict.fromkeys(out))  # de-duplicate, keep order


def build_filters(upload_id=None, plant=None, line=None, asset=None, tag=None, parameter=None, batch=None,
                  start: str | None = None, end: str | None = None) -> DataFilters:
    ids = _clean_list(upload_id, "upload_id")
    bad = [i for i in ids if not UUID_RE.match(i)]
    if bad:
        raise UnprocessableError("upload_id must be a UUID", {"invalid": bad})
    f = DataFilters(upload_ids=ids, plants=_clean_list(plant, "plant"), lines=_clean_list(line, "line"),
                    assets=_clean_list(asset, "asset"), tags=_clean_list(tag, "tag"),
                    parameters=_clean_list(parameter, "parameter", upper=True), batches=_clean_list(batch, "batch"),
                    start=parse_bound(start, "start", False), end=parse_bound(end, "end", True))
    if f.start and f.end and f.start > f.end:
        raise UnprocessableError("'start' must not be after 'end'")
    return f


def build_where(f: DataFilters, dataset: str, uploads_table: str, exclude: str | None = None) -> tuple[str, list]:
    """WHERE clause (always starts with WHERE) and positional params. `exclude` omits one filter (for facets)."""
    cols = COLUMNS[dataset]
    unsupported = [n for n in ("tags", "parameters") if n not in cols and getattr(f, n)]
    if unsupported:
        raise UnprocessableError(f"Filter(s) not applicable to {dataset} data: {', '.join(u.rstrip('s') for u in unsupported)}")
    clauses, params = [], []
    if f.upload_ids and exclude != "datasets":
        clauses.append(f"UPLOAD_ID IN ({','.join(['%s'] * len(f.upload_ids))})")
        params += f.upload_ids
    else:  # only fully processed datasets are visible
        clauses.append(f"UPLOAD_ID IN (SELECT UPLOAD_ID FROM {uploads_table} WHERE STATUS = 'COMPLETED')")
    for name, col in cols.items():
        vals = getattr(f, name)
        if vals and name != exclude:
            clauses.append(f"{col} IN ({','.join(['%s'] * len(vals))})")
            params += vals
    tcol = TIME_COLUMN[dataset]
    if f.start and exclude != "dates":
        clauses.append(f"{tcol} >= %s")
        params.append(f.start)
    if f.end and exclude != "dates":
        clauses.append(f"{tcol} <= %s")
        params.append(f.end)
    return "WHERE " + " AND ".join(clauses), params
