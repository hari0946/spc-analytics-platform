from fastapi import APIRouter, Depends, Request, Response

from app.api.deps import client_ip, require_permission
from app.core.permissions import PLANTS_MANAGE
from app.schemas import hierarchy as h
from app.schemas.auth import CurrentUser
from app.schemas.common import Page, PageParams
from app.schemas.plant import PlantOut
from app.services import hierarchy_service, plant_service

router = APIRouter(prefix="/plants", tags=["plants"])
Writer = Depends(require_permission(PLANTS_MANAGE))


@router.get("", response_model=Page[PlantOut])
def list_plants(params: PageParams = Depends()):
    items, total = plant_service.list_plants(params)
    return Page[PlantOut](items=items, total=total, page=params.page, page_size=params.page_size)


@router.get("/{plant_id}", response_model=PlantOut)
def get_plant(plant_id: str):
    return plant_service.get_plant(plant_id)


@router.post("", response_model=PlantOut, status_code=201)
def create_plant(body: h.PlantCreate, request: Request, actor: CurrentUser = Writer):
    return hierarchy_service.create("plants", {k.upper(): v for k, v in body.model_dump().items()}, actor, client_ip(request))


@router.patch("/{plant_id}", response_model=PlantOut)
def update_plant(plant_id: str, body: h.PlantUpdate, request: Request, actor: CurrentUser = Writer):
    return hierarchy_service.update("plants", plant_id, {k.upper(): v for k, v in body.model_dump(exclude_unset=True).items()}, actor, client_ip(request))


@router.delete("/{plant_id}", status_code=204)
def delete_plant(plant_id: str, request: Request, actor: CurrentUser = Writer):
    hierarchy_service.delete("plants", plant_id, actor, client_ip(request))
    return Response(status_code=204)
