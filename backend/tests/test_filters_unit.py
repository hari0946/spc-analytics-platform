"""Unit tests for filter parsing and WHERE-clause building (no network)."""
from datetime import datetime

import pytest

from app.core.exceptions import UnprocessableError
from app.services.filters import DataFilters, build_filters, build_where, parse_bound

U = "SPC_PLATFORM.METADATA.DATASET_UPLOADS"
UID = "11111111-2222-3333-4444-555555555555"


def test_date_only_bounds_cover_whole_day():
    assert parse_bound("2024-03-01", "start", False) == datetime(2024, 3, 1, 0, 0, 0)
    assert parse_bound("2024-03-01", "end", True) == datetime(2024, 3, 1, 23, 59, 59, 999999)


def test_datetime_bounds_and_timezone_conversion():
    assert parse_bound("2024-03-01T08:30:00", "start", False) == datetime(2024, 3, 1, 8, 30)
    assert parse_bound("2024-03-01 08:30:00", "start", False) == datetime(2024, 3, 1, 8, 30)
    assert parse_bound("2024-03-01T10:00:00+02:00", "end", True) == datetime(2024, 3, 1, 8, 0)
    assert parse_bound("2024-03-01T08:00:00Z", "end", True) == datetime(2024, 3, 1, 8, 0)
    assert parse_bound(None, "start", False) is None and parse_bound("  ", "start", False) is None


@pytest.mark.parametrize("bad", ["yesterday", "2024-13-01", "2024-02-30", "01/02/2024", "2024-03-01T25:00"])
def test_bad_dates_rejected(bad):
    with pytest.raises(UnprocessableError):
        parse_bound(bad, "start", False)


def test_start_after_end_rejected():
    with pytest.raises(UnprocessableError, match="must not be after"):
        build_filters(start="2024-03-02", end="2024-03-01")
    assert build_filters(start="2024-03-01", end="2024-03-01")        # same day is fine


def test_list_cleaning():
    f = build_filters(plant=[" P1 ", "P1", "", "P2"], parameter=["temperature", " Flow "])
    assert f.plants == ["P1", "P2"] and f.parameters == ["TEMPERATURE", "FLOW"]


def test_limits_and_uuid_validation():
    with pytest.raises(UnprocessableError):
        build_filters(plant=["x" * 101])
    with pytest.raises(UnprocessableError):
        build_filters(asset=[str(i) for i in range(51)])
    with pytest.raises(UnprocessableError, match="UUID"):
        build_filters(upload_id=["not-a-uuid"])
    assert build_filters(upload_id=[UID]).upload_ids == [UID]


def test_where_default_only_completed_uploads():
    where, params = build_where(DataFilters(), "sensor", U)
    assert where == f"WHERE UPLOAD_ID IN (SELECT UPLOAD_ID FROM {U} WHERE STATUS = 'COMPLETED')" and params == []


def test_where_combines_with_and_and_binds_values():
    evil = "x' OR '1'='1"
    f = build_filters(upload_id=[UID], plant=["P1", evil], asset=["M1"], parameter=["flow"],
                      start="2024-03-01", end="2024-03-02")
    where, params = build_where(f, "sensor", U)
    assert evil not in where and UID not in where
    assert where.count("%s") == len(params) == 1 + 2 + 1 + 1 + 2
    assert " AND PLANT_CODE IN (%s,%s)" in where and "PARAMETER IN (%s)" in where
    assert "MEASURED_AT >= %s" in where and "MEASURED_AT <= %s" in where
    assert params[:4] == [UID, "P1", evil, "M1"] and params[4] == "FLOW"


def test_exclusion_for_facets():
    f = build_filters(plant=["P1"], asset=["M1"], start="2024-03-01")
    w, p = build_where(f, "sensor", U, exclude="plants")
    assert "PLANT_CODE" not in w and "ASSET_CODE IN" in w and "MEASURED_AT >=" in w
    w, p = build_where(f, "sensor", U, exclude="dates")
    assert "MEASURED_AT" not in w and "PLANT_CODE IN" in w
    w, _ = build_where(build_filters(upload_id=[UID]), "sensor", U, exclude="datasets")
    assert "STATUS = 'COMPLETED'" in w and UID not in str(p)


def test_production_uses_event_time_and_rejects_sensor_only_filters():
    where, _ = build_where(build_filters(start="2024-03-01", batch=["L1"]), "production", U)
    assert "EVENT_TIME >= %s" in where and "BATCH_ID IN" in where
    for kw in ({"tag": ["T1"]}, {"parameter": ["TEMPERATURE"]}):
        with pytest.raises(UnprocessableError, match="not applicable"):
            build_where(build_filters(**kw), "production", U)
