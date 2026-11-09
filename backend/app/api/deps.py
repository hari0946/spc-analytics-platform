from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.core.permissions import has_permission
from app.core.security import decode_token
from app.schemas.auth import CurrentUser
from app.services import audit_service, auth_service

_bearer = HTTPBearer(auto_error=False)


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def get_current_user(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> CurrentUser:
    if creds is None:
        raise UnauthorizedError("Not authenticated")
    payload = decode_token(creds.credentials)
    user = auth_service.load_session_user(payload["sub"], payload["jti"])
    if user is None:
        raise UnauthorizedError("Session is no longer valid")
    return user


def require_permission(permission: str):
    def checker(request: Request, user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not has_permission(user.permissions, permission):
            audit_service.log("ACCESS_DENIED", actor_user_id=user.user_id, actor_username=user.username,
                              success=False, ip=client_ip(request),
                              details={"permission": permission, "path": request.url.path})
            raise ForbiddenError(f"Missing permission: {permission}")
        return user

    return checker
