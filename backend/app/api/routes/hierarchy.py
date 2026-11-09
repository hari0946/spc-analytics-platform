"""Plant / line / asset / tag management. Reads need data:read; writes need plants:manage, assets:manage or tags:manage."""
from fastapi import APIRouter, Depends, Query, Request, Response

from app.api.deps import client_ip, require_permission
from app.core.permissions import ASSETS_MANAGE, DATA_READ, PLANTS_MANAGE, TAGS_MANAGE
from app.schemas import hierarchy as h
from app.schemas.auth import CurrentUser
from app.schemas.common import Page, PageParams
from app.services import hierarchy_service as svc

router = APIRouter(tags=["hierarchy"])


def _upper(model, exclude_unset=True) -> dict:
    return {k.upper(): v for k, v in model.model_dump(exclude_unset=exclude_unset).items()}


def make(entity: str, prefix: str, perm: str, Create, Update, parent_param: str | None):
    Reader = Depends(require_permission(DATA_READ))
    Writer = Depends(require_permission(perm))

    @router.get(f"/{prefix}", response_model=Page[dict], name=f"list_{entity}")
    def list_(params: PageParams = Depends(), search: str | None = Query(None, max_length=100),
              parent_id: str | None = Query(None, alias=parent_param or "parent_id", max_length=64),
              active_only: bool = False, _: CurrentUser = Reader):
        items, total = svc.list_(entity, params, parent_id, search, active_only)
        return Page[dict](items=items, total=total, page=params.page, page_size=params.page_size)

    @router.get(f"/{prefix}/{{ident}}", response_model=dict, name=f"get_{entity}")
    def get_(ident: str, _: CurrentUser = Reader):
        return svc.get(entity, ident)

    @router.post(f"/{prefix}", response_model=dict, status_code=201, name=f"create_{entity}")
    def create_(body: Create, request: Request, actor: CurrentUser = Writer):
        return svc.create(entity, _upper(body), actor, client_ip(request))

    @router.patch(f"/{prefix}/{{ident}}", response_model=dict, name=f"update_{entity}")
    def update_(ident: str, body: Update, request: Request, actor: CurrentUser = Writer):
        return svc.update(entity, ident, _upper(body), actor, client_ip(request))

    @router.delete(f"/{prefix}/{{ident}}", status_code=204, name=f"delete_{entity}")
    def delete_(ident: str, request: Request, actor: CurrentUser = Writer):
        svc.delete(entity, ident, actor, client_ip(request))
        return Response(status_code=204)


make("lines", "lines", PLANTS_MANAGE, h.LineCreate, h.LineUpdate, "plant_id")
make("assets", "assets", ASSETS_MANAGE, h.AssetCreate, h.AssetUpdate, "line_id")
make("tags", "tags", TAGS_MANAGE, h.TagCreate, h.TagUpdate, "asset_id")
