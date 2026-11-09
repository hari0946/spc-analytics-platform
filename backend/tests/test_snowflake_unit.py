"""Unit tests for failure handling (no network)."""
import pytest
from fastapi.testclient import TestClient
from snowflake.connector.errors import DatabaseError

from app.core import snowflake as sf
from app.core.config import get_settings
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset():
    sf.close_connection()
    get_settings.cache_clear()
    yield
    sf.close_connection()
    get_settings.cache_clear()


def test_missing_settings_reported(monkeypatch):
    for k in ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PASSWORD"):
        monkeypatch.setenv(k, "")
    with pytest.raises(sf.SnowflakeConnectionError, match="SNOWFLAKE_ACCOUNT"):
        sf.get_connection()


def test_status_endpoint_returns_503_on_failure(monkeypatch):
    def boom(**kwargs):
        raise DatabaseError(msg="Incorrect username or password was specified.")

    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "x")
    monkeypatch.setenv("SNOWFLAKE_USER", "u")
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "super-secret-pw")
    monkeypatch.setattr(sf.snowflake.connector, "connect", boom)
    r = client.get("/api/health/ready")
    assert r.status_code == 503
    body = r.json()["error"]
    assert body["code"] == "database_unavailable"
    assert "Could not connect" in body["message"]
    assert "super-secret-pw" not in r.text
