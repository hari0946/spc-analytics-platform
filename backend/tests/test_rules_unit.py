"""Phase 13: predefined datasets with known violations (centre line 0, sigma 1 unless stated)."""
import json

import pytest

from app.spc_engine import rules
from app.spc_engine.rules import RuleConfigError, detect, summarize


def hits(points, rule, cl=0.0, sigma=1.0):
    return [(v["start_index"], v["point_index"]) for v in detect(points, cl, sigma, [rule])]


# ---- rule 1 (WE1 / N1)
@pytest.mark.parametrize("rule", ["WE1", "N1"])
def test_beyond_three_sigma(rule):
    assert hits([0, 0, 3.5, 0], rule) == [(2, 2)]
    assert hits([0, -3.01, 0], rule) == [(1, 1)]
    assert hits([0, 3.0, -3.0, 0], rule) == []                      # exactly on 3 sigma is NOT beyond
    assert hits([0.5, -0.5, 1, -1, 2.9], rule) == []


# ---- run on one side (WE4: 8, N2: 9)
def test_run_on_one_side_lengths():
    assert hits([0.5] * 7, "WE4") == [] and hits([0.5] * 8, "WE4") == [(0, 7)]
    assert hits([0.5] * 8, "N2") == [] and hits([0.5] * 9, "N2") == [(0, 8)]
    assert hits([-0.5] * 10, "N2") == [(0, 8), (1, 9)]              # run keeps holding -> each further point flagged
    assert hits([0.5] * 4 + [-0.5] + [0.5] * 4, "N2") == []         # crossing the centre line resets the run
    assert hits([0.5] * 4 + [0.0] + [0.5] * 5, "N2") == []          # a point ON the centre line is on neither side


# ---- trend (N3)
def test_trend_six_points():
    assert hits([0.1, 0.2, 0.3, 0.4, 0.5, 0.6], "N3") == [(0, 5)]
    assert hits([0.6, 0.5, 0.4, 0.3, 0.2, 0.1], "N3") == [(0, 5)]   # decreasing
    assert hits([0.1, 0.2, 0.3, 0.4, 0.5], "N3") == []              # only 5
    assert hits([0.1, 0.2, 0.3, 0.3, 0.5, 0.6], "N3") == []         # equal values break a strict trend
    assert hits([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7], "N3") == [(0, 5), (1, 6)]


# ---- alternating (N4)
def test_alternating_fourteen():
    alt = [1, -1] * 7
    assert hits(alt, "N4") == [(0, 13)] and hits(alt[:13], "N4") == []
    assert hits([1, -1] * 3 + [1, 1] + [-1, 1] * 3, "N4") == []


# ---- 2 of 3 beyond 2 sigma (WE2 / N5)
@pytest.mark.parametrize("rule", ["WE2", "N5"])
def test_two_of_three_beyond_two_sigma(rule):
    assert hits([0, 2.5, 2.2, 0], rule) == [(1, 2)]
    assert hits([2.5, 0, 2.2], rule) == [(0, 2)]                    # not adjacent but within 3
    assert hits([2.5, -2.5, 0], rule) == []                         # opposite sides
    assert hits([2.5, 0, 0, 2.5], rule) == []                       # more than 3 apart
    assert hits([-2.5, -2.1, 0], rule) == []                        # completes only when the current point is one of them
    assert hits([-2.5, -2.1], rule) == []                           # fewer than 3 points
    assert hits([2.0, 2.0, 2.0], rule) == []                        # exactly 2 sigma is not beyond


# ---- 4 of 5 beyond 1 sigma (WE3 / N6)
@pytest.mark.parametrize("rule", ["WE3", "N6"])
def test_four_of_five_beyond_one_sigma(rule):
    assert hits([1.5, 1.2, 0, 1.1, 1.3], rule) == [(0, 4)]
    assert hits([1.5, 1.2, 0, 1.1, -1.3], rule) == []               # one on the other side
    assert hits([1.5, 1.2, 0, 0, 1.3], rule) == []                  # only 3 beyond
    assert hits([-1.5, -1.2, -1.4, 0, -1.1], rule) == [(0, 4)]


# ---- 15 within 1 sigma (N7) / 8 beyond 1 sigma both sides (N8)
def test_stratification_and_mixture():
    calm = [0.1, -0.1, 0.2, -0.2, 0.3] * 3
    assert hits(calm, "N7") == [(0, 14)] and hits(calm[:14], "N7") == []
    assert hits(calm[:14] + [1.5], "N7") == []
    assert hits([1.5, -1.5] * 4, "N8") == [(0, 7)]
    assert hits([1.5, -1.5, 1.5, -1.5, 1.5, -1.5, 1.5, 0.5], "N8") == []        # one point inside 1 sigma


def test_in_control_data_has_no_violations():
    x = [0.3, -0.4, 0.8, -0.2, 0.5, -0.7, 0.1, -0.9, 0.4, -0.3]
    assert detect(x, 0, 1) == []


# ---- reporting
def test_full_report_fields_classification_and_timestamps():
    x = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 4.0]
    ts = [f"2024-01-01 00:0{i}:00" for i in range(7)]
    v = detect(x, 0, 1, "NELSON", values=[10 * a for a in x], timestamps=ts)
    by = {(a["rule_id"], a["point_index"]): a for a in v}
    n1, n3 = by[("N1", 6)], by[("N3", 5)]
    assert (n1["violation_type"], n1["severity"], n1["rule_set"]) == ("OUT_OF_CONTROL", "HIGH", "NELSON")
    assert n1["timestamp"] == "2024-01-01 00:06:00" and n1["observed_value"] == 40.0 and n1["z_score"] == 4.0
    assert (n3["violation_type"], n3["start_index"], n3["timestamp"]) == ("TREND", 0, "2024-01-01 00:05:00")
    assert [a["point_index"] for a in v] == sorted(a["point_index"] for a in v)
    s = summarize(v)
    assert s["by_rule"]["N1"] == 1 and s["by_type"]["TREND"] >= 1 and s["points_flagged"] == len({a["point_index"] for a in v})
    json.dumps(v)


def test_shift_classified_as_shift():
    v = detect([0.5] * 9, 0, 1, "NELSON")
    assert [a["rule_id"] for a in v] == ["N2"] and v[0]["violation_type"] == "SHIFT"


def test_rule_set_selection_and_both():
    x = [4.0]
    assert {a["rule_id"] for a in detect(x, 0, 1, "BOTH")} == {"WE1", "N1"}
    assert {a["rule_set"] for a in detect(x, 0, 1, "WESTERN_ELECTRIC")} == {"WESTERN_ELECTRIC"}
    assert detect(x, 0, 1, ["N3"]) == []


def test_non_centred_and_variable_sigma():
    # cl 10, sigma 2 -> 3 sigma beyond is > 16
    assert hits([10, 16.5, 10], "N1", cl=10, sigma=2) == [(1, 1)]
    # per-point sigma: the same value is beyond 3 sigma only where the limit is tight
    assert hits([12, 12], "N1", cl=[10, 10], sigma=[0.5, 1.0]) == [(0, 0)]
    # trend is evaluated on the plotted values even if sigma varies
    assert hits([1, 2, 3, 4, 5, 6], "N3", cl=0, sigma=[1, 2, 1, 2, 1, 2]) == [(0, 5)]


@pytest.mark.parametrize("call,msg", [
    (lambda: detect([1, 2], 0, 0), "sigma must be positive"),
    (lambda: detect([1, 2], 0, -1), "sigma must be positive"),
    (lambda: detect([1, float("nan")], 0, 1), "finite"),
    (lambda: detect([1, 2], 0, 1, "NOPE"), "Unknown rule set"),
    (lambda: detect([1, 2], 0, 1, ["N99"]), "Unknown rule"),
    (lambda: detect([1, 2], 0, 1, values=[1]), "same length"),
    (lambda: detect([1, 2], 0, 1, timestamps=["a"]), "same length"),
    (lambda: detect([[1, 2]], 0, 1), "one-dimensional"),
])
def test_invalid_input_rejected(call, msg):
    with pytest.raises(RuleConfigError, match=msg):
        call()


def test_empty_and_short_series():
    assert detect([], 0, 1) == [] and detect([1.0], 0, 1) == []
    assert set(rules.RULES) == {"WE1", "WE2", "WE3", "WE4", "N1", "N2", "N3", "N4", "N5", "N6", "N7", "N8"}
