"""P5-A2 temporal-holdout amendment gates (run_regimes) +
evaluation_only event-train waiver (HoldoutPlanV0).

Synthetic preflight probes — the temporal axis is a declared lock,
not a derived one: undeclared intervals, overlaps, embargo
violations, leaked rows, dual axes, and under-covered basins must
all fail closed.  The Indrawati-as-Bagmati relabel that Codex's
hydrology adjudication rejected is pinned as a synthetic probe.
"""
import dataclasses

import numpy as np
import pandas as pd
import pytest

from nepal.research_v0.records import HoldoutPlanV0
from nepal.science_v0.regimes import RegimeRunConfig, run_regimes


def _temporal_frame(n_groups=3, days_per_span=40):
    """3 basins x 3 spans of daily rows inside declared
    train/embargo/holdout intervals."""
    units = {"u-a": "basin_a", "u-b": "basin_b", "u-c": "basin_c"}
    if n_groups >= 4:
        units["u-d"] = "basin_d"
    spans = [("2001-06-01", 40), ("2018-06-01", 40),
             ("2020-06-01", 40)]
    rows = []
    for unit, grp in units.items():
        for start, n in spans:
            for i in range(n):
                rows.append({
                    "unit_id": unit,
                    "date": (pd.Timestamp(start)
                             + pd.Timedelta(days=i)
                             ).strftime("%Y-%m-%d"),
                    "f1": float(i % 7), "f2": float(i % 5),
                    "basin_group": grp,
                    "season": "JJA", "era": "e1"})
    return pd.DataFrame(rows)


def _temporal_cfg(**over):
    cfg = RegimeRunConfig(
        train_groups=("basin_a", "basin_b", "basin_c"),
        heldout_groups=(),
        holdout_axis="temporal",
        temporal_train_interval=("2001-06-01", "2017-08-31"),
        temporal_embargo_interval=("2018-06-01", "2019-08-31"),
        temporal_holdout_interval=("2020-06-01", "2025-08-31"),
        source_manifest={"fixture": True},
        bootstrap_block_len=12,
        effort_waiver_reason="synthetic fixture",
        era_waiver_reason="synthetic fixture is single-era")
    return dataclasses.replace(cfg, **over)


def _mask(df):
    d = pd.to_datetime(df["date"]).dt.date
    import datetime as _dt
    return ((d >= _dt.date(2001, 6, 1))
            & (d <= _dt.date(2017, 8, 31))).to_numpy()


class TestTemporalAxisPreflight:
    def test_undeclared_axis_rejected(self):
        df = _temporal_frame()
        cfg = dataclasses.replace(_temporal_cfg(),
                                  holdout_axis="sidereal")
        r = run_regimes(df, ["f1", "f2"], _mask(df), cfg)
        assert r["status"] == "RUN_ERROR"
        assert "holdout_axis" in r["reason"]

    def test_temporal_with_geographic_heldout_rejected(self):
        df = _temporal_frame()
        cfg = dataclasses.replace(
            _temporal_cfg(), heldout_groups=("basin_a",))
        r = run_regimes(df, ["f1", "f2"], _mask(df), cfg)
        assert r["status"] == "RUN_ERROR"
        assert "two lock axes" in r["reason"]

    def test_undeclared_intervals_rejected(self):
        df = _temporal_frame()
        cfg = dataclasses.replace(
            _temporal_cfg(), temporal_embargo_interval=())
        r = run_regimes(df, ["f1", "f2"], _mask(df), cfg)
        assert r["status"] == "RUN_ERROR"
        assert "temporal_embargo_interval" in r["reason"]

    def test_overlapping_intervals_rejected(self):
        df = _temporal_frame()
        cfg = dataclasses.replace(
            _temporal_cfg(),
            temporal_holdout_interval=("2017-01-01", "2025-08-31"))
        r = run_regimes(df, ["f1", "f2"], _mask(df), cfg)
        assert r["status"] == "RUN_ERROR"
        assert "ordered" in r["reason"]

    def test_embargo_outside_train_holdout_rejected(self):
        df = _temporal_frame()
        cfg = dataclasses.replace(
            _temporal_cfg(),
            temporal_embargo_interval=("2021-01-01", "2021-12-31"))
        r = run_regimes(df, ["f1", "f2"], _mask(df), cfg)
        assert r["status"] == "RUN_ERROR"
        assert "ordered" in r["reason"]

    def test_mask_outside_train_interval_rejected(self):
        df = _temporal_frame()
        # mask covers embargo rows too — leakage into the lock
        d = pd.to_datetime(df["date"]).dt.date
        import datetime as _dt
        leaky = (d <= _dt.date(2019, 8, 31)).to_numpy()
        r = run_regimes(df, ["f1", "f2"], leaky, _temporal_cfg())
        assert r["status"] == "RUN_ERROR"
        assert "outside the declared temporal_train_interval" \
            in r["reason"]

    def test_undeclared_rows_in_lock_rejected(self):
        df = _temporal_frame()
        # a stray row in 2005 (inside no declared interval when the
        # gap is unmasked — make holdout start later so the stray
        # row lands outside embargo+holdout)
        stray = df.iloc[[0]].copy()
        stray["date"] = "2019-12-01"  # between embargo end and
        df = pd.concat([df, stray], ignore_index=True)
        cfg = dataclasses.replace(
            _temporal_cfg(),
            temporal_holdout_interval=("2020-06-01", "2025-08-31"))
        d = pd.to_datetime(df["date"]).dt.date
        import datetime as _dt
        m = ((d >= _dt.date(2001, 6, 1))
             & (d <= _dt.date(2017, 8,31))).to_numpy()
        # the stray row is ~mask and outside embargo+holdout
        r = run_regimes(df, ["f1", "f2"], m, cfg)
        assert r["status"] == "RUN_ERROR"
        assert "outside the declared embargo+holdout" in r["reason"]

    def test_empty_holdout_interval_rejected(self):
        df = _temporal_frame()
        # no rows in 2021+ -> declare holdout interval with no rows
        cfg = dataclasses.replace(
            _temporal_cfg(),
            temporal_holdout_interval=("2021-06-01", "2025-08-31"))
        r = run_regimes(df, ["f1", "f2"], _mask(df), cfg)
        # a holdout window declared where no rows exist makes the
        # 2020 rows undeclared in the lock — either temporal gate's
        # refusal is honest (they fire in declaration order)
        assert r["status"] == "RUN_ERROR"
        assert ("embargo+holdout" in r["reason"]
                or "both contain rows" in r["reason"])

    def test_empty_embargo_rejected(self):
        # frame with no embargo-window rows
        df = _temporal_frame()
        df = df[~((df["date"] >= "2018-06-01")
                  & (df["date"] <= "2019-08-31"))]
        r = run_regimes(df, ["f1", "f2"], _mask(df), _temporal_cfg())
        assert r["status"] == "RUN_ERROR"

    def test_basin_missing_from_fit_rejected(self):
        df = _temporal_frame()
        # drop basin_c train rows -> 2 groups in fit
        df = df[~((df["basin_group"] == "basin_c")
                  & (df["date"] <= "2017-08-31"))]
        r = run_regimes(df, ["f1", "f2"], _mask(df), _temporal_cfg())
        assert r["status"] == "RUN_ERROR"
        assert "geographic groups" in r["reason"]

    def test_temporal_held_groups_outside_train_rejected(self):
        df = _temporal_frame(n_groups=4)
        # basin_d rows exist only in holdout interval
        cfg = dataclasses.replace(
            _temporal_cfg(),
            train_groups=("basin_a", "basin_b", "basin_c"))
        df = df[~((df["basin_group"] == "basin_d")
                  & (df["date"] <= "2019-08-31"))]
        r = run_regimes(df, ["f1", "f2"], _mask(df), cfg)
        assert r["status"] == "RUN_ERROR"
        assert "undeclared groups" in r["reason"]


class TestEvaluationOnlyEventTrain:
    _K = dict(holdout_plan_id="hp", assignment_rule="basin",
              validation_groups=("karnali",),
              test_groups=("koshi", "gandaki"),
              event_assignments={"e1": "koshi", "e2": "gandaki",
                                 "e3": "karnali"},
              evaluation_region_names=("koshi", "gandaki"),
              embargo_seconds=1.0)

    def test_empty_train_fails_without_waiver(self):
        p = HoldoutPlanV0(train_groups=(), **self._K)
        assert any("train groups must be non-empty"
                   in x for x in p.problems())

    def test_empty_train_passes_under_evaluation_only(self):
        p = HoldoutPlanV0(train_groups=(), holdout_mode="evaluation_only",
                          train_waiver_reason="events are evaluation "
                                              "labels only",
                          **self._K)
        assert p.problems() == [], p.problems()

    def test_waiver_without_mode_rejected(self):
        p = HoldoutPlanV0(train_groups=(), holdout_mode="standard",
                          train_waiver_reason="sneaky",
                          **self._K)
        assert any("without its mode" in x for x in p.problems())

    def test_mode_without_reason_rejected(self):
        p = HoldoutPlanV0(train_groups=(), holdout_mode="evaluation_only",
                          **self._K)
        assert any("train_waiver_reason" in x for x in p.problems())

    def test_unknown_mode_rejected(self):
        p = HoldoutPlanV0(train_groups=("x",), holdout_mode="partial",
                          **self._K)
        assert any("holdout_mode" in x for x in p.problems())

    def test_overlapping_groups_still_fail_under_waiver(self):
        p = HoldoutPlanV0(train_groups=(), holdout_mode="evaluation_only",
                          train_waiver_reason="x",
                          **{**self._K,
                             "validation_groups": ("koshi",)})
        assert any("overlap" in x for x in p.problems())

    def test_unassigned_event_still_fails_under_waiver(self):
        p = HoldoutPlanV0(train_groups=(), holdout_mode="evaluation_only",
                          train_waiver_reason="x",
                          **{**self._K,
                             "event_assignments": {"e1": "koshi"}})
        assert any("declared groups with no assigned events" in x
                   for x in p.problems())


class TestIndrawatiNeverBagmati:
    """Pin the adjudication: RDS7952 places Indrawati under L2 Koshi —
    no artifact may relabel it Bagmati."""

    def test_intake_map_melamchi_is_koshi(self):
        from nepal.research_v0.p3_intake import RIVER_BASIN_TO_UNIVERSE
        assert RIVER_BASIN_TO_UNIVERSE["melamchi"] == "koshi"

    def test_no_bagmati_in_intake_map(self):
        from nepal.research_v0.p3_intake import RIVER_BASIN_TO_UNIVERSE
        assert "bagmati" not in set(RIVER_BASIN_TO_UNIVERSE.values())

    def test_sidecar_binds_adjudication(self):
        import json
        from pathlib import Path
        ROOT = Path("/Users/sanjayb/nepal-event-anomaly-evidence/"
                    "p5-glof-2026-09-19")
        if not (ROOT / "retrieval/role_manifests_v0.json").exists():
            pytest.skip("evidence root absent")
        m = json.loads(
            (ROOT / "retrieval/role_manifests_v0.json").read_text())
        bound = {f["relpath"]
                 for f in m["sidecar"]["source_files"]}
        assert "retrieval/hydrology_adjudication_v0.json" in bound
        assert "retrieval/p5_amendment_v2_temporal_holdout.json" in bound

    def test_event_label_fields_never_reach_feature_frame(self):
        """The evaluation_only waiver is void if event labels enter
        fitting — at the binding level that means no event-label
        field may appear among the frame's feature columns."""
        import json
        from pathlib import Path
        import pandas as pd
        ROOT = Path("/Users/sanjayb/nepal-event-anomaly-evidence/"
                    "p5-glof-2026-09-19")
        frame_p = (ROOT / "era5-multibasin/features/"
                   "regime_frame_hma_jja_2001_2025.csv")
        if not frame_p.exists():
            pytest.skip("evidence root absent")
        pkg = json.loads(
            (ROOT / "glof-events/p3_runner_package_v0.json")
            .read_text())
        event_fields = set()
        for e in pkg["event_labels"]:
            event_fields |= set(e)
        frame_cols = set(pd.read_csv(frame_p, nrows=1).columns)
        leaked = event_fields & frame_cols
        # structural columns shared by both schemas are not labels
        leaked -= {"unit_id", "basin_group", "date"}
        assert not leaked, \
            f"event-label fields leaked into fit surface: {leaked}"
        # and the waiver mode is the one bound in the package
        hp = pkg["holdout_plan"]
        assert hp["holdout_mode"] == "evaluation_only"
        assert hp["train_waiver_reason"].strip()
