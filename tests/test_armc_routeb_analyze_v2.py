"""Behaviour + adversarial tests for armc_routeb_analyze_v2 (Nepal
diagnostic re-analysis).  Synthetic data only; no evidence files touched."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import armc_routeb_analyze as V1  # noqa: E402
import armc_routeb_analyze_v2 as A  # noqa: E402

DAY = pd.Timedelta(days=1)


def hourly(vals, start="2010-05-01", freq="h"):
    idx = pd.date_range(start, periods=len(vals), freq=freq, tz="UTC")
    return pd.Series(np.asarray(vals, float), index=idx)


def daily_series(start, n, seed=0, missing=()):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="D")
    s = pd.Series(rng.gamma(1.0, 2.0, n), index=idx)
    for m in missing:
        s.loc[pd.Timestamp(m)] = np.nan
    return s


class TestDailyAggregation:
    def test_accum_uses_end_of_hour_convention(self):
        s = hourly([1.0] * 49, start="2010-05-01 00:00")  # 00:00 day1 .. 00:00 day3
        d = A.daily_from_hourly(s, accum=True, require_complete=True)
        # validity 00:00 belongs to previous day -> day1 (01..24) and day2 complete
        assert list(d.index.strftime("%Y-%m-%d")) == ["2010-05-01", "2010-05-02"]
        assert (d == 24.0).all()

    def test_partial_day_dropped_with_guard_kept_without(self):
        s = hourly([1.0] * 25, start="2010-05-01 00:00")
        guarded = A.daily_from_hourly(s, accum=True, require_complete=True)
        raw = A.daily_from_hourly(s, accum=True, require_complete=False)
        assert pd.Timestamp("2010-04-30") not in guarded.index      # 1-hour stub dropped
        assert raw.loc[pd.Timestamp("2010-04-30")] == 1.0           # v1 behaviour kept for compat

    def test_mean_requires_24_hours(self):
        s = hourly([280.0] * 30, start="2010-05-01 00:00")
        d = A.daily_from_hourly(s, accum=False, require_complete=True)
        assert list(d.index.strftime("%Y-%m-%d")) == ["2010-05-01"]


class TestWindows:
    def test_rolled_nan_when_any_day_missing(self):
        df = pd.DataFrame({"tp": daily_series("2010-05-01", 20, missing=["2010-05-10"])})
        r = A.rolled(df, "tp", "sum", 7)
        assert np.isnan(r.loc["2010-05-12"]) and np.isnan(r.loc["2010-05-16"])
        assert not np.isnan(r.loc["2010-05-17"])

    def test_diff3(self):
        df = pd.DataFrame({"t2m": pd.Series(np.arange(10.0), index=pd.date_range("2010-05-01", periods=10))})
        r = A.rolled(df, "t2m", "diff3", 4)
        assert r.loc["2010-05-05"] == 3.0 and np.isnan(r.loc["2010-05-03"])

    def test_washout_mask_matches_v1_rule_for_L7(self):
        s = pd.concat([daily_series(f"{y}-05-01", 31, seed=y) for y in range(2001, 2011)])
        wash = [pd.Timestamp("2005-05-15"), pd.Timestamp("2008-05-03")]
        ref_v1 = V1.ref_distribution(s, 5, wash, accum=True)
        df = pd.DataFrame({"tp": s}).reindex(pd.date_range(s.index.min(), s.index.max()))
        rc = A.RefCache(A.rolled(df, "tp", "sum", 7), wash, 7)
        ref_v2 = rc.roll[rc.valid & (rc.roll.index.month == 5)].dropna()
        pd.testing.assert_index_equal(ref_v1.index, ref_v2.index)
        np.testing.assert_allclose(ref_v1.values, ref_v2.values)


class TestStatistics:
    def test_midrank_percentile(self):
        ref = np.sort(np.array([1, 2, 2, 3, 4], float))
        assert A.midrank_percentile(ref, 2.0) == pytest.approx((1 + 0.5 * 2) / 5)
        assert A.midrank_percentile(ref, 0.0) == 0.0
        assert A.midrank_percentile(ref, 9.0) == 1.0

    def test_refcache_insufficient_reference(self):
        df = pd.DataFrame({"tp": daily_series("2010-05-01", 31)})
        rc = A.RefCache(A.rolled(df, "tp", "sum", 7), [], 7)
        x, z, p, n, reason = rc.stat(pd.Timestamp("2010-05-20"))
        assert reason == "reference_insufficient" and z is None      # 25 windows < MIN_REF

    def test_refcache_window_incomplete(self):
        df = pd.DataFrame({"tp": daily_series("2001-05-01", 31 * 1)})
        rc = A.RefCache(A.rolled(df, "tp", "sum", 7), [], 7)
        assert rc.stat(pd.Timestamp("2001-05-03"))[4] == "window_incomplete"
        assert rc.stat(pd.Timestamp("1999-01-01"))[4] == "window_incomplete"

    def test_constant_reference_not_computable(self):
        idx = pd.concat([pd.Series(1.0, index=pd.date_range(f"{y}-05-01", periods=31)) for y in range(2001, 2011)])
        df = pd.DataFrame({"tp": idx}).reindex(pd.date_range("2001-05-01", "2010-05-31"))
        rc = A.RefCache(A.rolled(df, "tp", "sum", 7), [], 7)
        assert rc.stat(pd.Timestamp("2005-05-20"))[4] == "reference_insufficient"

    def test_pseudo_outcomes_exclude_washout_and_are_uniformish(self):
        s = pd.concat([daily_series(f"{y}-04-01", 61, seed=y) for y in range(1990, 2020)])
        df = pd.DataFrame({"tp": s}).reindex(pd.date_range(s.index.min(), s.index.max()))
        wash = [pd.Timestamp("2000-05-15")]
        rc = A.RefCache(A.rolled(df, "tp", "sum", 4), wash, 4)
        zs, ps, dates = rc.pseudo_outcomes(5, 0)
        assert not any((dates >= pd.Timestamp("2000-05-08")) & (dates <= pd.Timestamp("2000-05-25")))
        assert 0.45 < ps.mean() < 0.55 and abs(zs.mean()) < 0.1

    def test_mc_null_calibrated_under_null(self):
        rng = np.random.default_rng(1)
        pools = [rng.uniform(size=500) for _ in range(24)]
        obs = float(np.mean([p[0] for p in pools]))
        res = A.mc_null(obs, pools, np.random.default_rng(2))
        assert 0 < res["p_upper"] <= 1 and 0 < res["p_two_sided"] <= 1
        assert abs(res["null_mean"] - 0.5) < 0.01

    def test_mc_null_detects_extreme(self):
        pools = [np.random.default_rng(i).uniform(size=500) for i in range(24)]
        res = A.mc_null(0.95, pools, np.random.default_rng(3))
        assert res["p_upper"] < 0.001

    def test_mc_null_empty(self):
        assert A.mc_null(None, [], np.random.default_rng(0))["p_upper"] is None
        assert A.mc_null(0.5, [np.array([])], np.random.default_rng(0))["p_upper"] is None

    def test_cluster_bootstrap_groups_repeat_lakes(self):
        vals = np.array([1.0, 1.0, 0.0, np.nan])
        res = A.cluster_bootstrap(vals, ["dig", "dig", "b", "c"], np.random.default_rng(0))
        assert res["n_clusters"] == 2 and 0.0 <= res["ci95"][0] <= res["ci95"][1] <= 1.0

    def test_cluster_bootstrap_too_small(self):
        assert A.cluster_bootstrap(np.array([0.3]), ["a"], np.random.default_rng(0))["ci95"] is None

    def test_sign_test(self):
        res = A.sign_test(np.array([0.9, 0.8, 0.7, 0.5, 0.2]))
        assert res["n_above_median"] == 3 and res["n_informative"] == 4


class TestLabels:
    @pytest.mark.parametrize("drv,exp", [
        ("Intense rainfall", "RAIN"), ("Intense rainfall, melt", "RAIN"),
        ("High temperatures", "THERMAL"), ("Thaw, snow melt", "THERMAL"),
        ("Water level rise; water temperature rise", "THERMAL"),
        ("Ice avalanche", "MASS"), ("Rockfall", "MASS"), ("Unknown", "UNKNOWN"),
        ("", "OTHER"), (None, "OTHER"), ("Water level rise", "OTHER")])
    def test_driver_stratum(self, drv, exp):
        assert A.driver_stratum(drv) == exp

    def test_lake_key_unnamed_distinct(self):
        assert A.lake_key({"unit_id": "ep_02", "lake": "(unnamed)"}) != A.lake_key({"unit_id": "ep_03", "lake": "(unnamed)"})
        assert A.lake_key({"unit_id": "ep_04", "lake": "Dig Tsho"}) == A.lake_key({"unit_id": "ep_24", "lake": "Dig Tsho"})


class TestConflictGuardInherited:
    def test_conflicting_duplicate_across_lanes_raises(self):
        a = hourly([1.0] * 48)
        b = hourly([2.0] * 3, start="2010-05-01 05:00")
        with pytest.raises(ValueError, match="conflicting"):
            A.build_daily({"tp": {"a": a, "b": b}, "sf": {"a": a}, "t2m": {"a": a}}, {"a", "b"})
