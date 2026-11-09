import re
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, EmailStr, Field, StringConstraints, field_validator

Username = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[A-Za-z0-9_.-]{3,50}$")]


def validate_password_strength(v: str) -> str:
    if len(v) < 10:
        raise ValueError("Password must be at least 10 characters")
    if len(v.encode("utf-8")) > 72:
        raise ValueError("Password must be at most 72 bytes")
    if not re.search(r"[A-Za-z]", v) or not re.search(r"\d", v):
        raise ValueError("Password must contain at least one letter and one digit")
    return v


class RegisterRequest(BaseModel):
    username: Username
    email: EmailStr
    password: str
    full_name: str | None = Field(None, max_length=200)

    _pw = field_validator("password")(validate_password_strength)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


class CurrentUser(BaseModel):
    user_id: str
    username: str
    email: str
    full_name: str | None
    roles: list[str]
    permissions: list[str]
    session_id: str = Field(exclude=True)
