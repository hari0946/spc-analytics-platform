"""SPC analyses on the selected historical data. All endpoints take the common data filters
(upload_id, plant, line, asset, tag, batch, start, end) plus the analysis settings, compute from Snowflake GOLD data,
return the result immediately (with an `analysis_id`) and store it in Snowflake in the background."""
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Query

from app.api.deps import require_permission
from app.api.routes.data import filters
from app.core.permissions import DATA_READ
from app.schemas.auth import CurrentUser
from app.schemas.common import Page, PageParams
from app.services import analysis_service as svc
from app.services import analysis_store as store
from app.services.analysis_service import ChartOptions
from app.services.filters import DataFilters

router = APIRouter(prefix="/analysis", tags=["analysis"])
Reader = Depends(require_permission(DATA_READ))
Param = Query(..., min_length=1, max_length=100, description="parameter to analyse, e.g. TEMPERATURE")
OptParam = Query(None, max_length=100)


def _run(kind: str, result: dict, f: DataFilters, o: ChartOptions, parameter: str | None, user: CurrentUser,
         background: BackgroundTasks) -> dict:
    aid = str(uuid.uuid4())
    params = {"parameter": parameter, "options": o.model_dump(exclude_none=True),
              "filters": {k: v for k, v in f.__dict__.items() if v}}
    result = {**result, "analysis_id": aid}
    background.add_task(store.persist, aid, kind, result, params, user.user_id)
    return result


@router.get("/statistics")
def statistics(background: BackgroundTasks, parameter: str = Param, f: DataFilters = Depends(filters),
               user: CurrentUser = Reader):
    """Mean, median, mode, min, max, range, variance, std, skewness, kurtosis, percentiles, quartiles."""
    return _run("STATISTICS", svc.statistics_analysis(f, parameter), f, ChartOptions(), parameter, user, background)


@router.get("/control-chart")
def control_chart(background: BackgroundTasks, o: ChartOptions = Depends(), parameter: str | None = OptParam,
                  f: DataFilters = Depends(filters), user: CurrentUser = Reader):
    """chart = XBAR_R | XBAR_S | R | S | IMR | EWMA | CUSUM (parameter required) or P | NP | C | U (production data)."""
    _need_parameter(o, parameter)
    return _run("CONTROL_CHART", svc.control_chart_analysis(f, parameter, o), f, o, parameter, user, background)


@router.get("/limits")
def limits(background: BackgroundTasks, o: ChartOptions = Depends(), parameter: str | None = OptParam,
           f: DataFilters = Depends(filters), user: CurrentUser = Reader):
    """UCL / CL / LCL with USL / LSL / Target against the historical values."""
    _need_parameter(o, parameter)
    return _run("CONTROL_LIMITS", svc.limits_analysis(f, parameter, o), f, o, parameter, user, background)


@router.get("/violations")
def violations(background: BackgroundTasks, o: ChartOptions = Depends(), parameter: str | None = OptParam,
               f: DataFilters = Depends(filters), user: CurrentUser = Reader):
    """Western Electric / Nelson rule violations (and out-of-control points) on the chosen chart."""
    _need_parameter(o, parameter)
    return _run("VIOLATIONS", svc.violations_analysis(f, parameter, o), f, o, parameter, user, background)


@router.get("/capability")
def capability(background: BackgroundTasks, o: ChartOptions = Depends(), parameter: str = Param,
               f: DataFilters = Depends(filters), user: CurrentUser = Reader):
    """Cp, Cpk, Pp, Ppk, CPU, CPL, PPM and yield. Needs usl and/or lsl."""
    return _run("CAPABILITY", svc.capability_analysis(f, parameter, o), f, o, parameter, user, background)


@router.get("/distribution")
def distribution(background: BackgroundTasks, o: ChartOptions = Depends(), parameter: str = Param,
                 f: DataFilters = Depends(filters), user: CurrentUser = Reader):
    """Histogram, box plot, normality tests, probability plot and distribution fitting."""
    return _run("DISTRIBUTION", svc.distribution_analysis(f, parameter, o), f, o, parameter, user, background)


@router.get("/history")
def history(params: PageParams = Depends(), kind: str | None = Query(None, max_length=30), user: CurrentUser = Reader):
    items, total = store.list_history(params, user, kind)
    return Page[dict](items=items, total=total, page=params.page, page_size=params.page_size)


@router.get("/{analysis_id}")
def stored_result(analysis_id: str, user: CurrentUser = Reader):
    """Status and stored result of an earlier analysis."""
    return store.get_result(analysis_id, user)


def _need_parameter(o: ChartOptions, parameter: str | None) -> None:
    from app.core.exceptions import UnprocessableError
    if o.chart in svc.VARIABLE_CHARTS and not parameter:
        raise UnprocessableError("'parameter' is required for variables charts")
