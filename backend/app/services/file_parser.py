"""Pure file handling for uploads (no FastAPI / Snowflake): validate CSV / XLSX / XLS and convert them to
gzip'd NDJSON parts that Snowflake can COPY into a VARIANT column.

Each NDJSON line is {"r": <record number, header = 1>, "d": {<source column>: <original text value or null>}}.
Values are kept as text exactly as received; typing and cleaning happen in SILVER.
"""
import codecs
import csv
import datetime as dt
import gzip
import hashlib
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

PREVIEW_ROWS = 20
SUGGEST_SAMPLE_ROWS = 200
MAX_XLSX_UNCOMPRESSED = 2 * 1024**3
OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")


class FileValidationError(Exception):
    """The file cannot be accepted; the message is safe to show to the user."""


@dataclass
class ParseResult:
    kind: str
    columns: list[str]
    parts: list[Path]
    rows_received: int = 0
    empty_rows: int = 0
    duplicate_rows: int = 0
    ragged_rows: int = 0
    preview_rows: list[list[str | None]] = field(default_factory=list)
    sample_rows: list[list[str | None]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- type detection
def detect_kind(path: Path, filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in (".csv", ".xlsx", ".xls"):
        raise FileValidationError("Unsupported file type; upload a .csv, .xlsx or .xls file")
    if path.stat().st_size == 0:
        raise FileValidationError("The file is empty")
    with path.open("rb") as f:
        head = f.read(8192)
    if ext == ".xlsx" and not zipfile.is_zipfile(path):
        raise FileValidationError("The file is not a valid .xlsx workbook")
    if ext == ".xls" and not head.startswith(OLE_MAGIC):
        raise FileValidationError("The file is not a valid .xls workbook")
    if ext == ".csv" and (b"\x00" in head or head.startswith((b"PK\x03\x04", OLE_MAGIC))):
        raise FileValidationError("The file does not look like a text CSV file")
    return ext[1:]


# ---------------------------------------------------------------- readers: yield rows as list[str|None]
def _csv_encoding(path: Path) -> str:
    dec = codecs.getincrementaldecoder("utf-8-sig")()
    try:
        with path.open("rb") as f:
            while chunk := f.read(1 << 20):
                dec.decode(chunk)
            dec.decode(b"", final=True)
        return "utf-8-sig"
    except UnicodeDecodeError:
        return "cp1252"


def _iter_csv(path: Path) -> Iterator[list[str | None]]:
    enc = _csv_encoding(path)
    csv.field_size_limit(10 * 1024 * 1024)
    with path.open("r", encoding=enc, newline="", errors="replace") as f:
        sample = f.read(65536)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        try:
            for row in csv.reader(f, dialect):
                yield [c for c in row]
        except csv.Error as e:
            raise FileValidationError(f"Malformed CSV: {e}")


def _cell_text(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, dt.datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, (dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() and abs(v) < 1e15 else repr(v)
    return str(v)


def _iter_xlsx(path: Path, sheet: str | None) -> Iterator[list[str | None]]:
    import openpyxl

    try:
        total = sum(i.file_size for i in zipfile.ZipFile(path).infolist())
    except zipfile.BadZipFile:
        raise FileValidationError("The file is not a valid .xlsx workbook")
    if total > MAX_XLSX_UNCOMPRESSED:
        raise FileValidationError("The workbook expands to too much data")
    fh = path.open("rb")  # file object: openpyxl otherwise insists on a .xlsx file extension
    try:
        wb = openpyxl.load_workbook(fh, read_only=True, data_only=True)
    except Exception as e:  # noqa: BLE001 - openpyxl raises many types for corrupt files
        fh.close()
        raise FileValidationError(f"Could not read the workbook: {type(e).__name__}")
    try:
        if sheet:
            if sheet not in wb.sheetnames:
                raise FileValidationError(f"Sheet '{sheet}' not found. Available: {', '.join(wb.sheetnames)}")
            ws = wb[sheet]
        else:
            if not wb.worksheets:
                raise FileValidationError("The workbook has no worksheets")
            ws = wb.worksheets[0]
        for row in ws.iter_rows(values_only=True):
            yield [_cell_text(c) for c in row]
    finally:
        wb.close()
        fh.close()


def _iter_xls(path: Path, sheet: str | None) -> Iterator[list[str | None]]:
    import xlrd

    try:
        book = xlrd.open_workbook(path)
    except Exception as e:  # noqa: BLE001
        raise FileValidationError(f"Could not read the workbook: {type(e).__name__}")
    names = book.sheet_names()
    if sheet and sheet not in names:
        raise FileValidationError(f"Sheet '{sheet}' not found. Available: {', '.join(names)}")
    ws = book.sheet_by_name(sheet) if sheet else book.sheet_by_index(0)
    for i in range(ws.nrows):
        out: list[str | None] = []
        for c in ws.row(i):
            if c.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                out.append(None)
            elif c.ctype == xlrd.XL_CELL_DATE:
                out.append(_cell_text(xlrd.xldate.xldate_as_datetime(c.value, book.datemode)))
            elif c.ctype == xlrd.XL_CELL_BOOLEAN:
                out.append("TRUE" if c.value else "FALSE")
            elif c.ctype == xlrd.XL_CELL_ERROR:
                out.append(None)
            else:
                out.append(_cell_text(c.value))
        yield out


def iter_rows(path: Path, kind: str, sheet: str | None = None) -> Iterator[list[str | None]]:
    if kind == "csv":
        return _iter_csv(path)
    if kind == "xlsx":
        return _iter_xlsx(path, sheet)
    return _iter_xls(path, sheet)


# ---------------------------------------------------------------- preprocessing
def _clean_header(raw: list[str | None], max_cols: int) -> list[str]:
    while raw and (raw[-1] is None or str(raw[-1]).strip() == ""):
        raw = raw[:-1]  # trailing empty header cells (common in Excel)
    if not raw:
        raise FileValidationError("The header row is empty")
    if len(raw) > max_cols:
        raise FileValidationError(f"Too many columns ({len(raw)}); the limit is {max_cols}")
    cols = [(str(c).strip() if c is not None and str(c).strip() else f"unnamed_{i + 1}") for i, c in enumerate(raw)]
    seen: dict[str, str] = {}
    dups = []
    for c in cols:
        k = c.lower()
        if k in seen:
            dups.append(c)
        seen[k] = c
    if dups:
        raise FileValidationError(f"Duplicate column names: {', '.join(sorted(set(dups)))}")
    return cols


def preprocess(path: Path, filename: str, out_dir: Path, *, part_rows: int, max_rows: int, max_cols: int,
               sheet: str | None = None) -> ParseResult:
    kind = detect_kind(path, filename)
    it = iter_rows(path, kind, sheet)
    try:
        header = next(it)
    except StopIteration:
        raise FileValidationError("The file contains no data")
    cols = _clean_header(header, max_cols)
    n = len(cols)
    res = ParseResult(kind=kind, columns=cols, parts=[])
    seen: set[int] = set()
    fh = None
    in_part = 0

    def new_part():
        p = out_dir / f"part_{len(res.parts) + 1:04d}.json.gz"
        res.parts.append(p)
        return gzip.open(p, "wt", encoding="utf-8", compresslevel=5)

    try:
        for rec_no, row in enumerate(it, start=2):
            if all(c is None or str(c).strip() == "" for c in row):
                res.empty_rows += 1
                continue
            if res.rows_received >= max_rows:
                raise FileValidationError(f"Too many rows; the limit is {max_rows:,}")
            vals = list(row[:n]) + [None] * (n - len(row))
            if len(row) < n:
                res.ragged_rows += 1
            record = dict(zip(cols, vals))
            extra = [c for c in row[n:] if c is not None and str(c).strip() != ""]
            if extra:
                record["_extra"] = extra
                res.ragged_rows += 1
            key = int.from_bytes(hashlib.blake2b(json.dumps(vals, ensure_ascii=False).encode(), digest_size=8).digest(), "big")
            if key in seen:
                res.duplicate_rows += 1
            else:
                seen.add(key)
            if fh is None or in_part >= part_rows:
                if fh:
                    fh.close()
                fh, in_part = new_part(), 0
            fh.write(json.dumps({"r": rec_no, "d": record}, ensure_ascii=False, separators=(",", ":")) + "\n")
            in_part += 1
            res.rows_received += 1
            if res.rows_received <= PREVIEW_ROWS:
                res.preview_rows.append(vals)
            if res.rows_received <= SUGGEST_SAMPLE_ROWS:
                res.sample_rows.append(vals)
    finally:
        if fh:
            fh.close()
    if res.rows_received == 0:
        raise FileValidationError("The file has a header but no data rows")
    if res.empty_rows:
        res.warnings.append(f"{res.empty_rows} empty row(s) skipped")
    if res.ragged_rows:
        res.warnings.append(f"{res.ragged_rows} row(s) had a different number of cells than the header")
    if res.duplicate_rows:
        res.warnings.append(f"{res.duplicate_rows} duplicate row(s) found (kept as received; removed in SILVER)")
    return res


# ---------------------------------------------------------------- column mapping suggestions
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


SYNONYMS = {
    "timestamp": ["timestamp", "time_stamp", "datetime", "date_time", "time", "ts", "date", "measured_at",
                  "recorded_at", "event_time", "reading_time", "sample_time"],
    "parameter": ["parameter", "param", "variable", "measure", "measurement_type", "signal", "metric"],
    "value": ["value", "reading", "measurement", "val", "result", "pv"],
    "tag": ["tag", "tag_name", "tag_id", "sensor", "sensor_id", "sensor_name", "point"],
    "asset": ["asset", "asset_code", "asset_id", "machine", "machine_id", "equipment", "equipment_id"],
    "line": ["line", "line_code", "production_line", "line_id"],
    "plant": ["plant", "plant_code", "site", "facility"],
    "batch": ["batch", "batch_id", "lot", "lot_number", "lot_id", "batch_number"],
    "product": ["product", "product_code", "product_name", "sku", "part"],
    "units_produced": ["units_produced", "units", "quantity", "output", "produced", "qty"],
    "sample_size": ["sample_size", "n", "subgroup_size", "inspected", "units_inspected", "sample"],
    "defective_count": ["defective_count", "defectives", "defective", "rejects", "nonconforming", "defective_units"],
    "defect_count": ["defect_count", "defects", "nonconformities", "total_defects"],
}
SENSOR_KEYS = ["timestamp", "parameter", "value", "tag", "asset", "line", "plant", "batch"]
PRODUCTION_KEYS = ["timestamp", "batch", "product", "asset", "line", "plant", "units_produced",
                   "sample_size", "defective_count", "defect_count"]


def _is_numeric_column(rows: list[list[str | None]], idx: int) -> bool:
    vals = [r[idx] for r in rows if idx < len(r) and r[idx] not in (None, "")]
    if len(vals) < 2:
        return False
    ok = 0
    for v in vals:
        try:
            float(str(v).replace(",", ""))
            ok += 1
        except ValueError:
            pass
    return ok / len(vals) >= 0.8


def suggest_mapping(columns: list[str], sample_rows: list[list[str | None]], dataset_type: str) -> dict:
    norm = {c: _norm(c) for c in columns}
    used: set[str] = set()
    out: dict[str, Any] = {}
    keys = SENSOR_KEYS if dataset_type == "SENSOR" else PRODUCTION_KEYS
    for key in keys:
        for syn in SYNONYMS[key]:
            hit = next((c for c in columns if norm[c] == syn and c not in used), None)
            if hit:
                out[key] = hit
                used.add(hit)
                break
    if dataset_type == "SENSOR" and not ("parameter" in out and "value" in out):
        # wide layout: one numeric column per measured parameter
        for k in ("parameter", "value"):
            if k in out:
                used.discard(out.pop(k))
        wide = [c for i, c in enumerate(columns) if c not in used and _is_numeric_column(sample_rows, i)]
        if wide:
            out["value_columns"] = wide
    return out
