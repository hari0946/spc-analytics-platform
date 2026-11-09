"""Phase 9: chart series from GOLD. Verifies series values against Snowflake rows, aggregation, date filters, empty data."""
import uuid
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core import snowflake as sf
from app.main import app
from app.schemas.user import UserCreate
from app.services import user_service

pytestmark = pytest.mark.integration
client = TestClient(app, raise_server_exceptions=False)
PW = "Sample-Passw0rd!"
DB = "SPC_PLATFORM"
TAG = uuid.uuid4().hex[:8]
created: list[str] = []
N = 600


def csv_data():
    base = datetime(2024, 6, 1)
    lines = ["ts,param,val,asset"]
    for i in range(N):                                            # 600 one-minute readings per parameter / asset
        t = (base + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"{t},TEMPERATURE,{70 + (i % 10)},M1")       # saw-tooth 70..79
        lines.append(f"{t},PRESSURE,{1 + i / 1000:.3f},M1")
    lines.append("2024-06-01 00:00:00,SPEED,1500,M2")             # a one-point series
    lines.append("2024-06-01 00:00:00,FLOW,12.5,M2")
    return "\n".join(lines) + "\n"


MAP = {"timestamp": "ts", "parameter": "param", "value": "val", "asset": "asset"}


def hdr(t):
    return {"Authorization": f"Bearer {t}"}


@pytest.fixture(scope="module")
def eng():
    name = f"sample_auth_eng_{TAG}"
    user_service.create_user(UserCreate(username=name, email=f"{name}@example.com", password=PW, roles=["ENGINEER"]),
                             action="SAMPLE_SETUP")
    return hdr(client.post("/api/auth/login", json={"username": name, "password": PW}).json()["access_token"])


@pytest.fixture(scope="module")
def uid(eng):
    r = client.post("/api/uploads", headers=eng, files={"file": ("sample.csv", csv_data().encode())},
                    data={"dataset_type": "SENSOR", "allow_duplicate": "true"})
    assert r.status_code == 201, r.text
    u = r.json()["upload_id"]
    created.append(u)
    client.put(f"/api/uploads/{u}/mapping", headers=eng, json={"mapping": MAP})
    assert client.post(f"/api/uploads/{u}/load", headers=eng).status_code == 200
    assert client.post(f"/api/uploads/{u}/process", headers=eng).status_code == 202
    assert client.get(f"/api/uploads/{u}", headers=eng).json()["status"] == "COMPLETED"
    return u


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    yield
    for u in created:
        for sch, t in (("SILVER", "CLEAN_SENSOR_DATA"), ("SILVER", "REJECTED_RECORDS"), ("GOLD", "GOLD_SENSOR_DATA"),
                       ("GOLD", "GOLD_PROCESS_DATA"), ("BRONZE", "RAW_SENSOR_DATA")):
            sf.execute(f"DELETE FROM {DB}.{sch}.{t} WHERE UPLOAD_ID = %s", (u,))
        sf.execute(f"REMOVE @{DB}.BRONZE.UPLOAD_STAGE/{u}/")
        sf.execute(f"DELETE FROM {DB}.METADATA.DATASET_UPLOADS WHERE UPLOAD_ID = %s", (u,))
    M = f"{DB}.METADATA"
    for (x,) in sf.execute(f"SELECT USER_ID FROM {M}.USERS WHERE USERNAME LIKE 'sample_auth_%'"):
        sf.execute(f"DELETE FROM {M}.USER_SESSIONS WHERE USER_ID = %s", (x,))
        sf.execute(f"DELETE FROM {M}.USER_ROLES WHERE USER_ID = %s", (x,))
    sf.execute(f"DELETE FROM {M}.USERS WHERE USERNAME LIKE 'sample_auth_%'")
    sf.execute(f"DELETE FROM {M}.AUDIT_LOGS WHERE ACTOR_USERNAME LIKE 'sample_auth_%'")
    sf.close_connection()


def series(eng, uid, **q):
    r = client.get("/api/data/sensor/series", headers=eng, params={"upload_id": uid, **q})
    assert r.status_code == 200, r.text
    return {s["parameter"]: s for s in r.json()["series"]}


def test_raw_series_match_snowflake_exactly(eng, uid):
    s = series(eng, uid, max_points=10000)
    assert set(s) == {"TEMPERATURE", "PRESSURE", "SPEED", "FLOW"}
    for name, ser in s.items():
        assert ser["aggregated"] is False and ser["bucket_seconds"] is None
        sql = sf.execute(f"SELECT TO_CHAR(MEASURED_AT, 'YYYY-MM-DD HH24:MI:SS'), VALUE FROM {DB}.GOLD.GOLD_SENSOR_DATA "
                         "WHERE UPLOAD_ID = %s AND PARAMETER = %s ORDER BY MEASURED_AT", (uid, name))
        api = [(p["t"].replace("T", " ")[:19], p["v"]) for p in ser["points"]]
        assert api == [(t, float(v)) for t, v in sql]                                    # same points, same order
        assert ser["n_total"] == len(sql) and all(p["n"] == 1 and p["min"] == p["max"] == p["v"] for p in ser["points"])
    assert len(s["TEMPERATURE"]["points"]) == N and s["SPEED"]["points"][0]["v"] == 1500.0 and s["FLOW"]["points"][0]["v"] == 12.5


def test_aggregation_preserves_totals_and_extremes(eng, uid):
    s = series(eng, uid, max_points=100, parameter="TEMPERATURE")["TEMPERATURE"]
    pts = s["points"]
    assert s["aggregated"] is True and s["n_total"] == N and s["bucket_seconds"] == 360      # 599 min span / 100 -> 6 min buckets
    assert 90 <= len(pts) <= 101 and sum(p["n"] for p in pts) == N                          # every reading is in exactly one bucket
    assert min(p["min"] for p in pts) == 70 and max(p["max"] for p in pts) == 79            # extremes survive aggregation
    mean = sum(p["v"] * p["n"] for p in pts) / N
    sql_mean = float(sf.execute(f"SELECT AVG(VALUE) FROM {DB}.GOLD.GOLD_SENSOR_DATA WHERE UPLOAD_ID = %s AND PARAMETER = 'TEMPERATURE'", (uid,))[0][0])
    assert abs(mean - sql_mean) < 1e-9
    times = [p["t"] for p in pts]
    assert times == sorted(times)
    # bucket 0 = first 6 readings 70..75 -> avg 72.5
    assert pts[0]["n"] == 6 and pts[0]["v"] == 72.5 and (pts[0]["min"], pts[0]["max"]) == (70, 75)
    # series shorter than max_points stay raw even when others are aggregated
    mixed = series(eng, uid, max_points=100)
    assert mixed["TEMPERATURE"]["aggregated"] and not mixed["SPEED"]["aggregated"] and len(mixed["SPEED"]["points"]) == 1


def test_date_filtering(eng, uid):
    s = series(eng, uid, max_points=10000, parameter="TEMPERATURE", start="2024-06-01T01:00:00", end="2024-06-01T01:09:00")["TEMPERATURE"]
    assert [p["t"][11:16] for p in s["points"]] == [f"01:{m:02d}" for m in range(10)] and s["n_total"] == 10
    assert [p["v"] for p in s["points"]] == [70 + ((60 + m) % 10) for m in range(10)]
    last = series(eng, uid, max_points=10000, parameter="PRESSURE", start="2024-06-01T09:59:00")["PRESSURE"]     # last minute of data
    assert len(last["points"]) == 1 and last["points"][0]["v"] == 1.599
    assert series(eng, uid, start="2025-01-01") == {}                                  # nothing in range -> empty, not an error
    assert series(eng, uid, parameter="SPEED", asset="M1") == {}                       # contradictory filters -> empty


def test_filters_select_series(eng, uid):
    assert set(series(eng, uid, asset="M2")) == {"SPEED", "FLOW"}
    assert set(series(eng, uid, parameter=["temperature", "flow"])) == {"TEMPERATURE", "FLOW"}


def test_validation_and_limits(eng, uid):
    g = lambda **p: client.get("/api/data/sensor/series", headers=eng, params={"upload_id": uid, **p})
    assert g(max_points=5).status_code == 422 and g(max_points=10001).status_code == 422
    assert g(start="bad").status_code == 422
    assert client.get("/api/data/sensor/series").status_code == 401
    assert client.get("/api/data/sensor/series", headers=eng, params={"upload_id": str(uuid.uuid4())}).status_code == 404


def test_empty_dataset_state(eng):
    r = client.get("/api/data/sensor/series", headers=eng, params={"plant": f"NO_SUCH_{TAG}"})
    assert r.status_code == 200 and r.json() == {"max_points": 2000, "series": []}
