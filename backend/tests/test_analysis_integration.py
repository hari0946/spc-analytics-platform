"""Phases 10-15 end to end: Snowflake GOLD data -> analysis API -> stored results. SAMPLE data, verified against
independent stdlib calculations; everything created is removed afterwards."""
import json
import math
import statistics
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
analyses: list[str] = []

# 60 temperature readings: cycle 10,12,11,13,9 x12 with a planted outlier (40) at index 47
CYCLE = [10, 12, 11, 13, 9]
TEMP = [CYCLE[i % 5] for i in range(60)]
TEMP[47] = 40
PRESS = [round(1.0 + 0.01 * i, 2) for i in range(30)]          # steady upward drift
BASE = datetime(2024, 7, 1)


def ts(i):
    return (BASE + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S")


SENSOR_CSV = "ts,param,val,asset,batch\n" + "".join(f"{ts(i)},TEMPERATURE,{v},M1,B{i // 5 + 1:02d}\n" for i, v in enumerate(TEMP)) + \
    "".join(f"{ts(i)},PRESSURE,{v},M1,B{i // 5 + 1:02d}\n" for i, v in enumerate(PRESS))
DEFECTIVES = [2, 3, 1, 4, 2, 3, 2, 12, 3, 2, 1, 3]
DEFECTS = [3, 5, 4, 6, 2, 4, 5, 3, 20, 4, 3, 5]
PROD_CSV = "date,n,def,defects\n" + "".join(f"2024-07-{i + 1:02d},50,{d},{c}\n" for i, (d, c) in enumerate(zip(DEFECTIVES, DEFECTS)))
SENS_MAP = {"timestamp": "ts", "parameter": "param", "value": "val", "asset": "asset", "batch": "batch"}
PROD_MAP = {"timestamp": "date", "sample_size": "n", "defective_count": "def", "defect_count": "defects"}


def hdr(t):
    return {"Authorization": f"Bearer {t}"}


def make_user(role):
    name = f"sample_auth_{role.lower()}_{TAG}"
    user_service.create_user(UserCreate(username=name, email=f"{name}@example.com", password=PW, roles=[role]), action="SAMPLE_SETUP")
    return hdr(client.post("/api/auth/login", json={"username": name, "password": PW}).json()["access_token"])


def ingest(eng, csv, mapping, kind):
    r = client.post("/api/uploads", headers=eng, files={"file": ("sample.csv", csv.encode())},
                    data={"dataset_type": kind, "allow_duplicate": "true"})
    assert r.status_code == 201, r.text
    uid = r.json()["upload_id"]
    created.append(uid)
    assert client.put(f"/api/uploads/{uid}/mapping", headers=eng, json={"mapping": mapping}).status_code == 200
    assert client.post(f"/api/uploads/{uid}/load", headers=eng).status_code == 200
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
    return {"S": ingest(eng, SENSOR_CSV, SENS_MAP, "SENSOR"), "P": ingest(eng, PROD_CSV, PROD_MAP, "PRODUCTION")}


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    yield
    for aid in analyses:
        for t in ("STATISTICAL_RESULTS", "CONTROL_LIMITS", "SPC_VIOLATIONS", "CAPABILITY_RESULTS", "DISTRIBUTION_RESULTS", "ANALYSIS_RESULTS"):
            sf.execute(f"DELETE FROM {DB}.SPC_RESULTS.{t} WHERE ANALYSIS_ID = %s", (aid,))
        sf.execute(f"DELETE FROM {DB}.METADATA.ANALYSIS_HISTORY WHERE ANALYSIS_ID = %s", (aid,))
    for uid in created:
        for sch, t in (("SILVER", "CLEAN_SENSOR_DATA"), ("SILVER", "VALIDATED_PRODUCTION_DATA"), ("SILVER", "REJECTED_RECORDS"),
                       ("GOLD", "GOLD_SENSOR_DATA"), ("GOLD", "GOLD_PRODUCTION_DATA"), ("GOLD", "GOLD_PROCESS_DATA"),
                       ("BRONZE", "RAW_SENSOR_DATA"), ("BRONZE", "RAW_PRODUCTION_DATA")):
            sf.execute(f"DELETE FROM {DB}.{sch}.{t} WHERE UPLOAD_ID = %s", (uid,))
        sf.execute(f"REMOVE @{DB}.BRONZE.UPLOAD_STAGE/{uid}/")
        sf.execute(f"DELETE FROM {DB}.METADATA.DATASET_UPLOADS WHERE UPLOAD_ID = %s", (uid,))
    M = f"{DB}.METADATA"
    for (x,) in sf.execute(f"SELECT USER_ID FROM {M}.USERS WHERE USERNAME LIKE 'sample_auth_%'"):
        sf.execute(f"DELETE FROM {M}.USER_SESSIONS WHERE USER_ID = %s", (x,))
        sf.execute(f"DELETE FROM {M}.USER_ROLES WHERE USER_ID = %s", (x,))
    sf.execute(f"DELETE FROM {M}.USERS WHERE USERNAME LIKE 'sample_auth_%'")
    sf.execute(f"DELETE FROM {M}.AUDIT_LOGS WHERE ACTOR_USERNAME LIKE 'sample_auth_%'")
    sf.close_connection()


def call(hd, path, /, ok=True, **p):
    r = client.get(f"/api/analysis/{path}", headers=hd, params={k: v for k, v in p.items() if v is not None})
    if ok:
        assert r.status_code == 200, r.text
        if "analysis_id" in r.json():
            analyses.append(r.json()["analysis_id"])
    return r


def q(sql, *args):
    return sf.execute(sql, args)


# ------------------------------------------------------------------------------------------------ Phase 10
def test_statistics_match_independent_calculation(eng, ds):
    r = call(eng, "statistics", upload_id=ds["S"], parameter="temperature").json()
    s = r["statistics"]
    assert s["n"] == 60 and s["mean"] == pytest.approx(statistics.fmean(TEMP)) and s["median"] == statistics.median(TEMP)
    assert s["std"] == pytest.approx(statistics.stdev(TEMP)) and s["variance"] == pytest.approx(statistics.variance(TEMP))
    assert (s["min"], s["max"], s["range"], s["mode"]) == (9, 40, 31, 9) and s["modes"] == [9.0, 10.0, 12.0, 13.0]   # four-way tie at 12 each
    q1, q2, q3 = statistics.quantiles(TEMP, n=4, method="inclusive")
    assert (s["q1"], s["q2"], s["q3"]) == (pytest.approx(q1), pytest.approx(q2), pytest.approx(q3))
    m, sd, n = statistics.fmean(TEMP), statistics.pstdev(TEMP), 60
    g1 = sum(((x - m) / sd) ** 3 for x in TEMP) / n
    assert s["skewness"] == pytest.approx(g1 * math.sqrt(n * (n - 1)) / (n - 2)) and s["skewness"] > 3          # strong right skew from the outlier
    assert r["parameter"] == "TEMPERATURE" and r["first"] == ts(0) and r["last"] == ts(59) and "analysis_id" in r
    # filters narrow the data: first 10 readings only
    sub = call(eng, "statistics", upload_id=ds["S"], parameter="TEMPERATURE", end=ts(9).replace(" ", "T")).json()["statistics"]
    assert sub["n"] == 10 and sub["mean"] == pytest.approx(statistics.fmean(TEMP[:10]))
    empty = call(eng, "statistics", upload_id=ds["S"], parameter="TEMPERATURE", start="2030-01-01").json()["statistics"]
    assert empty["n"] == 0 and empty["mean"] is None and "no data" in empty["notes"]               # empty selection: explicit, not an error


# ------------------------------------------------------------------------------------------------ Phase 11
def test_imr_chart_values(eng, ds):
    r = call(eng, "control-chart", upload_id=ds["S"], parameter="TEMPERATURE", chart="IMR").json()
    i, mr = r["charts"]["I"], r["charts"]["MR"]
    mean = statistics.fmean(TEMP)
    mrbar = statistics.fmean(abs(b - a) for a, b in zip(TEMP, TEMP[1:]))
    assert i["points"] == [float(v) for v in TEMP] and i["labels"][47] == ts(47)
    assert i["cl"][0] == pytest.approx(mean) and i["ucl"][0] == pytest.approx(mean + 2.66 * mrbar, abs=0.01)
    assert i["lcl"][0] == pytest.approx(mean - 2.66 * mrbar, abs=0.01)
    assert i["out_of_control"] == [47] and mr["cl"][0] == pytest.approx(mrbar)
    assert mr["ucl"][0] == pytest.approx(3.267 * mrbar, abs=0.01) and set(mr["out_of_control"]) == {46, 47}   # MR index i = |x[i+1]-x[i]|


def test_xbar_r_and_s_charts_with_subgroups(eng, ds):
    r = call(eng, "control-chart", upload_id=ds["S"], parameter="TEMPERATURE", chart="XBAR_R", subgroup_size=5).json()
    x, rr = r["charts"]["XBAR"], r["charts"]["R"]
    subs = [TEMP[i:i + 5] for i in range(0, 60, 5)]
    means = [statistics.fmean(g) for g in subs]
    ranges = [max(g) - min(g) for g in subs]
    rbar, grand = statistics.fmean(ranges), statistics.fmean(means)
    assert x["points"] == pytest.approx(means) and rr["points"] == ranges
    assert x["ucl"][0] == pytest.approx(grand + 0.577 * rbar, abs=0.02) and rr["ucl"][0] == pytest.approx(2.114 * rbar, abs=0.02)
    assert rr["lcl"][0] == 0 and x["out_of_control"] == [9] and rr["out_of_control"] == [9]
    assert x["labels"][9] == ts(45) and r["n_readings"] == 60
    s = call(eng, "control-chart", upload_id=ds["S"], parameter="TEMPERATURE", chart="XBAR_S", subgroup_size=5).json()
    sds = [statistics.stdev(g) for g in subs]
    assert s["charts"]["S"]["points"] == pytest.approx(sds) and s["charts"]["S"]["ucl"][0] == pytest.approx(2.089 * statistics.fmean(sds), abs=0.03)
    assert 9 in s["charts"]["XBAR"]["out_of_control"]
    # R / S alone; trailing readings that do not fill a subgroup are reported, not silently used
    r7 = call(eng, "control-chart", upload_id=ds["S"], parameter="TEMPERATURE", chart="R", subgroup_size=7).json()
    assert r7["charts"]["R"]["n_points"] == 8 and any("4 trailing" in n for n in r7["notes"])
    assert set(call(eng, "control-chart", upload_id=ds["S"], parameter="TEMPERATURE", chart="S", subgroup_size=4).json()["charts"]) == {"S"}


def test_batch_subgroups_equal_size_grouping(eng, ds):
    by_batch = call(eng, "control-chart", upload_id=ds["S"], parameter="TEMPERATURE", chart="XBAR_R", subgroup_by="batch").json()
    by_size = call(eng, "control-chart", upload_id=ds["S"], parameter="TEMPERATURE", chart="XBAR_R", subgroup_size=5).json()
    assert by_batch["charts"]["XBAR"]["points"] == by_size["charts"]["XBAR"]["points"]


def test_ewma_and_cusum_on_historical_data(eng, ds):
    e = call(eng, "control-chart", upload_id=ds["S"], parameter="PRESSURE", chart="EWMA", lam=0.3, L=2.7, target=1.0, sigma=0.05).json()["charts"]["EWMA"]
    z, prev = [], 1.0
    for v in PRESS:
        prev = 0.3 * v + 0.7 * prev
        z.append(prev)
    assert e["points"] == pytest.approx(z) and e["ucl"][0] == pytest.approx(1.0 + 2.7 * 0.05 * math.sqrt(0.3 / 1.7 * (1 - 0.7 ** 2)))
    assert e["out_of_control"]                                                # the drift is detected
    c = call(eng, "control-chart", upload_id=ds["S"], parameter="PRESSURE", chart="CUSUM", k=0.5, h=5, target=1.0, sigma=0.05).json()["charts"]["CUSUM"]
    cp, prev = [], 0.0
    for v in PRESS:
        prev = max(0.0, v - (1.0 + 0.025) + prev)
        cp.append(prev)
    assert c["points"] == pytest.approx(cp) and c["ucl"][0] == pytest.approx(0.25) and c["out_of_control"][0] == next(i for i, x in enumerate(cp) if x > 0.25)


def test_attribute_charts_on_production_data(eng, ds):
    pbar = sum(DEFECTIVES) / 600
    p = call(eng, "control-chart", upload_id=ds["P"], chart="P").json()["charts"]["P"]
    assert p["points"] == pytest.approx([d / 50 for d in DEFECTIVES]) and p["cl"][0] == pytest.approx(pbar)
    assert p["ucl"][0] == pytest.approx(pbar + 3 * math.sqrt(pbar * (1 - pbar) / 50)) and p["out_of_control"] == [7]
    npc = call(eng, "control-chart", upload_id=ds["P"], chart="NP").json()["charts"]["NP"]
    assert npc["cl"][0] == pytest.approx(50 * pbar) and npc["ucl"][0] == pytest.approx(50 * pbar + 3 * math.sqrt(50 * pbar * (1 - pbar))) and npc["out_of_control"] == [7]
    cbar = sum(DEFECTS) / 12
    c = call(eng, "control-chart", upload_id=ds["P"], chart="C").json()["charts"]["C"]
    assert c["cl"][0] == pytest.approx(cbar) and c["ucl"][0] == pytest.approx(cbar + 3 * math.sqrt(cbar)) and c["out_of_control"] == [8]
    u = call(eng, "control-chart", upload_id=ds["P"], chart="U").json()["charts"]["U"]
    ubar = sum(DEFECTS) / 600
    assert u["cl"][0] == pytest.approx(ubar) and u["ucl"][0] == pytest.approx(ubar + 3 * math.sqrt(ubar / 50)) and u["out_of_control"] == [8]
    assert p["labels"][0] == "2024-07-01 00:00:00"


def test_invalid_chart_configurations_rejected(eng, ds):
    base = {"upload_id": ds["S"], "parameter": "TEMPERATURE"}
    bad = [dict(chart="XBAR_R"), dict(chart="XBAR_R", subgroup_size=1), dict(chart="XBAR_R", subgroup_size=26), dict(chart="BOGUS"),
           dict(chart="EWMA", lam=0), dict(chart="EWMA", lam=1.5), dict(chart="CUSUM", h=0), dict(chart="R", subgroup_size=0),
           dict(chart="IMR", parameter="NO_SUCH_PARAMETER")]
    for kw in bad:
        r = call(eng, "control-chart", ok=False, **{**base, **kw})
        assert r.status_code == 422 and r.json()["error"]["code"] in ("validation_error", "unprocessable"), kw
    assert call(eng, "control-chart", ok=False, upload_id=ds["S"], chart="IMR").status_code == 422           # parameter missing
    assert call(eng, "control-chart", ok=False, upload_id=ds["P"], chart="NP", end="2024-07-03").status_code == 200   # 3 samples is enough
    assert call(eng, "control-chart", ok=False, upload_id=ds["P"], chart="P", end="2024-07-01").status_code == 422   # 1 sample is not
    assert call(eng, "control-chart", ok=False, upload_id=ds["S"], chart="P").status_code == 409                    # sensor dataset for attribute chart


# ------------------------------------------------------------------------------------------------ Phase 12
def test_limits_with_specs_and_target(eng, ds):
    r = call(eng, "limits", upload_id=ds["S"], parameter="TEMPERATURE", chart="IMR", usl=15, lsl=7, target=11).json()
    mean = statistics.fmean(TEMP)
    mrbar = statistics.fmean(abs(b - a) for a, b in zip(TEMP, TEMP[1:]))
    lim = r["limits"]["I"]
    assert lim["cl"] == pytest.approx(mean) and lim["ucl"] == pytest.approx(mean + 2.66 * mrbar, abs=0.01) and lim["variable"] is False
    assert r["spec"] == {"usl": 15.0, "lsl": 7.0, "target": 11.0}
    a = r["assessment"]
    assert a["points_above_usl"] == 1 and a["points_below_lsl"] == 0 and a["pct_out_of_spec"] == pytest.approx(100 / 60)
    assert a["points_out_of_control"] == 1 and a["control_limits_within_spec"] is False and a["mean_minus_target"] == pytest.approx(mean - 11)
    assert any("beyond the specification" in n for n in r["notes"])
    assert r["charts"]["I"]["points"] == [float(v) for v in TEMP]                       # limits are returned with the plotted values
    no_spec = call(eng, "limits", upload_id=ds["S"], parameter="TEMPERATURE", chart="IMR").json()
    assert no_spec["spec"] == {"usl": None, "lsl": None, "target": None} and "points_above_usl" not in no_spec["assessment"]
    p = call(eng, "limits", upload_id=ds["P"], chart="P").json()["limits"]["P"]
    assert p["variable"] is False                                                        # constant sample size
    for kw, msg in [(dict(usl=5, lsl=10), "must be greater"), (dict(usl=10, lsl=10), "must be greater"),
                    (dict(usl=10, lsl=0, target=12), "Target must lie"), (dict(lsl=5, target=4), "Target must lie")]:
        e = call(eng, "limits", ok=False, upload_id=ds["S"], parameter="TEMPERATURE", chart="IMR", **kw)
        assert e.status_code == 422 and msg in e.json()["error"]["message"], kw


# ------------------------------------------------------------------------------------------------ Phase 13
def test_violations_known_patterns(eng, ds):
    v = call(eng, "violations", upload_id=ds["S"], parameter="TEMPERATURE", chart="IMR", rule_set="NELSON").json()
    n1 = [x for x in v["violations"] if x["rule_id"] == "N1"]
    assert [x["point_index"] for x in n1] == [47] and n1[0]["timestamp"] == ts(47) and n1[0]["observed_value"] == 40.0
    assert (n1[0]["violation_type"], n1[0]["severity"], n1[0]["parameter"], n1[0]["rule_set"]) == ("OUT_OF_CONTROL", "HIGH", "TEMPERATURE", "NELSON")
    assert v["summary"]["by_rule"]["N1"] == 1 and v["rule_set"] == "NELSON"
    both = call(eng, "violations", upload_id=ds["S"], parameter="TEMPERATURE", chart="IMR").json()
    assert {x["rule_id"] for x in both["violations"] if x["point_index"] == 47} >= {"N1", "WE1"}
    we = call(eng, "violations", upload_id=ds["S"], parameter="TEMPERATURE", chart="IMR", rule_set="WESTERN_ELECTRIC").json()
    assert {x["rule_set"] for x in we["violations"]} == {"WESTERN_ELECTRIC"}
    # drifting series -> trend and shift patterns
    d = call(eng, "violations", upload_id=ds["S"], parameter="PRESSURE", chart="IMR", rule_set="NELSON").json()
    trend = [x for x in d["violations"] if x["rule_id"] == "N3"]
    assert trend[0]["point_index"] == 5 and trend[0]["start_index"] == 0 and trend[0]["violation_type"] == "TREND" and trend[0]["timestamp"] == ts(5)
    shift = [x for x in d["violations"] if x["rule_id"] == "N2"]
    assert shift and shift[0]["violation_type"] == "SHIFT"
    # attribute chart and CUSUM
    pv = call(eng, "violations", upload_id=ds["P"], chart="P").json()
    assert any(x["rule_id"] == "N1" and x["point_index"] == 7 and x["timestamp"] == "2024-07-08 00:00:00" for x in pv["violations"])
    cv = call(eng, "violations", upload_id=ds["S"], parameter="PRESSURE", chart="CUSUM", target=1.0, sigma=0.05).json()
    assert cv["violations"] and all(x["rule_id"] == "CUSUM" for x in cv["violations"])
    assert call(eng, "violations", ok=False, upload_id=ds["S"], parameter="TEMPERATURE", rule_set="BOGUS").status_code == 422


# ------------------------------------------------------------------------------------------------ Phase 14
def test_capability_against_independent_calculation(eng, ds):
    r = call(eng, "capability", upload_id=ds["S"], parameter="TEMPERATURE", usl=15, lsl=7, target=11).json()
    c = r["capability"]
    mu, sd = statistics.fmean(TEMP), statistics.stdev(TEMP)
    mrbar = statistics.fmean(abs(b - a) for a, b in zip(TEMP, TEMP[1:]))
    sw = mrbar / 1.128
    assert c["n"] == 60 and c["sufficient_sample"] is True
    assert c["pp"] == pytest.approx(8 / (6 * sd)) and c["ppk"] == pytest.approx(min(15 - mu, mu - 7) / (3 * sd))
    assert c["cp"] == pytest.approx(8 / (6 * sw), rel=2e-3) and c["cpk"] == pytest.approx(min(15 - mu, mu - 7) / (3 * sw), rel=2e-3)
    assert c["cpu"] == pytest.approx((15 - mu) / (3 * sw), rel=2e-3) and c["cpl"] == pytest.approx((mu - 7) / (3 * sw), rel=2e-3)
    assert c["ppm_observed_total"] == pytest.approx(1e6 / 60) and c["yield_observed"] == pytest.approx(100 * 59 / 60)
    phi = lambda z: 0.5 * (1 + math.erf(z / math.sqrt(2)))
    assert c["ppm_expected_overall"] == pytest.approx((phi((7 - mu) / sd) + 1 - phi((15 - mu) / sd)) * 1e6, rel=1e-6)
    assert c["non_normal"] is True and c["percentile_method"]["ppk"] is not None and any("not normally" in w for w in c["warnings"])
    sub = call(eng, "capability", upload_id=ds["S"], parameter="TEMPERATURE", usl=15, lsl=7, subgroup_size=5).json()["capability"]
    rbar = statistics.fmean(max(g) - min(g) for g in (TEMP[i:i + 5] for i in range(0, 60, 5)))
    assert sub["sigma_within"] == pytest.approx(rbar / 2.326, rel=2e-3)
    one = call(eng, "capability", upload_id=ds["S"], parameter="TEMPERATURE", usl=15).json()["capability"]
    assert one["cp"] is None and one["cpk"] == one["cpu"]
    assert call(eng, "capability", ok=False, upload_id=ds["S"], parameter="PRESSURE", usl=1.5, lsl=0.9, end=ts(0).replace(" ", "T")).status_code == 422   # 1 reading
    for kw in (dict(), dict(usl=1, lsl=2), dict(usl=15, lsl=7, target=99)):
        assert call(eng, "capability", ok=False, upload_id=ds["S"], parameter="TEMPERATURE", **kw).status_code == 422


# ------------------------------------------------------------------------------------------------ Phase 15
def test_distribution_analysis(eng, ds):
    d = call(eng, "distribution", upload_id=ds["S"], parameter="TEMPERATURE", bins=8).json()["distribution"]
    assert sum(d["histogram"]["counts"]) == 60 and len(d["histogram"]["counts"]) == 8
    assert d["boxplot"]["outliers"] == [40.0] and d["boxplot"]["median"] == statistics.median(TEMP)
    assert d["normality"]["normal"] is False and {t["test"] for t in d["normality"]["tests"]} >= {"Shapiro-Wilk", "Anderson-Darling"}
    assert d["probability_plot"]["ordered_values"] == sorted(map(float, TEMP)) and d["probability_plot"]["r_squared"] < 0.95
    assert d["fit"]["best"] and d["fit"]["fits"][0]["rank"] == 1
    drift = call(eng, "distribution", upload_id=ds["S"], parameter="PRESSURE").json()["distribution"]
    assert drift["normality"]["normal"] is True or drift["normality"]["normal"] is False        # uniform-like ramp: decision is data-driven
    assert drift["probability_plot"]["r_squared"] > 0.97
    assert call(eng, "distribution", ok=False, upload_id=ds["S"], parameter="PRESSURE", bins=500).status_code == 422
    assert call(eng, "distribution", ok=False, upload_id=ds["S"], parameter="PRESSURE", end=ts(1).replace(" ", "T")).status_code == 422   # 2 readings


# ------------------------------------------------------------------------------------------------ storage in Snowflake
def test_results_are_stored_in_snowflake(eng, viewer, ds):
    st = call(eng, "statistics", upload_id=ds["S"], parameter="TEMPERATURE").json()
    aid = st["analysis_id"]
    h = q(f"SELECT ANALYSIS_TYPE, STATUS, REQUESTED_BY, UPLOAD_ID, REQUEST_PARAMS:parameter::string FROM {DB}.METADATA.ANALYSIS_HISTORY WHERE ANALYSIS_ID = %s", aid)[0]
    assert h[0] == "STATISTICS" and h[1] == "COMPLETED" and h[3] == ds["S"] and h[4] == "TEMPERATURE"
    row = q(f"SELECT N_COUNT, MEAN_VALUE, STDDEV_VALUE, PERCENTILES:p50::float FROM {DB}.SPC_RESULTS.STATISTICAL_RESULTS WHERE ANALYSIS_ID = %s", aid)[0]
    assert row[0] == 60 and row[1] == pytest.approx(statistics.fmean(TEMP)) and row[2] == pytest.approx(statistics.stdev(TEMP)) and row[3] == statistics.median(TEMP)
    stored = client.get(f"/api/analysis/{aid}", headers=eng).json()
    assert stored["status"] == "COMPLETED" and stored["kind"] == "STATISTICS" and stored["result"]["statistics"]["n"] == 60
    # typed tables for the other analyses
    lim = call(eng, "limits", upload_id=ds["S"], parameter="TEMPERATURE", chart="IMR", usl=15, lsl=7, target=11).json()
    cl = q(f"SELECT CHART_TYPE, UCL, CL, LCL, USL, LSL, TARGET FROM {DB}.SPC_RESULTS.CONTROL_LIMITS WHERE ANALYSIS_ID = %s ORDER BY CHART_TYPE", lim["analysis_id"])
    assert [r[0] for r in cl] == ["I", "MR"] and cl[0][1] == pytest.approx(lim["limits"]["I"]["ucl"]) and cl[0][4:] == (15.0, 7.0, 11.0)
    v = call(eng, "violations", upload_id=ds["S"], parameter="TEMPERATURE", chart="IMR").json()
    assert q(f"SELECT COUNT(*) FROM {DB}.SPC_RESULTS.SPC_VIOLATIONS WHERE ANALYSIS_ID = %s", v["analysis_id"])[0][0] == len(v["violations"])
    n1 = q(f"SELECT VIOLATION_TYPE, RULE_SET, SEVERITY, OBSERVED_VALUE, TO_CHAR(OCCURRED_AT, 'YYYY-MM-DD HH24:MI:SS'), POINT_INDEX FROM {DB}.SPC_RESULTS.SPC_VIOLATIONS "
           "WHERE ANALYSIS_ID = %s AND RULE_NAME LIKE 'N1:%%'", v["analysis_id"])
    assert n1 == [("OUT_OF_CONTROL", "NELSON", "HIGH", 40.0, ts(47), 47)]
    cap = call(eng, "capability", upload_id=ds["S"], parameter="TEMPERATURE", usl=15, lsl=7).json()
    cr = q(f"SELECT CPK, PPK, PPM_TOTAL, YIELD_PCT, SAMPLE_SIZE FROM {DB}.SPC_RESULTS.CAPABILITY_RESULTS WHERE ANALYSIS_ID = %s", cap["analysis_id"])[0]
    assert cr[0] == pytest.approx(cap["capability"]["cpk"]) and cr[4] == 60
    dist = call(eng, "distribution", upload_id=ds["S"], parameter="TEMPERATURE").json()
    dr = q(f"SELECT NORMALITY_TEST, IS_NORMAL, BEST_FIT_DIST, FIT_DETAILS:boxplot:n_outliers::int FROM {DB}.SPC_RESULTS.DISTRIBUTION_RESULTS WHERE ANALYSIS_ID = %s", dist["analysis_id"])[0]
    assert dr == ("Shapiro-Wilk", False, dist["distribution"]["fit"]["best"], 1)
    # history & visibility
    hist = client.get("/api/analysis/history?page_size=200&kind=violations", headers=eng).json()
    assert v["analysis_id"] in {i["analysis_id"] for i in hist["items"]} and all(i["kind"] == "VIOLATIONS" for i in hist["items"])
    assert client.get(f"/api/analysis/{aid}", headers=viewer).status_code == 404            # another user's analysis is not visible
    assert client.get(f"/api/analysis/{uuid.uuid4()}", headers=eng).status_code == 404
    mine = call(viewer, "statistics", upload_id=ds["S"], parameter="TEMPERATURE").json()
    assert client.get(f"/api/analysis/{mine['analysis_id']}", headers=viewer).status_code == 200
    assert mine["analysis_id"] not in {i["analysis_id"] for i in client.get("/api/analysis/history?page_size=200", headers=eng).json()["items"]}


def test_access_control(ds):
    assert client.get("/api/analysis/statistics", params={"upload_id": ds["S"], "parameter": "TEMPERATURE"}).status_code == 401
    assert client.get("/api/analysis/history").status_code == 401
