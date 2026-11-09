"""Phase 11: control chart formulas checked against hand calculations and the published factor tables."""
import json
import statistics

import pytest

from app.spc_engine import control_charts as cc
from app.spc_engine.control_charts import ChartConfigError

# n: (d2, c4, A2, D3, D4, A3, B3, B4)   -- standard published SPC tables (3 decimals)
TABLE = {
    2: (1.128, 0.7979, 1.880, 0, 3.267, 2.659, 0, 3.267), 3: (1.693, 0.8862, 1.023, 0, 2.574, 1.954, 0, 2.568),
    4: (2.059, 0.9213, 0.729, 0, 2.282, 1.628, 0, 2.266), 5: (2.326, 0.9400, 0.577, 0, 2.114, 1.427, 0, 2.089),
    6: (2.534, 0.9515, 0.483, 0, 2.004, 1.287, 0.030, 1.970), 7: (2.704, 0.9594, 0.419, 0.076, 1.924, 1.182, 0.118, 1.882),
    8: (2.847, 0.9650, 0.373, 0.136, 1.864, 1.099, 0.185, 1.815), 9: (2.970, 0.9693, 0.337, 0.184, 1.816, 1.032, 0.239, 1.761),
    10: (3.078, 0.9727, 0.308, 0.223, 1.777, 0.975, 0.284, 1.716), 15: (3.472, 0.9823, 0.223, 0.347, 1.653, 0.789, 0.428, 1.572),
    20: (3.735, 0.9869, 0.180, 0.415, 1.585, 0.680, 0.510, 1.490), 25: (3.931, 0.9896, 0.153, 0.459, 1.541, 0.606, 0.565, 1.435),
}


@pytest.mark.parametrize("n", sorted(TABLE))
def test_factors_match_published_tables(n):
    f = cc.factors(n)
    got = (f["d2"], f["c4"], f["A2"], f["D3"], f["D4"], f["A3"], f["B3"], f["B4"])
    for g, want, name in zip(got, TABLE[n], "d2 c4 A2 D3 D4 A3 B3 B4".split()):
        assert g == pytest.approx(want, abs=1.6e-3), f"n={n} {name}: {g} vs {want}"


@pytest.mark.parametrize("n", [2, 5, 10])
def test_embedded_d3_matches_numerical_integral(n):
    assert cc.d3(n) == pytest.approx(cc.d3_numeric(n), abs=2e-6)


@pytest.mark.parametrize("n", [1, 0, 26, -3])
def test_subgroup_size_out_of_range(n):
    with pytest.raises(ChartConfigError, match="between 2 and 25"):
        cc.factors(n)


G = [[10, 12, 11, 13, 9], [11, 10, 12, 12, 10], [9, 11, 10, 12, 13], [12, 13, 11, 10, 11]]
# means 11, 11, 11, 11.4 -> grand mean 11.1 ; ranges 4, 2, 4, 3 -> Rbar 3.25


def test_xbar_r_hand_computed():
    x = cc.xbar_chart(G, "R")
    assert x["points"] == pytest.approx([11, 11, 11, 11.4]) and x["cl"][0] == pytest.approx(11.1)
    A2 = TABLE[5][2]
    assert x["ucl"][0] == pytest.approx(11.1 + A2 * 3.25, abs=3e-3) and x["lcl"][0] == pytest.approx(11.1 - A2 * 3.25, abs=3e-3)
    assert x["params"]["spread_bar"] == pytest.approx(3.25) and x["out_of_control"] == []
    r = cc.r_chart(G)
    assert r["points"] == [4, 2, 4, 3] and r["cl"][0] == pytest.approx(3.25)
    assert r["ucl"][0] == pytest.approx(TABLE[5][4] * 3.25, abs=5e-3) and r["lcl"][0] == 0.0


def test_xbar_s_and_s_chart_hand_computed():
    sds = [statistics.stdev(g) for g in G]                   # independent sample std per subgroup
    sbar = sum(sds) / 4
    s = cc.s_chart(G)
    assert s["points"] == pytest.approx(sds) and s["cl"][0] == pytest.approx(sbar)
    assert s["ucl"][0] == pytest.approx(TABLE[5][7] * sbar, abs=5e-3) and s["lcl"][0] == 0.0
    x = cc.xbar_chart(G, "S")
    assert x["ucl"][0] == pytest.approx(11.1 + TABLE[5][5] * sbar, abs=5e-3)
    assert x["params"]["sigma_hat"] == pytest.approx(sbar / TABLE[5][1], abs=1e-3)


def test_xbar_detects_shifted_subgroup():
    groups = [[10, 11, 10, 11, 10]] * 3 + [[10, 10, 11, 11, 10], [20, 21, 20, 21, 20]] + [[10, 11, 10, 10, 11]] * 3
    assert 4 in cc.xbar_chart(groups, "R")["out_of_control"]


@pytest.mark.parametrize("n", [2, 3, 7, 10, 25])
def test_different_subgroup_sizes(n):
    groups, dropped = cc.make_subgroups([float(i % 7) + (i % 3) * 0.5 for i in range(n * 12 + 1)], n)
    assert len(groups) == 12 and dropped == 1
    for res in (cc.xbar_chart(groups, "R"), cc.xbar_chart(groups, "S"), cc.r_chart(groups), cc.s_chart(groups)):
        assert res["n_points"] == 12 and res["params"].get("subgroup_size", n) == n
        assert all(lo <= c <= up for lo, c, up in zip(res["lcl"], res["cl"], res["ucl"]))


def test_imr_hand_computed():
    out = cc.imr_chart([10, 12, 11, 13, 9, 12])
    mean, mrbar = 67 / 6, 2.4                                   # |diffs| = 2,1,2,4,3
    i, m = out["I"], out["MR"]
    assert i["cl"][0] == pytest.approx(mean) and i["params"]["mrbar"] == pytest.approx(mrbar)
    assert i["ucl"][0] == pytest.approx(mean + 2.66 * mrbar, abs=0.01) and i["lcl"][0] == pytest.approx(mean - 2.66 * mrbar, abs=0.01)
    assert m["points"] == [2, 1, 2, 4, 3] and m["cl"][0] == pytest.approx(2.4)
    assert m["ucl"][0] == pytest.approx(3.267 * 2.4, abs=0.01) and m["lcl"][0] == 0.0
    assert cc.imr_chart([10, 10.1, 9.9, 10, 10.1, 25])["I"]["out_of_control"] == [5]


def test_p_chart_variable_limits_hand_computed():
    p = cc.p_chart([2, 3, 1, 4], [50, 50, 100, 100])
    pbar = 10 / 300
    assert p["cl"][0] == pytest.approx(pbar) and p["points"] == pytest.approx([0.04, 0.06, 0.01, 0.04])
    assert p["ucl"][0] == pytest.approx(pbar + 3 * (pbar * (1 - pbar) / 50) ** 0.5) == pytest.approx(0.10949, abs=1e-4)
    assert p["ucl"][2] == pytest.approx(0.08718, abs=1e-4) and p["lcl"] == [0.0] * 4          # clipped at 0
    assert p["params"]["variable_limits"] is True
    assert cc.p_chart([1, 20, 2], [50, 50, 50])["out_of_control"] == [1]


def test_np_chart_hand_computed():
    r = cc.np_chart([2, 3, 1, 4], 50)
    assert r["cl"][0] == pytest.approx(2.5) and r["ucl"][0] == pytest.approx(2.5 + 3 * (50 * 0.05 * 0.95) ** 0.5, abs=1e-6)
    assert r["lcl"][0] == 0.0
    assert cc.np_chart([2, 3, 1, 4], [50, 50, 50, 50])["cl"][0] == pytest.approx(2.5)
    assert cc.np_chart([1, 1, 1, 15], 50)["out_of_control"] == [3]


def test_c_chart_hand_computed():
    c = cc.c_chart([3, 5, 4, 6, 2])
    assert c["cl"][0] == 4 and c["ucl"][0] == pytest.approx(10.0) and c["lcl"][0] == 0.0
    assert cc.c_chart([4] * 9 + [14])["out_of_control"] == [9]


def test_u_chart_hand_computed():
    u = cc.u_chart([4, 6, 3, 8], [10, 10, 20, 20])
    ubar = 21 / 60
    assert u["cl"][0] == pytest.approx(ubar) and u["points"] == pytest.approx([0.4, 0.6, 0.15, 0.4])
    assert u["ucl"][0] == pytest.approx(ubar + 3 * (ubar / 10) ** 0.5, abs=1e-6) and u["ucl"][2] == pytest.approx(0.74686, abs=1e-4)
    assert u["lcl"] == [0.0] * 4


def test_ewma_hand_computed():
    e = cc.ewma_chart([10, 12, 11], lam=0.2, L=3, target=10, sigma=1)
    assert e["points"] == pytest.approx([10.0, 10.4, 10.52])
    assert e["ucl"][0] == pytest.approx(10.6) and e["lcl"][0] == pytest.approx(9.4)            # 3*sqrt(.2/1.8*(1-.8^2))
    assert e["ucl"][1] == pytest.approx(10 + 0.76837, abs=1e-4)
    assert e["ucl"][2] > e["ucl"][1]                                                          # limits widen toward steady state
    long = cc.ewma_chart([10.0] * 200, lam=0.2, L=3, target=10, sigma=1)
    assert long["ucl"][-1] - 10 == pytest.approx(3 * (0.2 / 1.8) ** 0.5, abs=1e-6)            # steady-state half width
    shift = cc.ewma_chart([10, 10.1, 9.9, 10, 10.1, 10, 9.9, 10, 11, 11, 11, 11, 11, 11], lam=0.2, L=3, target=10, sigma=0.2)
    assert shift["out_of_control"] and shift["out_of_control"][0] >= 8                       # small sustained shift is caught


def test_cusum_hand_computed():
    c = cc.cusum_chart([10, 11, 12, 13, 14], k=0.5, h=5, target=10, sigma=1)
    assert c["points"] == pytest.approx([0, 0.5, 2.0, 4.5, 8.0]) and c["ucl"][0] == 5.0
    assert c["out_of_control"] == [4] and c["lower_points"] == pytest.approx([0] * 5)
    d = cc.cusum_chart([10, 9, 8, 7, 6], target=10, sigma=1)
    assert d["lower_points"] == pytest.approx([0, -0.5, -2.0, -4.5, -8.0]) and d["out_of_control"] == [4]
    assert cc.cusum_chart([10, 10.1, 9.9, 10, 10.05, 9.95, 10, 10.1], target=10, sigma=0.5)["out_of_control"] == []


def test_estimated_sigma_defaults():
    x = [10, 12, 11, 13, 9, 12, 10, 11]
    e = cc.ewma_chart(x)
    assert e["params"]["sigma"] == pytest.approx((sum(abs(b - a) for a, b in zip(x, x[1:])) / 7) / 1.128, abs=1e-3)
    assert e["params"]["target"] == pytest.approx(sum(x) / 8)


# ---------------------------------------------------------------- invalid configurations are rejected
@pytest.mark.parametrize("call,msg", [
    (lambda: cc.xbar_chart([[1, 2, 3]]), "At least 2 subgroups"),
    (lambda: cc.xbar_chart([[1, 2, 3], [1, 2]]), "same size"),
    (lambda: cc.xbar_chart([[1], [2], [3]]), "between 2 and 25"),
    (lambda: cc.xbar_chart([[1] * 26, [2] * 26]), "between 2 and 25"),
    (lambda: cc.xbar_chart(G, "Q"), "method"),
    (lambda: cc.r_chart([[1, float("nan")], [2, 3]]), "non-finite"),
    (lambda: cc.s_chart([["a", "b"], [1, 2]]), "numeric"),
    (lambda: cc.imr_chart([1, 2]), "At least 3"),
    (lambda: cc.p_chart([1, 2], [10]), "same length"),
    (lambda: cc.p_chart([11, 2], [10, 10]), "exceed"),
    (lambda: cc.p_chart([1, 2], [10, 0]), "positive"),
    (lambda: cc.p_chart([1.5, 2], [10, 10]), "whole"),
    (lambda: cc.p_chart([-1, 2], [10, 10]), "non-negative"),
    (lambda: cc.np_chart([1, 2, 3], [10, 20, 10]), "constant sample size"),
    (lambda: cc.c_chart([3]), "At least 2"),
    (lambda: cc.c_chart([3, -1]), "non-negative"),
    (lambda: cc.u_chart([1, 2], [5, 5, 5]), "same length"),
    (lambda: cc.ewma_chart([1, 2, 3, 4], lam=0), "lambda"),
    (lambda: cc.ewma_chart([1, 2, 3, 4], lam=1.5), "lambda"),
    (lambda: cc.ewma_chart([1, 2, 3, 4], L=0), "L must"),
    (lambda: cc.ewma_chart([5, 5, 5, 5]), "constant"),
    (lambda: cc.ewma_chart([1, 2, 3], sigma=-1), "sigma"),
    (lambda: cc.cusum_chart([1, 2, 3], k=-1), "k must"),
    (lambda: cc.cusum_chart([1, 2], target=1), "At least 3"),
    (lambda: cc.make_subgroups([1, 2, 3], 1), "between 2 and 25"),
])
def test_invalid_configurations_rejected(call, msg):
    with pytest.raises(ChartConfigError, match=msg):
        call()


def test_results_are_json_safe_and_consistent():
    for res in (cc.xbar_chart(G), cc.r_chart(G), cc.s_chart(G), cc.p_chart([1, 2], [10, 10]), cc.c_chart([1, 2, 3]),
                cc.ewma_chart([1, 2, 3, 4, 2]), cc.cusum_chart([1, 2, 3, 4, 2]), *cc.imr_chart([1, 2, 4, 3, 5]).values()):
        json.dumps(res)
        assert len(res["points"]) == len(res["cl"]) == len(res["ucl"]) == len(res["lcl"]) == res["n_points"]
