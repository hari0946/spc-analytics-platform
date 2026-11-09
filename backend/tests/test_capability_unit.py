"""Phase 14: capability indices verified against independent hand/stdlib calculations."""
import json
import math
import random
import statistics

import pytest

from app.spc_engine.capability import CapabilityConfigError, analyze, validate_specs

D2_2 = 1.128                       # published d2 for moving range of 2


def phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def test_hand_computed_centred_process():
    r = analyze([8, 9, 10, 11, 12], usl=13, lsl=7)
    s = math.sqrt(2.5)                                    # sample std of 8..12
    assert r["mean"] == 10 and r["sigma_overall"] == pytest.approx(s)
    assert r["sigma_within"] == pytest.approx(1 / D2_2, abs=1e-3)           # MRbar = 1
    assert r["pp"] == pytest.approx(6 / (6 * s)) and r["ppk"] == pytest.approx(3 / (3 * s))
    assert r["ppu"] == pytest.approx(r["ppl"])
    assert r["cp"] == pytest.approx(D2_2, abs=2e-3) and r["cpk"] == pytest.approx(D2_2, abs=2e-3)   # 6/(6*(1/d2)) = d2
    assert r["ppm_observed_total"] == 0 and r["yield_observed"] == 100
    z = 3 / s
    assert r["ppm_expected_overall"] == pytest.approx(2 * (1 - phi(z)) * 1e6, rel=1e-9)
    assert r["yield_expected"] == pytest.approx(100 - 2 * (1 - phi(z)) * 100, rel=1e-9)


def test_off_centre_process_cpk_less_than_cp():
    r = analyze([10, 11, 12, 13, 14], usl=15, lsl=7)
    sw = 1 / D2_2
    assert r["cp"] == pytest.approx(8 / (6 * sw), rel=2e-3)
    assert r["cpu"] == pytest.approx((15 - 12) / (3 * sw), rel=2e-3) and r["cpl"] == pytest.approx((12 - 7) / (3 * sw), rel=2e-3)
    assert r["cpk"] == r["cpu"] < r["cp"]
    assert r["ppk"] == pytest.approx(min(3, 5) / (3 * math.sqrt(2.5)))


def test_observed_ppm_and_yield():
    r = analyze([8, 9, 10, 11, 12], usl=11.5, lsl=8.5)
    assert r["ppm_observed_below"] == 200000 and r["ppm_observed_above"] == 200000 and r["ppm_observed_total"] == 400000
    assert r["yield_observed"] == pytest.approx(60.0)
    onesided = analyze([8, 9, 10, 11, 12], usl=11.5)
    assert onesided["ppm_observed_below"] is None and onesided["ppm_observed_total"] == 200000


def test_one_sided_specs():
    up = analyze([8, 9, 10, 11, 12], usl=14)
    assert up["cp"] is None and up["pp"] is None and up["cpl"] is None
    assert up["cpu"] == pytest.approx((14 - 10) / (3 / D2_2), rel=2e-3) and up["cpk"] == up["cpu"]
    assert any("One-sided" in n for n in up["notes"])
    lo = analyze([8, 9, 10, 11, 12], lsl=6)
    assert lo["cpu"] is None and lo["cpk"] == lo["cpl"] and lo["ppk"] == pytest.approx((10 - 6) / (3 * math.sqrt(2.5)))


def test_large_normal_sample_against_stdlib():
    rnd = random.Random(2)
    x = [rnd.gauss(50, 2) for _ in range(500)]
    r = analyze(x, usl=56, lsl=44, target=50)
    mu, sd = statistics.fmean(x), statistics.stdev(x)
    mr = statistics.fmean(abs(b - a) for a, b in zip(x, x[1:]))
    assert r["mean"] == pytest.approx(mu) and r["sigma_overall"] == pytest.approx(sd)
    assert r["pp"] == pytest.approx(12 / (6 * sd)) and r["ppk"] == pytest.approx(min(56 - mu, mu - 44) / (3 * sd))
    assert r["cp"] == pytest.approx(12 / (6 * mr / D2_2), rel=3e-3) and r["cpk"] == pytest.approx(min(56 - mu, mu - 44) / (3 * mr / D2_2), rel=3e-3)
    assert 0.9 < r["pp"] < 1.1 and r["sufficient_sample"] and r["non_normal"] is False
    assert r["normality"]["test"] == "Shapiro-Wilk" and r["normality"]["normal"] is True
    assert r["cpm"] == pytest.approx(12 / (6 * math.sqrt(sd ** 2 + (mu - 50) ** 2)))
    assert r["percentile_method"] is None and r["warnings"] == []


def test_non_normal_data_flagged_with_percentile_method():
    rnd = random.Random(3)
    x = [rnd.expovariate(1 / 5) for _ in range(300)]               # strongly right-skewed
    r = analyze(x, usl=40, lsl=0)
    assert r["non_normal"] is True and r["normality"]["p_value"] < 0.001
    assert any("not normally distributed" in w for w in r["warnings"])
    pm = r["percentile_method"]
    xs = sorted(x)

    def pct(p):
        k = (len(xs) - 1) * p / 100
        lo = math.floor(k)
        return xs[lo] + (xs[min(lo + 1, len(xs) - 1)] - xs[lo]) * (k - lo)

    assert pm["p50"] == pytest.approx(pct(50)) and pm["p99.865"] == pytest.approx(pct(99.865))
    assert pm["pp"] == pytest.approx(40 / (pct(99.865) - pct(0.135)))
    assert pm["ppk"] == pytest.approx(min((40 - pct(50)) / (pct(99.865) - pct(50)), (pct(50) - 0) / (pct(50) - pct(0.135))))


def test_subgroup_based_within_sigma():
    groups = [[10, 11, 12], [10, 10, 11], [11, 12, 12], [9, 10, 11]]
    rbar = (2 + 1 + 1 + 2) / 4
    flat = [v for g in groups for v in g]
    r = analyze(flat, usl=14, lsl=8, subgroups=groups)
    assert r["sigma_within"] == pytest.approx(rbar / 1.693, abs=1e-3) and "Rbar/d2" in r["sigma_within_method"]
    assert analyze(flat, usl=14, lsl=8, sigma_within_override=0.5)["sigma_within"] == 0.5


def test_insufficient_and_small_samples():
    with pytest.raises(CapabilityConfigError, match="Insufficient data"):
        analyze([5], usl=6, lsl=4)
    with pytest.raises(CapabilityConfigError, match="Insufficient data"):
        analyze([None, float("nan")], usl=6, lsl=4)
    small = analyze([10, 11, 9, 10.5, 9.5], usl=13, lsl=7)
    assert small["sufficient_sample"] is False and any("Small sample" in w for w in small["warnings"])
    two = analyze([10, 12], usl=14, lsl=8)
    assert two["n"] == 2 and two["pp"] is not None and two["sigma_within"] is None          # 1 moving range value only -> n<3
    assert two["cp"] is None and any("Within-subgroup sigma" in n for n in two["notes"])


def test_constant_data_is_undefined_not_infinite():
    r = analyze([10.0] * 20, usl=12, lsl=8)
    assert r["cp"] is None and r["cpk"] is None and r["pp"] is None and r["ppk"] is None
    assert any("identical" in n for n in r["notes"]) and r["yield_observed"] == 100 and r["yield_expected"] == 100
    out = analyze([20.0] * 20, usl=12, lsl=8)
    assert out["yield_observed"] == 0 and out["ppm_observed_total"] == 1e6 and out["yield_expected"] == 0


def test_missing_values_ignored():
    r = analyze([8, None, 9, 10, "x", 11, 12, float("inf")], usl=13, lsl=7)
    assert r["n"] == 5 and r["n_missing"] == 3


@pytest.mark.parametrize("kw,msg", [
    ({}, "At least one specification limit"),
    ({"usl": 5, "lsl": 5}, "must be greater"), ({"usl": 4, "lsl": 5}, "must be greater"),
    ({"usl": "abc"}, "must be a number"), ({"usl": float("inf")}, "finite"), ({"lsl": float("nan")}, "finite"),
    ({"usl": 10, "lsl": 0, "target": 11}, "Target must lie"), ({"usl": 10, "lsl": 0, "target": -1}, "Target must lie"),
    ({"usl": 10, "target": 11}, "Target must lie"),
])
def test_invalid_specification_limits(kw, msg):
    with pytest.raises(CapabilityConfigError, match=msg):
        validate_specs(**kw)
    with pytest.raises(CapabilityConfigError, match=msg):
        analyze([1, 2, 3, 4, 5], **kw)


def test_bad_subgroups_rejected():
    with pytest.raises(CapabilityConfigError):
        analyze([1, 2, 3, 4], usl=5, lsl=0, subgroups=[[1, 2, 3, 4]])
    with pytest.raises(CapabilityConfigError, match="between"):
        analyze([1, 2, 3, 4], usl=5, lsl=0, subgroups=[[1], [2]])


def test_json_safe():
    json.dumps(analyze([8, 9, 10, 11, 12], usl=13, lsl=7))
    json.dumps(analyze([10.0] * 5, usl=13, lsl=7))
