"""Live upload tests (Snowflake stage + BRONZE). Files are SAMPLE data generated here; all uploads, users
and bronze rows created are removed afterwards."""
import io
import uuid
from datetime import datetime, timedelta

import openpyxl
import pytest
import xlwt
from fastapi.testclient import TestClient

from app.core import snowflake as sf
from app.core.config import get_settings
from app.main import app
from app.schemas.user import UserCreate
from app.services import user_service

pytestmark = pytest.mark.integration
client = TestClient(app, raise_server_exceptions=False)
PW = "Sample-Passw0rd!"
DB = "SPC_PLATFORM"
TAG = uuid.uuid4().hex[:8]
created_uploads: list[str] = []


def uname(s):
    return f"sample_auth_{s}_{TAG}"


def hdr(t):
    return {"Authorization": f"Bearer {t}"}


def token(username):
    r = client.post("/api/auth/login", json={"username": username, "password": PW})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def eng():
    u = user_service.create_user(UserCreate(username=uname("eng"), email=f"{uname('eng')}@example.com",
                                            password=PW, roles=["ENGINEER"]), action="SAMPLE_SETUP")
    return hdr(token(u.username))


@pytest.fixture(scope="module")
def viewer():
    u = user_service.create_user(UserCreate(username=uname("view"), email=f"{uname('view')}@example.com",
                                            password=PW, roles=["VIEWER"]), action="SAMPLE_SETUP")
    return hdr(token(u.username))


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    yield
    for uid in created_uploads:
        for t in ("RAW_SENSOR_DATA", "RAW_PRODUCTION_DATA"):
            sf.execute(f"DELETE FROM {DB}.BRONZE.{t} WHERE UPLOAD_ID = %s", (uid,))
        sf.execute(f"REMOVE @{DB}.BRONZE.UPLOAD_STAGE/{uid}/")
        sf.execute(f"DELETE FROM {DB}.METADATA.DATASET_UPLOADS WHERE UPLOAD_ID = %s", (uid,))
    M = f"{DB}.METADATA"
    for (uid,) in sf.execute(f"SELECT USER_ID FROM {M}.USERS WHERE USERNAME LIKE 'sample_auth_%'"):
        sf.execute(f"DELETE FROM {M}.USER_SESSIONS WHERE USER_ID = %s", (uid,))
        sf.execute(f"DELETE FROM {M}.USER_ROLES WHERE USER_ID = %s", (uid,))
    sf.execute(f"DELETE FROM {M}.USERS WHERE USERNAME LIKE 'sample_auth_%'")
    sf.execute(f"DELETE FROM {M}.AUDIT_LOGS WHERE ACTOR_USERNAME LIKE 'sample_auth_%'")
    sf.close_connection()


# ---------------------------------------------------------------- sample files
def long_csv(n=30, salt=""):
    base = datetime(2024, 1, 1)
    lines = ["timestamp,parameter,value,machine"]
    for i in range(n):
        lines.append(f"{(base + timedelta(minutes=i)).strftime('%Y-%m-%d %H:%M:%S')},TEMPERATURE,{70 + i * 0.1:.1f},M1{salt}")
    lines.append(lines[1])                       # exact duplicate record
    lines.append(",,,")                          # empty row
    lines.append("2024-01-01 01:00:00,PRESSURE,,M1")  # missing value
    return ("\n".join(lines) + "\n").encode()


def post(eng, content, name, dataset_type="SENSOR", **extra):
    r = client.post("/api/uploads", headers=eng, files={"file": (name, content)},
                    data={"dataset_type": dataset_type, **extra})
    if r.status_code == 201:
        created_uploads.append(r.json()["upload_id"])
    return r


def to_bronze(eng, content, name, mapping, dataset_type="SENSOR", **extra):
    up = post(eng, content, name, dataset_type, **extra)
    assert up.status_code == 201, up.text
    uid = up.json()["upload_id"]
    m = client.put(f"/api/uploads/{uid}/mapping", headers=eng, json={"mapping": mapping})
    assert m.status_code == 200, m.text
    ld = client.post(f"/api/uploads/{uid}/load", headers=eng)
    assert ld.status_code == 200, ld.text
    return ld.json()


LONG_MAP = {"timestamp": "timestamp", "parameter": "parameter", "value": "value", "asset": "machine"}


# ---------------------------------------------------------------- happy paths
def test_csv_end_to_end_verified_in_snowflake(eng):
    content = long_csv(30, salt=TAG)
    up = post(eng, content, "sample_sensor.csv").json()
    uid = up["upload_id"]
    assert up["status"] == "STAGED" and up["rows_received"] == 32 and up["duplicate_rows"] == 1
    assert up["empty_rows_skipped"] == 1 and up["dataset_type"] == "SENSOR" and up["file_type"] == "CSV"
    assert up["preview"]["suggested_mapping"] == {"timestamp": "timestamp", "parameter": "parameter",
                                                  "value": "value", "asset": "machine"}
    assert len(up["preview"]["rows"]) == 20 and up["preview"]["columns"][0] == "timestamp"
    # the part file really is in the internal stage
    assert len(sf.execute(f"LIST @{DB}.BRONZE.UPLOAD_STAGE/{uid}/")) == 1
    # cannot load before mapping
    assert client.post(f"/api/uploads/{uid}/load", headers=eng).status_code == 409
    m = client.put(f"/api/uploads/{uid}/mapping", headers=eng, json={"mapping": LONG_MAP})
    assert m.json()["status"] == "MAPPED" and m.json()["column_mapping"]["asset"] == "machine"
    ld = client.post(f"/api/uploads/{uid}/load", headers=eng).json()
    assert ld["status"] == "LOADED_BRONZE" and ld["rows_loaded"] == 32
    # --- verify in Snowflake
    assert sf.execute(f"SELECT COUNT(*) FROM {DB}.BRONZE.RAW_SENSOR_DATA WHERE UPLOAD_ID = %s", (uid,))[0][0] == 32
    first = sf.execute(f"SELECT SOURCE_ROW, RAW_RECORD:timestamp::string, RAW_RECORD:value::string, RAW_RECORD:machine::string "
                       f"FROM {DB}.BRONZE.RAW_SENSOR_DATA WHERE UPLOAD_ID = %s ORDER BY SOURCE_ROW LIMIT 1", (uid,))[0]
    assert first == (2, "2024-01-01 00:00:00", "70.0", f"M1{TAG}")
    miss = sf.execute(f"SELECT RAW_RECORD:value::string FROM {DB}.BRONZE.RAW_SENSOR_DATA WHERE UPLOAD_ID = %s "
                      "AND RAW_RECORD:parameter::string = 'PRESSURE'", (uid,))
    assert miss == [("",)]                                  # missing value kept as received
    dup = sf.execute(f"SELECT COUNT(*) FROM (SELECT RAW_RECORD FROM {DB}.BRONZE.RAW_SENSOR_DATA WHERE UPLOAD_ID = %s "
                     "GROUP BY RAW_RECORD HAVING COUNT(*) > 1)", (uid,))[0][0]
    assert dup == 1                                         # duplicate record preserved in BRONZE
    assert sf.execute(f"LIST @{DB}.BRONZE.UPLOAD_STAGE/{uid}/") == []   # PURGE removed the staged part
    # cannot load twice
    assert client.post(f"/api/uploads/{uid}/load", headers=eng).status_code == 409
    # history
    h = client.get("/api/uploads?status=loaded_bronze&page_size=200", headers=eng).json()
    assert uid in {i["upload_id"] for i in h["items"]}
    assert client.get(f"/api/uploads/{uid}", headers=eng).json()["rows_loaded"] == 32


def test_excel_xlsx_wide_layout(eng):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Time", "Temperature", "Pressure", "Speed", "Flow"])
    for i in range(12):
        ws.append([datetime(2024, 2, 1, 0, i), 70 + i * 0.5, 1.2 + i * 0.01, 1500 + i, 10 + i * 0.1 + int(TAG[:2], 16) / 1000])
    buf = io.BytesIO()
    wb.save(buf)
    up = post(eng, buf.getvalue(), "sample_wide.xlsx").json()
    assert up["file_type"] == "XLSX" and up["rows_received"] == 12
    sug = up["preview"]["suggested_mapping"]
    assert sug["timestamp"] == "Time" and sug["value_columns"] == ["Temperature", "Pressure", "Speed", "Flow"]
    uid = up["upload_id"]
    client.put(f"/api/uploads/{uid}/mapping", headers=eng, json={"mapping": sug})
    assert client.post(f"/api/uploads/{uid}/load", headers=eng).json()["rows_loaded"] == 12
    r = sf.execute(f"SELECT RAW_RECORD:Time::string, RAW_RECORD:Temperature::string, RAW_RECORD:Speed::string "
                   f"FROM {DB}.BRONZE.RAW_SENSOR_DATA WHERE UPLOAD_ID = %s AND SOURCE_ROW = 3", (uid,))[0]
    assert r == ("2024-02-01 00:01:00", "70.5", "1501")


def test_excel_xls_production_dataset(eng):
    wb = xlwt.Workbook()
    ws = wb.add_sheet("Prod")
    for c, h in enumerate(["Date", "Lot", "Sample Size", "Defectives"]):
        ws.write(0, c, h)
    for i in range(1, 8):
        ws.write(i, 0, f"2024-03-0{i}")
        ws.write(i, 1, f"L{i}-{TAG}")
        ws.write(i, 2, 50)
        ws.write(i, 3, i % 3)
    buf = io.BytesIO()
    wb.save(buf)
    up = post(eng, buf.getvalue(), "sample_prod.xls", "PRODUCTION").json()
    assert up["file_type"] == "XLS" and up["rows_received"] == 7
    uid = up["upload_id"]
    sug = up["preview"]["suggested_mapping"]
    assert sug == {"timestamp": "Date", "batch": "Lot", "sample_size": "Sample Size", "defective_count": "Defectives"}
    client.put(f"/api/uploads/{uid}/mapping", headers=eng, json={"mapping": sug})
    assert client.post(f"/api/uploads/{uid}/load", headers=eng).json()["rows_loaded"] == 7
    assert sf.execute(f"SELECT COUNT(*) FROM {DB}.BRONZE.RAW_PRODUCTION_DATA WHERE UPLOAD_ID = %s", (uid,))[0][0] == 7
    assert sf.execute(f"SELECT COUNT(*) FROM {DB}.BRONZE.RAW_SENSOR_DATA WHERE UPLOAD_ID = %s", (uid,))[0][0] == 0


def test_larger_multi_part_file(eng, monkeypatch):
    monkeypatch.setenv("UPLOAD_PART_ROWS", "400")
    get_settings.cache_clear()
    try:
        n = 1500
        body = "ts,param,val\n" + "".join(f"2024-01-01 00:{i // 60 % 60:02d}:{i % 60:02d},FLOW,{i}.{TAG[:3].replace('e', '1')}\n" for i in range(n))
        up = post(eng, body.encode(), "sample_big.csv").json()
        uid = up["upload_id"]
        assert up["rows_received"] == n
        assert len(sf.execute(f"LIST @{DB}.BRONZE.UPLOAD_STAGE/{uid}/")) == 4      # 400+400+400+300
        client.put(f"/api/uploads/{uid}/mapping", headers=eng,
                   json={"mapping": {"timestamp": "ts", "parameter": "param", "value": "val"}})
        assert client.post(f"/api/uploads/{uid}/load", headers=eng).json()["rows_loaded"] == n
        assert sf.execute(f"SELECT COUNT(DISTINCT SOURCE_ROW) FROM {DB}.BRONZE.RAW_SENSOR_DATA WHERE UPLOAD_ID = %s", (uid,))[0][0] == n
    finally:
        get_settings.cache_clear()


# ---------------------------------------------------------------- invalid input
@pytest.mark.parametrize("name,content,status,code", [
    ("notes.txt", b"a,b\n1,2\n", 415, "unsupported_media_type"),
    ("empty.csv", b"", 422, "unprocessable"),
    ("header_only.csv", b"a,b\n", 422, "unprocessable"),
    ("dupcols.csv", b"a,b,a\n1,2,3\n", 422, "unprocessable"),
    ("fake.xlsx", b"this is not a workbook", 422, "unprocessable"),
    ("binary.csv", b"a,b\n\x00\x01\x02\x03\n", 422, "unprocessable"),
])
def test_invalid_files_rejected(eng, name, content, status, code):
    r = post(eng, content, name)
    assert r.status_code == status and r.json()["error"]["code"] == code, r.text
    assert "Traceback" not in r.text


def test_oversize_file_rejected(eng, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "0")
    get_settings.cache_clear()
    try:
        r = post(eng, b"a,b\n1,2\n", "tiny.csv")
        assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"
    finally:
        get_settings.cache_clear()


def test_missing_columns_and_bad_mappings(eng):
    uid = post(eng, long_csv(5, salt="mm" + TAG[:4]), "sample_map.csv").json()["upload_id"]
    put = lambda mp: client.put(f"/api/uploads/{uid}/mapping", headers=eng, json={"mapping": mp})
    r = put({**LONG_MAP, "value": "no_such_column", "batch": "also_missing"})
    err = r.json()["error"]
    assert r.status_code == 422 and set(err["details"]["missing_columns"]) == {"no_such_column", "also_missing"}
    assert "timestamp" in err["details"]["available_columns"]
    assert put({"parameter": "parameter", "value": "value"}).status_code == 422                  # timestamp missing
    assert put({"timestamp": "timestamp"}).status_code == 422                                     # no value layout
    assert put({"timestamp": "timestamp", "parameter": "parameter"}).status_code == 422           # half of long layout
    assert put({**LONG_MAP, "tag": "value"}).status_code == 422                                   # column mapped twice
    assert put({**LONG_MAP, "surprise": "x"}).status_code == 422                                  # unknown target
    assert client.get(f"/api/uploads/{uid}", headers=eng).json()["status"] == "STAGED"             # nothing changed


def test_duplicate_file_detection(eng):
    content = long_csv(6, salt="dup" + TAG[:4])
    first = post(eng, content, "sample_dup.csv")
    assert first.status_code == 201
    second = post(eng, content, "sample_dup_again.csv")
    assert second.status_code == 409 and second.json()["error"]["details"]["existing_upload_id"] == first.json()["upload_id"]
    third = post(eng, content, "sample_dup_again.csv", allow_duplicate="true")
    assert third.status_code == 201 and third.json()["upload_id"] != first.json()["upload_id"]
    # same bytes as a different dataset type is a different upload
    assert post(eng, content, "sample_dup.csv", "PRODUCTION").status_code == 201


def test_failed_load_rolls_back(eng):
    uid = post(eng, long_csv(4, salt="rb" + TAG[:4]), "sample_rb.csv").json()["upload_id"]
    client.put(f"/api/uploads/{uid}/mapping", headers=eng, json={"mapping": LONG_MAP})
    sf.execute(f"REMOVE @{DB}.BRONZE.UPLOAD_STAGE/{uid}/")           # simulate lost staged file
    r = client.post(f"/api/uploads/{uid}/load", headers=eng)
    assert r.status_code == 502 and r.json()["error"]["code"] == "load_failed"
    up = client.get(f"/api/uploads/{uid}", headers=eng).json()
    assert up["status"] == "FAILED" and "BRONZE load failed" in up["error_message"]
    assert sf.execute(f"SELECT COUNT(*) FROM {DB}.BRONZE.RAW_SENSOR_DATA WHERE UPLOAD_ID = %s", (uid,))[0][0] == 0


# ---------------------------------------------------------------- access control, lookup, delete
def test_permissions_and_not_found(eng, viewer):
    r = client.post("/api/uploads", headers=viewer, files={"file": ("a.csv", b"a\n1\n")}, data={"dataset_type": "SENSOR"})
    assert r.status_code == 403
    assert client.get("/api/uploads", headers=viewer).status_code == 200            # viewers may read history
    assert client.post("/api/uploads", files={"file": ("a.csv", b"a\n1\n")}, data={"dataset_type": "SENSOR"}).status_code == 401
    assert client.post("/api/uploads", headers=eng, files={"file": ("a.csv", b"a\n1\n")}).status_code == 422   # no type
    assert client.post("/api/uploads", headers=eng, files={"file": ("a.csv", b"a\n1\n")},
                       data={"dataset_type": "BOGUS"}).status_code == 422
    assert client.get("/api/uploads/not-a-uuid", headers=eng).status_code == 404
    assert client.get(f"/api/uploads/{uuid.uuid4()}", headers=eng).status_code == 404
    assert client.delete(f"/api/uploads/{uuid.uuid4()}", headers=eng).status_code == 404


def test_delete_removes_bronze_and_stage(eng):
    lj = to_bronze(eng, long_csv(5, salt="del" + TAG[:4]), "sample_del.csv", LONG_MAP)
    uid = lj["upload_id"]
    assert client.delete(f"/api/uploads/{uid}", headers=eng).status_code == 204
    assert client.get(f"/api/uploads/{uid}", headers=eng).status_code == 404
    assert sf.execute(f"SELECT COUNT(*) FROM {DB}.BRONZE.RAW_SENSOR_DATA WHERE UPLOAD_ID = %s", (uid,))[0][0] == 0
    # and an un-loaded staged upload can be deleted too (stage cleaned)
    u2 = post(eng, long_csv(5, salt="del2" + TAG[:3]), "sample_del2.csv").json()["upload_id"]
    assert len(sf.execute(f"LIST @{DB}.BRONZE.UPLOAD_STAGE/{u2}/")) == 1
    assert client.delete(f"/api/uploads/{u2}", headers=eng).status_code == 204
    assert sf.execute(f"LIST @{DB}.BRONZE.UPLOAD_STAGE/{u2}/") == []
