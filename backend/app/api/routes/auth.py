from fastapi import APIRouter, Depends, Request, Response

from app.api.deps import client_ip, get_current_user
from app.core.exceptions import TooManyRequestsError, UnauthorizedError
from app.core.ratelimit import login_limiter
from app.schemas.auth import CurrentUser, LoginRequest, RegisterRequest, TokenResponse
from app.schemas.user import UserOut
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=201)
def register(body: RegisterRequest, request: Request):
    return auth_service.register(body, client_ip(request))


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request):
    key = f"{client_ip(request)}|{body.username.lower()}"
    lim = login_limiter()
    wait = lim.retry_after(key)
    if wait:
        raise TooManyRequestsError("Too many failed sign-in attempts; try again later", {"retry_after_seconds": wait},
                                   headers={"Retry-After": str(wait)})
    try:
        token = auth_service.login(body, client_ip(request))
    except UnauthorizedError:
        lim.record_failure(key)
        raise
    lim.reset(key)
    return token


@router.post("/logout", status_code=204)
def logout(request: Request, user: CurrentUser = Depends(get_current_user)):
    auth_service.logout(user, client_ip(request))
    return Response(status_code=204)


@router.get("/me", response_model=CurrentUser)
def me(user: CurrentUser = Depends(get_current_user)):
    return user
