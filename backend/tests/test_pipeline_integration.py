"""Live BRONZE -> SILVER -> GOLD tests. SAMPLE data with hand-counted expected outcomes; everything is removed afterwards."""
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
DB = "SPC_PLATFORM"
TAG = uuid.uuid4().hex[:8]
created: list[str] = []


def hdr(t):
    return {"Authorization": f"Bearer {t}"}


def make_user(role):
    name = f"sample_auth_{role.lower()}_{TAG}"
    user_service.create_user(UserCreate(username=name, email=f"{name}@example.com", password=PW, roles=[role]),
                             action="SAMPLE_SETUP")
    r = client.post("/api/auth/login", json={"username": name, "password": PW})
    return hdr(r.json()["access_token"])


@pytest.fixture(scope="module")
def eng():
    return make_user("ENGINEER")


@pytest.fixture(scope="module")
def viewer():
    return make_user("VIEWER")


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    yield
    for uid in created:
        for t in ("CLEAN_SENSOR_DATA", "VALIDATED_PRODUCTION_DATA", "REJECTED_RECORDS"):
            sf.execute(f"DELETE FROM {DB}.SILVER.{t} WHERE UPLOAD_ID = %s", (uid,))
        for t in ("GOLD_SENSOR_DATA", "GOLD_PRODUCTION_DATA", "GOLD_PROCESS_DATA"):
            sf.execute(f"DELETE FROM {DB}.GOLD.{t} WHERE UPLOAD_ID = %s", (uid,))
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


def load(eng, csv: str, mapping: dict, dataset_type="SENSOR"):
    r = client.post("/api/uploads", headers=eng, files={"file": ("sample.csv", csv.encode())},
                    data={"dataset_type": dataset_type, "allow_duplicate": "true"})
    assert r.status_code == 201, r.text
    uid = r.json()["upload_id"]
    created.append(uid)
    assert client.put(f"/api/uploads/{uid}/mapping", headers=eng, json={"mapping": mapping}).status_code == 200
    ld = client.post(f"/api/uploads/{uid}/load", headers=eng)
    assert ld.status_code == 200, ld.text
    return uid


def process(eng, uid, body=None):
    r = client.post(f"/api/uploads/{uid}/process", headers=eng, json=body)
    assert r.status_code == 202, r.text
    assert r.json()["status"] in ("PROCESSING", "COMPLETED")    # TestClient runs the background task before returning
    return client.get(f"/api/uploads/{uid}", headers=eng).json()


def count(table, uid):
    return sf.execute(f"SELECT COUNT(*) FROM {DB}.{table} WHERE UPLOAD_ID = %s", (uid,))[0][0]


SENSOR_CSV = f"""timestamp,parameter,value,machine,batch
2024-03-01 08:00:00,TEMPERATURE,70.0,M1,B1
2024-03-01 08:10:00,TEMPERATURE,72.0,M1,B1
2024-03-01 08:20:00,temperature ,74.0,M1,B2
2024-03-01 08:00:00,TEMPERATURE,70.0,M1,B1
2024-03-01 08:10:00,TEMPERATURE,99.0,M1,B1
,TEMPERATURE,71.0,M1,B1
not-a-date,TEMPERATURE,71.0,M1,B1
2024-03-01 08:30:00,TEMPERATURE,N/A,M1,B2
2024-03-01 08:40:00,TEMPERATURE,abc,M1,B2
2999-01-01 00:00:00,TEMPERATURE,70.0,M1,B2
2024-03-01 08:50:00,,70.0,M1,B2
2024-03-01T08:30:00,PRESSURE, 1.5 ,M1,B1
2024-03-01 09:30:00,PRESSURE,1.7,M2,B3
2024-03-01 08:45:00,TEMPERATURE,inf,M1,B2
"""
LONG_MAP = {"timestamp": "timestamp", "parameter": "parameter", "value": "value", "asset": "machine", "batch": "batch"}


def test_sensor_long_pipeline_known_outcomes(eng):
    uid = load(eng, SENSOR_CSV, LONG_MAP)
    assert count("BRONZE.RAW_SENSOR_DATA", uid) == 14
    up = process(eng, uid)
    assert up["status"] == "COMPLETED", up
    rep = client.get(f"/api/uploads/{uid}/report", headers=eng).json()
    c = rep["counts"]
    assert (c["bronze_rows"], c["silver_rows"], c["gold_rows"], c["rejected_rows"]) == (14, 5, 5, 9)
    assert c["rejected_by_reason"] == {"INVALID_VALUE": 2, "CONFLICTING_DUPLICATE": 1, "DUPLICATE": 1, "INVALID_TIMESTAMP": 1,
                                       "MISSING_PARAMETER": 1, "MISSING_TIMESTAMP": 1, "MISSING_VALUE": 1,
                                       "TIMESTAMP_OUT_OF_RANGE": 1}
    assert c["duplicates_removed"] == 2
    assert rep["all_checks_passed"] is True and {x["name"] for x in rep["checks"]} >= {"reconciliation", "gold_matches_silver"}
    temp = next(p for p in rep["summary"]["parameters"] if p["parameter"] == "TEMPERATURE")
    assert (temp["n"], temp["min"], temp["max"], temp["mean"]) == (3, 70, 74, 72)
    assert rep["summary"]["assets"] == ["M1", "M2"] and rep["layout"] == "long"
    assert rep["time_range"] == {"first": "2024-03-01 08:00:00", "last": "2024-03-01 09:30:00"}

    # ---- verify directly in Snowflake
    silver = sf.execute(f"SELECT PARAMETER, ASSET_CODE, TO_CHAR(MEASURED_AT, 'HH24:MI'), VALUE, BATCH_ID "
                        f"FROM {DB}.SILVER.CLEAN_SENSOR_DATA WHERE UPLOAD_ID = %s ORDER BY PARAMETER, MEASURED_AT", (uid,))
    assert silver == [("PRESSURE", "M1", "08:30", 1.5, "B1"), ("PRESSURE", "M2", "09:30", 1.7, "B3"),
                      ("TEMPERATURE", "M1", "08:00", 70.0, "B1"), ("TEMPERATURE", "M1", "08:10", 72.0, "B1"),
                      ("TEMPERATURE", "M1", "08:20", 74.0, "B2")]          # first of a conflicting pair wins (72, not 99)
    gold = sf.execute(f"SELECT PARAMETER, BATCH_ID, SUBGROUP_ID FROM {DB}.GOLD.GOLD_SENSOR_DATA WHERE UPLOAD_ID = %s "
                      "ORDER BY PARAMETER, MEASURED_AT", (uid,))
    assert gold == [("PRESSURE", "B1", 1), ("PRESSURE", "B3", 2), ("TEMPERATURE", "B1", 1), ("TEMPERATURE", "B1", 1),
                    ("TEMPERATURE", "B2", 2)]
    agg = sf.execute(f"SELECT PARAMETER, ASSET_CODE, TO_CHAR(BUCKET_START, 'HH24'), N_POINTS, MEAN_VALUE, MIN_VALUE, MAX_VALUE, STDDEV_VALUE "
                     f"FROM {DB}.GOLD.GOLD_PROCESS_DATA WHERE UPLOAD_ID = %s ORDER BY PARAMETER, ASSET_CODE", (uid,))
    assert agg[0] == ("PRESSURE", "M1", "08", 1, 1.5, 1.5, 1.5, None)         # single point: no stddev invented
    assert agg[1] == ("PRESSURE", "M2", "09", 1, 1.7, 1.7, 1.7, None)
    assert agg[2] == ("TEMPERATURE", "M1", "08", 3, 72.0, 70.0, 74.0, 2.0)
    # nothing is in two places, nothing lost
    assert count("SILVER.CLEAN_SENSOR_DATA", uid) + count("SILVER.REJECTED_RECORDS", uid) == count("BRONZE.RAW_SENSOR_DATA", uid)
    # BRONZE is untouched
    assert count("BRONZE.RAW_SENSOR_DATA", uid) == 14

    # ---- rejections endpoint: filter, pagination, raw record preserved
    rj = client.get(f"/api/uploads/{uid}/rejections?reason=invalid_value", headers=eng).json()
    assert rj["total"] == 2 and {r["raw_record"]["value"] for r in rj["items"]} == {"abc", "inf"}
    pg = client.get(f"/api/uploads/{uid}/rejections?page_size=4&page=3", headers=eng).json()
    assert pg["total"] == 9 and len(pg["items"]) == 1
    all_rows = [r["source_row"] for p in (1, 2, 3) for r in client.get(f"/api/uploads/{uid}/rejections?page_size=4&page={p}", headers=eng).json()["items"]]
    assert len(all_rows) == 9 and all_rows == sorted(all_rows)

    # ---- re-processing replaces, never duplicates
    again = process(eng, uid)
    assert again["status"] == "COMPLETED"
    assert (count("SILVER.CLEAN_SENSOR_DATA", uid), count("GOLD.GOLD_SENSOR_DATA", uid),
            count("SILVER.REJECTED_RECORDS", uid), count("GOLD.GOLD_PROCESS_DATA", uid)) == (5, 5, 9, 3)


def test_wide_layout_reconciles_per_measurement(eng):
    csv = ("Time,Temperature,Pressure,Note\n"
           "2024-04-01 00:00:00,70.1,1.1,ok\n"
           "2024-04-01 00:01:00,70.2,,ok\n"
           "2024-04-01 00:02:00,x,1.3,ok\n"
           "2024-04-01 00:03:00,70.4,1.4,ok\n")
    uid = load(eng, csv, {"timestamp": "Time", "value_columns": ["Temperature", "Pressure"]})
    assert process(eng, uid)["status"] == "COMPLETED"
    rep = client.get(f"/api/uploads/{uid}/report", headers=eng).json()
    c = rep["counts"]
    assert (c["bronze_rows"], c["input_units"], c["silver_rows"], c["rejected_rows"]) == (4, 8, 6, 2)
    assert c["rejected_by_reason"] == {"INVALID_VALUE": 1, "MISSING_VALUE": 1} and rep["layout"] == "wide"
    assert rep["all_checks_passed"] is True
    rj = client.get(f"/api/uploads/{uid}/rejections", headers=eng).json()["items"]
    assert {(r["source_row"], r["parameter"], r["reason"]) for r in rj} == {(3, "PRESSURE", "MISSING_VALUE"),
                                                                           (4, "TEMPERATURE", "INVALID_VALUE")}
    assert sf.execute(f"SELECT DISTINCT PARAMETER FROM {DB}.SILVER.CLEAN_SENSOR_DATA WHERE UPLOAD_ID = %s ORDER BY 1", (uid,)) == [
        ("PRESSURE",), ("TEMPERATURE",)]


def test_timestamp_format_option_and_reprocessing(eng):
    csv = ("ts,param,val\n13/01/2024 08:00,FLOW,10\n14/01/2024 08:00,FLOW,11\n15/01/2024 08:00,FLOW,12\n")
    uid = load(eng, csv, {"timestamp": "ts", "parameter": "param", "value": "val"})
    bad = process(eng, uid, {"timestamp_format": "YYYY-MM-DD"})
    assert bad["status"] == "COMPLETED"
    rep = client.get(f"/api/uploads/{uid}/report", headers=eng).json()
    assert rep["counts"]["silver_rows"] == 0 and rep["counts"]["rejected_by_reason"] == {"INVALID_TIMESTAMP": 3}
    assert rep["all_checks_passed"] is True                                    # nothing lost, even when everything is rejected
    assert process(eng, uid, {"timestamp_format": "DD/MM/YYYY HH24:MI"})["status"] == "COMPLETED"
    rep = client.get(f"/api/uploads/{uid}/report", headers=eng).json()
    assert rep["counts"]["silver_rows"] == 3 and rep["counts"]["rejected_rows"] == 0
    assert rep["options"] == {"timestamp_format": "DD/MM/YYYY HH24:MI"}
    first = sf.execute(f"SELECT TO_CHAR(MIN(MEASURED_AT), 'YYYY-MM-DD HH24:MI') FROM {DB}.SILVER.CLEAN_SENSOR_DATA WHERE UPLOAD_ID = %s", (uid,))
    assert first == [("2024-01-13 08:00",)]
    assert client.post(f"/api/uploads/{uid}/process", headers=eng, json={"timestamp_format": "bad;fmt"}).status_code == 422


PROD_CSV = """date,lot,n,defectives,defects
2024-03-01,L1,50,2,3
2024-03-02,L2,50,0,1
2024-03-03,L3,50,60,1
2024-03-04,L4,0,0,0
2024-03-05,L5,abc,1,1
2024-03-06,L6,50,-1,1
2024-03-07,L7,50,2.5,1
bad-date,L8,50,1,1
2024-03-02,L2,50,0,1
2024-03-02,L2,50,5,1
2024-03-08,L9,,,
2024-03-09,L10,40,N/A,2
"""


def test_production_pipeline_known_outcomes(eng):
    m = {"timestamp": "date", "batch": "lot", "sample_size": "n", "defective_count": "defectives", "defect_count": "defects"}
    uid = load(eng, PROD_CSV, m, "PRODUCTION")
    assert process(eng, uid)["status"] == "COMPLETED"
    rep = client.get(f"/api/uploads/{uid}/report", headers=eng).json()
    c = rep["counts"]
    assert (c["bronze_rows"], c["silver_rows"], c["gold_rows"], c["rejected_rows"]) == (12, 3, 3, 9)
    assert c["rejected_by_reason"] == {"INVALID_COUNT": 4, "CONFLICTING_DUPLICATE": 1, "DUPLICATE": 1,
                                       "INCONSISTENT_COUNTS": 1, "INVALID_TIMESTAMP": 1, "MISSING_VALUE": 1}
    assert rep["summary"]["totals"] == {"units_produced": None, "sample_size": 140, "defective_count": 2,
                                        "defect_count": 6, "batches": 3}
    assert rep["all_checks_passed"] is True and rep["layout"] == "production"
    rows = sf.execute(f"SELECT BATCH_ID, SAMPLE_SIZE, DEFECTIVE_COUNT, DEFECT_COUNT FROM {DB}.GOLD.GOLD_PRODUCTION_DATA "
                      "WHERE UPLOAD_ID = %s ORDER BY EVENT_TIME", (uid,))
    assert rows == [("L1", 50, 2, 3), ("L2", 50, 0, 1), ("L10", 40, None, 2)]    # blank defectives stays NULL, not 0
    by_row = {r["source_row"]: r["reason"] for r in client.get(f"/api/uploads/{uid}/rejections?page_size=50", headers=eng).json()["items"]}
    assert by_row == {4: "INCONSISTENT_COUNTS", 5: "INVALID_COUNT", 6: "INVALID_COUNT", 7: "INVALID_COUNT",
                      8: "INVALID_COUNT", 9: "INVALID_TIMESTAMP", 10: "DUPLICATE", 11: "CONFLICTING_DUPLICATE",
                      12: "MISSING_VALUE"}
    assert count("SILVER.CLEAN_SENSOR_DATA", uid) == 0 and count("GOLD.GOLD_SENSOR_DATA", uid) == 0


def test_guards_permissions_and_failure_handling(eng, viewer):
    # not loaded yet -> cannot process
    r = client.post("/api/uploads", headers=eng, files={"file": ("g.csv", f"t,p,v\n2024-01-01,A,{TAG}\n".encode())},
                    data={"dataset_type": "SENSOR"})
    staged = r.json()["upload_id"]
    created.append(staged)
    assert client.post(f"/api/uploads/{staged}/process", headers=eng).status_code == 409
    assert client.get(f"/api/uploads/{staged}/report", headers=eng).status_code == 404
    # unknown / malformed ids
    assert client.post(f"/api/uploads/{uuid.uuid4()}/process", headers=eng).status_code == 404
    assert client.get(f"/api/uploads/{uuid.uuid4()}/rejections", headers=eng).status_code == 404
    assert client.post("/api/uploads/not-a-uuid/process", headers=eng).status_code in (404, 409)
    # roles
    uid = load(eng, "t,p,v\n2024-01-01 00:00:00,A,1\n2024-01-01 00:01:00,A,2\n", {"timestamp": "t", "parameter": "p", "value": "v"})
    assert client.post(f"/api/uploads/{uid}/process", headers=viewer).status_code == 403
    assert process(eng, uid)["status"] == "COMPLETED"
    assert client.get(f"/api/uploads/{uid}/report", headers=viewer).status_code == 200      # viewers can read results
    assert client.get(f"/api/uploads/{uid}/report").status_code == 401
    # a broken mapping makes the run fail cleanly: status, message, no half-built output
    sf.execute(f"UPDATE {DB}.METADATA.DATASET_UPLOADS SET COLUMN_MAPPING = PARSE_JSON('{{}}') WHERE UPLOAD_ID = %s", (uid,))
    assert client.post(f"/api/uploads/{uid}/process", headers=eng).status_code == 202
    failed = client.get(f"/api/uploads/{uid}", headers=eng).json()
    assert failed["status"] == "PROCESS_FAILED" and "Processing failed" in failed["error_message"]
    assert count("SILVER.CLEAN_SENSOR_DATA", uid) == 0 and count("GOLD.GOLD_SENSOR_DATA", uid) == 0
    assert count("BRONZE.RAW_SENSOR_DATA", uid) == 2                                        # bronze untouched
    # fix and retry
    sf.execute(f"UPDATE {DB}.METADATA.DATASET_UPLOADS SET COLUMN_MAPPING = PARSE_JSON('{{\"timestamp\":\"t\",\"parameter\":\"p\",\"value\":\"v\"}}') "
               "WHERE UPLOAD_ID = %s", (uid,))
    assert process(eng, uid)["status"] == "COMPLETED" and count("SILVER.CLEAN_SENSOR_DATA", uid) == 2
    # status gate while PROCESSING
    sf.execute(f"UPDATE {DB}.METADATA.DATASET_UPLOADS SET STATUS = 'PROCESSING' WHERE UPLOAD_ID = %s", (uid,))
    assert client.post(f"/api/uploads/{uid}/process", headers=eng).status_code == 409
    assert client.delete(f"/api/uploads/{uid}", headers=eng).status_code == 409
    sf.execute(f"UPDATE {DB}.METADATA.DATASET_UPLOADS SET STATUS = 'COMPLETED' WHERE UPLOAD_ID = %s", (uid,))
    # audit
    # (admin-only endpoint; verify directly)
    acts = {a[0] for a in sf.execute(f"SELECT ACTION FROM {DB}.METADATA.AUDIT_LOGS WHERE ENTITY_ID = %s", (uid,))}
    assert {"UPLOAD_PROCESSED", "UPLOAD_PROCESS_FAILED"} <= acts


def test_delete_cascades_to_silver_and_gold(eng):
    uid = load(eng, SENSOR_CSV, LONG_MAP)
    assert process(eng, uid)["status"] == "COMPLETED"
    assert count("GOLD.GOLD_SENSOR_DATA", uid) == 5 and count("SILVER.REJECTED_RECORDS", uid) == 9
    assert client.delete(f"/api/uploads/{uid}", headers=eng).status_code == 204
    for t in ("BRONZE.RAW_SENSOR_DATA", "SILVER.CLEAN_SENSOR_DATA", "SILVER.REJECTED_RECORDS", "GOLD.GOLD_SENSOR_DATA",
              "GOLD.GOLD_PROCESS_DATA"):
        assert count(t, uid) == 0, t
    created.remove(uid)


def test_duplicate_file_detection_covers_processed_uploads(eng):
    csv = f"t,p,v\n2024-01-01 00:00:00,A,{TAG}\n"
    uid = load(eng, csv, {"timestamp": "t", "parameter": "p", "value": "v"})
    process(eng, uid)
    again = client.post("/api/uploads", headers=eng, files={"file": ("x.csv", csv.encode())}, data={"dataset_type": "SENSOR"})
    assert again.status_code == 409 and again.json()["error"]["details"]["existing_upload_id"] == uid
