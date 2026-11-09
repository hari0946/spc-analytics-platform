"""Plant > Production line > Asset > Tag management (Phase 19). One config-driven CRUD implementation.

Rules: codes are unique within their parent (Snowflake does not enforce UNIQUE, so it is checked here); a parent must
exist and a record cannot be deleted while it still has children; tag limits are validated (LSL < USL, target inside).
Every change is audited."""
from app.core import snowflake as sf
from app.core.exceptions import ConflictError, NotFoundError, UnprocessableError
from app.schemas.common import PageParams
from app.services import audit_service

M = "SPC_PLATFORM.METADATA"

ENTITIES = {
    "plants": dict(table="PLANTS", id="PLANT_ID", parent=None, name="PLANT_CODE", fields=["PLANT_CODE", "PLANT_NAME", "LOCATION", "IS_ACTIVE"],
                   label="Plant", child=("PRODUCTION_LINES", "PLANT_ID")),
    "lines": dict(table="PRODUCTION_LINES", id="LINE_ID", parent=("PLANTS", "PLANT_ID"), name="LINE_CODE",
                  fields=["PLANT_ID", "LINE_CODE", "LINE_NAME", "IS_ACTIVE"], label="Production line", child=("ASSETS", "LINE_ID")),
    "assets": dict(table="ASSETS", id="ASSET_ID", parent=("PRODUCTION_LINES", "LINE_ID"), name="ASSET_CODE",
                   fields=["LINE_ID", "ASSET_CODE", "ASSET_NAME", "ASSET_TYPE", "IS_ACTIVE"], label="Asset", child=("TAGS", "ASSET_ID")),
    "tags": dict(table="TAGS", id="TAG_ID", parent=("ASSETS", "ASSET_ID"), name="TAG_NAME",
                 fields=["ASSET_ID", "TAG_NAME", "PARAMETER", "UNIT", "LSL", "USL", "TARGET", "IS_ACTIVE"], label="Tag", child=None),
}


def _cfg(entity: str) -> dict:
    return ENTITIES[entity]


def _row(cfg: dict, r: tuple, cols: list[str]) -> dict:
    return {c.lower(): v for c, v in zip(cols, r)}


def _cols(cfg: dict) -> list[str]:
    return [cfg["id"], *cfg["fields"], "CREATED_AT", "UPDATED_AT"]


def get(entity: str, ident: str) -> dict:
    cfg = _cfg(entity)
    cols = _cols(cfg)
    rows = sf.execute(f"SELECT {', '.join(cols)} FROM {M}.{cfg['table']} WHERE {cfg['id']} = %s", (ident,))
    if not rows:
        raise NotFoundError(f"{cfg['label']} '{ident}' not found")
    return _row(cfg, rows[0], cols)


def list_(entity: str, p: PageParams, parent_id: str | None = None, search: str | None = None,
          active_only: bool = False) -> tuple[list[dict], int]:
    cfg = _cfg(entity)
    cols = _cols(cfg)
    where, args = [], []
    if parent_id and cfg["parent"]:
        where.append(f"{cfg['parent'][1]} = %s"); args.append(parent_id)
    if search:
        where.append(f"LOWER({cfg['name']}) LIKE %s"); args.append(f"%{search.lower()}%")
    if active_only:
        where.append("IS_ACTIVE")
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    total = sf.execute(f"SELECT COUNT(*) FROM {M}.{cfg['table']} {clause}", args)[0][0]
    rows = sf.execute(f"SELECT {', '.join(cols)} FROM {M}.{cfg['table']} {clause} ORDER BY {cfg['name']}, {cfg['id']} LIMIT %s OFFSET %s",
                      [*args, p.page_size, p.offset])
    return [_row(cfg, r, cols) for r in rows], total


def _check_limits(lsl, usl, target) -> None:
    if lsl is not None and usl is not None and lsl >= usl:
        raise UnprocessableError(f"LSL ({lsl}) must be less than USL ({usl})")
    if target is not None and ((lsl is not None and target < lsl) or (usl is not None and target > usl)):
        raise UnprocessableError("Target must lie within the specification limits")


def _unique(cfg: dict, data: dict, exclude_id: str | None = None) -> None:
    name = cfg["name"]
    where, args = [f"LOWER({name}) = %s"], [str(data[name]).lower()]
    if cfg["parent"]:
        where.append(f"{cfg['parent'][1]} = %s"); args.append(data[cfg["parent"][1]])
    if exclude_id:
        where.append(f"{cfg['id']} <> %s"); args.append(exclude_id)
    if sf.execute(f"SELECT 1 FROM {M}.{cfg['table']} WHERE {' AND '.join(where)} LIMIT 1", args):
        raise ConflictError(f"{cfg['label']} '{data[name]}' already exists" + (" in this parent" if cfg["parent"] else ""))


def _audit(action, actor, ip, entity, ident, details):
    audit_service.log(action, actor_user_id=actor.user_id, actor_username=actor.username, entity_type=entity.upper().rstrip("S"),
                      entity_id=ident, ip=ip, details=details)


def create(entity: str, data: dict, actor, ip) -> dict:
    cfg = _cfg(entity)
    if cfg["parent"]:
        ptable, pcol = cfg["parent"]
        if not sf.execute(f"SELECT 1 FROM {M}.{ptable} WHERE {pcol} = %s", (data[pcol],)):
            raise UnprocessableError(f"Parent not found ({pcol.lower()} '{data[pcol]}')")
    if entity == "tags":
        _check_limits(data.get("LSL"), data.get("USL"), data.get("TARGET"))
    _unique(cfg, data)
    fields = [f for f in cfg["fields"] if f in data]
    sf.execute(f"INSERT INTO {M}.{cfg['table']} ({', '.join(fields)}) VALUES ({', '.join(['%s'] * len(fields))})",
               [data[f] for f in fields])
    pcol_filter = ""
    args = [str(data[cfg["name"]]).lower()]
    if cfg["parent"]:
        pcol_filter = f" AND {cfg['parent'][1]} = %s"
        args.append(data[cfg["parent"][1]])
    ident = sf.execute(f"SELECT {cfg['id']} FROM {M}.{cfg['table']} WHERE LOWER({cfg['name']}) = %s{pcol_filter}", args)[0][0]
    _audit(f"{entity.upper().rstrip('S')}_CREATED", actor, ip, entity, ident, {k.lower(): v for k, v in data.items()})
    return get(entity, ident)


def update(entity: str, ident: str, data: dict, actor, ip) -> dict:
    cfg = _cfg(entity)
    current = get(entity, ident)
    if not data:
        return current
    merged = {k.lower(): v for k, v in current.items()}
    merged.update({k.lower(): v for k, v in data.items()})
    if entity == "tags":
        _check_limits(merged["lsl"], merged["usl"], merged["target"])
    if cfg["name"] in data:
        probe = {cfg["name"]: data[cfg["name"]]}
        if cfg["parent"]:
            probe[cfg["parent"][1]] = current[cfg["parent"][1].lower()]
        _unique(cfg, probe, exclude_id=ident)
    sets = ", ".join(f"{k} = %s" for k in data) + ", UPDATED_AT = CURRENT_TIMESTAMP()"
    sf.execute(f"UPDATE {M}.{cfg['table']} SET {sets} WHERE {cfg['id']} = %s", [*data.values(), ident])
    _audit(f"{entity.upper().rstrip('S')}_UPDATED", actor, ip, entity, ident, {k.lower(): v for k, v in data.items()})
    return get(entity, ident)


def delete(entity: str, ident: str, actor, ip) -> None:
    cfg = _cfg(entity)
    current = get(entity, ident)
    if cfg["child"]:
        ctable, ccol = cfg["child"]
        n = sf.execute(f"SELECT COUNT(*) FROM {M}.{ctable} WHERE {ccol} = %s", (ident,))[0][0]
        if n:
            raise ConflictError(f"{cfg['label']} still has {n} child record(s); delete or move them first")
    sf.execute(f"DELETE FROM {M}.{cfg['table']} WHERE {cfg['id']} = %s", (ident,))
    _audit(f"{entity.upper().rstrip('S')}_DELETED", actor, ip, entity, ident, {cfg["name"].lower(): current[cfg["name"].lower()]})
