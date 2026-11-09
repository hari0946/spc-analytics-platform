from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from app.api.deps import client_ip, require_permission
from app.core.permissions import REPORTS_GENERATE
from app.schemas.auth import CurrentUser
from app.services import audit_service, report_service
from app.services.filters import build_filters

router = APIRouter(prefix="/reports", tags=["reports"])

MEDIA = {"pdf": "application/pdf", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}


class ReportRequest(BaseModel):
    type: report_service.ReportType
    format: str = Field("pdf", pattern="^(pdf|xlsx)$")
    parameter: str | None = Field(None, max_length=100)
    upload_id: list[str] = []
    plant: list[str] = []
    line: list[str] = []
    asset: list[str] = []
    tag: list[str] = []
    batch: list[str] = []
    start: str | None = None
    end: str | None = None
    options: dict = Field(default_factory=dict, description="chart, subgroup_size, usl, lsl, target, rule_set, ...")


@router.post("")
def generate(body: ReportRequest, request: Request, user: CurrentUser = Depends(require_permission(REPORTS_GENERATE))):
    """Generate a report for the selected dataset / asset / parameter / date range as a PDF or Excel download."""
    f = build_filters(body.upload_id, body.plant, body.line, body.asset, body.tag, None, body.batch, body.start, body.end)
    doc = report_service.build(body.type, f, body.parameter, body.options, user)
    data = report_service.to_pdf(doc) if body.format == "pdf" else report_service.to_xlsx(doc)
    audit_service.log("REPORT_GENERATED", actor_user_id=user.user_id, actor_username=user.username, entity_type="REPORT",
                      ip=client_ip(request), details={"type": body.type, "format": body.format, "parameter": body.parameter,
                                                      "bytes": len(data), "uploads": f.upload_ids})
    name = f"spc_{body.type}_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.{body.format}"
    return Response(content=data, media_type=MEDIA[body.format], headers={"Content-Disposition": f'attachment; filename="{name}"'})
