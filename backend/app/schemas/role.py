from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.core.permissions import ALL_PERMISSIONS

RoleName = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{1,49}$")]


def _check_permissions(v: list[str]) -> list[str]:
    unknown = sorted(set(v) - ALL_PERMISSIONS)
    if unknown:
        raise ValueError(f"Unknown permissions: {', '.join(unknown)}")
    return sorted(set(v))


class RoleOut(BaseModel):
    role_id: str
    role_name: str
    description: str | None
    permissions: list[str]
    is_builtin: bool
    created_at: datetime


class RoleCreate(BaseModel):
    role_name: RoleName
    description: str | None = Field(None, max_length=500)
    permissions: list[str] = Field(default_factory=list)

    _perm = field_validator("permissions")(_check_permissions)


class RoleUpdate(BaseModel):
    description: str | None = Field(None, max_length=500)
    permissions: list[str] | None = None

    @field_validator("permissions")
    @classmethod
    def _perm(cls, v):
        return None if v is None else _check_permissions(v)
