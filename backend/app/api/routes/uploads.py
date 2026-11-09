from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, Request, Response, UploadFile

from app.api.deps import client_ip, require_permission
from app.core.permissions import DATA_READ, DATA_UPLOAD
from app.schemas.auth import CurrentUser
from app.schemas.common import Page, PageParams
from app.schemas.upload import DatasetType, MappingRequest, UploadDetail, UploadSummary
from app.schemas.processing import ProcessOptions, RejectionOut
from app.services import processing_service, upload_service

router = APIRouter(prefix="/uploads", tags=["uploads"])
Writer = Depends(require_permission(DATA_UPLOAD))
Reader = Depends(require_permission(DATA_READ))


@router.post("", response_model=UploadDetail, status_code=201)
def create_upload(request: Request, file: UploadFile = File(...), dataset_type: DatasetType = Form(...),
                  allow_duplicate: bool = Form(False), sheet: str | None = Form(None),
                  actor: CurrentUser = Writer):
    """Validate the file, convert it (Excel -> NDJSON), PUT it to the internal stage and return a preview
    with a suggested column mapping."""
    return upload_service.create_upload(file, dataset_type, allow_duplicate, sheet, actor, client_ip(request))


@router.get("", response_model=Page[UploadSummary])
def list_uploads(params: PageParams = Depends(), status: str | None = Query(None, max_length=20),
                 dataset_type: str | None = Query(None, max_length=20), _: CurrentUser = Reader):
    items, total = upload_service.list_uploads(params, status, dataset_type)
    return Page[UploadSummary](items=items, total=total, page=params.page, page_size=params.page_size)


@router.get("/{upload_id}", response_model=UploadDetail)
def get_upload(upload_id: str, _: CurrentUser = Reader):
    return upload_service.get_upload(upload_id)


@router.put("/{upload_id}/mapping", response_model=UploadDetail)
def set_mapping(upload_id: str, body: MappingRequest, request: Request, actor: CurrentUser = Writer):
    return upload_service.set_mapping(upload_id, body.mapping, actor, client_ip(request))


@router.post("/{upload_id}/load", response_model=UploadDetail)
def load_upload(upload_id: str, request: Request, actor: CurrentUser = Writer):
    """COPY the staged file into the BRONZE table for this dataset type."""
    return upload_service.load_to_bronze(upload_id, actor, client_ip(request))


@router.post("/{upload_id}/process", response_model=UploadDetail, status_code=202)
def process_upload(upload_id: str, request: Request, background: BackgroundTasks,
                   options: ProcessOptions | None = None, actor: CurrentUser = Writer):
    """Start BRONZE -> SILVER -> GOLD processing in the background. Poll GET /uploads/{id} until the status is
    COMPLETED or PROCESS_FAILED, then read GET /uploads/{id}/report."""
    options = options or ProcessOptions()
    ip = client_ip(request)
    processing_service.start_processing(upload_id, actor, ip)
    background.add_task(processing_service.run_processing, upload_id, options, actor.user_id, actor.username, ip)
    return upload_service.get_upload(upload_id)


@router.get("/{upload_id}/report")
def get_report(upload_id: str, _: CurrentUser = Reader) -> dict:
    """Data-validation report produced by the last successful processing run."""
    return processing_service.get_report(upload_id)


@router.get("/{upload_id}/rejections", response_model=Page[RejectionOut])
def list_rejections(upload_id: str, params: PageParams = Depends(), reason: str | None = Query(None, max_length=40),
                    _: CurrentUser = Reader):
    items, total = processing_service.list_rejections(upload_id, reason, params)
    return Page[RejectionOut](items=items, total=total, page=params.page, page_size=params.page_size)


@router.delete("/{upload_id}", status_code=204)
def delete_upload(upload_id: str, request: Request, actor: CurrentUser = Writer):
    upload_service.delete_upload(upload_id, actor, client_ip(request))
    return Response(status_code=204)
