"""Historical data selection. Common query filters (repeat a parameter to give several values):
upload_id, plant, line, asset, tag, parameter, batch, start, end."""
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import Field

from app.api.deps import require_permission
from app.core.permissions import DATA_READ
from app.schemas.auth import CurrentUser
from app.schemas.common import Page, PageParams
from app.schemas.data import (FilterOptions, ProductionRow, ProductionSummary, SensorRow, SensorSummary,
                              SeriesResponse)
from app.services import data_service
from app.services.filters import DataFilters, build_filters

router = APIRouter(prefix="/data", tags=["data"], dependencies=[Depends(require_permission(DATA_READ))])

Multi = Annotated[list[str] | None, Query(max_length=50)]


def filters(upload_id: Multi = None, plant: Multi = None, line: Multi = None, asset: Multi = None, tag: Multi = None,
            parameter: Multi = None, batch: Multi = None,
            start: str | None = Query(None, max_length=40, description="inclusive; date or ISO datetime"),
            end: str | None = Query(None, max_length=40, description="inclusive; date-only means end of that day")) -> DataFilters:
    return build_filters(upload_id, plant, line, asset, tag, parameter, batch, start, end)


class DataPage(PageParams):
    page_size: int = Field(100, ge=1, le=1000)


@router.get("/sensor/options", response_model=FilterOptions)
def sensor_options(f: DataFilters = Depends(filters)):
    """Selectable values for each filter, narrowed by the other filters that are already set."""
    return data_service.options(f, "sensor")


@router.get("/sensor/summary", response_model=SensorSummary)
def sensor_summary(f: DataFilters = Depends(filters)):
    return data_service.sensor_summary(f)


@router.get("/sensor/series", response_model=SeriesResponse)
def sensor_series(f: DataFilters = Depends(filters), max_points: int = Query(2000, ge=10, le=10000)):
    """Chart-ready series (one per plant/line/asset/tag/parameter). Long series are aggregated into time buckets."""
    return data_service.sensor_series(f, max_points)


@router.get("/sensor", response_model=Page[SensorRow])
def sensor_data(f: DataFilters = Depends(filters), p: DataPage = Depends()):
    items, total = data_service.sensor_rows(f, p)
    return Page[SensorRow](items=items, total=total, page=p.page, page_size=p.page_size)


@router.get("/production/options", response_model=FilterOptions)
def production_options(f: DataFilters = Depends(filters)):
    return data_service.options(f, "production")


@router.get("/production/summary", response_model=ProductionSummary)
def production_summary(f: DataFilters = Depends(filters)):
    return data_service.production_summary(f)


@router.get("/production", response_model=Page[ProductionRow])
def production_data(f: DataFilters = Depends(filters), p: DataPage = Depends()):
    items, total = data_service.production_rows(f, p)
    return Page[ProductionRow](items=items, total=total, page=p.page, page_size=p.page_size)
