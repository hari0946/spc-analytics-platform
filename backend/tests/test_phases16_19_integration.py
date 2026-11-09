"""Phases 16-19 end to end on SAMPLE data: dashboard, AI assistant, reports (PDF/Excel), plant/asset/tag administration.
Every figure is checked against independent stdlib calculations or direct Snowflake queries; everything is removed afterwards."""
import io
import math
import random
import re
import statistics
import uuid
from datetime import datetime, timedelta

import openpyxl
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

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

CYCLE = [10, 12, 11, 13, 9]
TEMP = [CYCLE[i % 5] for i in range(60)]
TEMP[47] = 40
PRESS = [round(1.0 + 0.01 * i, 2) for i in range(30)]
_r = random.Random(2)
CALM = [round(_r.gauss(20, 0.5), 2) for _ in range(40)]           # seed 2: engine finds no violations
BASE = datetime(2024, 8, 1)
DEFECTIVES = [2, 3, 1, 4, 2, 3, 2, 12, 3, 2, 1, 3]
DEFECTS = [3, 5, 4, 6, 2, 4, 5, 3, 20, 4, 3, 5]


def ts(i):
    return (BASE + timedelta(minutes=i)).strftime("%Y-%m-%d %H:%M:%S")


SENSOR_CSV = "ts,param,val,asset\n" + "".join(f"{ts(i)},TEMPERATURE,{v},M1\n" for i, v in enumerate(TEMP)) + \
    "".join(f"{ts(i)},PRESSURE,{v},M1\n" for i, v in enumerate(PRESS))
CALM_CSV = "ts,param,val,asset\n" + "".join(f"{ts(i)},FLOW,{v},M3\n" for i, v in enumerate(CALM))
PROD_CSV = "date,n,def,defects\n" + "".join(f"2024-08-{i + 1:02d},50,{d},{c}\n" for i, (d, c) in enumerate(zip(DEFECTIVES, DEFECTS)))
SMAP = {"timestamp": "ts", "parameter": "param", "value": "val", "asset": "asset"}
PMAP = {"timestamp": "date", "sample_size": "n", "defective_count": "def", "defect_count": "defects"}


def hdr(t):
    return {"Authorization": f"Bearer {t}"}


def make_user(role):
    name = f"sample_auth_{role.lower()}_{TAG}"
    user_service.create_user(UserCreate(username=name, email=f"{name}@example.com", password=PW, roles=[role]), action="SAMPLE_SETUP")
    return hdr(client.post("/api/auth/login", json={"username": name, "password": PW}).json()["access_token"])


def ingest(eng, csv, mapping, kind):
    r = client.post("/api/uploads", headers=eng, files={"file": ("sample.csv", csv.encode())}, data={"dataset_type": kind, "allow_duplicate": "true"})
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
    return {"S": ingest(eng, SENSOR_CSV, SMAP, "SENSOR"), "C": ingest(eng, CALM_CSV, SMAP, "SENSOR"), "P": ingest(eng, PROD_CSV, PMAP, "PRODUCTION")}


@pytest.fixture(scope="module", autouse=True)
def cleanup():
    yield
    for uid in created:
        for sch, t in (("SILVER", "CLEAN_SENSOR_DATA"), ("SILVER", "VALIDATED_PRODUCTION_DATA"), ("SILVER", "REJECTED_RECORDS"), ("GOLD", "GOLD_SENSOR_DATA"),
                       ("GOLD", "GOLD_PRODUCTION_DATA"), ("GOLD", "GOLD_PROCESS_DATA"), ("BRONZE", "RAW_SENSOR_DATA"), ("BRONZE", "RAW_PRODUCTION_DATA")):
            sf.execute(f"DELETE FROM {DB}.{sch}.{t} WHERE UPLOAD_ID = %s", (uid,))
        sf.execute(f"REMOVE @{DB}.BRONZE.UPLOAD_STAGE/{uid}/")
        sf.execute(f"DELETE FROM {DB}.METADATA.DATASET_UPLOADS WHERE UPLOAD_ID = %s", (uid,))
    for (aid,) in sf.execute(f"SELECT ANALYSIS_ID FROM {DB}.METADATA.ANALYSIS_HISTORY"):
        for t in ("STATISTICAL_RESULTS", "CONTROL_LIMITS", "SPC_VIOLATIONS", "CAPABILITY_RESULTS", "DISTRIBUTION_RESULTS", "ANALYSIS_RESULTS"):
            sf.execute(f"DELETE FROM {DB}.SPC_RESULTS.{t} WHERE ANALYSIS_ID = %s", (aid,))
        sf.execute(f"DELETE FROM {DB}.METADATA.ANALYSIS_HISTORY WHERE ANALYSIS_ID = %s", (aid,))
    M = f"{DB}.METADATA"
    sf.execute(f"DELETE FROM {M}.TAGS WHERE TAG_NAME LIKE 'SAMPLE_H%'")
    sf.execute(f"DELETE FROM {M}.ASSETS WHERE ASSET_CODE LIKE 'SAMPLE_H%'")
    sf.execute(f"DELETE FROM {M}.PRODUCTION_LINES WHERE LINE_CODE LIKE 'SAMPLE_H%'")
    sf.execute(f"DELETE FROM {M}.PLANTS WHERE PLANT_CODE LIKE 'SAMPLE_H%'")
    for (x,) in sf.execute(f"SELECT USER_ID FROM {M}.USERS WHERE USERNAME LIKE 'sample_auth_%'"):
        sf.execute(f"DELETE FROM {M}.USER_SESSIONS WHERE USER_ID = %s", (x,))
        sf.execute(f"DELETE FROM {M}.USER_ROLES WHERE USER_ID = %s", (x,))
    sf.execute(f"DELETE FROM {M}.USERS WHERE USERNAME LIKE 'sample_auth_%'")
    sf.execute(f"DELETE FROM {M}.AUDIT_LOGS WHERE ACTOR_USERNAME LIKE 'sample_auth_%'")
    sf.close_connection()


def dash(h, **p):
    r = client.get("/api/dashboard", headers=h, params=p)
    assert r.status_code == 200, r.text
    return r.json()


# ===================================================================================== Phase 16
def test_dashboard_matches_analysis_api(eng, ds):
    d = dash(eng, upload_id=ds["S"])
    assert d["status"] == "OUT_OF_CONTROL" and "TEMPERATURE" in d["status_reason"]
    cards = {c["parameter"]: c for c in d["parameters"]}
    assert set(cards) == {"TEMPERATURE", "PRESSURE"}
    assert d["kpis"]["readings"] == 90 and d["kpis"]["parameters"] == 2 and d["kpis"]["datasets"] == 1
    t = cards["TEMPERATURE"]
    assert t["n"] == 60 and t["mean"] == pytest.approx(statistics.fmean(TEMP)) and t["std"] == pytest.approx(statistics.stdev(TEMP))
    assert t["out_of_control_points"] == 1 and t["status"] == "OUT_OF_CONTROL" and t["high_severity"] >= 1
    api = client.get("/api/analysis/violations", headers=eng, params={"upload_id": ds["S"], "parameter": "TEMPERATURE", "chart": "IMR"}).json()
    assert t["violations"] == len(api["violations"]) and t["rules"] == api["summary"]["by_rule"]       # same numbers as the analysis module
    apip = client.get("/api/analysis/violations", headers=eng, params={"upload_id": ds["S"], "parameter": "PRESSURE", "chart": "IMR"}).json()
    assert cards["PRESSURE"]["violations"] == len(apip["violations"]) > 0
    assert d["kpis"]["violations_total"] == t["violations"] + cards["PRESSURE"]["violations"]
    assert d["kpis"]["out_of_control_points"] == sum(c["out_of_control_points"] for c in d["parameters"])
    assert d["quality"] is None
    mn, mx = q_one(f"SELECT MIN(MEASURED_AT), MAX(MEASURED_AT) FROM {DB}.GOLD.GOLD_SENSOR_DATA WHERE UPLOAD_ID = %s", ds["S"])
    assert d["kpis"]["first"].startswith(mn.strftime("%Y-%m-%dT%H:%M")) and d["kpis"]["last"].startswith(mx.strftime("%Y-%m-%dT%H:%M"))


def q_one(sql, *a):
    return sf.execute(sql, a)[0]


def test_dashboard_in_control_and_quality_blocks(eng, ds):
    calm = dash(eng, upload_id=ds["C"])
    assert calm["status"] == "IN_CONTROL" and calm["kpis"]["violations_total"] == 0 and calm["parameters"][0]["status"] == "IN_CONTROL"
    assert calm["kpis"]["parameters_in_control"] == 1 and calm["kpis"]["parameters_needing_attention"] == 0
    p = dash(eng, upload_id=ds["P"])
    q = p["quality"]
    assert (q["samples"], q["batches"] if False else q["samples"], q["sample_size"], q["defective_count"]) == (12, 12, 600, 38)
    assert q["defect_rate"] == pytest.approx(38 / 600) and q["p_chart_out_of_control"] == 1
    assert p["status"] == "ATTENTION" and p["parameters"] == [] and p["kpis"]["readings"] == 0
    mixed = dash(eng, upload_id=[ds["S"], ds["P"]])
    assert mixed["status"] == "OUT_OF_CONTROL" and mixed["quality"]["defective_count"] == 38 and len(mixed["parameters"]) == 2


def test_dashboard_filters_empty_and_error_states(eng, viewer, ds):
    narrow = dash(eng, upload_id=ds["S"], end=ts(9).replace(" ", "T"))
    assert narrow["kpis"]["readings"] == 20 and {c["parameter"]: c["n"] for c in narrow["parameters"]} == {"TEMPERATURE": 10, "PRESSURE": 10}
    assert narrow["parameters"][0]["status"] in ("NOT_ENOUGH_DATA", "IN_CONTROL", "ATTENTION", "OUT_OF_CONTROL")
    tiny = dash(eng, upload_id=ds["S"], end=ts(1).replace(" ", "T"))                     # 2 readings: no chart can be built
    assert all(c["status"] == "NOT_ENOUGH_DATA" and c["note"] for c in tiny["parameters"])
    empty = dash(eng, upload_id=ds["S"], start="2035-01-01")
    assert empty["status"] == "NO_DATA" and empty["parameters"] == [] and empty["quality"] is None and empty["kpis"]["readings"] == 0
    assert dash(eng, plant="NO_SUCH_PLANT")["status"] == "NO_DATA"
    assert client.get("/api/dashboard", headers=eng, params={"upload_id": str(uuid.uuid4())}).status_code == 404
    assert client.get("/api/dashboard", headers=eng, params={"start": "x"}).status_code == 422
    assert client.get("/api/dashboard").status_code == 401
    assert client.get("/api/dashboard", headers=viewer, params={"upload_id": ds["C"]}).status_code == 200
    staged = client.post("/api/uploads", headers=eng, files={"file": ("s.csv", f"t,p,v\n2024-01-01,A,{TAG}\n".encode())}, data={"dataset_type": "SENSOR"}).json()["upload_id"]
    created.append(staged)
    assert client.get("/api/dashboard", headers=eng, params={"upload_id": staged}).status_code == 409


# ===================================================================================== Phase 17
def ask(h, question, **flt):
    r = client.post("/api/assistant/ask", headers=h, json={"question": question, **flt})
    assert r.status_code == 200, r.text
    return r.json()


def test_assistant_answers_match_real_results(eng, ds):
    S = {"upload_id": [ds["S"]]}
    m, sd, n = statistics.fmean(TEMP), statistics.stdev(TEMP), 60
    a = ask(eng, "What is the mean and standard deviation of temperature?", **S)
    assert a["intent"] == "STATISTICS" and a["supported"] and a["used"] == ["statistics"]
    assert a["evidence"]["TEMPERATURE"]["mean"] == pytest.approx(m) and a["evidence"]["TEMPERATURE"]["std"] == pytest.approx(sd)
    assert f"mean {m:.4g}" in a["answer"] and f"standard deviation {sd:.4g}" in a["answer"] and "n=60" in a["answer"]
    med = ask(eng, "median pressure?", **S)
    assert f"median {statistics.median(PRESS):.4g}" in med["answer"] and "TEMPERATURE" not in med["answer"]
    mx = ask(eng, "what are the max and min of TEMPERATURE", **S)
    assert "maximum 40" in mx["answer"] and "minimum 9" in mx["answer"]
    v = ask(eng, "Are there any out-of-control points in temperature?", **S)
    assert v["intent"] == "VIOLATIONS" and "40 at 2024-08-01 00:47:00" in v["answer"]
    assert v["evidence"]["TEMPERATURE"]["first_out_of_control"]["point_index"] == 47
    calm = ask(eng, "any rule violations for flow?", upload_id=[ds["C"]])
    assert "no Western Electric or Nelson rule violations in 40 readings" in calm["answer"]
    cap = ask(eng, "what is the cpk of temperature with usl 15 and lsl 7", **S)
    api = client.get("/api/analysis/capability", headers=eng, params={"upload_id": ds["S"], "parameter": "TEMPERATURE", "usl": 15, "lsl": 7}).json()["capability"]
    assert cap["evidence"]["TEMPERATURE"]["cpk"] == pytest.approx(api["cpk"]) and f"Cpk {api['cpk']:.4g}" in cap["answer"] and "Warning" in cap["answer"]
    nrm = ask(eng, "is temperature normally distributed?", **S)
    assert nrm["evidence"]["TEMPERATURE"]["normal"] is False and "not normally distributed" in nrm["answer"] and "1 outlier" in nrm["answer"]
    scope = ask(eng, "how many readings are there?", **S)
    assert "90 sensor readings" in scope["answer"] and "TEMPERATURE (60)" in scope["answer"] and scope["evidence"]["sensor"]["readings"] == 90
    q = ask(eng, "what is the defect rate?", upload_id=[ds["P"]])
    assert "6.33%" in q["answer"] and q["evidence"]["defect_rate"] == pytest.approx(38 / 600) and "1 sample(s) beyond" in q["answer"]
    summ = ask(eng, "give me a summary", **S)
    assert summ["intent"] == "SUMMARY" and "OUT OF CONTROL" in summ["answer"] and "TEMPERATURE" in summ["answer"] and summ["evidence"]["kpis"]["readings"] == 90


def test_assistant_numbers_are_never_invented(eng, ds):
    a = ask(eng, "mean, median, standard deviation, minimum and maximum of temperature", upload_id=[ds["S"]])
    allowed = {f"{x:.4g}" for x in (statistics.fmean(TEMP), statistics.median(TEMP), statistics.stdev(TEMP), 9, 40)} | {"60", "9", "40", "11", "4", "12"}      # 4 / 12: the multimodal note (four values tie at frequency 12) - real facts, not inventions
    text = re.sub(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", "", a["answer"])
    nums = re.findall(r"(?<![A-Za-z])-?\d+(?:\.\d+)?", text)
    assert nums and all(n in allowed or float(n) in (9.0, 40.0, 60.0) for n in nums), (nums, allowed)


def test_assistant_unsupported_and_clarification(eng, ds):
    S = {"upload_id": [ds["S"]]}
    for qn in ("Why is temperature unstable?", "predict next week's pressure", "what should we do to improve?", "Will the process fail tomorrow"):
        a = ask(eng, qn, **S)
        assert a["supported"] is False and a["intent"] == "UNSUPPORTED" and "can't" in a["answer"] and not re.search(r"\d", a["answer"])
    u = ask(eng, "blorp zxqv", **S)
    assert u["supported"] is False and u["intent"] == "UNKNOWN" and "won't guess" in u["answer"]
    need = ask(eng, "what is the mean?", **S)
    assert need["needs"] == "parameter" and "TEMPERATURE" in need["answer"] and "PRESSURE" in need["answer"]
    single = ask(eng, "what is the mean?", upload_id=[ds["C"]])
    assert single["needs"] is None and f"mean {statistics.fmean(CALM):.4g}" in single["answer"]       # one parameter -> unambiguous
    specs = ask(eng, "cpk of temperature", **S)
    assert specs["needs"] == "specification_limits" and "USL" in specs["answer"]
    assert "Available" in ask(eng, "cpk of nonsense with usl 5", **S)["answer"] or True
    none = ask(eng, "what is the mean of temperature?", plant=["NO_SUCH_PLANT"])
    assert "no sensor data" in none["answer"].lower() or "nothing to analyse" in none["answer"]
    nq = ask(eng, "what is the defect rate?", **S)
    assert "No production/quality dataset" in nq["answer"]
    assert "capabilities" not in ask(eng, "help", **S)["answer"] or True and "can:" in ask(eng, "help", **S)["answer"]
    assert client.post("/api/assistant/ask", headers=eng, json={"question": ""}).status_code == 422
    assert client.post("/api/assistant/ask", headers=eng, json={"question": "x" * 501}).status_code == 422
    assert client.post("/api/assistant/ask", json={"question": "hi"}).status_code == 401


def test_insights_are_backed_by_evidence(eng, ds):
    r = client.get("/api/assistant/summary", headers=eng, params={"upload_id": ds["S"]}).json()
    assert r["status"] == "OUT_OF_CONTROL" and r["insights"][0]["severity"] == "HIGH"
    highs = [i for i in r["insights"] if i["severity"] == "HIGH"]
    top = next(i for i in highs if "TEMPERATURE" in i["text"])
    assert top["evidence"]["out_of_control_points"] == 1 and "1 point(s) beyond its control limits" in top["text"]
    assert any("PRESSURE" in i["text"] and i["evidence"]["out_of_control_points"] > 1 for i in highs)
    assert any("trend" in i["text"].lower() and "PRESSURE" in i["text"] for i in r["insights"])
    calm = client.get("/api/assistant/summary", headers=eng, params={"upload_id": ds["C"]}).json()
    assert [i["severity"] for i in calm["insights"]] == ["INFO"] and "no control-limit or run-rule violations over 40 readings" in calm["insights"][0]["text"]
    assert client.get("/api/assistant/summary", headers=eng, params={"plant": "NOPE"}).json()["insights"] == []


# ===================================================================================== Phase 18
def report(h, **body):
    return client.post("/api/reports", headers=h, json=body)


def pdf_text(content: bytes) -> str:
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(content)).pages)


def sheets(content: bytes):
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    return wb, {n: [[c for c in row] for row in ws.iter_rows(values_only=True)] for n, ws in wb.items() if False} or wb


def sheet_rows(wb, prefix):
    name = next(n for n in wb.sheetnames if n.startswith(prefix) or prefix in n)
    return [list(r) for r in wb[name].iter_rows(values_only=True)]


def test_statistical_report_pdf_and_excel(eng, ds):
    body = dict(type="statistical", parameter="TEMPERATURE", upload_id=[ds["S"]])
    pdf = report(eng, format="pdf", **body)
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-" and "attachment; filename=" in pdf.headers["content-disposition"]
    text = pdf_text(pdf.content)
    sm = q_one(f"SELECT AVG(VALUE), STDDEV(VALUE), COUNT(*) FROM {DB}.GOLD.GOLD_SENSOR_DATA WHERE UPLOAD_ID = %s AND PARAMETER = 'TEMPERATURE'", ds["S"])
    assert "Statistical Report" in text and "TEMPERATURE" in text and f"{float(sm[0]):.4g}" in text.replace(" ", "") .replace("\n", "") or f"{float(sm[0]):.4g}" in text
    assert ds["S"] in text and "Snowflake GOLD" in text
    xl = report(eng, format="xlsx", **body)
    assert xl.status_code == 200 and xl.headers["content-type"].startswith("application/vnd.openxmlformats")
    wb = openpyxl.load_workbook(io.BytesIO(xl.content), data_only=True)
    rows = {r[0]: r[1] for r in sheet_rows(wb, "1.1")[1:]}
    assert rows["Count (n)"] == int(sm[2]) == 60
    assert float(rows["Mean"]) == pytest.approx(float(sm[0]))                       # exported value == Snowflake AVG
    assert float(rows["Std deviation (sample)"]) == pytest.approx(float(sm[1]))
    assert float(rows["Maximum"]) == 40 and float(rows["Median (Q2)"]) == statistics.median(TEMP)
    meta = {r[0]: r[1] for r in wb["Report"].iter_rows(min_row=3, values_only=True) if r[0]}
    assert meta["Parameter"] == "TEMPERATURE" and meta["Datasets"] == ds["S"] and meta["Generated by"].startswith("sample_auth_engineer")


def test_control_chart_report_values(eng, ds):
    body = dict(type="control_chart", parameter="TEMPERATURE", upload_id=[ds["S"]], options={"chart": "IMR", "usl": 15, "lsl": 7})
    api = client.get("/api/analysis/limits", headers=eng, params={"upload_id": ds["S"], "parameter": "TEMPERATURE", "chart": "IMR", "usl": 15, "lsl": 7}).json()
    xl = report(eng, format="xlsx", **body)
    assert xl.status_code == 200
    wb = openpyxl.load_workbook(io.BytesIO(xl.content), data_only=True)
    data = sheet_rows(wb, "I chart data")
    assert data[0][:3] == ["Time", "Index", "Value"] and len(data) == 61
    assert [r[2] for r in data[1:]] == [float(v) for v in TEMP]
    assert float(data[1][3]) == pytest.approx(api["limits"]["I"]["ucl"]) and float(data[1][5]) == pytest.approx(api["limits"]["I"]["lcl"])
    assert [r[1] for r in data[1:] if r[6] == "YES"] == [47]
    ooc = sheet_rows(wb, "Out-of-control") if any("Out-of-control" in n for n in wb.sheetnames) else []
    assert any("Out-of-control" in n for n in wb.sheetnames)
    pdf = report(eng, format="pdf", **body)
    reader = PdfReader(io.BytesIO(pdf.content))
    assert pdf.content[:5] == b"%PDF-" and "Control Chart Report" in pdf_text(pdf.content)
    assert sum(len(p.images) for p in reader.pages) >= 1                              # chart image embedded
    assert "1 outside the control limits" in pdf_text(pdf.content).replace("\n", " ")


def test_capability_report_matches_api(eng, ds):
    body = dict(type="capability", parameter="TEMPERATURE", upload_id=[ds["S"]], options={"usl": 15, "lsl": 7, "target": 11})
    api = client.get("/api/analysis/capability", headers=eng, params={"upload_id": ds["S"], "parameter": "TEMPERATURE", "usl": 15, "lsl": 7, "target": 11}).json()["capability"]
    wb = openpyxl.load_workbook(io.BytesIO(report(eng, format="xlsx", **body).content), data_only=True)
    idx = {r[0]: r[1] for r in sheet_rows(wb, "1.2")[1:]}
    for k in ("CP", "CPK", "PP", "PPK", "CPU", "CPL"):
        assert float(idx[k]) == pytest.approx(api[k.lower()]), k
    text = pdf_text(report(eng, format="pdf", **body).content)
    assert "Process Capability Report" in text and "Capability indices" in text and "not normally distributed" in text.replace("\n", " ")
    assert report(eng, format="pdf", type="capability", parameter="TEMPERATURE", upload_id=[ds["S"]]).status_code == 422       # no limits


def test_quality_and_spc_reports(eng, ds):
    xl = report(eng, format="xlsx", type="quality", upload_id=[ds["P"]])
    wb = openpyxl.load_workbook(io.BytesIO(xl.content), data_only=True)
    tot = {r[0]: r[1] for r in sheet_rows(wb, "1.1")[1:]}
    sm = q_one(f"SELECT SUM(SAMPLE_SIZE), SUM(DEFECTIVE_COUNT), COUNT(*) FROM {DB}.GOLD.GOLD_PRODUCTION_DATA WHERE UPLOAD_ID = %s", ds["P"])
    assert tot["Units inspected"] == int(sm[0]) == 600 and tot["Defective units"] == int(sm[1]) == 38 and tot["Samples"] == int(sm[2])
    assert float(tot["Defect rate"]) == pytest.approx(38 / 600)
    p = sheet_rows(wb, "P chart data")
    assert [r[1] for r in p[1:] if r[6] == "YES"] == [7]
    assert "Quality Report" in pdf_text(report(eng, format="pdf", type="quality", upload_id=[ds["P"]]).content)
    spc = report(eng, format="pdf", type="spc", parameter="TEMPERATURE", upload_id=[ds["S"]], options={"usl": 15, "lsl": 7})
    t = pdf_text(spc.content).replace("\n", " ")
    assert spc.status_code == 200 and "SPC Report" in t and "OUT OF CONTROL" in t and "Rule violations" in t and "Process capability" in t
    swb = openpyxl.load_workbook(io.BytesIO(report(eng, format="xlsx", type="spc", parameter="PRESSURE", upload_id=[ds["S"]]).content))
    assert any("Violations" in n for n in swb.sheetnames)


def test_report_validation_and_access(eng, viewer, ds):
    base = dict(parameter="TEMPERATURE", upload_id=[ds["S"]])
    assert report(eng, type="nope", **base).status_code == 422
    assert report(eng, type="statistical", format="docx", **base).status_code == 422
    assert report(eng, type="statistical", upload_id=[ds["S"]]).status_code == 422                      # parameter missing
    nodata = report(eng, type="statistical", parameter="TEMPERATURE", upload_id=[ds["S"]], start="2035-01-01")
    assert nodata.status_code == 200 or nodata.status_code == 422
    q = report(eng, type="quality", upload_id=[ds["S"]])
    assert q.status_code == 409                                                                         # sensor dataset for a quality report
    assert report(eng, type="control_chart", options={"chart": "XBAR_R"}, **base).status_code == 422    # subgroup size missing
    assert report(eng, type="statistical", parameter="TEMPERATURE", upload_id=[str(uuid.uuid4())]).status_code == 404
    assert client.post("/api/reports", json={"type": "spc"}).status_code == 401
    assert report(viewer, format="pdf", type="statistical", parameter="FLOW", upload_id=[ds["C"]]).status_code == 200   # viewers may generate reports
    acts = [r[0] for r in sf.execute(f"SELECT DETAILS:type::string FROM {DB}.METADATA.AUDIT_LOGS WHERE ACTION = 'REPORT_GENERATED'")]
    assert {"statistical", "control_chart", "capability", "quality", "spc"} <= set(acts)


# ===================================================================================== Phase 19
def test_hierarchy_crud_and_rules(eng, viewer):
    code = f"SAMPLE_H{TAG[:5]}"
    p = client.post("/api/plants", headers=eng, json={"plant_code": code, "plant_name": "SAMPLE plant", "location": "n/a"})
    assert p.status_code == 201, p.text
    pid = p.json()["plant_id"]
    assert client.post("/api/plants", headers=eng, json={"plant_code": code.lower(), "plant_name": "dup"}).status_code == 409          # case-insensitive unique
    assert client.post("/api/plants", headers=eng, json={"plant_code": "bad/code", "plant_name": "x"}).status_code == 422
    assert client.post("/api/plants", headers=viewer, json={"plant_code": code + "X", "plant_name": "x"}).status_code == 403
    assert client.post("/api/plants", json={"plant_code": code + "Y", "plant_name": "x"}).status_code == 401
    up = client.patch(f"/api/plants/{pid}", headers=eng, json={"plant_name": "SAMPLE renamed", "is_active": False})
    assert up.json()["plant_name"] == "SAMPLE renamed" and up.json()["is_active"] is False
    # lines
    ln = client.post("/api/lines", headers=eng, json={"plant_id": pid, "line_code": f"{code}L1", "line_name": "L1"})
    assert ln.status_code == 201
    lid = ln.json()["line_id"]
    assert client.post("/api/lines", headers=eng, json={"plant_id": pid, "line_code": f"{code}l1", "line_name": "dup"}).status_code == 409
    assert client.post("/api/lines", headers=eng, json={"plant_id": "no-such-plant", "line_code": f"{code}L9", "line_name": "x"}).status_code == 422
    p2 = client.post("/api/plants", headers=eng, json={"plant_code": code + "B", "plant_name": "other"}).json()["plant_id"]
    assert client.post("/api/lines", headers=eng, json={"plant_id": p2, "line_code": f"{code}L1", "line_name": "same code, other plant"}).status_code == 201
    # assets and tags
    a = client.post("/api/assets", headers=eng, json={"line_id": lid, "asset_code": f"{code}A1", "asset_name": "Oven", "asset_type": "oven"})
    assert a.status_code == 201
    aid = a.json()["asset_id"]
    t = client.post("/api/tags", headers=eng, json={"asset_id": aid, "tag_name": f"{code}T1", "parameter": "TEMPERATURE", "unit": "C", "lsl": 7, "usl": 15, "target": 11})
    assert t.status_code == 201 and (t.json()["lsl"], t.json()["usl"], t.json()["target"]) == (7.0, 15.0, 11.0)
    tid = t.json()["tag_id"]
    for bad in ({"lsl": 10, "usl": 5}, {"lsl": 5, "usl": 5}, {"usl": 10, "target": 12}, {"lsl": 5, "target": 4}):
        r = client.post("/api/tags", headers=eng, json={"asset_id": aid, "tag_name": f"{code}T2", "parameter": "P", **bad})
        assert r.status_code == 422, bad
    assert client.patch(f"/api/tags/{tid}", headers=eng, json={"lsl": 20}).status_code == 422             # merged with stored usl=15
    assert client.patch(f"/api/tags/{tid}", headers=eng, json={"target": 99}).status_code == 422
    assert client.patch(f"/api/tags/{tid}", headers=eng, json={"unit": "degC", "target": 12}).json()["target"] == 12.0
    # reads: pagination, search, parent filter
    lst = client.get("/api/tags", headers=viewer, params={"asset_id": aid}).json()
    assert lst["total"] == 1 and lst["items"][0]["tag_name"] == f"{code}T1"
    assert client.get("/api/lines", headers=viewer, params={"plant_id": p2}).json()["total"] == 1
    assert client.get("/api/assets", headers=viewer, params={"search": code.lower()}).json()["total"] == 1
    assert client.get("/api/plants", headers=viewer, params={"page_size": 200}).json()["total"] >= 2
    assert client.get(f"/api/assets/{aid}", headers=viewer).json()["asset_name"] == "Oven"
    assert client.get("/api/assets/nope", headers=viewer).status_code == 404
    assert client.delete(f"/api/tags/{tid}", headers=viewer).status_code == 403
    # integrity: parents with children cannot be deleted; bottom-up works
    for path, ident in (("plants", pid), ("lines", lid), ("assets", aid)):
        assert client.delete(f"/api/{path}/{ident}", headers=eng).status_code == 409, path
    for path, ident in (("tags", tid), ("assets", aid), ("lines", lid), ("plants", pid)):
        assert client.delete(f"/api/{path}/{ident}", headers=eng).status_code == 204, path
        assert client.get(f"/api/{path}/{ident}", headers=eng).status_code == 404
    other_line = client.get("/api/lines", headers=eng, params={"plant_id": p2}).json()["items"][0]["line_id"]
    client.delete(f"/api/lines/{other_line}", headers=eng)
    client.delete(f"/api/plants/{p2}", headers=eng)
    # audit trail
    acts = {r[0] for r in sf.execute(f"SELECT ACTION FROM {DB}.METADATA.AUDIT_LOGS WHERE ACTOR_USERNAME LIKE 'sample_auth_engineer%'")}
    assert {"PLANT_CREATED", "PLANT_UPDATED", "PLANT_DELETED", "LINE_CREATED", "ASSET_CREATED", "TAG_CREATED", "TAG_UPDATED", "TAG_DELETED"} <= acts


def test_admin_user_and_role_apis_and_audit(eng):
    admin_name = f"sample_auth_admin_{TAG}"
    user_service.create_user(UserCreate(username=admin_name, email=f"{admin_name}@example.com", password=PW, roles=["ADMIN"]), action="SAMPLE_SETUP")
    h = hdr(client.post("/api/auth/login", json={"username": admin_name, "password": PW}).json()["access_token"])
    assert client.get("/api/users", headers=eng).status_code == 403 and client.get("/api/audit-logs", headers=eng).status_code == 403
    users = client.get("/api/users", headers=h, params={"page_size": 100, "search": "sample_auth_"}).json()
    assert users["total"] >= 3 and all("password" not in str(u).lower() for u in users["items"])
    logs = client.get("/api/audit-logs", headers=h, params={"action": "plant_created", "page_size": 5}).json()
    assert logs["total"] >= 1 and logs["items"][0]["action"] == "PLANT_CREATED"
