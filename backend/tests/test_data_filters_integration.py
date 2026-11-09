"""Live tests of historical-data filtering against Snowflake GOLD tables. SAMPLE data, hand-counted, removed afterwards."""
import uuid
from urllib.parse import urlencode

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

SENSOR_A = """ts,param,val,plant,line,asset,tag,batch
2024-05-01 08:00:00,TEMPERATURE,70,P1,L1,M1,T1,B1
2024-05-01 12:00:00,TEMPERATURE,71,P1,L1,M1,T1,B1
2024-05-02 09:00:00,TEMPERATURE,72,P1,L1,M2,T2,B2
2024-05-02 10:00:00,PRESSURE,1.1,P1,L2,M3,T3,B2
2024-05-03 23:30:00,PRESSURE,1.2,P2,L3,M4,T4,B3
2024-05-04 00:00:00,FLOW,10,P2,L3,M4,T5,B3
"""
SENSOR_B = """ts,param,val,plant,line,asset,tag,batch
2024-05-02 09:30:00,TEMPERATURE,80,P1,L1,M1,T1,B9
2024-05-05 10:00:00,TEMPERATURE,81,P3,L9,M9,T9,B9
"""
PROD_C = """date,plant,line,asset,lot,n,def
2024-05-01,P1,L1,M1,LOT1,50,1
2024-05-02,P1,L1,M2,LOT2,50,2
2024-05-03,P2,L3,M4,LOT3,50,3
"""
SENS_MAP = {"timestamp": "ts", "parameter": "param", "value": "val", "plant": "plant", "line": "line",
            "asset": "asset", "tag": "tag", "batch": "batch"}
PROD_MAP = {"timestamp": "date", "plant": "plant", "line": "line", "asset": "asset", "batch": "lot",
            "sample_size": "n", "defective_count": "def"}


def hdr(t):
    return {"Authorization": f"Bearer {t}"}


def make_user(role):
    name = f"sample_auth_{role.lower()}_{TAG}"
    user_service.create_user(UserCreate(username=name, email=f"{name}@example.com", password=PW, roles=[role]),
                             action="SAMPLE_SETUP")
    return hdr(client.post("/api/auth/login", json={"username": name, "password": PW}).json()["access_token"])


def ingest(eng, csv, mapping, kind, process=True):
    r = client.post("/api/uploads", headers=eng, files={"file": ("sample.csv", (csv + f"\n").encode())},
                    data={"dataset_type": kind, "allow_duplicate": "true"})
    assert r.status_code == 201, r.text
    uid = r.json()["upload_id"]
    created.append(uid)
    assert client.put(f"/api/uploads/{uid}/mapping", headers=eng, json={"mapping": mapping}).status_code == 200
    assert client.post(f"/api/uploads/{uid}/load", headers=eng).status_code == 200
    if process:
        assert client.post(f"/api/uploads/{uid}/process", headers=eng).status_code == 202
        assert client.get(f"/api/uploads/{uid}", headers=eng).json()["status"] == "COMPLETED"
    return uid


@pytest.fixture(scope="module")
def eng():
    return make_user("ENGINEER")


@pytest.fixture(scope="module")
def viewer():
    return make_user("VIEWER")


@pytest.fixture(scope="module")
def ds(eng):
    return {"A": ingest(eng, SENSOR_A, SENS_MAP, "SENSOR"), "B": ingest(eng, SENSOR_B, SENS_MAP, "SENSOR"),
            "C": ingest(eng, PROD_C, PROD_MAP, "PRODUCTION")}


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    yield
    for uid in created:
        for sch, t in (("SILVER", "CLEAN_SENSOR_DATA"), ("SILVER", "VALIDATED_PRODUCTION_DATA"), ("SILVER", "REJECTED_RECORDS"),
                       ("GOLD", "GOLD_SENSOR_DATA"), ("GOLD", "GOLD_PRODUCTION_DATA"), ("GOLD", "GOLD_PROCESS_DATA"),
                       ("BRONZE", "RAW_SENSOR_DATA"), ("BRONZE", "RAW_PRODUCTION_DATA")):
            sf.execute(f"DELETE FROM {DB}.{sch}.{t} WHERE UPLOAD_ID = %s", (uid,))
        sf.execute(f"REMOVE @{DB}.BRONZE.UPLOAD_STAGE/{uid}/")
        sf.execute(f"DELETE FROM {DB}.METADATA.DATASET_UPLOADS WHERE UPLOAD_ID = %s", (uid,))
    M = f"{DB}.METADATA"
    for (uid,) in sf.execute(f"SELECT USER_ID FROM {M}.USERS WHERE USERNAME LIKE 'sample_auth_%'"):
        sf.execute(f"DELETE FROM {M}.USER_SESSIONS WHERE USER_ID = %s", (uid,))
        sf.execute(f"DELETE FROM {M}.USER_ROLES WHERE USER_ID = %s", (uid,))
    sf.execute(f"DELETE FROM {M}.USERS WHERE USERNAME LIKE 'sample_auth_%'")
    sf.execute(f"DELETE FROM {M}.AUDIT_LOGS WHERE ACTOR_USERNAME LIKE 'sample_auth_%'")
    sf.close_connection()


def get(eng, path, **params):
    qs = urlencode([(k, x) for k, v in params.items() for x in (v if isinstance(v, list) else [v])])
    return client.get(f"/api/data/{path}?{qs}", headers=eng)


def rows(eng, ids, path="sensor", **flt):
    r = get(eng, path, upload_id=ids, page_size=1000, **flt)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == len(body["items"])
    return body["items"]


def stamps(items):
    return [i["measured_at"][:16].replace("T", " ") for i in items]


# ---------------------------------------------------------------- every single filter (row contents, not just counts)
def test_baseline_and_dataset_filter(eng, ds):
    a, b = ds["A"], ds["B"]
    assert len(rows(eng, [a])) == 6 and len(rows(eng, [b])) == 2 and len(rows(eng, [a, b])) == 8
    items = rows(eng, [a])
    assert stamps(items) == sorted(stamps(items))                                    # ordered by time
    assert items[0] == {"upload_id": a, "plant_code": "P1", "line_code": "L1", "asset_code": "M1", "tag_name": "T1",
                        "parameter": "TEMPERATURE", "measured_at": items[0]["measured_at"], "value": 70.0,
                        "batch_id": "B1", "subgroup_id": 1}


@pytest.mark.parametrize("flt,key,expected_n", [
    ({"plant": "P1"}, "plant_code", 5), ({"plant": "P2"}, "plant_code", 2), ({"plant": "P3"}, "plant_code", 1),
    ({"line": "L1"}, "line_code", 4), ({"line": "L3"}, "line_code", 2),
    ({"asset": "M1"}, "asset_code", 3), ({"asset": "M4"}, "asset_code", 2),
    ({"tag": "T1"}, "tag_name", 3), ({"tag": "T5"}, "tag_name", 1),
    ({"parameter": "TEMPERATURE"}, "parameter", 5), ({"parameter": "pressure"}, "parameter", 2),   # case-insensitive
    ({"batch": "B2"}, "batch_id", 2), ({"batch": "B9"}, "batch_id", 2),
])
def test_each_filter_individually(eng, ds, flt, key, expected_n):
    items = rows(eng, [ds["A"], ds["B"]], **flt)
    assert len(items) == expected_n
    want = {v.upper() if key == "parameter" else v for v in flt.values()}
    assert {i[key] for i in items} == want                                            # every returned row satisfies the filter


def test_multi_value_filters_are_or_within_a_filter(eng, ds):
    a = [ds["A"]]
    assert len(rows(eng, a, plant=["P1", "P2"])) == 6
    assert {i["asset_code"] for i in rows(eng, a, asset=["M1", "M3"])} == {"M1", "M3"}
    assert len(rows(eng, a, parameter=["TEMPERATURE", "FLOW"])) == 4
    assert len(rows(eng, a, plant=["P1", "P1"])) == 4                                   # duplicate values harmless


def test_combined_filters_are_and(eng, ds):
    ab = [ds["A"], ds["B"]]
    assert len(rows(eng, ab, plant="P1", parameter="TEMPERATURE")) == 4
    assert len(rows(eng, ab, plant="P1", parameter="TEMPERATURE", asset="M1")) == 3
    assert len(rows(eng, ab, plant="P1", parameter="TEMPERATURE", asset="M1", start="2024-05-02")) == 1     # only B's row
    assert len(rows(eng, ab, plant="P1", line="L2", asset="M3", tag="T3", parameter="PRESSURE", batch="B2")) == 1
    assert rows(eng, ab, plant="P2", line="L1") == []                                   # contradictory combo -> empty, not error
    assert rows(eng, ab, plant="P1", asset="M4") == []


# ---------------------------------------------------------------- date ranges
def test_date_range_semantics(eng, ds):
    a, b = ds["A"], ds["B"]
    assert stamps(rows(eng, [a, b], start="2024-05-02", end="2024-05-02")) == ["2024-05-02 09:00", "2024-05-02 09:30", "2024-05-02 10:00"]
    assert len(rows(eng, [a], start="2024-05-02")) == 4                                  # open-ended
    assert len(rows(eng, [a], end="2024-05-01")) == 2                                    # date-only end = whole day
    assert stamps(rows(eng, [a], start="2024-05-03T23:30:00", end="2024-05-04T00:00:00")) == ["2024-05-03 23:30", "2024-05-04 00:00"]
    assert stamps(rows(eng, [a], end="2024-05-01T08:00:00")) == ["2024-05-01 08:00"]    # boundaries inclusive
    assert rows(eng, [a], start="2024-05-01T08:00:01", end="2024-05-01T11:59:59") == []
    assert stamps(rows(eng, [a], end="2024-05-01T10:00:00+02:00")) == ["2024-05-01 08:00"]   # offset converted to UTC
    assert rows(eng, [a], start="2030-01-01") == [] and rows(eng, [a], end="2000-01-01") == []
    assert get(eng, "sensor", upload_id=a, start="2024-05-03", end="2024-05-02").status_code == 422
    assert get(eng, "sensor", upload_id=a, start="not-a-date").status_code == 422


# ---------------------------------------------------------------- pagination
def test_pagination_is_stable_and_complete(eng, ds):
    a = ds["A"]
    seen = []
    for p in (1, 2, 3):
        body = get(eng, "sensor", upload_id=a, page=p, page_size=2).json()
        assert body["total"] == 6 and body["page"] == p and len(body["items"]) == 2
        seen += stamps(body["items"])
    assert seen == stamps(rows(eng, [a])) and len(set(seen)) == 6
    assert get(eng, "sensor", upload_id=a, page=4, page_size=2).json()["items"] == []
    assert get(eng, "sensor", upload_id=a, page_size=1001).status_code == 422
    assert get(eng, "sensor", upload_id=a, page=0).status_code == 422


# ---------------------------------------------------------------- facets / options
def vals(opts):
    return {o["value"]: o["count"] for o in opts}


def test_filter_options_are_faceted(eng, ds):
    a = ds["A"]
    o = get(eng, "sensor/options", upload_id=a).json()
    assert vals(o["plants"]) == {"P1": 4, "P2": 2} and vals(o["lines"]) == {"L1": 3, "L2": 1, "L3": 2}
    assert vals(o["assets"]) == {"M1": 2, "M2": 1, "M3": 1, "M4": 2} and set(vals(o["parameters"])) == {"FLOW", "PRESSURE", "TEMPERATURE"}
    assert vals(o["batches"]) == {"B1": 2, "B2": 2, "B3": 2} and vals(o["tags"]) == {"T1": 2, "T2": 1, "T3": 1, "T4": 1, "T5": 1}
    assert vals(o["datasets"]) == {a: 6, ds["B"]: 2} and o["first"].startswith("2024-05-01T08:00") and o["last"].startswith("2024-05-04T00:00")
    # choosing a plant narrows the OTHER dimensions but keeps all plants selectable
    o = get(eng, "sensor/options", upload_id=a, plant="P1").json()
    assert vals(o["plants"]) == {"P1": 4, "P2": 2} and vals(o["lines"]) == {"L1": 3, "L2": 1}
    assert set(vals(o["assets"])) == {"M1", "M2", "M3"} and set(vals(o["parameters"])) == {"PRESSURE", "TEMPERATURE"}
    o = get(eng, "sensor/options", upload_id=a, asset="M4").json()
    assert set(vals(o["parameters"])) == {"FLOW", "PRESSURE"} and set(vals(o["plants"])) == {"P2"}
    # a date filter narrows values but not the reported available range
    o = get(eng, "sensor/options", upload_id=a, start="2024-05-03").json()
    assert vals(o["plants"]) == {"P2": 2} and o["first"].startswith("2024-05-01")
    # across datasets
    o = get(eng, "sensor/options", upload_id=[a, ds["B"]]).json()
    assert vals(o["datasets"]) == {a: 6, ds["B"]: 2} and "P3" in vals(o["plants"])
    assert vals(get(eng, "sensor/options", upload_id=a, plant="P3").json()["datasets"]) == {ds["B"]: 1}   # own filter ignored
    assert get(eng, "sensor/options", upload_id=a, plant="NOPE").json()["lines"] == []


def test_summary(eng, ds):
    s = get(eng, "sensor/summary", upload_id=ds["A"]).json()
    assert (s["total_rows"], s["datasets"]) == (6, 1)
    p = {x["parameter"]: x for x in s["parameters"]}
    assert (p["TEMPERATURE"]["n"], p["TEMPERATURE"]["min"], p["TEMPERATURE"]["max"], p["TEMPERATURE"]["mean"]) == (3, 70, 72, 71)
    assert p["PRESSURE"]["n"] == 2 and p["FLOW"]["n"] == 1
    s = get(eng, "sensor/summary", upload_id=ds["A"], plant="P2").json()
    assert s["total_rows"] == 2 and {x["parameter"] for x in s["parameters"]} == {"FLOW", "PRESSURE"}
    empty = get(eng, "sensor/summary", upload_id=ds["A"], plant="NOPE").json()
    assert empty["total_rows"] == 0 and empty["parameters"] == [] and empty["first"] is None


# ---------------------------------------------------------------- production data
def test_production_filters_and_summary(eng, ds):
    c = [ds["C"]]
    assert len(rows(eng, c, "production")) == 3
    assert len(rows(eng, c, "production", plant="P1")) == 2 and len(rows(eng, c, "production", line="L3")) == 1
    assert [r["batch_id"] for r in rows(eng, c, "production", batch="LOT3")] == ["LOT3"]
    assert [r["batch_id"] for r in rows(eng, c, "production", start="2024-05-02", end="2024-05-03")] == ["LOT2", "LOT3"]
    assert [r["batch_id"] for r in rows(eng, c, "production", plant="P1", asset="M2")] == ["LOT2"]
    s = get(eng, "production/summary", upload_id=ds["C"]).json()
    assert (s["total_rows"], s["sample_size"], s["defective_count"], s["batches"]) == (3, 150, 6, 3)
    o = get(eng, "production/options", upload_id=ds["C"], plant="P1").json()
    assert vals(o["plants"]) == {"P1": 2, "P2": 1} and vals(o["batches"]) == {"LOT1": 1, "LOT2": 1} and o["tags"] == []
    for flt in ({"tag": "T1"}, {"parameter": "TEMPERATURE"}):
        assert get(eng, "production", upload_id=ds["C"], **flt).status_code == 422       # sensor-only filters


# ---------------------------------------------------------------- dataset rules
def test_dataset_validation_and_visibility(eng, ds):
    a, b, c = ds["A"], ds["B"], ds["C"]
    assert get(eng, "sensor", upload_id=c).status_code == 409                            # production id on sensor endpoint
    assert get(eng, "production", upload_id=a).status_code == 409
    assert get(eng, "sensor", upload_id=str(uuid.uuid4())).status_code == 404
    assert get(eng, "sensor", upload_id="not-a-uuid").status_code == 422
    staged = client.post("/api/uploads", headers=eng, files={"file": ("s.csv", f"t,p,v\n2024-01-01,A,{TAG}\n".encode())},
                         data={"dataset_type": "SENSOR"}).json()["upload_id"]
    created.append(staged)
    r = get(eng, "sensor", upload_id=staged)
    assert r.status_code == 409 and "not processed" in r.json()["error"]["message"]
    # without upload_id only COMPLETED datasets are visible
    assert get(eng, "sensor", plant="P3").json()["total"] == 1
    sf.execute(f"UPDATE {DB}.METADATA.DATASET_UPLOADS SET STATUS = 'PROCESSING' WHERE UPLOAD_ID = %s", (b,))
    try:
        assert get(eng, "sensor", plant="P3").json()["total"] == 0                       # being re-processed -> hidden
    finally:
        sf.execute(f"UPDATE {DB}.METADATA.DATASET_UPLOADS SET STATUS = 'COMPLETED' WHERE UPLOAD_ID = %s", (b,))
    assert get(eng, "sensor", plant="P3").json()["total"] == 1
    all_p1 = get(eng, "sensor", plant="P1", page_size=1000).json()
    assert {a, b} <= {i["upload_id"] for i in all_p1["items"]}                           # no upload_id spans all datasets


# ---------------------------------------------------------------- data really comes from Snowflake
def test_results_come_from_snowflake(eng, ds):
    a = ds["A"]
    api_sum = sum(i["value"] for i in rows(eng, [a], parameter="TEMPERATURE"))
    sql_sum = sf.execute(f"SELECT SUM(VALUE) FROM {DB}.GOLD.GOLD_SENSOR_DATA WHERE UPLOAD_ID = %s AND PARAMETER = 'TEMPERATURE'", (a,))[0][0]
    assert api_sum == float(sql_sum) == 213.0
    sf.execute(f"UPDATE {DB}.GOLD.GOLD_SENSOR_DATA SET VALUE = 999.5 WHERE UPLOAD_ID = %s AND PARAMETER = 'FLOW'", (a,))
    assert rows(eng, [a], parameter="FLOW")[0]["value"] == 999.5                         # change in Snowflake is visible at once
    assert get(eng, "sensor/summary", upload_id=a, parameter="FLOW").json()["parameters"][0]["max"] == 999.5
    sf.execute(f"UPDATE {DB}.GOLD.GOLD_SENSOR_DATA SET VALUE = 10 WHERE UPLOAD_ID = %s AND PARAMETER = 'FLOW'", (a,))


def test_injection_attempts_are_inert(eng, ds):
    for evil in ["x' OR '1'='1", "'; DROP TABLE USERS; --", "P1' --", "%", "P1%"]:
        r = get(eng, "sensor", upload_id=ds["A"], plant=evil)
        assert r.status_code == 200 and r.json()["total"] == 0, evil
    assert sf.execute(f"SELECT COUNT(*) FROM {DB}.METADATA.USERS")[0][0] >= 1                 # users table still intact


def test_access_control(eng, viewer, ds):
    assert client.get("/api/data/sensor").status_code == 401
    assert client.get("/api/data/sensor/options").status_code == 401
    assert get(viewer, "sensor", upload_id=ds["A"]).status_code == 200                   # viewers have data:read
    assert get(viewer, "production/summary", upload_id=ds["C"]).status_code == 200
