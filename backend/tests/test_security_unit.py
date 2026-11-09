"""Pure unit tests: hashing, JWT, permission logic, input schemas. No network."""
import pytest
from pydantic import ValidationError

from app.core import security
from app.core.config import get_settings
from app.core.exceptions import UnauthorizedError
from app.core.permissions import has_permission
from app.schemas.auth import RegisterRequest
from app.schemas.role import RoleCreate

SECRET = "x" * 40


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_hash_is_salted_and_verifies():
    h1, h2 = security.hash_password("Correct-horse-9"), security.hash_password("Correct-horse-9")
    assert h1 != h2 and h1.startswith("$2b$") and "Correct" not in h1
    assert security.verify_password("Correct-horse-9", h1)
    assert not security.verify_password("correct-horse-9", h1)
    assert not security.verify_password("x", "not-a-hash")


def test_token_roundtrip_and_unique_jti():
    t1, j1, exp = security.create_access_token("user-1")
    _, j2, _ = security.create_access_token("user-1")
    p = security.decode_token(t1)
    assert p["sub"] == "user-1" and p["jti"] == j1 != j2 and exp.tzinfo is None


def test_tampered_token_rejected():
    t, _, _ = security.create_access_token("u")
    with pytest.raises(UnauthorizedError):
        security.decode_token(t[:-3] + ("aaa" if not t.endswith("aaa") else "bbb"))
    with pytest.raises(UnauthorizedError):
        security.decode_token("garbage")


def test_token_signed_with_other_secret_rejected(monkeypatch):
    t, _, _ = security.create_access_token("u")
    monkeypatch.setenv("JWT_SECRET", "y" * 40)
    get_settings.cache_clear()
    with pytest.raises(UnauthorizedError):
        security.decode_token(t)


def test_expired_token_rejected(monkeypatch):
    monkeypatch.setenv("JWT_EXPIRE_MINUTES", "-1")
    get_settings.cache_clear()
    t, _, _ = security.create_access_token("u")
    with pytest.raises(UnauthorizedError, match="expired"):
        security.decode_token(t)


def test_short_or_missing_secret_refused(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "short")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError):
        security.create_access_token("u")


def test_permission_logic():
    assert has_permission(["*"], "anything")
    assert has_permission(["data:read"], "data:read")
    assert not has_permission(["data:read"], "users:manage")
    assert not has_permission([], "data:read")


@pytest.mark.parametrize("pw", ["short1", "alllettersnodigit", "1234567890123", "a1" * 40])
def test_weak_passwords_rejected(pw):
    with pytest.raises(ValidationError):
        RegisterRequest(username="valid_user", email="a@example.com", password=pw)


@pytest.mark.parametrize("name", ["ab", "has space", "x" * 51, "bad/char"])
def test_bad_usernames_rejected(name):
    with pytest.raises(ValidationError):
        RegisterRequest(username=name, email="a@example.com", password="Passw0rd-long")


def test_role_schema_validation():
    assert RoleCreate(role_name="QA_LEAD", permissions=["data:read", "data:read"]).permissions == ["data:read"]
    for bad in ({"role_name": "lower"}, {"role_name": "OK", "permissions": ["nope:nope"]}):
        with pytest.raises(ValidationError):
            RoleCreate(**bad)
