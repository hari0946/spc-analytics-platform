from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: object | None = None
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


class PageParams(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(50, ge=1, le=200)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    timestamp: datetime


class SnowflakeStatus(BaseModel):
    connected: bool
    version: str
    account: str
    user: str
    role: str | None
    warehouse: str | None
    database: str | None
    schema_: str | None = Field(None, alias="schema")

    model_config = {"populate_by_name": True}


class ReadinessResponse(BaseModel):
    status: str
    snowflake: SnowflakeStatus | None = None
