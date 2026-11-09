from fastapi import APIRouter, Depends, Request, Response

from app.api.deps import client_ip, require_permission
from app.core.permissions import ALL_PERMISSIONS, ROLES_MANAGE
from app.schemas.auth import CurrentUser
from app.schemas.common import Page, PageParams
from app.schemas.role import RoleCreate, RoleOut, RoleUpdate
from app.services import role_service

router = APIRouter(prefix="/roles", tags=["roles"])
Admin = Depends(require_permission(ROLES_MANAGE))


@router.get("", response_model=Page[RoleOut])
def list_roles(params: PageParams = Depends(), _: CurrentUser = Admin):
    items, total = role_service.list_roles(params)
    return Page[RoleOut](items=items, total=total, page=params.page, page_size=params.page_size)


@router.get("/permissions", response_model=list[str])
def list_permissions(_: CurrentUser = Admin):
    return sorted(ALL_PERMISSIONS)


@router.post("", response_model=RoleOut, status_code=201)
def create_role(body: RoleCreate, request: Request, actor: CurrentUser = Admin):
    return role_service.create_role(body, actor, client_ip(request))


@router.get("/{role_id}", response_model=RoleOut)
def get_role(role_id: str, _: CurrentUser = Admin):
    return role_service.get_role(role_id)


@router.patch("/{role_id}", response_model=RoleOut)
def update_role(role_id: str, body: RoleUpdate, request: Request, actor: CurrentUser = Admin):
    return role_service.update_role(role_id, body, actor, client_ip(request))


@router.delete("/{role_id}", status_code=204)
def delete_role(role_id: str, request: Request, actor: CurrentUser = Admin):
    role_service.delete_role(role_id, actor, client_ip(request))
    return Response(status_code=204)
