"""Upload workflow:  save -> validate/preprocess -> PUT to internal stage (STAGED) -> column mapping (MAPPED)
-> COPY INTO BRONZE (LOADING -> LOADED_BRONZE | FAILED).  Silver/Gold processing is Phase 7."""
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import UploadFile
from pydantic import ValidationError

from app.core import snowflake as sf
from app.core.config import get_settings
from app.core.exceptions import (ConflictError, LoadFailedError, NotFoundError, PayloadTooLargeError,
                                 UnprocessableError, UnsupportedMediaError)
from app.schemas.common import PageParams
from app.schemas.upload import MAPPING_MODELS, PreviewOut, UploadDetail, UploadSummary
from app.services import audit_service, processing_service
from app.services.file_parser import FileValidationError, preprocess, suggest_mapping

logger = logging.getLogger(__name__)

DB = "SPC_PLATFORM"
U = f"{DB}.METADATA.DATASET_UPLOADS"
STAGE = f"@{DB}.BRONZE.UPLOAD_STAGE"
BRONZE = {"SENSOR": f"{DB}.BRONZE.RAW_SENSOR_DATA", "PRODUCTION": f"{DB}.BRONZE.RAW_PRODUCTION_DATA"}
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_COLS = ("UPLOAD_ID, FILE_NAME, FILE_TYPE, DATASET_TYPE, FILE_SIZE_BYTES, STATUS, ROWS_RECEIVED, ROWS_LOADED, "
         "ROWS_REJECTED, DUPLICATE_ROWS, ERROR_MESSAGE, UPLOADED_BY, UPLOADED_AT")
# statuses after which the stored file/bronze data exist and re-staging is pointless
_ACTIVE = ("STAGED", "MAPPED", "LOADING", "LOADED_BRONZE", "PROCESSING", "COMPLETED", "PROCESS_FAILED")


def _safe_uuid(upload_id: str) -> str:
    if not _UUID.match(upload_id):
        raise NotFoundError(f"Upload '{upload_id}' not found")  # also keeps non-UUID text out of SQL identifiers
    return upload_id


def _summary(r: tuple) -> UploadSummary:
    return UploadSummary(upload_id=r[0], file_name=r[1], file_type=r[2], dataset_type=r[3], file_size_bytes=r[4],
                         status=r[5], rows_received=r[6], rows_loaded=r[7], empty_rows_skipped=r[8],
                         duplicate_rows=r[9], error_message=r[10], uploaded_by=r[11], uploaded_at=r[12])


def _clean_filename(name: str | None) -> str:
    base = os.path.basename((name or "upload").replace("\\", "/"))
    base = re.sub(r"[\x00-\x1f]", "", base).strip()[:255]
    return base or "upload"


def _save(file: UploadFile, dest: Path) -> tuple[int, str]:
    limit = get_settings().max_upload_mb * 1024 * 1024
    h, size = hashlib.sha256(), 0
    with dest.open("wb") as out:
        while chunk := file.file.read(1 << 20):
            size += len(chunk)
            if size > limit:
                raise PayloadTooLargeError(f"File exceeds the {get_settings().max_upload_mb} MB limit")
            h.update(chunk)
            out.write(chunk)
    return size, h.hexdigest()


def _set_status(upload_id: str, status: str, error: str | None = None) -> None:
    sf.execute(f"UPDATE {U} SET STATUS = %s, ERROR_MESSAGE = %s WHERE UPLOAD_ID = %s", (status, error, upload_id))


def _remove_staged(upload_id: str) -> None:
    try:
        sf.execute(f"REMOVE {STAGE}/{_safe_uuid(upload_id)}/")
    except Exception:  # noqa: BLE001 - best effort
        logger.warning("Could not remove staged files for %s", upload_id, exc_info=True)


# ------------------------------------------------------------------ create (validate + stage)
def create_upload(file: UploadFile, dataset_type: str, allow_duplicate: bool, sheet: str | None,
                  actor, ip) -> UploadDetail:
    s = get_settings()
    filename = _clean_filename(file.filename)
    if Path(filename).suffix.lower() not in (".csv", ".xlsx", ".xls"):
        raise UnsupportedMediaError("Unsupported file type; upload a .csv, .xlsx or .xls file")
    tmp = Path(tempfile.mkdtemp(prefix="spc_upload_"))
    upload_id = str(uuid.uuid4())
    row_inserted = False
    try:
        src = tmp / "source.bin"
        size, sha = _save(file, src)
        if not allow_duplicate:
            dup = sf.execute(f"SELECT UPLOAD_ID, FILE_NAME FROM {U} WHERE FILE_HASH = %s AND DATASET_TYPE = %s "
                             f"AND STATUS IN ({','.join(['%s'] * len(_ACTIVE))}) LIMIT 1", (sha, dataset_type, *_ACTIVE))
            if dup:
                raise ConflictError(f"This exact file was already uploaded as '{dup[0][1]}'",
                                    {"existing_upload_id": dup[0][0], "hint": "Set allow_duplicate=true to upload it again"})
        parts_dir = tmp / "parts"
        parts_dir.mkdir()
        try:
            res = preprocess(src, filename, parts_dir, part_rows=s.upload_part_rows, max_rows=s.max_upload_rows,
                             max_cols=s.max_upload_columns, sheet=sheet)
        except FileValidationError as e:
            audit_service.log("UPLOAD_REJECTED", actor_user_id=actor.user_id, actor_username=actor.username,
                              success=False, ip=ip, details={"file_name": filename, "reason": str(e)})
            raise UnprocessableError(str(e))

        suggested = suggest_mapping(res.columns, res.sample_rows, dataset_type)
        preview = {"columns": res.columns, "rows": res.preview_rows, "suggested_mapping": suggested,
                   "warnings": res.warnings}
        sf.execute(
            f"INSERT INTO {U} (UPLOAD_ID, FILE_NAME, FILE_TYPE, DATASET_TYPE, FILE_SIZE_BYTES, STAGE_PATH, STATUS, "
            "ROWS_RECEIVED, ROWS_REJECTED, DUPLICATE_ROWS, FILE_HASH, PREVIEW, UPLOADED_BY) "
            "SELECT %s, %s, %s, %s, %s, %s, 'STAGING', %s, %s, %s, %s, PARSE_JSON(%s), %s",
            (upload_id, filename, res.kind.upper(), dataset_type, size, f"{STAGE}/{upload_id}/", res.rows_received,
             res.empty_rows, res.duplicate_rows, sha, json.dumps(preview), actor.user_id))
        row_inserted = True
        try:
            for part in res.parts:
                uri = part.resolve().as_posix()
                if "'" in uri:
                    raise RuntimeError("unsafe temp path")
                sf.execute(f"PUT 'file://{uri}' {STAGE}/{upload_id}/ AUTO_COMPRESS=FALSE OVERWRITE=TRUE")
        except Exception as e:  # noqa: BLE001
            _remove_staged(upload_id)
            _set_status(upload_id, "FAILED", f"Staging failed: {e}"[:500])
            raise LoadFailedError("Could not stage the file in Snowflake")
        _set_status(upload_id, "STAGED")
        audit_service.log("UPLOAD_CREATED", actor_user_id=actor.user_id, actor_username=actor.username,
                          entity_type="UPLOAD", entity_id=upload_id, ip=ip,
                          details={"file_name": filename, "dataset_type": dataset_type, "rows": res.rows_received})
        return get_upload(upload_id)
    except Exception:
        if row_inserted:
            try:
                row = sf.execute(f"SELECT STATUS FROM {U} WHERE UPLOAD_ID = %s", (upload_id,))
                if row and row[0][0] == "STAGING":
                    _set_status(upload_id, "FAILED", "Upload aborted unexpectedly")
            except Exception:  # noqa: BLE001
                pass
        raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ read
def get_upload(upload_id: str) -> UploadDetail:
    _safe_uuid(upload_id)
    rows = sf.execute(f"SELECT {_COLS}, COLUMN_MAPPING, PREVIEW FROM {U} WHERE UPLOAD_ID = %s", (upload_id,))
    if not rows:
        raise NotFoundError(f"Upload '{upload_id}' not found")
    r = rows[0]
    prev = json.loads(r[14]) if r[14] else None
    return UploadDetail(**_summary(r[:13]).model_dump(),
                        column_mapping=json.loads(r[13]) if r[13] else None,
                        preview=PreviewOut(**prev) if prev else None)


def list_uploads(p: PageParams, status: str | None, dataset_type: str | None) -> tuple[list[UploadSummary], int]:
    where, args = [], []
    if status:
        where.append("STATUS = %s"); args.append(status.upper())
    if dataset_type:
        where.append("DATASET_TYPE = %s"); args.append(dataset_type.upper())
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    total = sf.execute(f"SELECT COUNT(*) FROM {U} {clause}", args)[0][0]
    rows = sf.execute(f"SELECT {_COLS} FROM {U} {clause} ORDER BY UPLOADED_AT DESC LIMIT %s OFFSET %s",
                      [*args, p.page_size, p.offset])
    return [_summary(r) for r in rows], total


# ------------------------------------------------------------------ mapping
def set_mapping(upload_id: str, mapping: dict, actor, ip) -> UploadDetail:
    up = get_upload(upload_id)
    if up.status not in ("STAGED", "MAPPED"):
        raise ConflictError(f"Mapping can only be set while the upload is STAGED or MAPPED (current: {up.status})")
    try:
        parsed = MAPPING_MODELS[up.dataset_type](**mapping)
    except ValidationError as e:
        raise UnprocessableError("Invalid column mapping", [
            {"loc": [str(x) for x in err["loc"]], "message": err["msg"].removeprefix("Value error, ")}
            for err in e.errors()])
    available = set(up.preview.columns)
    missing = sorted({c for c in parsed.mapped_columns() if c not in available})
    if missing:
        raise UnprocessableError("Mapped columns not found in the file", {"missing_columns": missing,
                                                                             "available_columns": up.preview.columns})
    sf.execute(f"UPDATE {U} SET COLUMN_MAPPING = PARSE_JSON(%s), STATUS = 'MAPPED', ERROR_MESSAGE = NULL "
               "WHERE UPLOAD_ID = %s", (json.dumps(parsed.model_dump(exclude_none=True)), upload_id))
    audit_service.log("UPLOAD_MAPPED", actor_user_id=actor.user_id, actor_username=actor.username,
                      entity_type="UPLOAD", entity_id=upload_id, ip=ip,
                      details=parsed.model_dump(exclude_none=True))
    return get_upload(upload_id)


# ------------------------------------------------------------------ load into BRONZE
def load_to_bronze(upload_id: str, actor, ip) -> UploadDetail:
    up = get_upload(upload_id)
    if up.status == "LOADED_BRONZE":
        raise ConflictError("This upload is already loaded into BRONZE")
    if up.status == "LOADING":
        raise ConflictError("This upload is currently loading")
    if up.status != "MAPPED":
        raise ConflictError(f"Set a valid column mapping before loading (current status: {up.status})")
    claimed = sf.execute(f"UPDATE {U} SET STATUS = 'LOADING' WHERE UPLOAD_ID = %s AND STATUS = 'MAPPED'", (upload_id,))
    if not claimed or claimed[0][0] != 1:
        raise ConflictError("This upload is already being loaded")
    table = BRONZE[up.dataset_type]
    try:
        # idempotent: clear any partial rows from an earlier failed attempt
        sf.execute(f"DELETE FROM {table} WHERE UPLOAD_ID = %s", (upload_id,))
        result = sf.execute(
            f"COPY INTO {table} (UPLOAD_ID, SOURCE_ROW, RAW_RECORD) "
            f"FROM (SELECT '{upload_id}', $1:r::NUMBER, $1:d FROM {STAGE}/{upload_id}/) "
            "FILE_FORMAT = (TYPE = 'JSON') ON_ERROR = 'ABORT_STATEMENT' PURGE = TRUE")
        loaded = sf.execute(f"SELECT COUNT(*) FROM {table} WHERE UPLOAD_ID = %s", (upload_id,))[0][0]
        if loaded != up.rows_received:
            raise RuntimeError(f"Loaded {loaded} rows but the file contained {up.rows_received}")
    except Exception as e:  # noqa: BLE001
        logger.error("BRONZE load failed for %s: %s", upload_id, e)
        try:
            sf.execute(f"DELETE FROM {table} WHERE UPLOAD_ID = %s", (upload_id,))
        finally:
            _set_status(upload_id, "FAILED", f"BRONZE load failed: {e}"[:500])
        audit_service.log("UPLOAD_FAILED", actor_user_id=actor.user_id, actor_username=actor.username,
                          entity_type="UPLOAD", entity_id=upload_id, success=False, ip=ip, details={"error": str(e)[:300]})
        raise LoadFailedError("Loading into BRONZE failed; the upload was rolled back", {"upload_id": upload_id})
    sf.execute(f"UPDATE {U} SET STATUS = 'LOADED_BRONZE', ROWS_LOADED = %s, ERROR_MESSAGE = NULL WHERE UPLOAD_ID = %s",
               (loaded, upload_id))
    audit_service.log("UPLOAD_LOADED", actor_user_id=actor.user_id, actor_username=actor.username,
                      entity_type="UPLOAD", entity_id=upload_id, ip=ip, details={"rows_loaded": loaded, "table": table})
    return get_upload(upload_id)


# ------------------------------------------------------------------ delete
def delete_upload(upload_id: str, actor, ip) -> None:
    up = get_upload(upload_id)
    if up.status in ("LOADING", "PROCESSING"):
        raise ConflictError(f"The upload is currently {up.status.lower()}")
    processing_service.clear_derived(upload_id)
    for table in BRONZE.values():
        sf.execute(f"DELETE FROM {table} WHERE UPLOAD_ID = %s", (upload_id,))
    _remove_staged(upload_id)
    sf.execute(f"DELETE FROM {U} WHERE UPLOAD_ID = %s", (upload_id,))
    audit_service.log("UPLOAD_DELETED", actor_user_id=actor.user_id, actor_username=actor.username,
                      entity_type="UPLOAD", entity_id=upload_id, ip=ip, details={"file_name": up.file_name})
