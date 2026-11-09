from datetime import datetime

from pydantic import BaseModel


class SensorRow(BaseModel):
    upload_id: str
    plant_code: str | None
    line_code: str | None
    asset_code: str | None
    tag_name: str | None
    parameter: str
    measured_at: datetime
    value: float
    batch_id: str | None
    subgroup_id: int | None


class ProductionRow(BaseModel):
    upload_id: str
    plant_code: str | None
    line_code: str | None
    asset_code: str | None
    batch_id: str | None
    product: str | None
    event_time: datetime
    units_produced: int | None
    sample_size: int | None
    defective_count: int | None
    defect_count: int | None


class OptionValue(BaseModel):
    value: str
    count: int


class FilterOptions(BaseModel):
    """Values that can still be chosen for each filter, given all OTHER filters currently applied."""
    plants: list[OptionValue]
    lines: list[OptionValue]
    assets: list[OptionValue]
    tags: list[OptionValue] = []
    parameters: list[OptionValue] = []
    batches: list[OptionValue]
    datasets: list[OptionValue]          # upload ids
    first: datetime | None
    last: datetime | None


class ParameterSummary(BaseModel):
    parameter: str
    n: int
    min: float
    max: float
    mean: float


class SensorSummary(BaseModel):
    total_rows: int
    first: datetime | None
    last: datetime | None
    datasets: int
    parameters: list[ParameterSummary]


class ProductionSummary(BaseModel):
    total_rows: int
    first: datetime | None
    last: datetime | None
    datasets: int
    units_produced: int | None
    sample_size: int | None
    defective_count: int | None
    defect_count: int | None
    batches: int


class SeriesPoint(BaseModel):
    t: datetime
    v: float              # raw value, or the bucket average when the series is aggregated
    min: float
    max: float
    n: int                # readings represented by this point (1 for raw)


class Series(BaseModel):
    plant_code: str | None
    line_code: str | None
    asset_code: str | None
    tag_name: str | None
    parameter: str
    n_total: int          # readings in the selection (before any aggregation)
    aggregated: bool
    bucket_seconds: int | None
    points: list[SeriesPoint]


class SeriesResponse(BaseModel):
    max_points: int
    series: list[Series]
