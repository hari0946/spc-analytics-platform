from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.auth import Username, validate_password_strength


class UserOut(BaseModel):
    user_id: str
    username: str
    email: str
    full_name: str | None
    is_active: bool
    roles: list[str]
    last_login_at: datetime | None
    created_at: datetime


class UserCreate(BaseModel):
    username: Username
    email: EmailStr
    password: str
    full_name: str | None = Field(None, max_length=200)
    roles: list[str] = Field(default_factory=lambda: ["VIEWER"], min_length=1)

    _pw = field_validator("password")(validate_password_strength)


class UserUpdate(BaseModel):
    email: EmailStr | None = None
    full_name: str | None = Field(None, max_length=200)
    is_active: bool | None = None
    password: str | None = None

    @field_validator("password")
    @classmethod
    def _pw(cls, v):
        return None if v is None else validate_password_strength(v)


class UserRolesUpdate(BaseModel):
    roles: list[str] = Field(min_length=1)
