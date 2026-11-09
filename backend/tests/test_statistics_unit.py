"""Phase 10: every statistic is verified against hand-computed values (not against the implementation's own libraries)."""
import math

import pytest

from app.spc_engine.statistics import clean, descriptive

# Known sample: 2, 4, 4, 4, 5, 5, 7, 9   (classic example: mean 5, population variance 4)
KNOWN = [2, 4, 4, 4, 5, 5, 7, 9]


def test_known_dataset_hand_computed():
    d = descriptive(KNOWN)
    assert d["n"] == 8 and d["n_missing"] == 0
    assert d["mean"] == 5.0                                   # 40 / 8
    assert d["median"] == 4.5                                 # (4 + 5) / 2
    assert d["mode"] == 4.0 and d["modes"] == [4.0]
    assert (d["min"], d["max"], d["range"]) == (2, 9, 7)
    assert d["pop_variance"] == 4.0 and d["pop_std"] == 2.0   # sum sq dev = 32 -> 32/8
    assert d["variance"] == pytest.approx(32 / 7) and d["std"] == pytest.approx(math.sqrt(32 / 7))
    assert d["sem"] == pytest.approx(math.sqrt(32 / 7) / math.sqrt(8))
    assert d["cv"] == pytest.approx(math.sqrt(32 / 7) / 5)
    # central moments by hand: m3 = sum (x-5)^3 / 8 = (-27-1-1-1+0+0+8+64)/8 = 42/8 ; m4 = (81+1+1+1+0+0+16+256)/8 = 356/8
    m2, m3, m4 = 4.0, 42 / 8, 356 / 8
    n = 8
    g1 = m3 / m2 ** 1.5
    g2 = m4 / m2 ** 2 - 3
    assert d["skewness"] == pytest.approx(g1 * math.sqrt(n * (n - 1)) / (n - 2))                         # adjusted Fisher-Pearson
    assert d["kurtosis"] == pytest.approx(((n - 1) / ((n - 2) * (n - 3))) * ((n + 1) * g2 + 6))          # bias-corrected excess kurtosis


def test_percentiles_and_quartiles_linear_interpolation():
    d = descriptive(list(range(1, 11)))                        # 1..10
    assert d["q1"] == 3.25 and d["q2"] == 5.5 and d["q3"] == 7.75 and d["iqr"] == 4.5   # (n-1)p + 1 positions
    p = d["percentiles"]
    assert p["p10"] == pytest.approx(1.9) and p["p90"] == pytest.approx(9.1) and p["p50"] == 5.5
    assert p["p1"] == pytest.approx(1.09) and p["p99"] == pytest.approx(9.91)


def test_symmetric_data_has_zero_skew():
    d = descriptive([1, 2, 3, 4, 5, 6, 7])
    assert d["skewness"] == pytest.approx(0.0, abs=1e-12)
    assert d["kurtosis"] == pytest.approx(-1.2)                # known excess kurtosis of 1..7 (bias-corrected)


def test_right_skew_is_positive():
    assert descriptive([1, 1, 1, 2, 2, 3, 10, 30])["skewness"] > 1


def test_mode_rules():
    assert descriptive([1, 2, 2, 3, 3])["modes"] == [2.0, 3.0] and descriptive([1, 2, 2, 3, 3])["mode"] == 2.0
    assert any("multimodal" in n for n in descriptive([1, 2, 2, 3, 3])["notes"])
    u = descriptive([1, 2, 3])
    assert u["mode"] is None and u["modes"] == [] and any("no mode" in n for n in u["notes"])


def test_missing_values_are_removed_and_counted():
    d = descriptive([1, None, 3, float("nan"), "x", float("inf"), 5, "7"])
    assert d["n"] == 4 and d["n_missing"] == 4                 # None, NaN, "x", inf removed; "7" parsed
    assert d["mean"] == 4.0 and d["max"] == 7
    arr, miss = clean([None, "abc"])
    assert arr.size == 0 and miss == 2


def test_empty_dataset():
    for empty in ([], [None, None], [float("nan")]):
        d = descriptive(empty)
        assert d["n"] == 0 and d["mean"] is None and d["median"] is None and d["std"] is None
        assert d["percentiles"]["p50"] is None and "no data" in d["notes"]


def test_constant_values():
    d = descriptive([5.0] * 10)
    assert (d["mean"], d["median"], d["mode"], d["range"]) == (5.0, 5.0, 5.0, 0.0)
    assert d["variance"] == 0.0 and d["std"] == 0.0 and d["cv"] is None
    assert d["skewness"] is None and d["kurtosis"] is None     # undefined, not 0 and not NaN
    assert any("constant" in n for n in d["notes"])
    assert d["q1"] == d["q3"] == 5.0 and d["iqr"] == 0.0


def test_tiny_samples_report_undefined_not_garbage():
    one = descriptive([42])
    assert one["mean"] == 42 and one["variance"] is None and one["std"] is None and one["skewness"] is None
    two = descriptive([1, 3])
    assert two["variance"] == 2.0 and two["skewness"] is None and two["kurtosis"] is None
    three = descriptive([1, 2, 6])
    assert three["skewness"] is not None and three["kurtosis"] is None
    four = descriptive([1, 2, 3, 10])
    assert four["kurtosis"] is not None


def test_negative_and_mixed_sign_data():
    d = descriptive([-3, -1, 0, 1, 3])
    assert d["mean"] == 0 and d["cv"] is None                  # cv undefined for zero mean
    assert d["median"] == 0 and d["range"] == 6


def test_results_are_json_safe():
    import json
    json.dumps(descriptive(KNOWN)), json.dumps(descriptive([]))        # no NaN / numpy types leak out
    assert "NaN" not in json.dumps(descriptive([5.0] * 3))
