"""Backend foundation tests: routing, validation, error handling, pagination, logging.
Tests marked integration hit live Snowflake (sample rows are cleaned up)."""
import logging

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.core import snowflake as sf
from app.core.exceptions import ConflictError
from app.main import app
from app.schemas.auth import CurrentUser
from app.services import plant_service

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _as_logged_in_user():
    """Auth is covered in test_auth_integration.py; here the plant routes just need *a* user."""
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="u", username="sample_user", email="u@example.com", full_name=None,
        roles=["VIEWER"], permissions=["data:read"], session_id="s")
    yield
    app.dependency_overrides.clear()


# ---------- no external dependencies ----------
def test_health_shape_and_request_id():
    r = client.get("/health")
    assert r.status_code == 200
    assert set(r.json()) == {"status", "service", "version", "timestamp"}
    assert len(r.headers["x-request-id"]) > 0


def test_request_id_is_echoed():
    r = client.get("/api/health", headers={"x-request-id": "abc123"})
    assert r.headers["x-request-id"] == "abc123"


def test_unknown_route_uniform_404():
    r = client.get("/api/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "http_error"


def test_wrong_method_405():
    r = client.post("/health")
    assert r.status_code == 405
    assert r.json()["error"]["code"] == "http_error"


@pytest.mark.parametrize("qs", ["page=0", "page=abc", "page_size=0", "page_size=201", "page=-1"])
def test_invalid_pagination_422(qs):
    r = client.get(f"/api/plants?{qs}")
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error" and err["details"][0]["loc"]


def test_app_error_mapped(monkeypatch):
    def boom(*_a, **_k):
        raise ConflictError("duplicate", {"field": "plant_code"})

    monkeypatch.setattr(plant_service, "list_plants", boom)
    r = client.get("/api/plants")
    assert r.status_code == 409
    assert r.json()["error"] == {**r.json()["error"], "code": "conflict", "message": "duplicate",
                                 "details": {"field": "plant_code"}}


def test_unhandled_exception_hides_internals(monkeypatch, caplog):
    def boom(*_a, **_k):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(plant_service, "list_plants", boom)
    with caplog.at_level(logging.ERROR):
        r = client.get("/api/plants", headers={"x-request-id": "rid-500"})
    assert r.status_code == 500
    err = r.json()["error"]
    assert err["code"] == "internal_error" and err["request_id"] == "rid-500"
    assert "secret internal detail" not in r.text
    assert any("secret internal detail" in (rec.exc_text or "") or rec.exc_info for rec in caplog.records)


def test_snowflake_down_returns_503(monkeypatch):
    def down(*_a, **_k):
        raise sf.SnowflakeConnectionError("Could not connect to Snowflake: simulated")

    monkeypatch.setattr(sf, "execute", down)
    for path in ("/api/plants", "/api/health/ready"):
        r = client.get(path)
        assert r.status_code == 503, path
        assert r.json()["error"]["code"] == "database_unavailable"
    assert client.get("/health").status_code == 200  # liveness unaffected


# ---------- live Snowflake ----------
@pytest.mark.integration
def test_ready_is_public_without_details_and_status_needs_admin():
    app.dependency_overrides.clear()                              # the autouse fixture logs in a viewer; test anonymous access first
    r = client.get("/api/health/ready")
    assert r.status_code == 200 and r.json() == {"status": "ready", "snowflake": None}      # no account details leak
    assert client.get("/api/snowflake/status").status_code == 401
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="u", username="sample_admin", email="a@example.com", full_name=None, roles=["VIEWER"], permissions=["data:read"], session_id="s")
    assert client.get("/api/snowflake/status").status_code == 403                            # a non-admin cannot see them either
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="u", username="sample_admin", email="a@example.com", full_name=None, roles=["ADMIN"], permissions=["*"], session_id="s")
    s = client.get("/api/snowflake/status").json()
    assert s["connected"] is True and s["database"] == "SPC_PLATFORM" and s["role"] == "SPC_ADMIN" and s["schema"] == "METADATA"


@pytest.mark.integration
def test_plants_pagination_and_not_found_live():
    codes = [f"SAMPLE_PG_{i}" for i in range(3)]
    try:
        for c in codes:
            sf.execute("INSERT INTO SPC_PLATFORM.METADATA.PLANTS (PLANT_CODE, PLANT_NAME) VALUES (%s, %s)",
                       (c, "SAMPLE plant (test data)"))
        r1 = client.get("/api/plants?page=1&page_size=2").json()
        assert r1["total"] >= 3 and len(r1["items"]) == 2 and r1["page"] == 1
        r2 = client.get("/api/plants?page=2&page_size=2").json()
        assert r2["items"][0]["plant_code"] not in {i["plant_code"] for i in r1["items"]}
        one = r1["items"][0]
        assert client.get(f"/api/plants/{one['plant_id']}").json()["plant_code"] == one["plant_code"]
        assert client.get("/api/plants/does-not-exist").status_code == 404
        assert client.get("/api/plants/%27%20OR%201%3D1%20--").status_code == 404  # injection attempt is inert
    finally:
        sf.execute("DELETE FROM SPC_PLATFORM.METADATA.PLANTS WHERE PLANT_CODE LIKE 'SAMPLE_PG_%'")
        sf.close_connection()
