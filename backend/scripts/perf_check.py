"""Performance check on a SAMPLE dataset (generated, clearly marked) through the real API and Snowflake.

Usage (from backend/):  python -m scripts.perf_check [rows]      (default 100000; split across two parameters)
Creates a temporary sample ENGINEER user and dataset, prints timings, and removes everything afterwards.
"""
import math
import random
import sys
import time
import uuid
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from app.core import snowflake as sf
from app.main import app
from app.schemas.user import UserCreate
from app.services import user_service

PW = "Sample-Passw0rd!"
DB = "SPC_PLATFORM"


def main(rows: int = 100_000) -> int:
    client = TestClient(app, raise_server_exceptions=False)
    tag = uuid.uuid4().hex[:8]
    name = f"sample_perf_{tag}"
    user_service.create_user(UserCreate(username=name, email=f"{name}@example.com", password=PW, roles=["ENGINEER"]), action="SAMPLE_SETUP")
    h = {"Authorization": "Bearer " + client.post("/api/auth/login", json={"username": name, "password": PW}).json()["access_token"]}
    n_each = rows // 2
    rnd = random.Random(42)
    base = datetime(2024, 1, 1)
    t_gen = time.perf_counter()
    lines = ["ts,param,val,asset"]
    for i in range(n_each):
        t = (base + timedelta(seconds=30 * i)).strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"{t},TEMPERATURE,{rnd.gauss(75, 1.5):.3f},M1")
        lines.append(f"{t},PRESSURE,{rnd.gauss(2.0, 0.05) + 0.2 * math.sin(i / 500):.4f},M1")
    data = ("\n".join(lines) + "\n").encode()
    print(f"generated {rows:,} rows, {len(data) / 1e6:.1f} MB in {time.perf_counter() - t_gen:.1f}s")
    timings: list[tuple[str, float, str]] = []
    uid = None

    def step(label, fn, note=lambda r: ""):
        t0 = time.perf_counter()
        r = fn()
        dt = time.perf_counter() - t0
        timings.append((label, dt, note(r)))
        return r

    try:
        up = step("upload + validate + stage", lambda: client.post("/api/uploads", headers=h, files={"file": ("sample_perf.csv", data)},
                  data={"dataset_type": "SENSOR", "allow_duplicate": "true"}), lambda r: f"{r.json().get('rows_received')} rows")
        assert up.status_code == 201, up.text
        uid = up.json()["upload_id"]
        step("save mapping", lambda: client.put(f"/api/uploads/{uid}/mapping", headers=h, json={"mapping": {
            "timestamp": "ts", "parameter": "param", "value": "val", "asset": "asset"}}))
        step("COPY INTO BRONZE", lambda: client.post(f"/api/uploads/{uid}/load", headers=h), lambda r: f"{r.json().get('rows_loaded')} rows")
        step("process BRONZE -> SILVER -> GOLD", lambda: client.post(f"/api/uploads/{uid}/process", headers=h))
        rep = client.get(f"/api/uploads/{uid}/report", headers=h).json()
        print("pipeline report:", rep["counts"], "checks passed:", rep["all_checks_passed"])
        q = {"upload_id": uid}
        step("filter options", lambda: client.get("/api/data/sensor/options", headers=h, params=q))
        step("summary", lambda: client.get("/api/data/sensor/summary", headers=h, params=q))
        step("page of rows (100)", lambda: client.get("/api/data/sensor", headers=h, params=q))
        step("chart series (<=2000 pts/series)", lambda: client.get("/api/data/sensor/series", headers=h, params=q),
             lambda r: f"{sum(len(s['points']) for s in r.json()['series'])} points")
        p = {**q, "parameter": "TEMPERATURE"}
        step("statistics", lambda: client.get("/api/analysis/statistics", headers=h, params=p))
        step("I-MR chart (all points)", lambda: client.get("/api/analysis/control-chart", headers=h, params={**p, "chart": "IMR"}),
             lambda r: f"{len(r.content) / 1e6:.1f} MB response")
        step("X-bar/R (n=5)", lambda: client.get("/api/analysis/control-chart", headers=h, params={**p, "chart": "XBAR_R", "subgroup_size": 5}))
        step("violations (all rules)", lambda: client.get("/api/analysis/violations", headers=h, params={**p, "chart": "IMR"}),
             lambda r: f"{len(r.json()['violations'])} violations")
        step("capability", lambda: client.get("/api/analysis/capability", headers=h, params={**p, "usl": 80, "lsl": 70}))
        step("distribution + fitting", lambda: client.get("/api/analysis/distribution", headers=h, params=p))
        step("dashboard", lambda: client.get("/api/dashboard", headers=h, params=q))
        step("PDF report (SPC)", lambda: client.post("/api/reports", headers=h, json={"type": "spc", "format": "pdf", "parameter": "TEMPERATURE",
             "upload_id": [uid], "options": {"usl": 80, "lsl": 70}}), lambda r: f"{len(r.content) / 1e6:.1f} MB")
        step("Excel report (control chart)", lambda: client.post("/api/reports", headers=h, json={"type": "control_chart", "format": "xlsx",
             "parameter": "TEMPERATURE", "upload_id": [uid], "options": {"chart": "IMR"}}), lambda r: f"{len(r.content) / 1e6:.1f} MB")
    finally:
        try:
            if uid:
                client.delete(f"/api/uploads/{uid}", headers=h)
            for (aid,) in sf.execute(f"SELECT ANALYSIS_ID FROM {DB}.METADATA.ANALYSIS_HISTORY WHERE REQUESTED_BY IN (SELECT USER_ID FROM {DB}.METADATA.USERS WHERE USERNAME = %s)", (name,)):
                for t in ("STATISTICAL_RESULTS", "CONTROL_LIMITS", "SPC_VIOLATIONS", "CAPABILITY_RESULTS", "DISTRIBUTION_RESULTS", "ANALYSIS_RESULTS"):
                    sf.execute(f"DELETE FROM {DB}.SPC_RESULTS.{t} WHERE ANALYSIS_ID = %s", (aid,))
                sf.execute(f"DELETE FROM {DB}.METADATA.ANALYSIS_HISTORY WHERE ANALYSIS_ID = %s", (aid,))
            for (x,) in sf.execute(f"SELECT USER_ID FROM {DB}.METADATA.USERS WHERE USERNAME = %s", (name,)):
                for t in ("USER_SESSIONS", "USER_ROLES"):
                    sf.execute(f"DELETE FROM {DB}.METADATA.{t} WHERE USER_ID = %s", (x,))
                sf.execute(f"DELETE FROM {DB}.METADATA.USERS WHERE USER_ID = %s", (x,))
            sf.execute(f"DELETE FROM {DB}.METADATA.AUDIT_LOGS WHERE ACTOR_USERNAME = %s", (name,))
        finally:
            sf.close_connection()
    print(f"\n{'step':42} {'seconds':>9}  note")
    for label, dt, note in timings:
        print(f"{label:42} {dt:9.2f}  {note}")
    print(f"{'TOTAL':42} {sum(t for _, t, _ in timings):9.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 100_000))
