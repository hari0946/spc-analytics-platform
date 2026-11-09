import json

from app.core import snowflake as sf
from app.core.exceptions import UnauthorizedError
from app.core.permissions import DEFAULT_REGISTRATION_ROLE
from app.core.security import DUMMY_HASH, create_access_token, verify_password
from app.schemas.auth import CurrentUser, LoginRequest, RegisterRequest, TokenResponse
from app.schemas.user import UserCreate, UserOut
from app.services import audit_service, user_service

U = "SPC_PLATFORM.METADATA.USERS"
S = "SPC_PLATFORM.METADATA.USER_SESSIONS"
UR = "SPC_PLATFORM.METADATA.USER_ROLES"
R = "SPC_PLATFORM.METADATA.ROLES"

_INVALID = "Invalid username or password"


def register(body: RegisterRequest, ip: str | None) -> UserOut:
    """Self-registration always yields the least-privileged role; admins grant more."""
    return user_service.create_user(
        UserCreate(username=body.username, email=body.email, password=body.password,
                   full_name=body.full_name, roles=[DEFAULT_REGISTRATION_ROLE]),
        actor=None, ip=ip, action="USER_REGISTERED")


def login(body: LoginRequest, ip: str | None) -> TokenResponse:
    rows = sf.execute(f"SELECT USER_ID, USERNAME, PASSWORD_HASH, IS_ACTIVE FROM {U} WHERE LOWER(USERNAME) = %s",
                      (body.username.lower(),))
    user = rows[0] if rows else None
    # always run a bcrypt verification so timing doesn't reveal whether the username exists
    ok = verify_password(body.password, user[2] if user else DUMMY_HASH)
    if not user or not ok:
        audit_service.log("LOGIN_FAILED", actor_username=body.username[:100], success=False, ip=ip,
                          details={"reason": "invalid_credentials"})
        raise UnauthorizedError(_INVALID)
    if not user[3]:
        audit_service.log("LOGIN_FAILED", actor_user_id=user[0], actor_username=user[1], success=False, ip=ip,
                          details={"reason": "account_disabled"})
        raise UnauthorizedError(_INVALID)
    token, jti, expires_at = create_access_token(user[0])
    sf.execute(f"INSERT INTO {S} (SESSION_ID, USER_ID, EXPIRES_AT, IP_ADDRESS) VALUES (%s, %s, %s, %s)",
               (jti, user[0], expires_at, ip))
    sf.execute(f"UPDATE {U} SET LAST_LOGIN_AT = CURRENT_TIMESTAMP() WHERE USER_ID = %s", (user[0],))
    audit_service.log("LOGIN", actor_user_id=user[0], actor_username=user[1], entity_type="USER",
                      entity_id=user[0], ip=ip)
    return TokenResponse(access_token=token, expires_at=expires_at)


def logout(user: CurrentUser, ip: str | None) -> None:
    sf.execute(f"UPDATE {S} SET REVOKED_AT = CURRENT_TIMESTAMP() WHERE SESSION_ID = %s AND REVOKED_AT IS NULL",
               (user.session_id,))
    audit_service.log("LOGOUT", actor_user_id=user.user_id, actor_username=user.username, ip=ip)


def load_session_user(user_id: str, session_id: str) -> CurrentUser | None:
    """Returns the user only if the session is live (not revoked/expired) and the account is active."""
    rows = sf.execute(
        f"SELECT u.USER_ID, u.USERNAME, u.EMAIL, u.FULL_NAME FROM {S} s JOIN {U} u ON u.USER_ID = s.USER_ID "
        "WHERE s.SESSION_ID = %s AND s.USER_ID = %s AND s.REVOKED_AT IS NULL "
        "AND s.EXPIRES_AT > CURRENT_TIMESTAMP() AND u.IS_ACTIVE", (session_id, user_id))
    if not rows:
        return None
    u = rows[0]
    roles, perms = [], set()
    for name, p in sf.execute(
            f"SELECT r.ROLE_NAME, r.PERMISSIONS FROM {UR} ur JOIN {R} r ON r.ROLE_ID = ur.ROLE_ID "
            "WHERE ur.USER_ID = %s ORDER BY r.ROLE_NAME", (user_id,)):
        roles.append(name)
        perms.update(json.loads(p) if p else [])
    return CurrentUser(user_id=u[0], username=u[1], email=u[2], full_name=u[3], roles=roles,
                       permissions=sorted(perms), session_id=session_id)
