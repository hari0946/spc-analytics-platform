"""Unit tests for file parsing/validation. All data below is SAMPLE data generated for tests."""
import gzip
import json
from datetime import datetime

import openpyxl
import pytest
import xlwt
from pydantic import ValidationError

from app.schemas.upload import ProductionMapping, SensorMapping
from app.services.file_parser import FileValidationError, preprocess, suggest_mapping


def run(path, tmp_path, name=None, **kw):
    out = tmp_path / "parts"
    out.mkdir(exist_ok=True)
    return preprocess(path, name or path.name, out, part_rows=kw.pop("part_rows", 1000),
                      max_rows=kw.pop("max_rows", 10000), max_cols=kw.pop("max_cols", 50), **kw)


def read_parts(res):
    rows = []
    for p in res.parts:
        with gzip.open(p, "rt", encoding="utf-8") as f:
            rows += [json.loads(line) for line in f]
    return rows


def write(tmp_path, name, text, enc="utf-8"):
    p = tmp_path / name
    p.write_bytes(text.encode(enc))
    return p


LONG = ("timestamp,parameter,value,asset\n"
        "2024-01-01 00:00:00,TEMPERATURE,72.5,M1\n"
        "2024-01-01 00:01:00,TEMPERATURE,73.1,M1\n"
        "2024-01-01 00:01:00,TEMPERATURE,73.1,M1\n"      # exact duplicate
        ",,,\n"                                          # empty row
        "2024-01-01 00:02:00,PRESSURE,,M1\n")            # missing value


def test_csv_long_layout(tmp_path):
    res = run(write(tmp_path, "s.csv", LONG), tmp_path)
    assert res.kind == "csv" and res.columns == ["timestamp", "parameter", "value", "asset"]
    assert (res.rows_received, res.empty_rows, res.duplicate_rows) == (4, 1, 1)
    rows = read_parts(res)
    assert [r["r"] for r in rows] == [2, 3, 4, 6]                      # record numbers, header = 1
    assert rows[0]["d"] == {"timestamp": "2024-01-01 00:00:00", "parameter": "TEMPERATURE", "value": "72.5", "asset": "M1"}
    assert rows[3]["d"]["value"] == ""                                 # missing value preserved, not invented
    assert any("duplicate" in w for w in res.warnings) and any("empty" in w for w in res.warnings)


def test_csv_delimiters_encoding_and_bom(tmp_path):
    semi = write(tmp_path, "a.csv", "﻿time;temp\n2024-01-01;1,5\n2024-01-02;2,5\n")
    assert run(semi, tmp_path).columns == ["time", "temp"]
    tab = write(tmp_path, "b.csv", "time\ttemp\n2024-01-01\t1\n")
    assert run(tab, tmp_path).columns == ["time", "temp"]
    latin = write(tmp_path, "c.csv", "time,note\n2024-01-01,caf\xe9\n", enc="latin-1")
    assert read_parts(run(latin, tmp_path))[0]["d"]["note"] == "café"


def test_ragged_rows_and_quoted_newlines(tmp_path):
    p = write(tmp_path, "r.csv", 'a,b,c\n1,2\n3,4,5,6\n"x\ny",z,w\n')
    res = run(p, tmp_path)
    rows = read_parts(res)
    assert res.ragged_rows == 2 and res.rows_received == 3
    assert rows[0]["d"]["c"] is None and rows[1]["d"]["_extra"] == ["6"] and rows[2]["d"]["a"] == "x\ny"


def test_parts_split(tmp_path):
    body = "t,v\n" + "".join(f"{i},{i}\n" for i in range(25))
    res = run(write(tmp_path, "big.csv", body), tmp_path, part_rows=10)
    assert len(res.parts) == 3 and len(read_parts(res)) == 25


def test_xlsx_values_and_dates(tmp_path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Timestamp", "Temp", "Flow", None])
    ws.append([datetime(2024, 1, 1, 8, 30), 72.5, 10, None])
    ws.append([datetime(2024, 1, 1, 8, 31), 73.0, 11, None])
    ws.append([None, None, None, None])
    other = wb.create_sheet("Other")
    other.append(["x"])
    other.append([1])
    p = tmp_path / "s.xlsx"
    wb.save(p)
    res = run(p, tmp_path)
    assert res.kind == "xlsx" and res.columns == ["Timestamp", "Temp", "Flow"]
    rows = read_parts(res)
    assert rows[0]["d"] == {"Timestamp": "2024-01-01 08:30:00", "Temp": "72.5", "Flow": "10"}
    assert rows[1]["d"]["Temp"] == "73" and res.rows_received == 2 and res.empty_rows == 1
    assert run(p, tmp_path, sheet="Other").columns == ["x"]
    with pytest.raises(FileValidationError, match="Sheet 'nope' not found"):
        run(p, tmp_path, sheet="nope")


def test_xlsx_stored_under_neutral_temp_name(tmp_path):
    """Regression: the service saves uploads as 'source.bin'; openpyxl must not care about the extension."""
    wb = openpyxl.Workbook()
    wb.active.append(["a", "b"])
    wb.active.append([1, 2])
    p = tmp_path / "source.bin"
    wb.save(p)
    assert run(p, tmp_path, name="original_name.xlsx").rows_received == 1


def test_xls(tmp_path):
    wb = xlwt.Workbook()
    ws = wb.add_sheet("S")
    for c, h in enumerate(["time", "temp"]):
        ws.write(0, c, h)
    style = xlwt.easyxf(num_format_str="YYYY-MM-DD HH:MM:SS")
    ws.write(1, 0, datetime(2024, 1, 1, 8, 30), style)
    ws.write(1, 1, 72.5)
    p = tmp_path / "s.xls"
    wb.save(str(p))
    res = run(p, tmp_path)
    assert res.kind == "xls" and read_parts(res)[0]["d"] == {"time": "2024-01-01 08:30:00", "temp": "72.5"}


@pytest.mark.parametrize("name,content,msg", [
    ("a.txt", b"a,b\n1,2\n", "Unsupported file type"),
    ("e.csv", b"", "empty"),
    ("h.csv", b"a,b\n", "no data rows"),
    ("blank.csv", b"\n\n", "no data|header"),
    ("d.csv", b"a,b,A\n1,2,3\n", "Duplicate column names"),
    ("bin.csv", b"a,b\n\x00\x01\x02\n", "does not look like a text CSV"),
    ("fake.xlsx", b"just text", "not a valid .xlsx"),
    ("fake.xls", b"just text", "not a valid .xls"),
    ("zip.csv", b"PK\x03\x04rest", "does not look like a text CSV"),
])
def test_invalid_files(tmp_path, name, content, msg):
    p = tmp_path / name
    p.write_bytes(content)
    with pytest.raises(FileValidationError, match=msg):
        run(p, tmp_path)


def test_limits(tmp_path):
    p = write(tmp_path, "m.csv", "a\n1\n2\n3\n")
    with pytest.raises(FileValidationError, match="Too many rows"):
        run(p, tmp_path, max_rows=2)
    with pytest.raises(FileValidationError, match="Too many columns"):
        run(write(tmp_path, "w.csv", "a,b,c\n1,2,3\n"), tmp_path, max_cols=2)


def test_blank_headers_named(tmp_path):
    assert run(write(tmp_path, "u.csv", "a,,c\n1,2,3\n"), tmp_path).columns == ["a", "unnamed_2", "c"]


# ---- suggestions & mapping models
def test_suggest_long_and_wide():
    cols = ["Timestamp", "Parameter", "Value", "Machine", "Batch ID"]
    assert suggest_mapping(cols, [], "SENSOR") == {"timestamp": "Timestamp", "parameter": "Parameter",
                                                   "value": "Value", "asset": "Machine", "batch": "Batch ID"}
    wide = ["Time", "Temperature", "Pressure", "Note"]
    rows = [["2024-01-01", "70.1", "1.2", "ok"], ["2024-01-02", "71", "1.3", "fine"], ["2024-01-03", "72", "1.1", "x"]]
    s = suggest_mapping(wide, rows, "SENSOR")
    assert s["timestamp"] == "Time" and s["value_columns"] == ["Temperature", "Pressure"]


def test_suggest_production():
    s = suggest_mapping(["date", "lot", "n", "defectives", "defects"], [], "PRODUCTION")
    assert s == {"timestamp": "date", "batch": "lot", "sample_size": "n", "defective_count": "defectives",
                 "defect_count": "defects"}


def test_mapping_models_validate():
    assert SensorMapping(timestamp="t", parameter="p", value="v")
    assert SensorMapping(timestamp="t", value_columns=["a", "b"])
    for bad in ({"timestamp": "t"}, {"timestamp": "t", "parameter": "p"},
                {"timestamp": "t", "parameter": "p", "value": "v", "value_columns": ["a"]},
                {"timestamp": "t", "parameter": "t", "value": "v"}, {"parameter": "p", "value": "v"},
                {"timestamp": "t", "value_columns": ["a"], "bogus": "x"}):
        with pytest.raises(ValidationError):
            SensorMapping(**bad)
    assert ProductionMapping(timestamp="t", sample_size="n")
    with pytest.raises(ValidationError):
        ProductionMapping(timestamp="t", batch="b")
