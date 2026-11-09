import re

from pydantic import BaseModel, field_validator

# Snowflake timestamp format elements only (it is passed as a bound parameter, this is just early feedback)
_FMT = re.compile(r"^[A-Za-z0-9:/\-. ,TZ+']{3,60}$")


class ProcessOptions(BaseModel):
    """Optional processing settings. Without `timestamp_format` Snowflake auto-detects common formats
    (ISO 8601, 'YYYY-MM-DD HH:MI:SS', ...). Day-first dates need an explicit format, e.g. 'DD/MM/YYYY HH24:MI'."""
    timestamp_format: str | None = None

    @field_validator("timestamp_format")
    @classmethod
    def _check(cls, v):
        if v is not None and not _FMT.match(v):
            raise ValueError("timestamp_format contains unsupported characters")
        return v


class RejectionOut(BaseModel):
    source_row: int
    parameter: str | None
    reason: str
    raw_record: dict | None
