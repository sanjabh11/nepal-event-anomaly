"""Contract tests for the B3 forecast-evaluation scaffold (synthetic).

Exercises ``nepal.experiment_v0.baselines/metrics/evaluation`` on
planted-signal synthetic cases only: metric plumbing, locked-region
guard, vintage-digest admission and vintage-metadata timing, the
explicit opportunity denominator, strict case reconstruction,
censored accounting, missing-feed degradation, power gating,
lead-time buckets, deterministic bootstrap replay, and the claim-text
scan.  Contract-layer evidence only — nothing here is a real-data
finding.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from nepal.experiment_v0 import metrics as M
from nepal.experiment_v0.baselines import (
    BASELINE_NAMES, ThresholdRule, climatology_probs,
    fit_regularized_supervised, null_probs, rule_probs)
from nepal.experiment_v0.evaluation import (
    EvaluationReport, ForecastCase, evaluate, locked_region_problems,
    missing_feed_degradation, power_report, uncertainty_report)
from nepal.research_v0._hashing import canonical_json, sha256_canonical
from nepal.research_v0.gates import scan_claims_text
from nepal.research_v0.records import NEUTRAL_RESEARCH_STATUSES

from tests.fixtures import synthetic_exp_b3 as fx


# ---------------------------------------------------------------------
# Baseline primitives
# ---------------------------------------------------------------------

class _Case:
    def __init__(self, unit_id="u1", season="s1", features=None):
        self.unit_id = unit_id
        self.season = season
        self.features = features or {}


class TestBaselines:
    def test_baseline_names_pinned(self):
        assert BASELINE_NAMES == ("climatology", "rule", "null",
                                  "regularized_supervised")

    def test_climatology_lookup_and_global_mean(self):
        cases = [_Case("u1", "s1"), _Case("u9", "s9")]
        rates = {("u1", "s1"): 0.3, ("u2", "s2"): 0.7}
        out = climatology_probs(cases, rates)
        assert out[0] == pytest.approx(0.3)
        assert out[1] == pytest.approx(0.5)  # global mean fallback

    def test_rule_and_combine(self):
        rules = (ThresholdRule("a", 1.0, 0.1, 0.9),
                 ThresholdRule("b", 2.0, 0.2, 0.8))
        fired = _Case(features={"a": 1.5, "b": 3.0})
        partial = _Case(features={"a": 1.5, "b": 0.0})
        missing = _Case(features={"a": 1.5})
        out = rule_probs([fired, partial, missing], rules)
        # AND-combine = weakest conjunct governs.
        assert out[0] == pytest.approx(0.8)
        assert out[1] == pytest.approx(0.2)
        assert out[2] == pytest.approx(0.2)

    def test_rule_empty_is_degenerate(self):
        assert rule_probs([_Case()], ()) == [0.0]

    def test_null_constant(self):
        assert null_probs(3, 0.25) == [0.25, 0.25, 0.25]

    def test_supervised_fit_predicts(self):
        train = fx.planted_cases(seed=3, regions=("train_basin_a",),
                                 per_cluster=10)
        X = [c.features for c in train]
        y = [1 if c.y_state == "POSITIVE" else 0 for c in train]
        predict = fit_regularized_supervised(X, y, seed=0)
        probs = predict(X)
        assert len(probs) == len(X)
        assert all(0.0 <= p <= 1.0 for p in probs)

    def test_supervised_single_class_is_constant(self):
        predict = fit_regularized_supervised(
            [[0.0], [1.0]], [0, 0], seed=0)
        assert predict([[5.0], [6.0]]) == [0.0, 0.0]


# ---------------------------------------------------------------------
# Metric primitives
# ---------------------------------------------------------------------

class TestMetrics:
    def test_brier_known_value(self):
        assert M.brier_score([1, 0], [1.0, 0.0]) == 0.0
        assert M.brier_score([1, 0], [0.0, 1.0]) == 1.0
        assert M.brier_score([1, 0], [0.5, 0.5]) == pytest.approx(0.25)

    def test_brier_empty(self):
        assert M.brier_score([], []) == 0.0

    def test_calibration_bins_cover(self):
        bins = M.calibration_bins([1, 0, 1], [0.05, 0.5, 1.0], n_bins=4)
        assert len(bins) == 4
        assert sum(b["count"] for b in bins) == 3
        assert bins[3]["count"] == 1 and bins[3]["mean_observed"] == 1.0

    def test_calibration_fit_well_calibrated(self):
        # Per-level observed rate equals the predicted probability:
        # perfectly calibrated data -> slope ~ 1, intercept ~ 0.
        y, p = [], []
        for level in range(1, 10):
            pi = level / 10.0
            for rep in range(20):
                p.append(pi)
                y.append(1 if rep < level * 2 else 0)
        fit = M.calibration_fit(y, p)
        assert fit["slope"] == pytest.approx(1.0, abs=0.2)
        assert fit["intercept"] == pytest.approx(0.0, abs=0.2)

    def test_calibration_fit_degenerate(self):
        fit = M.calibration_fit([1, 1], [0.9, 0.8])
        assert fit["slope"] == 0.0
        assert fit["intercept"] > 0.0

    def test_pr_curve_perfect(self):
        pr = M.pr_curve([1, 1, 0, 0], [0.9, 0.8, 0.2, 0.1])
        assert pr["auprc"] == pytest.approx(1.0)

    def test_pr_curve_constant_equals_rate(self):
        pr = M.pr_curve([1, 0, 1, 0], [0.5, 0.5, 0.5, 0.5])
        assert pr["auprc"] == pytest.approx(0.5)

    def test_event_recall(self):
        assert M.event_recall([1, 1, 0], [1, 0, 1]) == pytest.approx(0.5)
        assert M.event_recall([0, 0], [1, 1]) == 0.0

    def test_false_alarms_per_opportunity(self):
        assert M.false_alarms_per_opportunity([1, 0, 1], 4) == 0.5
        assert M.false_alarms_per_opportunity([1, 1], 0) == 0.0

    def test_misaligned_rejects(self):
        with pytest.raises(ValueError):
            M.brier_score([1], [0.5, 0.5])
        with pytest.raises(ValueError):
            M.brier_score([2], [0.5])


# ---------------------------------------------------------------------
# ForecastCase round-trip and strict reconstruction
# ---------------------------------------------------------------------

class TestForecastCase:
    def test_round_trip(self):
        c = fx.planted_cases(seed=1, per_cluster=1)[0]
        d = c.to_dict()
        again = ForecastCase.from_dict(d)
        assert again == c
        canonical_json(d)  # JSON-safe

    def test_frozen(self):
        c = fx.planted_cases(seed=1, per_cluster=1)[0]
        with pytest.raises(dataclasses.FrozenInstanceError):
            c.region = "elsewhere"  # type: ignore[misc]

    def _payload(self):
        return fx.planted_cases(seed=1, per_cluster=1)[0].to_dict()

    def test_from_dict_unknown_field_rejects(self):
        d = self._payload()
        d["surprise"] = 1
        with pytest.raises(ValueError, match="unknown fields"):
            ForecastCase.from_dict(d)

    def test_from_dict_missing_required_rejects(self):
        d = self._payload()
        del d["vintage_digest"]
        with pytest.raises(ValueError, match="missing required"):
            ForecastCase.from_dict(d)

    def test_from_dict_wrong_types_reject(self):
        d = self._payload()
        d["y_prob"] = "0.5"
        with pytest.raises(ValueError, match="y_prob"):
            ForecastCase.from_dict(d)
        d = self._payload()
        d["case_id"] = 7
        with pytest.raises(ValueError, match="case_id"):
            ForecastCase.from_dict(d)

    def test_from_dict_string_false_never_boolean(self):
        d = self._payload()
        d["lead_seconds"] = "false"
        with pytest.raises(ValueError):
            ForecastCase.from_dict(d)
        d = self._payload()
        d["y_state"] = True
        with pytest.raises(ValueError):
            ForecastCase.from_dict(d)

    def test_from_dict_features_must_be_str_number_map(self):
        d = self._payload()
        d["features"] = ["not", "a", "map"]
        with pytest.raises(ValueError, match="features"):
            ForecastCase.from_dict(d)
        d = self._payload()
        d["features"] = {3: 1.0}
        with pytest.raises(ValueError, match="feature names"):
            ForecastCase.from_dict(d)
        d = self._payload()
        d["features"] = {"x": "high"}
        with pytest.raises(ValueError, match="finite number"):
            ForecastCase.from_dict(d)

    def test_from_dict_case_type_mismatch_rejects(self):
        d = self._payload()
        d["case_type"] = "SomethingElse"
        with pytest.raises(ValueError, match="case_type"):
            ForecastCase.from_dict(d)


# ---------------------------------------------------------------------
# evaluate(): the full contract
# ---------------------------------------------------------------------

def _eval_kwargs(design):
    cases = design["cases"]
    return {"holdout": design["holdout"],
            "baseline_probs": fx.make_baseline_probs(cases),
            "admitted_vintages": fx.admitted_for_cases(cases),
            "n_opportunities": len(cases),
            "n_boot": 50, "seed": 7}


class TestEvaluate:
    def test_full_metric_table_and_planted_signal(self):
        design = fx.powered_design()
        report = evaluate(design["cases"], **_eval_kwargs(design))
        assert isinstance(report, EvaluationReport)
        assert set(report.metrics) == {"model", "climatology", "rule",
                                       "null", "regularized_supervised"}
        for name, bundle in report.metrics.items():
            for key in ("brier", "calibration", "precision_recall",
                        "event_recall",
                        "false_alarms_per_opportunity", "n",
                        "n_opportunities"):
                assert key in bundle, (name, key)
            assert "auprc" in bundle["precision_recall"]
            assert "slope" in bundle["calibration"]["fit"]
            assert bundle["calibration"]["bins"]
        auprc_model = report.metrics["model"]["precision_recall"]["auprc"]
        auprc_null = report.metrics["null"]["precision_recall"]["auprc"]
        assert auprc_model > auprc_null
        assert report.status == "FORECAST_EXPERIMENT_ONLY"
        assert report.status in NEUTRAL_RESEARCH_STATUSES
        assert report.claim_scope == \
            "research_only_no_operational_authorization"

    def test_opportunity_denominator_is_explicit(self):
        design = fx.underpowered_design()
        cases = design["cases"]
        kwargs = _eval_kwargs(design)
        kwargs["n_opportunities"] = len(cases) + 9
        report = evaluate(cases, **kwargs)
        flagged = sum(
            1 for c in cases
            if c.y_state != "CENSORED_OR_AMBIGUOUS"
            and c.y_prob >= 0.5)
        expected = flagged / (len(cases) + 9)
        assert report.metrics["model"][
            "false_alarms_per_opportunity"] == pytest.approx(expected)
        # The recorded denominator is the explicit count, never the
        # unambiguous-case count.
        assert report.metrics["model"]["n_opportunities"] == \
            len(cases) + 9
        assert report.metrics["model"]["n"] == len(cases)

    def test_missing_or_undersized_opportunities_reject(self):
        design = fx.underpowered_design()
        cases = design["cases"]
        kwargs = _eval_kwargs(design)
        omitted = {k: v for k, v in kwargs.items()
                   if k != "n_opportunities"}
        with pytest.raises(ValueError, match="n_opportunities"):
            evaluate(cases, **omitted)
        for bad in (-1, 0, len(cases) - 1):
            kwargs["n_opportunities"] = bad
            with pytest.raises(ValueError, match="n_opportunities"):
                evaluate(cases, **kwargs)
        kwargs["n_opportunities"] = "many"
        with pytest.raises(ValueError, match="n_opportunities"):
            evaluate(cases, **kwargs)

    def test_region_not_in_test_groups_rejects(self):
        design = fx.underpowered_design()
        cases = [dataclasses.replace(design["cases"][0],
                                     region="not_a_test_group")]
        cases += design["cases"][1:]
        with pytest.raises(ValueError, match="test groups"):
            evaluate(cases, **_eval_kwargs(design))

    def test_single_region_rejects(self):
        design = fx.underpowered_design()
        cases = [c for c in design["cases"]
                 if c.region == "region_east"]
        assert cases
        with pytest.raises(ValueError, match="two distinct"):
            evaluate(cases, **_eval_kwargs(design))

    def test_unlocked_test_rejects(self):
        design = fx.underpowered_design()
        holdout = fx.synthetic_holdout(
            test_groups=("region_east", "region_west"),
            test_locked=False)
        kwargs = _eval_kwargs(design)
        kwargs["holdout"] = holdout
        with pytest.raises(ValueError, match="not locked"):
            evaluate(design["cases"], **kwargs)

    def test_locked_region_problems_direct(self):
        holdout = fx.synthetic_holdout()
        assert locked_region_problems(
            ("region_east", "region_west"), holdout) == []
        assert locked_region_problems(("region_east",), holdout)
        assert locked_region_problems(("nowhere", "elsewhere"), holdout)

    def test_unadmitted_vintage_rejects(self):
        design = fx.underpowered_design()
        kwargs = _eval_kwargs(design)
        kwargs["admitted_vintages"] = {}
        with pytest.raises(ValueError, match="vintage_digest"):
            evaluate(design["cases"], **kwargs)

    def test_issue_time_must_equal_vintage(self):
        design = fx.underpowered_design()
        cases = [dataclasses.replace(
            design["cases"][0], issue_time="2021-01-02T00:00:00Z")]
        cases += design["cases"][1:]
        with pytest.raises(ValueError, match="issue_time"):
            evaluate(cases, **_eval_kwargs(design))

    def test_post_issue_valid_start_rejects(self):
        design = fx.underpowered_design()
        # issue_time moved after valid_start: both the issue-equality
        # and the post-issue window checks must fire.
        c0 = design["cases"][0]
        bad = dataclasses.replace(
            c0, issue_time="2021-01-01T12:00:00Z")
        cases = [bad] + design["cases"][1:]
        with pytest.raises(ValueError) as exc:
            evaluate(cases, **_eval_kwargs(design))
        assert "precedes issue_time" in str(exc.value)

    def test_inverted_valid_window_rejects(self):
        design = fx.underpowered_design()
        c0 = design["cases"][0]
        bad = dataclasses.replace(c0, valid_end=c0.issue_time)
        cases = [bad] + design["cases"][1:]
        with pytest.raises(ValueError, match="inverted"):
            evaluate(cases, **_eval_kwargs(design))

    def test_window_outside_vintage_rejects(self):
        design = fx.underpowered_design()
        bad = dataclasses.replace(
            design["cases"][0], valid_end="2021-06-01T00:00:00Z")
        cases = [bad] + design["cases"][1:]
        with pytest.raises(ValueError, match="vintage"):
            evaluate(cases, **_eval_kwargs(design))

    def test_lead_seconds_mismatch_rejects(self):
        design = fx.underpowered_design()
        bad = dataclasses.replace(design["cases"][0],
                                  lead_seconds=1.0)
        cases = [bad] + design["cases"][1:]
        with pytest.raises(ValueError, match="lead_seconds"):
            evaluate(cases, **_eval_kwargs(design))

    def test_horizon_width_mismatch_rejects(self):
        design = fx.underpowered_design()
        bad = dataclasses.replace(design["cases"][0], horizon="48h")
        cases = [bad] + design["cases"][1:]
        with pytest.raises(ValueError, match="valid window width"):
            evaluate(cases, **_eval_kwargs(design))

    def test_bad_horizon_and_state_reject(self):
        design = fx.underpowered_design()
        bad_h = dataclasses.replace(design["cases"][0],
                                    horizon="99d")
        bad_s = dataclasses.replace(design["cases"][1],
                                    y_state="MAYBE")
        cases = [bad_h, bad_s] + design["cases"][2:]
        with pytest.raises(ValueError) as exc:
            evaluate(cases, **_eval_kwargs(design))
        assert "horizon" in str(exc.value)
        assert "y_state" in str(exc.value)

    def test_duplicate_case_id_rejects(self):
        design = fx.underpowered_design()
        dup = dataclasses.replace(design["cases"][1],
                                  case_id=design["cases"][0].case_id)
        cases = [design["cases"][0], dup] + design["cases"][2:]
        with pytest.raises(ValueError, match="duplicate case_id"):
            evaluate(cases, **_eval_kwargs(design))

    def test_missing_baseline_rejects(self):
        design = fx.underpowered_design()
        kwargs = _eval_kwargs(design)
        kwargs["baseline_probs"] = {
            k: v for k, v in kwargs["baseline_probs"].items()
            if k != "rule"}
        with pytest.raises(ValueError, match="mandatory baselines"):
            evaluate(design["cases"], **kwargs)

    def test_misaligned_baseline_rejects(self):
        design = fx.underpowered_design()
        kwargs = _eval_kwargs(design)
        kwargs["baseline_probs"]["null"] = [0.5]
        with pytest.raises(ValueError, match="aligned"):
            evaluate(design["cases"], **kwargs)

    def test_nonfinite_baseline_rejects(self):
        design = fx.underpowered_design()
        kwargs = _eval_kwargs(design)
        probs = list(kwargs["baseline_probs"]["rule"])
        probs[0] = float("nan")
        kwargs["baseline_probs"]["rule"] = probs
        with pytest.raises(ValueError, match="finite"):
            evaluate(design["cases"], **kwargs)

    def test_all_problems_listed_together(self):
        design = fx.underpowered_design()
        cases = [dataclasses.replace(design["cases"][0],
                                     region="nowhere",
                                     horizon="bad",
                                     vintage_digest="0" * 64)]
        with pytest.raises(ValueError) as exc:
            evaluate(cases, **_eval_kwargs(design))
        text = str(exc.value)
        assert "test groups" in text
        assert "horizon" in text
        assert "vintage_digest" in text
        assert "two distinct" in text

    def test_all_negative_edge_graceful(self):
        design = fx.underpowered_design()
        cases = [dataclasses.replace(c, y_state="NEGATIVE")
                 for c in design["cases"]]
        report = evaluate(cases, **_eval_kwargs(
            {"cases": cases, "holdout": design["holdout"]}))
        model = report.metrics["model"]
        assert model["n"] == len(cases)
        assert model["n_positive"] == 0
        assert model["precision_recall"]["auprc"] == 0.0
        assert model["event_recall"] == 0.0
        assert model["calibration"]["fit"]["slope"] == 0.0
        canonical_json(report.to_dict())


# ---------------------------------------------------------------------
# Censored accounting
# ---------------------------------------------------------------------

class TestCensoring:
    def test_censored_excluded_counted(self):
        design = fx.underpowered_design()
        cases = [dataclasses.replace(
            c, y_state="CENSORED_OR_AMBIGUOUS", y_prob=1.0)
            for c in design["cases"][:3]] + design["cases"][3:]
        report = evaluate(cases, **_eval_kwargs(
            {"cases": cases, "holdout": design["holdout"]}))
        assert report.n_cases == len(cases)
        assert report.n_censored == 3
        for bundle in report.metrics.values():
            assert bundle["n"] == len(cases) - 3

    def test_censored_never_folded_into_negatives(self):
        design = fx.underpowered_design()
        cases = design["cases"]
        # A maximally wrong censored case must not perturb the metrics.
        planted = dataclasses.replace(
            cases[0], y_state="CENSORED_OR_AMBIGUOUS", y_prob=1.0)
        cases = [planted] + cases[1:]
        kwargs = _eval_kwargs(
            {"cases": cases, "holdout": design["holdout"]})
        report = evaluate(cases, **kwargs)
        rest = [c for c in cases[1:]
                if c.y_state != "CENSORED_OR_AMBIGUOUS"]
        y = [1 if c.y_state == "POSITIVE" else 0 for c in rest]
        expected_brier = M.brier_score(y, [c.y_prob for c in rest])
        assert report.metrics["model"]["brier"] == pytest.approx(
            expected_brier)


# ---------------------------------------------------------------------
# Missing-feed degradation
# ---------------------------------------------------------------------

class TestDegradation:
    def test_drop_units_recomputes_never_imputes(self):
        design = fx.powered_design()
        cases = design["cases"]
        dropped_unit = cases[0].unit_id
        expected_gone = sum(1 for c in cases
                            if c.unit_id == dropped_unit)
        out = missing_feed_degradation(
            cases, [{"name": "unit_dropout",
                     "drop_units": (dropped_unit,)}])
        sc = out["scenarios"][0]
        assert sc["n_dropped"] == expected_gone
        assert sc["n_cases"] == len(cases) - expected_gone
        assert sc["metrics"]["n"] == \
            out["reference_metrics"]["n"] - sum(
                1 for c in cases
                if c.unit_id == dropped_unit
                and c.y_state != "CENSORED_OR_AMBIGUOUS")
        # Degradation shrinks the sample; it never fabricates values.
        assert "delta" in sc

    def test_drop_fraction_deterministic(self):
        design = fx.powered_design()
        cases = design["cases"]
        out1 = missing_feed_degradation(
            cases, [{"name": "half", "drop_fraction": 0.5}])
        out2 = missing_feed_degradation(
            cases, [{"name": "half", "drop_fraction": 0.5}])
        assert out1 == out2
        sc = out1["scenarios"][0]
        assert 0 < sc["n_dropped"] < len(cases)

    def test_evaluate_embeds_degradation_block(self):
        design = fx.underpowered_design()
        report = evaluate(design["cases"], **_eval_kwargs(design))
        assert report.degradation["policy"] == \
            "degradation_recompute_never_imputation"
        assert "reference_metrics" in report.degradation
        assert report.degradation["scenarios"] == []


# ---------------------------------------------------------------------
# Power gating
# ---------------------------------------------------------------------

class TestPower:
    def test_underpowered_status(self):
        design = fx.underpowered_design()
        report = evaluate(design["cases"], **_eval_kwargs(design))
        assert report.power["powered"] is False
        assert report.status == "UNDERPOWERED_DESCRIPTIVE_ONLY"

    def test_powered_status(self):
        design = fx.powered_design()
        report = evaluate(design["cases"], **_eval_kwargs(design))
        assert report.power["powered"] is True
        assert report.power["effective_n"] >= report.power["required_n"]
        assert report.status == "FORECAST_EXPERIMENT_ONLY"

    def test_power_report_direct(self):
        design = fx.underpowered_design()
        power = power_report(design["cases"])
        assert power["cluster_key"] == "region+season+mechanism"
        assert power["powered"] is False


# ---------------------------------------------------------------------
# Uncertainty / determinism / lead-time
# ---------------------------------------------------------------------

class TestUncertaintyAndDeterminism:
    def test_uncertainty_report_shape(self):
        design = fx.underpowered_design()
        cases = design["cases"]
        out = uncertainty_report(cases, fx.make_baseline_probs(cases),
                                 n_boot=40, seed=3)
        assert set(out["scorers"]) >= {"model", "climatology", "rule",
                                       "null",
                                       "regularized_supervised"}
        for scorer in out["scorers"].values():
            for metric in ("brier", "auprc", "event_recall",
                           "false_alarms_per_opportunity"):
                ci = scorer[metric]
                assert ci["ci_low"] <= ci["ci_high"]
                assert ci["n_replicates"] == 40

    def test_determinism_identical_digest(self):
        design = fx.underpowered_design()
        kwargs = _eval_kwargs(design)
        r1 = evaluate(design["cases"], **kwargs)
        r2 = evaluate(design["cases"], **kwargs)
        assert sha256_canonical(r1.to_dict()) == \
            sha256_canonical(r2.to_dict())

    def test_report_json_safe(self):
        design = fx.underpowered_design()
        report = evaluate(design["cases"], **_eval_kwargs(design))
        canonical_json(report.to_dict())

    def test_lead_time_buckets_per_horizon(self):
        design = fx.underpowered_design()
        report = evaluate(design["cases"], **_eval_kwargs(design))
        present = {c.horizon for c in design["cases"]}
        assert set(report.lead_time) == present
        for bundle in report.lead_time.values():
            assert "scorers" in bundle and "model" in bundle["scorers"]

    def test_slices_axes(self):
        design = fx.underpowered_design()
        report = evaluate(design["cases"], **_eval_kwargs(design))
        assert set(report.slices) == {"region", "season", "mechanism"}
        assert set(report.slices["region"]) == \
            {"region_east", "region_west"}


# ---------------------------------------------------------------------
# Claim-scan cleanliness of module sources
# ---------------------------------------------------------------------

class TestClaimScan:
    def test_module_sources_clean(self):
        root = Path(__file__).resolve().parent.parent
        for rel in ("nepal/experiment_v0/baselines.py",
                    "nepal/experiment_v0/metrics.py",
                    "nepal/experiment_v0/evaluation.py",
                    "tests/fixtures/synthetic_exp_b3.py",
                    "tests/test_experiment_v0_b3_evaluation.py",
                    "tests/test_experiment_v0_b3_vintage_timing.py"):
            findings = scan_claims_text((root / rel).read_text())
            assert findings == [], (rel, findings)
