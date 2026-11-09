import json

from app.core import snowflake as sf
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.permissions import BUILTIN_ROLES
from app.schemas.common import PageParams
from app.schemas.role import RoleCreate, RoleOut, RoleUpdate
from app.services import audit_service

R = "SPC_PLATFORM.METADATA.ROLES"
UR = "SPC_PLATFORM.METADATA.USER_ROLES"
_COLS = "ROLE_ID, ROLE_NAME, DESCRIPTION, PERMISSIONS, CREATED_AT"


def _out(r: tuple) -> RoleOut:
    return RoleOut(role_id=r[0], role_name=r[1], description=r[2], permissions=json.loads(r[3]) if r[3] else [],
                   is_builtin=r[1] in BUILTIN_ROLES, created_at=r[4])


def list_roles(p: PageParams) -> tuple[list[RoleOut], int]:
    total = sf.execute(f"SELECT COUNT(*) FROM {R}")[0][0]
    rows = sf.execute(f"SELECT {_COLS} FROM {R} ORDER BY ROLE_NAME LIMIT %s OFFSET %s", (p.page_size, p.offset))
    return [_out(r) for r in rows], total


def get_role(role_id: str) -> RoleOut:
    rows = sf.execute(f"SELECT {_COLS} FROM {R} WHERE ROLE_ID = %s", (role_id,))
    if not rows:
        raise NotFoundError(f"Role '{role_id}' not found")
    return _out(rows[0])


def resolve_role_ids(names: list[str]) -> dict[str, str]:
    """Map role names to ids; raises BadRequestError listing unknown names."""
    wanted = sorted({n.upper() for n in names})
    ph = ",".join(["%s"] * len(wanted))
    found = {r[0]: r[1] for r in sf.execute(f"SELECT ROLE_NAME, ROLE_ID FROM {R} WHERE ROLE_NAME IN ({ph})", wanted)}
    missing = [n for n in wanted if n not in found]
    if missing:
        raise BadRequestError(f"Unknown role(s): {', '.join(missing)}")
    return found


def create_role(body: RoleCreate, actor, ip) -> RoleOut:
    if sf.execute(f"SELECT 1 FROM {R} WHERE ROLE_NAME = %s", (body.role_name,)):
        raise ConflictError(f"Role '{body.role_name}' already exists")
    sf.execute(f"INSERT INTO {R} (ROLE_NAME, DESCRIPTION, PERMISSIONS) SELECT %s, %s, PARSE_JSON(%s)",
               (body.role_name, body.description, json.dumps(body.permissions)))
    role = _out(sf.execute(f"SELECT {_COLS} FROM {R} WHERE ROLE_NAME = %s", (body.role_name,))[0])
    audit_service.log("ROLE_CREATED", actor_user_id=actor.user_id, actor_username=actor.username,
                      entity_type="ROLE", entity_id=role.role_id, details={"role_name": role.role_name,
                      "permissions": role.permissions}, ip=ip)
    return role


def update_role(role_id: str, body: RoleUpdate, actor, ip) -> RoleOut:
    role = get_role(role_id)
    if role.role_name == "ADMIN":
        raise ConflictError("The built-in ADMIN role cannot be modified")
    sets, args = [], []
    if body.description is not None:
        sets.append("DESCRIPTION = %s"); args.append(body.description)
    if body.permissions is not None:
        sets.append("PERMISSIONS = PARSE_JSON(%s)"); args.append(json.dumps(body.permissions))
    if sets:
        sf.execute(f"UPDATE {R} SET {', '.join(sets)} WHERE ROLE_ID = %s", [*args, role_id])
        audit_service.log("ROLE_UPDATED", actor_user_id=actor.user_id, actor_username=actor.username,
                          entity_type="ROLE", entity_id=role_id,
                          details=body.model_dump(exclude_none=True), ip=ip)
    return get_role(role_id)


def delete_role(role_id: str, actor, ip) -> None:
    role = get_role(role_id)
    if role.role_name in BUILTIN_ROLES:
        raise ConflictError(f"Built-in role '{role.role_name}' cannot be deleted")
    if sf.execute(f"SELECT COUNT(*) FROM {UR} WHERE ROLE_ID = %s", (role_id,))[0][0]:
        raise ConflictError("Role is assigned to users; unassign it first")
    sf.execute(f"DELETE FROM {R} WHERE ROLE_ID = %s", (role_id,))
    audit_service.log("ROLE_DELETED", actor_user_id=actor.user_id, actor_username=actor.username,
                      entity_type="ROLE", entity_id=role_id, details={"role_name": role.role_name}, ip=ip)
