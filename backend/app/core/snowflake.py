"""Reusable Snowflake connection module (raw SQL, Snowflake Python Connector only)."""
import logging
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Sequence

import snowflake.connector
from snowflake.connector import SnowflakeConnection
from snowflake.connector.errors import Error as SnowflakeError

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class SnowflakeConnectionError(Exception):
    """Raised when a connection cannot be established or a query fails."""


_lock = threading.Lock()
_connection: SnowflakeConnection | None = None


def _missing_settings() -> list[str]:
    s = get_settings()
    missing = []
    if not s.snowflake_account:
        missing.append("SNOWFLAKE_ACCOUNT")
    if not s.snowflake_user:
        missing.append("SNOWFLAKE_USER")
    if not s.snowflake_password.get_secret_value():
        missing.append("SNOWFLAKE_PASSWORD")
    return missing


def _connect() -> SnowflakeConnection:
    missing = _missing_settings()
    if missing:
        raise SnowflakeConnectionError(f"Missing required settings: {', '.join(missing)}")
    s = get_settings()
    params: dict[str, Any] = {
        "account": s.snowflake_account,
        "user": s.snowflake_user,
        "password": s.snowflake_password.get_secret_value(),
        "login_timeout": s.snowflake_login_timeout,
        "network_timeout": 60,
        "application": "SPC_Analytics_Platform",
        # all timestamps (defaults, sessions, audit) are UTC regardless of account default
        "session_parameters": {"TIMEZONE": "UTC"},
    }
    # Optional context; only passed when configured
    for key, value in (
        ("warehouse", s.snowflake_warehouse),
        ("database", s.snowflake_database),
        ("schema", s.snowflake_schema),
        ("role", s.snowflake_role),
    ):
        if value:
            params[key] = value
    try:
        return snowflake.connector.connect(**params)
    except SnowflakeError as e:
        # Never include credentials in the message
        logger.error("Snowflake connection failed: %s", e.msg if hasattr(e, "msg") else type(e).__name__)
        raise SnowflakeConnectionError(f"Could not connect to Snowflake: {getattr(e, 'msg', None) or type(e).__name__}") from e


def get_connection() -> SnowflakeConnection:
    """Return the shared connection, (re)creating it if missing or closed."""
    global _connection
    with _lock:
        if _connection is None or _connection.is_closed():
            _connection = _connect()
        return _connection


def close_connection() -> None:
    global _connection
    with _lock:
        if _connection is not None:
            try:
                _connection.close()
            except SnowflakeError:
                logger.warning("Error while closing Snowflake connection", exc_info=True)
            _connection = None


@contextmanager
def cursor() -> Iterator[Any]:
    cur = get_connection().cursor()
    try:
        yield cur
    finally:
        cur.close()


def execute(sql: str, params: Sequence[Any] | dict | None = None) -> list[tuple]:
    """Run a parameterized query (use %s / %(name)s placeholders) and return all rows."""
    try:
        with cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    except SnowflakeError as e:
        raise SnowflakeConnectionError(f"Query failed: {getattr(e, 'msg', None) or type(e).__name__}") from e


def test_connection() -> dict:
    """Verify connectivity and report version, role, warehouse, database, schema."""
    rows = execute(
        "SELECT CURRENT_VERSION(), CURRENT_ACCOUNT(), CURRENT_USER(), CURRENT_ROLE(), "
        "CURRENT_WAREHOUSE(), CURRENT_DATABASE(), CURRENT_SCHEMA()"
    )
    v = rows[0]
    return {
        "version": v[0],
        "account": v[1],
        "user": v[2],
        "role": v[3],
        "warehouse": v[4],
        "database": v[5],
        "schema": v[6],
    }
