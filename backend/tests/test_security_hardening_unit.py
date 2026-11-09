"""Phase 20 security checks that need no Snowflake: headers, CORS, rate limiting, route authentication audit."""
import time

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.api.routes import auth as auth_routes
from app.core.exceptions import UnauthorizedError
from app.core.ratelimit import FailureLimiter, reset_login_limiter
from app.main import app

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _fresh_limiter():
    reset_login_limiter()
    yield
    reset_login_limiter()


# ---------------------------------------------------------------- limiter
def test_limiter_blocks_after_max_failures_and_resets():
    lim = FailureLimiter(max_failures=3, window_seconds=60)
    for _ in range(2):
        lim.record_failure("k")
    assert lim.retry_after("k") == 0
    lim.record_failure("k")
    assert 0 < lim.retry_after("k") <= 61 and lim.retry_after("other") == 0
    lim.reset("k")
    assert lim.retry_after("k") == 0


def test_limiter_window_expires():
    lim = FailureLimiter(max_failures=2, window_seconds=1)
    lim.record_failure("k"); lim.record_failure("k")
    assert lim.retry_after("k") > 0
    time.sleep(1.2)
    assert lim.retry_after("k") == 0


def test_login_endpoint_throttles_repeated_failures(monkeypatch):
    calls = {"n": 0}

    def fake_login(body, ip):
        calls["n"] += 1
        if body.password == "right-Passw0rd":
            return {"access_token": "t", "token_type": "bearer", "expires_at": "2030-01-01T00:00:00"}
        raise UnauthorizedError("Invalid username or password")

    monkeypatch.setattr(auth_routes.auth_service, "login", fake_login)
    bad = {"username": "victim", "password": "nope"}
    for _ in range(5):
        assert client.post("/api/auth/login", json=bad).status_code == 401
    blocked = client.post("/api/auth/login", json=bad)
    assert blocked.status_code == 429 and blocked.json()["error"]["code"] == "too_many_requests"
    assert int(blocked.headers["retry-after"]) > 0 and calls["n"] == 5                      # the backend was not even queried
    assert client.post("/api/auth/login", json={"username": "victim", "password": "right-Passw0rd"}).status_code == 429   # even the right password waits
    assert client.post("/api/auth/login", json={"username": "someone-else", "password": "right-Passw0rd"}).status_code == 200  # other users unaffected


def test_success_clears_failure_count(monkeypatch):
    def fake_login(body, ip):
        if body.password == "ok-Passw0rd1":
            return {"access_token": "t", "token_type": "bearer", "expires_at": "2030-01-01T00:00:00"}
        raise UnauthorizedError("Invalid username or password")

    monkeypatch.setattr(auth_routes.auth_service, "login", fake_login)
    for _ in range(4):
        client.post("/api/auth/login", json={"username": "u", "password": "bad"})
    assert client.post("/api/auth/login", json={"username": "u", "password": "ok-Passw0rd1"}).status_code == 200
    for _ in range(4):
        assert client.post("/api/auth/login", json={"username": "u", "password": "bad"}).status_code == 401      # counter restarted


# ---------------------------------------------------------------- headers and CORS
def test_security_headers_present():
    for path in ("/health", "/api/health"):
        h = client.get(path).headers
        assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY" and h["referrer-policy"] == "no-referrer"
        assert "geolocation=()" in h["permissions-policy"]
    api = client.get("/api/health").headers
    assert api["cache-control"] == "no-store" and "default-src 'none'" in api["content-security-policy"]
    assert "content-security-policy" not in client.get("/health").headers
    assert client.get("/docs").status_code == 200                                      # Swagger UI still loads (no CSP on /docs)
    err = client.get("/api/users")
    assert err.status_code == 401 and err.headers["x-content-type-options"] == "nosniff"      # headers also on error responses


def test_cors_only_allows_configured_origin():
    ok = client.options("/api/health", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    evil = client.options("/api/health", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in evil.headers
    simple = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in simple.headers


# ---------------------------------------------------------------- every route is authenticated unless explicitly public
PUBLIC = {("GET", "/health"), ("GET", "/health/ready"), ("GET", "/api/health"), ("GET", "/api/health/ready"), ("POST", "/api/auth/login"), ("POST", "/api/auth/register")}


def _routes():
    for r in app.routes:
        if isinstance(r, APIRoute):
            for m in r.methods - {"HEAD", "OPTIONS"}:
                yield m, r.path


def test_all_non_public_routes_require_authentication():
    checked = 0
    for method, path in _routes():
        if (method, path) in PUBLIC:
            continue
        url = path.replace("{ident}", "x").replace("{upload_id}", "x").replace("{plant_id}", "x").replace("{user_id}", "x") \
                  .replace("{role_id}", "x").replace("{analysis_id}", "x")
        r = client.request(method, url)
        assert r.status_code == 401, f"{method} {path} returned {r.status_code} without credentials"
        checked += 1
    assert checked >= 50


def test_garbage_credentials_rejected_everywhere():
    for h in ({"Authorization": "Bearer abc.def.ghi"}, {"Authorization": "Basic Zm9vOmJhcg=="}, {"Authorization": "Bearer "}):
        assert client.get("/api/users", headers=h).status_code == 401
        assert client.get("/api/dashboard", headers=h).status_code == 401


def test_public_routes_are_the_documented_minimum():
    declared = {(m, p) for m, p in _routes()}
    assert PUBLIC <= declared
    r = client.get("/api/health/ready")                     # may be 200 or 503, but never leaks account details
    assert "account" not in r.text and "warehouse" not in r.text and "role" not in r.text.lower().replace("error", "")


def test_error_responses_hide_internals():
    r = client.get("/api/uploads/%27%3B%20DROP%20TABLE%20USERS%3B--")
    assert r.status_code == 401 and "Traceback" not in r.text and "SPC_PLATFORM" not in r.text
