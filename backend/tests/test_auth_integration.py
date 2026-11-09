"""Live auth / RBAC / admin tests against Snowflake. All records are sample data prefixed
'sample_auth_' (users) / 'SAMPLE_AUTH_' (roles) and are removed afterwards."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core import snowflake as sf
from app.main import app
from app.schemas.user import UserCreate
from app.services import user_service

pytestmark = pytest.mark.integration
client = TestClient(app, raise_server_exceptions=False)
PW = "Sample-Passw0rd!"
M = "SPC_PLATFORM.METADATA"
TAG = uuid.uuid4().hex[:8]


def uname(s: str) -> str:
    return f"sample_auth_{s}_{TAG}"


def mail(s: str) -> str:
    return f"{uname(s)}@example.com"


def login(username, password=PW):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def hdr(token):
    return {"Authorization": f"Bearer {token}"}


def token_for(username):
    r = login(username)
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def admin():
    u = user_service.create_user(UserCreate(username=uname("admin"), email=mail("admin"), password=PW,
                                           roles=["ADMIN"]), action="SAMPLE_SETUP")
    yield {"user": u, "token": token_for(u.username)}


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    yield
    like = "sample_auth_%"
    ids = [r[0] for r in sf.execute(f"SELECT USER_ID FROM {M}.USERS WHERE USERNAME LIKE %s", (like,))]
    for uid in ids:
        sf.execute(f"DELETE FROM {M}.USER_SESSIONS WHERE USER_ID = %s", (uid,))
        sf.execute(f"DELETE FROM {M}.USER_ROLES WHERE USER_ID = %s", (uid,))
    sf.execute(f"DELETE FROM {M}.USERS WHERE USERNAME LIKE %s", (like,))
    sf.execute(f"DELETE FROM {M}.USER_ROLES WHERE ROLE_ID IN (SELECT ROLE_ID FROM {M}.ROLES WHERE ROLE_NAME LIKE 'SAMPLE_AUTH_%')")
    sf.execute(f"DELETE FROM {M}.ROLES WHERE ROLE_NAME LIKE 'SAMPLE_AUTH_%'")
    sf.execute(f"DELETE FROM {M}.AUDIT_LOGS WHERE ACTOR_USERNAME LIKE %s OR DETAILS:username::string LIKE %s", (like, like))
    sf.close_connection()


# ---------------- registration ----------------
def test_register_success_and_no_secret_leak():
    r = client.post("/api/auth/register", json={"username": uname("reg"), "email": mail("reg"), "password": PW})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["roles"] == ["VIEWER"] and body["is_active"] is True
    assert "password" not in r.text.lower() and "$2b$" not in r.text
    stored = sf.execute(f"SELECT PASSWORD_HASH FROM {M}.USERS WHERE USER_ID = %s", (body["user_id"],))[0][0]
    assert stored.startswith("$2b$") and PW not in stored


def test_register_duplicates_and_validation():
    base = {"username": uname("dup"), "email": mail("dup"), "password": PW}
    assert client.post("/api/auth/register", json=base).status_code == 201
    r = client.post("/api/auth/register", json={**base, "email": mail("dup2")})
    assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"
    r = client.post("/api/auth/register", json={**base, "username": uname("dupX")})
    assert r.status_code == 409  # same email
    assert client.post("/api/auth/register", json={**base, "username": uname("a"), "email": "not-an-email"}).status_code == 422
    assert client.post("/api/auth/register", json={**base, "username": uname("b"), "email": mail("b"), "password": "weak"}).status_code == 422


def test_register_cannot_self_assign_roles():
    r = client.post("/api/auth/register", json={"username": uname("sneaky"), "email": mail("sneaky"),
                                                "password": PW, "roles": ["ADMIN"]})
    assert r.status_code == 201 and r.json()["roles"] == ["VIEWER"]


# ---------------- login / session ----------------
def test_login_success_me_and_invalid(admin):
    r = login(admin["user"].username)
    assert r.status_code == 200 and r.json()["token_type"] == "bearer"
    me = client.get("/api/auth/me", headers=hdr(r.json()["access_token"])).json()
    assert me["roles"] == ["ADMIN"] and me["permissions"] == ["*"] and "session_id" not in me
    bad_pw = login(admin["user"].username, "Wrong-Passw0rd")
    unknown = login(uname("ghost"))
    assert bad_pw.status_code == unknown.status_code == 401
    assert bad_pw.json()["error"]["message"] == unknown.json()["error"]["message"]  # no user enumeration
    assert login(admin["user"].username.upper()).status_code == 200  # case-insensitive username


def test_unauthorized_access():
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/plants").status_code == 401
    assert client.get("/api/users", headers=hdr("garbage.token.value")).status_code == 401
    assert client.get("/api/users", headers={"Authorization": "Basic abc"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "x"}).status_code == 422


def test_logout_invalidates_token():
    client.post("/api/auth/register", json={"username": uname("out"), "email": mail("out"), "password": PW})
    t = token_for(uname("out"))
    assert client.get("/api/auth/me", headers=hdr(t)).status_code == 200
    assert client.post("/api/auth/logout", headers=hdr(t)).status_code == 204
    r = client.get("/api/auth/me", headers=hdr(t))
    assert r.status_code == 401 and "no longer valid" in r.json()["error"]["message"]
    assert client.post("/api/auth/logout", headers=hdr(t)).status_code == 401
    t2 = token_for(uname("out"))  # a fresh login still works
    assert client.get("/api/auth/me", headers=hdr(t2)).status_code == 200


# ---------------- RBAC ----------------
def test_viewer_is_forbidden_from_admin_endpoints_but_can_read_data():
    client.post("/api/auth/register", json={"username": uname("view"), "email": mail("view"), "password": PW})
    t = token_for(uname("view"))
    for path in ("/api/users", "/api/roles", "/api/audit-logs"):
        r = client.get(path, headers=hdr(t))
        assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden", path
    assert client.post("/api/users", headers=hdr(t), json={}).status_code in (403, 422)
    assert client.get("/api/plants", headers=hdr(t)).status_code == 200


def test_admin_user_management_lifecycle(admin):
    h = hdr(admin["token"])
    r = client.post("/api/users", headers=h, json={"username": uname("eng"), "email": mail("eng"),
                                                    "password": PW, "roles": ["ENGINEER"]})
    assert r.status_code == 201 and r.json()["roles"] == ["ENGINEER"]
    uid = r.json()["user_id"]
    assert client.post("/api/users", headers=h, json={"username": uname("eng"), "email": mail("eng9"),
                                                      "password": PW}).status_code == 409
    assert client.post("/api/users", headers=h, json={"username": uname("bad"), "email": mail("bad"),
                                                      "password": PW, "roles": ["NOPE"]}).status_code == 400
    # list + search + pagination
    lst = client.get(f"/api/users?search={uname('eng')}", headers=h).json()
    assert lst["total"] == 1 and lst["items"][0]["user_id"] == uid
    assert client.get("/api/users?page_size=1", headers=h).json()["page_size"] == 1
    assert client.get("/api/users/nope", headers=h).status_code == 404
    # engineer: allowed data perms, not admin perms
    et = token_for(uname("eng"))
    assert client.get("/api/auth/me", headers=hdr(et)).json()["permissions"].count("data:upload") == 1
    assert client.get("/api/users", headers=hdr(et)).status_code == 403
    # promote then demote via roles endpoint (existing token reflects new roles on next request)
    assert client.put(f"/api/users/{uid}/roles", headers=h, json={"roles": ["ADMIN"]}).json()["roles"] == ["ADMIN"]
    assert client.get("/api/users", headers=hdr(et)).status_code == 200
    assert client.put(f"/api/users/{uid}/roles", headers=h, json={"roles": ["VIEWER"]}).json()["roles"] == ["VIEWER"]
    assert client.get("/api/users", headers=hdr(et)).status_code == 403
    # update profile
    r = client.patch(f"/api/users/{uid}", headers=h, json={"full_name": "Sample Engineer"})
    assert r.json()["full_name"] == "Sample Engineer"
    # password change revokes sessions and the new password works
    assert client.patch(f"/api/users/{uid}", headers=h, json={"password": "New-Passw0rd-123"}).status_code == 200
    assert client.get("/api/auth/me", headers=hdr(et)).status_code == 401
    assert login(uname("eng"), PW).status_code == 401
    assert login(uname("eng"), "New-Passw0rd-123").status_code == 200
    # deactivate -> cannot log in, token dead
    t = login(uname("eng"), "New-Passw0rd-123").json()["access_token"]
    assert client.patch(f"/api/users/{uid}", headers=h, json={"is_active": False}).json()["is_active"] is False
    assert client.get("/api/auth/me", headers=hdr(t)).status_code == 401
    assert login(uname("eng"), "New-Passw0rd-123").status_code == 401
    # delete
    assert client.delete(f"/api/users/{uid}", headers=h).status_code == 204
    assert client.get(f"/api/users/{uid}", headers=h).status_code == 404


def test_cannot_delete_self_or_remove_last_admin(admin):
    h = hdr(admin["token"])
    me = admin["user"].user_id
    assert client.delete(f"/api/users/{me}", headers=h).status_code == 409
    if user_service._active_admin_ids() == {me}:  # only when this sample admin is the sole admin
        assert client.put(f"/api/users/{me}/roles", headers=h, json={"roles": ["VIEWER"]}).status_code == 409
        assert client.patch(f"/api/users/{me}", headers=h, json={"is_active": False}).status_code == 409
        assert client.get("/api/auth/me", headers=h).status_code == 200


def test_role_management(admin):
    h = hdr(admin["token"])
    name = f"SAMPLE_AUTH_{TAG.upper()}"
    r = client.post("/api/roles", headers=h, json={"role_name": name, "permissions": ["data:read"]})
    assert r.status_code == 201 and r.json()["is_builtin"] is False
    rid = r.json()["role_id"]
    assert client.post("/api/roles", headers=h, json={"role_name": name}).status_code == 409
    assert client.post("/api/roles", headers=h, json={"role_name": "lower"}).status_code == 422
    assert client.post("/api/roles", headers=h, json={"role_name": "SAMPLE_AUTH_X", "permissions": ["bogus"]}).status_code == 422
    r = client.patch(f"/api/roles/{rid}", headers=h, json={"permissions": ["data:read", "reports:generate"]})
    assert r.json()["permissions"] == ["data:read", "reports:generate"]
    assert "users:manage" in client.get("/api/roles/permissions", headers=h).json()
    # assigned role cannot be deleted
    u = client.post("/api/users", headers=h, json={"username": uname("rl"), "email": mail("rl"),
                                                    "password": PW, "roles": [name]}).json()
    assert client.delete(f"/api/roles/{rid}", headers=h).status_code == 409
    client.put(f"/api/users/{u['user_id']}/roles", headers=h, json={"roles": ["VIEWER"]})
    assert client.delete(f"/api/roles/{rid}", headers=h).status_code == 204
    assert client.get(f"/api/roles/{rid}", headers=h).status_code == 404
    # built-ins are protected
    roles = {x["role_name"]: x for x in client.get("/api/roles", headers=h).json()["items"]}
    assert client.delete(f"/api/roles/{roles['VIEWER']['role_id']}", headers=h).status_code == 409
    assert client.patch(f"/api/roles/{roles['ADMIN']['role_id']}", headers=h, json={"permissions": []}).status_code == 409


def test_audit_trail(admin):
    h = hdr(admin["token"])
    login(uname("ghost"), "Whatever-123")  # failed login by unknown user
    actor = admin["user"].username
    logs = client.get(f"/api/audit-logs?actor={actor}&page_size=200", headers=h).json()
    actions = {x["action"] for x in logs["items"]}
    assert {"LOGIN", "USER_CREATED", "USER_ROLES_CHANGED", "USER_UPDATED", "USER_DELETED", "ROLE_CREATED",
            "ROLE_UPDATED", "ROLE_DELETED"} <= actions
    failed = client.get(f"/api/audit-logs?action=login_failed&actor={uname('ghost')}", headers=h).json()
    assert failed["total"] >= 1 and failed["items"][0]["success"] is False
    denied = client.get("/api/audit-logs?action=ACCESS_DENIED&page_size=200", headers=h).json()
    assert any(x["actor_username"] == uname("view") for x in denied["items"])
    assert "logout" in {x["action"].lower() for x in client.get(f"/api/audit-logs?actor={uname('out')}", headers=h).json()["items"]}
    # secrets never audited
    pw_change = [x for x in logs["items"] if x["action"] == "USER_UPDATED" and x["details"] and "password" in x["details"]]
    assert pw_change and all(x["details"]["password"] == "***changed***" for x in pw_change)
    assert "New-Passw0rd" not in str(logs) and PW not in str(logs)
    # time filter + pagination
    assert client.get("/api/audit-logs?since=2999-01-01T00:00:00", headers=h).json()["total"] == 0
    assert len(client.get("/api/audit-logs?page_size=2", headers=h).json()["items"]) == 2
