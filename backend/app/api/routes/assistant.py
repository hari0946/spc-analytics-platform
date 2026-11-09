from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends

from app.api.deps import require_permission
from app.api.routes.data import filters
from app.core.permissions import DATA_READ
from app.schemas.auth import CurrentUser
from app.services import assistant_service
from app.services.filters import DataFilters, build_filters

router = APIRouter(prefix="/assistant", tags=["assistant"])
Reader = Depends(require_permission(DATA_READ))


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    upload_id: list[str] = []
    plant: list[str] = []
    line: list[str] = []
    asset: list[str] = []
    tag: list[str] = []
    batch: list[str] = []
    start: str | None = None
    end: str | None = None


@router.post("/ask")
def ask(body: AskRequest, _: CurrentUser = Reader):
    """Answer a question using real analysis results of the selected historical data (never invented)."""
    f = build_filters(body.upload_id, body.plant, body.line, body.asset, body.tag, None, body.batch, body.start, body.end)
    return assistant_service.ask(body.question, f)


@router.get("/summary")
def summary(f: DataFilters = Depends(filters), _: CurrentUser = Reader):
    return assistant_service.insights(f)
