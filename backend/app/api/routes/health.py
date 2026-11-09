from fastapi import APIRouter, Depends

from app.api.deps import require_permission
from app.core.permissions import USERS_MANAGE
from app.schemas.common import HealthResponse, ReadinessResponse, SnowflakeStatus
from app.services import health_service

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health():
    """Liveness: the process is up (no external calls)."""
    return health_service.liveness()


@router.get("/health/ready", response_model=ReadinessResponse)
def ready():
    """Readiness: Snowflake reachable (503 database_unavailable otherwise). Public, so it reveals no account details."""
    health_service.snowflake_status()
    return {"status": "ready"}


@router.get("/snowflake/status", response_model=SnowflakeStatus, response_model_by_alias=True,
            dependencies=[Depends(require_permission(USERS_MANAGE))])
def snowflake_status():
    """Connection details (account, role, warehouse, database). Administrators only."""
    return health_service.snowflake_status()
