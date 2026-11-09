from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.api.deps import require_permission
from app.core.permissions import AUDIT_READ
from app.schemas.audit import AuditLogOut
from app.schemas.auth import CurrentUser
from app.schemas.common import Page, PageParams
from app.services import audit_service

router = APIRouter(prefix="/audit-logs", tags=["audit"])


@router.get("", response_model=Page[AuditLogOut])
def list_audit_logs(params: PageParams = Depends(), action: str | None = Query(None, max_length=50),
                    actor: str | None = Query(None, max_length=100), since: datetime | None = None,
                    until: datetime | None = None, _: CurrentUser = Depends(require_permission(AUDIT_READ))):
    items, total = audit_service.list_logs(params, action, actor, since, until)
    return Page[AuditLogOut](items=items, total=total, page=params.page, page_size=params.page_size)
