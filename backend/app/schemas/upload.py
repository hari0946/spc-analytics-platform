from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

DatasetType = Literal["SENSOR", "PRODUCTION"]


class _Mapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def mapped_columns(self) -> list[str]:
        cols: list[str] = []
        for v in self.model_dump(exclude_none=True).values():
            cols.extend(v if isinstance(v, list) else [v])
        return cols

    @model_validator(mode="after")
    def _no_column_used_twice(self):
        cols = self.mapped_columns()
        dup = sorted({c for c in cols if cols.count(c) > 1})
        if dup:
            raise ValueError(f"Source column(s) mapped more than once: {', '.join(dup)}")
        return self


class SensorMapping(_Mapping):
    """Long layout: `parameter` + `value` columns. Wide layout: `value_columns` (one column per parameter)."""
    timestamp: str
    parameter: str | None = None
    value: str | None = None
    value_columns: list[str] | None = None
    tag: str | None = None
    asset: str | None = None
    line: str | None = None
    plant: str | None = None
    batch: str | None = None

    @model_validator(mode="after")
    def _layout(self):
        long_ = self.parameter is not None or self.value is not None
        if long_ and (self.parameter is None or self.value is None):
            raise ValueError("Long layout needs both 'parameter' and 'value'")
        if long_ and self.value_columns:
            raise ValueError("Use either parameter+value (long) or value_columns (wide), not both")
        if not long_ and not self.value_columns:
            raise ValueError("Map either parameter+value (long layout) or value_columns (wide layout)")
        return self


class ProductionMapping(_Mapping):
    timestamp: str
    batch: str | None = None
    product: str | None = None
    asset: str | None = None
    line: str | None = None
    plant: str | None = None
    units_produced: str | None = None
    sample_size: str | None = None
    defective_count: str | None = None
    defect_count: str | None = None

    @model_validator(mode="after")
    def _has_quantity(self):
        if not any([self.units_produced, self.sample_size, self.defective_count, self.defect_count]):
            raise ValueError("Map at least one of units_produced, sample_size, defective_count, defect_count")
        return self


MAPPING_MODELS = {"SENSOR": SensorMapping, "PRODUCTION": ProductionMapping}


class MappingRequest(BaseModel):
    mapping: dict


class PreviewOut(BaseModel):
    columns: list[str]
    rows: list[list[str | None]]
    suggested_mapping: dict
    warnings: list[str] = []


class UploadSummary(BaseModel):
    upload_id: str
    file_name: str
    file_type: str
    dataset_type: str | None
    file_size_bytes: int | None
    status: str
    rows_received: int | None
    rows_loaded: int | None
    empty_rows_skipped: int | None
    duplicate_rows: int | None
    error_message: str | None
    uploaded_by: str | None
    uploaded_at: datetime


class UploadDetail(UploadSummary):
    column_mapping: dict | None = None
    preview: PreviewOut | None = None
