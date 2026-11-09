from datetime import datetime, timezone

from app.core import snowflake as sf
from app.core.config import get_settings


def liveness() -> dict:
    s = get_settings()
    return {
        "status": "ok",
        "service": s.app_name,
        "version": s.app_version,
        "timestamp": datetime.now(timezone.utc),
    }


def snowflake_status() -> dict:
    """Raises SnowflakeConnectionError when Snowflake is unreachable."""
    return {"connected": True, **sf.test_connection()}
