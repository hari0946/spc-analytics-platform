from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.deps import client_ip, require_permission
from app.core.permissions import USERS_MANAGE
from app.schemas.auth import CurrentUser
from app.schemas.common import Page, PageParams
from app.schemas.user import UserCreate, UserOut, UserRolesUpdate, UserUpdate
from app.services import user_service

router = APIRouter(prefix="/users", tags=["users"])
Admin = Depends(require_permission(USERS_MANAGE))


@router.get("", response_model=Page[UserOut])
def list_users(params: PageParams = Depends(), search: str | None = Query(None, max_length=100),
               _: CurrentUser = Admin):
    items, total = user_service.list_users(params, search)
    return Page[UserOut](items=items, total=total, page=params.page, page_size=params.page_size)


@router.post("", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, request: Request, actor: CurrentUser = Admin):
    return user_service.create_user(body, actor, client_ip(request))


@router.get("/{user_id}", response_model=UserOut)
def get_user(user_id: str, _: CurrentUser = Admin):
    return user_service.get_user(user_id)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: str, body: UserUpdate, request: Request, actor: CurrentUser = Admin):
    return user_service.update_user(user_id, body, actor, client_ip(request))


@router.put("/{user_id}/roles", response_model=UserOut)
def set_roles(user_id: str, body: UserRolesUpdate, request: Request, actor: CurrentUser = Admin):
    return user_service.set_roles(user_id, body, actor, client_ip(request))


@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: str, request: Request, actor: CurrentUser = Admin):
    user_service.delete_user(user_id, actor, client_ip(request))
    return Response(status_code=204)
