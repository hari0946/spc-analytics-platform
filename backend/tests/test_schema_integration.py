"""Live Snowflake integration tests. Uses clearly-marked SAMPLE rows and removes them afterwards."""
import pytest

from app.core import snowflake as sf

pytestmark = pytest.mark.integration

EXPECTED = {
    "METADATA": {"USERS", "ROLES", "USER_ROLES", "PLANTS", "PRODUCTION_LINES", "ASSETS",
                 "TAGS", "DATASET_UPLOADS", "ANALYSIS_HISTORY",
                 "USER_SESSIONS", "AUDIT_LOGS",  # Phase 5
                 "SCHEMA_MIGRATIONS"},          # Phase 7
    "BRONZE": {"RAW_SENSOR_DATA", "RAW_PRODUCTION_DATA"},
    "SILVER": {"CLEAN_SENSOR_DATA", "VALIDATED_PRODUCTION_DATA", "REJECTED_RECORDS"},  # REJECTED_RECORDS: Phase 7
    "GOLD": {"GOLD_SENSOR_DATA", "GOLD_PRODUCTION_DATA", "GOLD_PROCESS_DATA"},
    "SPC_RESULTS": {"CONTROL_LIMITS", "STATISTICAL_RESULTS", "SPC_VIOLATIONS",
                    "CAPABILITY_RESULTS", "DISTRIBUTION_RESULTS", "ANALYSIS_RESULTS"},
}
DB = "SPC_PLATFORM"


@pytest.fixture(scope="module", autouse=True)
def _close():
    yield
    sf.close_connection()


@pytest.mark.parametrize("schema", EXPECTED)
def test_tables_exist(schema):
    rows = sf.execute(
        f"SELECT TABLE_NAME FROM {DB}.INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA = %s", (schema,)
    )
    assert {r[0] for r in rows} == EXPECTED[schema]


def test_not_null_enforced():
    with pytest.raises(sf.SnowflakeConnectionError):
        sf.execute(f"INSERT INTO {DB}.METADATA.PLANTS (PLANT_CODE, PLANT_NAME) VALUES (NULL, 'x')")


def test_crud_metadata_chain():
    code = "SAMPLE_TEST_PLANT"
    sf.execute(f"DELETE FROM {DB}.METADATA.PLANTS WHERE PLANT_CODE = %s", (code,))
    try:
        # Create
        sf.execute(f"INSERT INTO {DB}.METADATA.PLANTS (PLANT_CODE, PLANT_NAME, LOCATION) VALUES (%s, %s, %s)",
                   (code, "SAMPLE plant (test data)", "n/a"))
        # Read (defaults applied)
        row = sf.execute(f"SELECT PLANT_ID, PLANT_NAME, IS_ACTIVE, CREATED_AT FROM {DB}.METADATA.PLANTS "
                         "WHERE PLANT_CODE = %s", (code,))[0]
        assert len(row[0]) == 36 and row[2] is True and row[3] is not None
        plant_id = row[0]
        # Child rows
        sf.execute(f"INSERT INTO {DB}.METADATA.PRODUCTION_LINES (PLANT_ID, LINE_CODE, LINE_NAME) VALUES (%s,'SAMPLE_L1','SAMPLE line')", (plant_id,))
        # Update
        sf.execute(f"UPDATE {DB}.METADATA.PLANTS SET PLANT_NAME = %s WHERE PLANT_ID = %s", ("SAMPLE renamed", plant_id))
        assert sf.execute(f"SELECT PLANT_NAME FROM {DB}.METADATA.PLANTS WHERE PLANT_ID = %s", (plant_id,))[0][0] == "SAMPLE renamed"
        # Join across tables
        assert sf.execute(
            f"SELECT COUNT(*) FROM {DB}.METADATA.PLANTS p JOIN {DB}.METADATA.PRODUCTION_LINES l ON l.PLANT_ID = p.PLANT_ID "
            "WHERE p.PLANT_ID = %s", (plant_id,))[0][0] == 1
    finally:
        sf.execute(f"DELETE FROM {DB}.METADATA.PRODUCTION_LINES WHERE LINE_CODE = 'SAMPLE_L1'")
        sf.execute(f"DELETE FROM {DB}.METADATA.PLANTS WHERE PLANT_CODE = %s", (code,))
    assert sf.execute(f"SELECT COUNT(*) FROM {DB}.METADATA.PLANTS WHERE PLANT_CODE = %s", (code,))[0][0] == 0


def test_crud_variant_and_medallion_layers():
    uid = "SAMPLE_TEST_UPLOAD"
    tables = [f"{DB}.BRONZE.RAW_SENSOR_DATA", f"{DB}.SILVER.CLEAN_SENSOR_DATA", f"{DB}.GOLD.GOLD_SENSOR_DATA"]
    try:
        sf.execute(f"INSERT INTO {tables[0]} (UPLOAD_ID, SOURCE_ROW, RAW_RECORD) SELECT %s, 1, PARSE_JSON(%s)",
                   (uid, '{"ts":"2024-01-01 00:00:00","temp":"72.5"}'))
        sf.execute(f"INSERT INTO {tables[1]} (UPLOAD_ID, SOURCE_ROW, PARAMETER, MEASURED_AT, VALUE) "
                   "SELECT UPLOAD_ID, SOURCE_ROW, 'TEMPERATURE', TO_TIMESTAMP_NTZ(RAW_RECORD:ts::string), RAW_RECORD:temp::float "
                   f"FROM {tables[0]} WHERE UPLOAD_ID = %s", (uid,))
        sf.execute(f"INSERT INTO {tables[2]} (UPLOAD_ID, PARAMETER, MEASURED_AT, VALUE) "
                   f"SELECT UPLOAD_ID, PARAMETER, MEASURED_AT, VALUE FROM {tables[1]} WHERE UPLOAD_ID = %s", (uid,))
        row = sf.execute(f"SELECT PARAMETER, VALUE, MEASURED_AT FROM {tables[2]} WHERE UPLOAD_ID = %s", (uid,))[0]
        assert row[0] == "TEMPERATURE" and row[1] == 72.5 and row[2].year == 2024
    finally:
        for t in tables:
            sf.execute(f"DELETE FROM {t} WHERE UPLOAD_ID = %s", (uid,))
    for t in tables:
        assert sf.execute(f"SELECT COUNT(*) FROM {t} WHERE UPLOAD_ID = %s", (uid,))[0][0] == 0


def test_spc_results_insert_read_delete():
    aid = "SAMPLE_TEST_ANALYSIS"
    t = f"{DB}.SPC_RESULTS.CONTROL_LIMITS"
    try:
        sf.execute(f"INSERT INTO {t} (ANALYSIS_ID, PARAMETER, CHART_TYPE, UCL, CL, LCL) VALUES (%s,'TEMPERATURE','IMR',3.0,0.0,-3.0)", (aid,))
        assert sf.execute(f"SELECT UCL, CL, LCL FROM {t} WHERE ANALYSIS_ID = %s", (aid,))[0] == (3.0, 0.0, -3.0)
    finally:
        sf.execute(f"DELETE FROM {t} WHERE ANALYSIS_ID = %s", (aid,))


def test_permissions_ownership_and_context():
    assert sf.execute("SELECT CURRENT_ROLE()")[0][0] == "SPC_ADMIN"
    owners = {r[0] for r in sf.execute(f"SELECT TABLE_OWNER FROM {DB}.INFORMATION_SCHEMA.TABLES WHERE TABLE_SCHEMA <> 'INFORMATION_SCHEMA'")}
    assert owners == {"SPC_ADMIN"}
    grants = {(r[1], r[2]) for r in sf.execute(f"SHOW GRANTS ON DATABASE {DB}")}
    assert ("OWNERSHIP", "DATABASE") in {(g[0], "DATABASE") for g in grants}
