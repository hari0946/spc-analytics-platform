"""Unit tests for the SQL builders (no network): injection safety and structure."""
import pytest
from pydantic import ValidationError

from app.schemas.processing import ProcessOptions
from app.services import pipeline_sql as q

UID = "11111111-2222-3333-4444-555555555555"
EVIL = 'x"); DROP TABLE USERS; --'


def all_text(stmts):
    return " ".join(s for s, _ in stmts)


def test_sensor_long_uses_bound_parameters_only():
    m = {"timestamp": EVIL, "parameter": "p", "value": "v", "asset": "a"}
    stmts = q.sensor_silver(m, "DD/MM/YYYY", UID)
    assert len(stmts) == 2
    for sql, params in stmts:
        assert EVIL not in sql and UID not in sql and "DD/MM/YYYY" not in sql
        assert params["c_timestamp"] == EVIL and params["uid"] == UID and params["ts_fmt"] == "DD/MM/YYYY"
        assert "%(c_timestamp)s" in sql and "FLATTEN" not in sql
    assert "NULL::VARCHAR AS PLANT_CODE" in stmts[0][0]          # unmapped optional columns become NULL
    assert "c_plant" not in stmts[0][1]


def test_sensor_wide_uses_flatten_and_json_param():
    m = {"timestamp": "t", "value_columns": ["Temp", "Press"], "batch": "b"}
    sql, params = q.sensor_silver(m, None, UID)[0]
    assert "LATERAL FLATTEN" in sql and params["cols"] == '["Temp", "Press"]'
    assert "ts_fmt" not in params and "TRY_TO_TIMESTAMP_NTZ(TRIM(TS_RAW))" in sql
    assert "Temp" not in sql


def test_sensor_rules_present():
    sql = q.sensor_silver({"timestamp": "t", "parameter": "p", "value": "v"}, None, UID)[1][0]
    for reason in ("MISSING_TIMESTAMP", "INVALID_TIMESTAMP", "TIMESTAMP_OUT_OF_RANGE", "MISSING_PARAMETER",
                   "MISSING_VALUE", "INVALID_VALUE", "DUPLICATE", "CONFLICTING_DUPLICATE"):
        assert reason in sql


def test_production_builder_only_checks_mapped_fields():
    m = {"timestamp": "t", "sample_size": "n", "defective_count": "d", "batch": "b"}
    sql, params = q.production_silver(m, None, UID)[0]
    assert "INCONSISTENT_COUNTS" in sql and "SAMPLE_SIZE_NUM = 0" in sql
    assert params["c_sample_size"] == "n" and "c_units_produced" not in params
    assert "NULL::STRING AS UNITS_PRODUCED_RAW" in sql
    only_units = q.production_silver({"timestamp": "t", "units_produced": "u"}, None, UID)[0][0]
    assert "INCONSISTENT_COUNTS" not in only_units


def test_gold_and_clear_statements():
    assert len(q.sensor_gold(UID)) == 2 and len(q.production_gold(UID)) == 1
    assert "DENSE_RANK" in q.sensor_gold(UID)[0][0]
    cleared = " ".join(s for s, _ in q.clear_statements(UID))
    for t in (q.CLEAN, q.VALIDATED, q.REJECTED, q.GOLD_SENSOR, q.GOLD_PROD, q.GOLD_PROCESS):
        assert f"DELETE FROM {t} " in cleared
    assert all("UPLOAD_ID = %(uid)s" in s for s, _ in q.clear_statements(UID))


@pytest.mark.parametrize("fmt", ["YYYY-MM-DD HH24:MI:SS", "DD/MM/YYYY", "YYYY-MM-DD\"T\"HH24:MI", None])
def test_valid_formats(fmt):
    if fmt and '"' in fmt:
        with pytest.raises(ValidationError):
            ProcessOptions(timestamp_format=fmt)
    else:
        assert ProcessOptions(timestamp_format=fmt).timestamp_format == fmt


@pytest.mark.parametrize("bad", ["'; DROP", "a", "x" * 61, "%(uid)s", "a;b"])
def test_bad_formats_rejected(bad):
    with pytest.raises(ValidationError):
        ProcessOptions(timestamp_format=bad)
