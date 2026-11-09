from app.core import snowflake as sf
from app.core.exceptions import ConflictError, NotFoundError
from app.core.security import hash_password
from app.schemas.common import PageParams
from app.schemas.user import UserCreate, UserOut, UserRolesUpdate, UserUpdate
from app.services import audit_service, role_service

U = "SPC_PLATFORM.METADATA.USERS"
UR = "SPC_PLATFORM.METADATA.USER_ROLES"
R = "SPC_PLATFORM.METADATA.ROLES"
S = "SPC_PLATFORM.METADATA.USER_SESSIONS"
_COLS = "USER_ID, USERNAME, EMAIL, FULL_NAME, IS_ACTIVE, LAST_LOGIN_AT, CREATED_AT"


def _roles_for(user_ids: list[str]) -> dict[str, list[str]]:
    if not user_ids:
        return {}
    ph = ",".join(["%s"] * len(user_ids))
    out: dict[str, list[str]] = {}
    for uid, name in sf.execute(
        f"SELECT ur.USER_ID, r.ROLE_NAME FROM {UR} ur JOIN {R} r ON r.ROLE_ID = ur.ROLE_ID "
        f"WHERE ur.USER_ID IN ({ph}) ORDER BY r.ROLE_NAME", user_ids):
        out.setdefault(uid, []).append(name)
    return out


def _to_out(rows: list[tuple]) -> list[UserOut]:
    roles = _roles_for([r[0] for r in rows])
    return [UserOut(user_id=r[0], username=r[1], email=r[2], full_name=r[3], is_active=r[4],
                    roles=roles.get(r[0], []), last_login_at=r[5], created_at=r[6]) for r in rows]


def list_users(p: PageParams, search: str | None = None) -> tuple[list[UserOut], int]:
    clause, args = "", []
    if search:
        clause = "WHERE LOWER(USERNAME) LIKE %s OR LOWER(EMAIL) LIKE %s"
        like = f"%{search.lower()}%"
        args = [like, like]
    total = sf.execute(f"SELECT COUNT(*) FROM {U} {clause}", args)[0][0]
    rows = sf.execute(f"SELECT {_COLS} FROM {U} {clause} ORDER BY USERNAME LIMIT %s OFFSET %s",
                      [*args, p.page_size, p.offset])
    return _to_out(rows), total


def get_user(user_id: str) -> UserOut:
    rows = sf.execute(f"SELECT {_COLS} FROM {U} WHERE USER_ID = %s", (user_id,))
    if not rows:
        raise NotFoundError(f"User '{user_id}' not found")
    return _to_out(rows)[0]


def _active_admin_ids() -> set[str]:
    return {r[0] for r in sf.execute(
        f"SELECT DISTINCT u.USER_ID FROM {U} u JOIN {UR} ur ON ur.USER_ID = u.USER_ID "
        f"JOIN {R} r ON r.ROLE_ID = ur.ROLE_ID WHERE r.ROLE_NAME = 'ADMIN' AND u.IS_ACTIVE")}


def _guard_last_admin(user_id: str) -> None:
    admins = _active_admin_ids()
    if admins == {user_id}:
        raise ConflictError("This would leave the system without an active administrator")


def _assert_unique(username: str | None, email: str | None, exclude_id: str | None = None) -> None:
    if username and sf.execute(f"SELECT USER_ID FROM {U} WHERE LOWER(USERNAME) = %s AND USER_ID <> %s",
                               (username.lower(), exclude_id or "")):
        raise ConflictError(f"Username '{username}' is already taken")
    if email and sf.execute(f"SELECT USER_ID FROM {U} WHERE LOWER(EMAIL) = %s AND USER_ID <> %s",
                            (email.lower(), exclude_id or "")):
        raise ConflictError("Email is already registered")


def create_user(body: UserCreate, actor=None, ip: str | None = None, action: str = "USER_CREATED") -> UserOut:
    role_ids = role_service.resolve_role_ids(body.roles)
    _assert_unique(body.username, body.email)
    sf.execute(f"INSERT INTO {U} (USERNAME, EMAIL, PASSWORD_HASH, FULL_NAME) VALUES (%s, %s, %s, %s)",
               (body.username, body.email.lower(), hash_password(body.password), body.full_name))
    uid = sf.execute(f"SELECT USER_ID FROM {U} WHERE LOWER(USERNAME) = %s", (body.username.lower(),))[0][0]
    for rid in role_ids.values():
        sf.execute(f"INSERT INTO {UR} (USER_ID, ROLE_ID) VALUES (%s, %s)", (uid, rid))
    audit_service.log(action, actor_user_id=actor.user_id if actor else uid,
                      actor_username=actor.username if actor else body.username, entity_type="USER",
                      entity_id=uid, details={"username": body.username, "roles": sorted(role_ids)}, ip=ip)
    return get_user(uid)


def update_user(user_id: str, body: UserUpdate, actor, ip) -> UserOut:
    current = get_user(user_id)
    if body.email is not None:
        _assert_unique(None, body.email, user_id)
    if body.is_active is False and current.is_active:
        if "ADMIN" in current.roles:
            _guard_last_admin(user_id)
    sets, args = [], []
    if body.email is not None:
        sets.append("EMAIL = %s"); args.append(body.email.lower())
    if body.full_name is not None:
        sets.append("FULL_NAME = %s"); args.append(body.full_name)
    if body.is_active is not None:
        sets.append("IS_ACTIVE = %s"); args.append(body.is_active)
    if body.password is not None:
        sets.append("PASSWORD_HASH = %s"); args.append(hash_password(body.password))
    if sets:
        sets.append("UPDATED_AT = CURRENT_TIMESTAMP()")
        sf.execute(f"UPDATE {U} SET {', '.join(sets)} WHERE USER_ID = %s", [*args, user_id])
        if body.is_active is False or body.password is not None:
            revoke_sessions(user_id)
        changed = body.model_dump(exclude_none=True)
        if "password" in changed:
            changed["password"] = "***changed***"
        audit_service.log("USER_UPDATED", actor_user_id=actor.user_id, actor_username=actor.username,
                          entity_type="USER", entity_id=user_id, details=changed, ip=ip)
    return get_user(user_id)


def set_roles(user_id: str, body: UserRolesUpdate, actor, ip) -> UserOut:
    current = get_user(user_id)
    role_ids = role_service.resolve_role_ids(body.roles)
    if current.is_active and "ADMIN" in current.roles and "ADMIN" not in role_ids:
        _guard_last_admin(user_id)
    sf.execute(f"DELETE FROM {UR} WHERE USER_ID = %s", (user_id,))
    for rid in role_ids.values():
        sf.execute(f"INSERT INTO {UR} (USER_ID, ROLE_ID) VALUES (%s, %s)", (user_id, rid))
    audit_service.log("USER_ROLES_CHANGED", actor_user_id=actor.user_id, actor_username=actor.username,
                      entity_type="USER", entity_id=user_id,
                      details={"before": current.roles, "after": sorted(role_ids)}, ip=ip)
    return get_user(user_id)


def delete_user(user_id: str, actor, ip) -> None:
    current = get_user(user_id)
    if user_id == actor.user_id:
        raise ConflictError("You cannot delete your own account")
    if current.is_active and "ADMIN" in current.roles:
        _guard_last_admin(user_id)
    for t in (S, UR):
        sf.execute(f"DELETE FROM {t} WHERE USER_ID = %s", (user_id,))
    sf.execute(f"DELETE FROM {U} WHERE USER_ID = %s", (user_id,))
    audit_service.log("USER_DELETED", actor_user_id=actor.user_id, actor_username=actor.username,
                      entity_type="USER", entity_id=user_id, details={"username": current.username}, ip=ip)


def revoke_sessions(user_id: str) -> None:
    sf.execute(f"UPDATE {S} SET REVOKED_AT = CURRENT_TIMESTAMP() WHERE USER_ID = %s AND REVOKED_AT IS NULL", (user_id,))
