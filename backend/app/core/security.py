"""Password hashing (bcrypt) and JWT access tokens. No Snowflake or FastAPI imports."""
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import get_settings
from app.core.exceptions import UnauthorizedError

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


# Verified against when the user is unknown, so login timing doesn't reveal which usernames exist.
DUMMY_HASH = hash_password("not-a-real-password-0")


def _secret() -> str:
    secret = get_settings().jwt_secret.get_secret_value()
    if len(secret) < 32:
        raise RuntimeError("JWT_SECRET must be set to a random value of at least 32 characters")
    return secret


def create_access_token(user_id: str) -> tuple[str, str, datetime]:
    """Returns (token, session_id/jti, expires_at as naive UTC)."""
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=get_settings().jwt_expire_minutes)
    jti = uuid.uuid4().hex
    token = jwt.encode({"sub": user_id, "jti": jti, "iat": now, "exp": exp}, _secret(), algorithm=ALGORITHM)
    return token, jti, exp.replace(tzinfo=None)


def decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, _secret(), algorithms=[ALGORITHM], options={"require": ["exp", "sub", "jti"]})
    except jwt.ExpiredSignatureError:
        raise UnauthorizedError("Token has expired")
    except jwt.PyJWTError:
        raise UnauthorizedError("Invalid token")
    return payload
