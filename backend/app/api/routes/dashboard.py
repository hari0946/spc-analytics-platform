from fastapi import APIRouter, Depends

from app.api.deps import require_permission
from app.api.routes.data import filters
from app.core.permissions import DATA_READ
from app.schemas.auth import CurrentUser
from app.services import dashboard_service
from app.services.filters import DataFilters

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("")
def dashboard(f: DataFilters = Depends(filters), _: CurrentUser = Depends(require_permission(DATA_READ))):
    """Overall historical process status, KPIs, violations and quality summary for the selected data."""
    return dashboard_service.build(f)
