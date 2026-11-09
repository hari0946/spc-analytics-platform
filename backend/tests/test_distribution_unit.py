"""Phase 15: histogram, box plot, normality, probability plot and fitting on normal / skewed / outlier data."""
import json
import math
import random
import statistics

import numpy as np
import pytest

from app.spc_engine import distribution as dist
from app.spc_engine.distribution import DistributionConfigError

rnd = random.Random(11)
NORMAL = [rnd.gauss(0, 1) for _ in range(400)]
SKEWED = [rnd.lognormvariate(0, 0.8) for _ in range(500)]
OUTLIERS = [rnd.gauss(50, 2) for _ in range(200)] + [80, 85, 20]


def test_histogram_counts_and_edges():
    h = dist.histogram(NORMAL)
    assert sum(h["counts"]) == 400 and h["n"] == 400
    assert h["bin_edges"][0] == pytest.approx(min(NORMAL)) and h["bin_edges"][-1] == pytest.approx(max(NORMAL))
    assert len(h["bin_edges"]) == len(h["counts"]) + 1 and len(h["density"]) == len(h["counts"])
    area = sum(d * (b - a) for d, a, b in zip(h["density"], h["bin_edges"], h["bin_edges"][1:]))
    assert area == pytest.approx(1.0)                                          # density integrates to 1
    assert sum(h["normal_expected_counts"]) == pytest.approx(400, rel=0.05)    # normal overlay is on the count scale
    assert sum(dist.histogram(NORMAL, bins=7)["counts"]) == 400 and len(dist.histogram(NORMAL, bins=7)["counts"]) == 7
    assert sum(dist.histogram([1, 2, 3, 4, 5], bins=[0, 2.5, 6])["counts"]) == 5


def test_histogram_exact_counts_small():
    h = dist.histogram([1, 1, 2, 3, 3, 3], bins=3)
    assert h["counts"] == [2, 1, 3] and h["bin_edges"] == pytest.approx([1, 1.6666667, 2.3333333, 3])


def test_histogram_edge_cases():
    c = dist.histogram([5.0] * 10)
    assert c["counts"] == [10] and c["normal_expected_counts"] is None           # constant data: one bin
    assert dist.histogram([3])["counts"] == [1]
    with pytest.raises(DistributionConfigError, match="bins must be"):
        dist.histogram(NORMAL, bins=0)
    with pytest.raises(DistributionConfigError, match="at least 1"):
        dist.histogram([])
    assert len(dist.histogram(list(range(100000)))["counts"]) <= dist.MAX_BINS


def test_boxplot_against_independent_calculation():
    b = dist.boxplot(OUTLIERS)
    xs = np.array(OUTLIERS)
    q1, q3 = np.percentile(xs, [25, 75])
    lo, hi = q1 - 1.5 * (q3 - q1), q3 + 1.5 * (q3 - q1)
    assert (b["q1"], b["q3"]) == (pytest.approx(q1), pytest.approx(q3)) and b["lower_fence"] == pytest.approx(lo)
    assert b["outliers"] == sorted(float(v) for v in xs if v < lo or v > hi) and {20, 80, 85} <= set(b["outliers"])
    assert b["whisker_high"] <= hi and b["whisker_low"] >= lo and b["whisker_high"] == max(v for v in OUTLIERS if v <= hi)
    assert b["median"] == pytest.approx(statistics.median(OUTLIERS)) and b["mean"] == pytest.approx(statistics.fmean(OUTLIERS))
    assert dist.boxplot([1, 2, 3, 4, 5])["outliers"] == [] and dist.boxplot([1, 2, 3, 4, 100])["outliers"] == [100.0]
    assert dist.boxplot([7.0] * 5)["iqr"] == 0 and dist.boxplot([7.0])["median"] == 7


def test_normality_normal_data_not_rejected():
    n = dist.normality(NORMAL)
    assert n["normal"] is True and n["primary_test"] == "Shapiro-Wilk"
    by = {t["test"]: t for t in n["tests"]}
    assert set(by) == {"Shapiro-Wilk", "D'Agostino K2", "Jarque-Bera", "Anderson-Darling"}
    assert all(by[k]["normal"] for k in by) and by["Shapiro-Wilk"]["p_value"] > 0.05
    assert by["Anderson-Darling"]["critical_values"][5.0] > by["Anderson-Darling"]["statistic"]


def test_normality_skewed_data_rejected():
    n = dist.normality(SKEWED)
    assert n["normal"] is False and "not normally distributed" in n["conclusion"]
    assert all(t["normal"] is False for t in n["tests"])
    assert next(t for t in n["tests"] if t["test"] == "Shapiro-Wilk")["p_value"] < 1e-6


def test_normality_with_outliers_rejected_and_without_accepted():
    assert dist.normality(OUTLIERS)["normal"] is False
    assert dist.normality(OUTLIERS[:200])["normal"] is True


def test_normality_edge_cases():
    assert dist.normality([5.0] * 10)["normal"] is None
    with pytest.raises(DistributionConfigError, match="at least 3"):
        dist.normality([1, 2])
    small = dist.normality([1, 2, 4])                                            # n < 8: no K2 test
    assert "D'Agostino K2" not in {t["test"] for t in small["tests"]}
    big = dist.normality(np.random.default_rng(0).normal(size=6000).tolist())
    assert big["tests"][0]["normal"] is None and big["primary_test"] == "D'Agostino K2"


def test_probability_plot():
    p = dist.probability_plot(NORMAL)
    assert p["r_squared"] > 0.995 and p["slope"] == pytest.approx(statistics.stdev(NORMAL), rel=0.05)
    assert p["intercept"] == pytest.approx(statistics.fmean(NORMAL), abs=0.05)
    assert p["ordered_values"] == sorted(NORMAL) and p["theoretical_quantiles"] == sorted(p["theoretical_quantiles"])
    assert dist.probability_plot(SKEWED)["r_squared"] < p["r_squared"]            # skewed data bend away from the line
    big = dist.probability_plot(np.random.default_rng(1).normal(size=5000).tolist())
    assert big["sampled_for_display"] and len(big["ordered_values"]) <= dist.MAX_PLOT_POINTS


def test_fitting_normal_data_prefers_normal():
    f = dist.fit_distributions(NORMAL)
    assert f["best"] == "norm" and f["fits"][0]["rank"] == 1
    assert {s["distribution"] for s in f["skipped"]} == {"lognorm", "weibull_min", "gamma", "expon"}   # data contain negatives
    mu, sd = f["fits"][0]["parameters"]
    assert mu == pytest.approx(statistics.fmean(NORMAL)) and sd == pytest.approx(statistics.pstdev(NORMAL))   # MLE = population std


def test_fitting_skewed_data_prefers_lognormal():
    f = dist.fit_distributions(SKEWED)
    assert f["best"] == "lognorm" and f["fits"][0]["ks_p_value"] > 0.05
    names = [x["distribution"] for x in f["fits"]]
    assert names.index("lognorm") < names.index("norm") and f["fits"][0]["aic"] <= f["fits"][1]["aic"]
    s, loc, scale = f["fits"][0]["parameters"]
    assert loc == 0 and s == pytest.approx(0.8, abs=0.1) and math.log(scale) == pytest.approx(0.0, abs=0.12)    # true params (mu=0, sigma=.8)
    assert all(x["aic"] == pytest.approx(2 * x["n_free_parameters"] - 2 * x["log_likelihood"]) for x in f["fits"])


def test_fitting_errors():
    with pytest.raises(DistributionConfigError, match="at least 8"):
        dist.fit_distributions([1, 2, 3])
    with pytest.raises(DistributionConfigError, match="identical"):
        dist.fit_distributions([2.0] * 20)


def test_full_analysis_bundle_is_json_safe():
    a = dist.analyze(SKEWED)
    assert set(a) >= {"histogram", "boxplot", "normality", "probability_plot", "fit"}
    json.dumps(a)
    const = dist.analyze([4.0] * 10)
    assert const["fit"] is None and const["notes"] and json.dumps(const)
