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
from nepal.experiment_v0.baselines import (FitPartition,
    BASELINE_NAMES, ThresholdRule, climatology_probs,
    fit_climatology_rates, fit_marginal_rate,
    fit_partition_provenance, fit_regularized_supervised,
    null_probs, rule_probs)
from nepal.experiment_v0.evaluation import (
    DEFAULT_SCENARIOS, EvaluationReport, ForecastCase,
    ForecastExperimentDeclaration, evaluate,
    locked_region_problems, missing_feed_degradation, power_report,
    uncertainty_report)
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
        part = FitPartition(
            partition="TRAIN_ONLY",
            rows=[c.features for c in train],
            labels=[1 if c.y_state == "POSITIVE" else 0
                    for c in train])
        X = part.rows
        predict = fit_regularized_supervised(part, seed=0)
        probs = predict(X)
        assert len(probs) == len(X)
        assert all(0.0 <= p <= 1.0 for p in probs)

    def test_supervised_single_class_is_constant(self):
        predict = fit_regularized_supervised(
            FitPartition(partition="TRAIN_ONLY",
                         rows=[[0.0], [1.0]], labels=[0, 0]),
            seed=0)
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
            "opportunities": design.get(
                "opportunities", fx.opportunity_registry(cases)),
            "unit_basins": design.get(
                "unit_basins", fx.unit_basins_for(cases)),
            "region_basins": design.get(
                "region_basins",
                fx.region_basins_for(
                    sorted({c.region for c in cases}))),
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
        # EVAL-03: a powered fixture-only call can no longer emit a
        # forecast status — that requires a complete bound
        # declaration plus byte-bound baseline evidence.
        assert report.status == "UNDERPOWERED_DESCRIPTIVE_ONLY"
        assert report.status in NEUTRAL_RESEARCH_STATUSES
        assert report.claim_scope == \
            "research_only_no_operational_authorization"
        # the same powered design reaches FORECAST_EXPERIMENT_ONLY
        # only when the declaration and baseline evidence are bound
        from nepal.research_v0._hashing import sha256_canonical
        cases = design["cases"]
        probs = fx.make_baseline_probs(cases)
        kw = _eval_kwargs(design)
        kw["baseline_evidence"] = {
            name: {"digest": sha256_canonical(
                       [round(float(v), 9) for v in v_]),
                   "fit_provenance": "bound"}
            for name, v_ in probs.items()}
        kw["experiment"] = ForecastExperimentDeclaration(
            declaration_id="decl-powered",
            feature_artifact_digest="a" * 64,
            threshold_record={"threshold": 0.5},
            ablations=("model",),
            vintage_lineage=tuple(
                sorted({c.vintage_digest for c in cases})))
        declared = evaluate(cases, **kw)
        assert declared.status == "FORECAST_EXPERIMENT_ONLY"

    def test_opportunity_denominator_is_explicit(self):
        design = fx.underpowered_design()
        cases = design["cases"]
        kwargs = _eval_kwargs(design)
        report = evaluate(cases, **kwargs)
        flagged = sum(
            1 for c in cases
            if c.y_state != "CENSORED_OR_AMBIGUOUS"
            and c.y_prob >= 0.5)
        derived = len(design["opportunities"])
        expected = flagged / derived
        assert report.metrics["model"][
            "false_alarms_per_opportunity"] == pytest.approx(expected)
        # The recorded denominator is the registry-derived count,
        # never the unambiguous-case count and never caller-chosen.
        assert report.metrics["model"]["n_opportunities"] == derived
        assert report.metrics["model"]["n"] == len(cases)

    def test_missing_or_undersized_opportunities_reject(self):
        design = fx.underpowered_design()
        cases = design["cases"]
        kwargs = _eval_kwargs(design)
        # a registry missing a case's opportunity id rejects
        shrunk = {k: v for k, v in
                  design["opportunities"].items()
                  if k != cases[0].opportunity_id}
        kwargs["opportunities"] = shrunk
        with pytest.raises(ValueError, match="absent from the"):
            evaluate(cases, **kwargs)
        # a declared count that disagrees with the registry rejects
        kwargs = _eval_kwargs(design)
        kwargs["n_opportunities_declared"] = 10 ** 6
        with pytest.raises(ValueError,
                           match="n_opportunities_declared"):
            evaluate(cases, **kwargs)
        # an inflated free integer is not even a valid parameter
        kwargs.pop("n_opportunities_declared")
        kwargs["n_opportunities"] = 10 ** 6
        with pytest.raises(TypeError):
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
# Per-case lineage identifiers
# ---------------------------------------------------------------------

class TestLineage:
    def test_payload_carries_lineage_fields(self):
        d = fx.planted_cases(seed=1, per_cluster=1)[0].to_dict()
        for key in ("opportunity_id", "outcome_source_id",
                    "cutoff_time"):
            assert d[key], key
        assert ForecastCase.from_dict(d) == \
            ForecastCase.from_dict(dict(d))

    def test_empty_lineage_ids_reject(self):
        design = fx.underpowered_design()
        for field_name in ("opportunity_id", "outcome_source_id"):
            bad = dataclasses.replace(design["cases"][0],
                                      **{field_name: ""})
            cases = [bad] + design["cases"][1:]
            with pytest.raises(ValueError, match=field_name):
                evaluate(cases, **_eval_kwargs(design))
            bad = dataclasses.replace(design["cases"][0],
                                      **{field_name: "   "})
            cases = [bad] + design["cases"][1:]
            with pytest.raises(ValueError, match=field_name):
                evaluate(cases, **_eval_kwargs(design))

    def test_missing_or_malformed_cutoff_rejects(self):
        design = fx.underpowered_design()
        for bad_cutoff in ("", "2021-01-01 00:00:00",
                           "2021-01-01T00:00:00"):
            bad = dataclasses.replace(design["cases"][0],
                                      cutoff_time=bad_cutoff)
            cases = [bad] + design["cases"][1:]
            with pytest.raises(ValueError, match="cutoff_time"):
                evaluate(cases, **_eval_kwargs(design))

    def test_cutoff_after_issue_rejects(self):
        design = fx.underpowered_design()
        bad = dataclasses.replace(design["cases"][0],
                                  cutoff_time="2021-01-02T00:00:00Z")
        cases = [bad] + design["cases"][1:]
        with pytest.raises(ValueError, match="postdates issue_time"):
            evaluate(cases, **_eval_kwargs(design))

    def test_cutoff_before_issue_admitted(self):
        design = fx.underpowered_design()
        earlier = dataclasses.replace(
            design["cases"][0], cutoff_time="2020-12-31T00:00:00Z")
        cases = [earlier] + design["cases"][1:]
        report = evaluate(cases, **_eval_kwargs(design))
        assert report.n_cases == len(cases)


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
        # EVAL-01: an undeclared scenario set runs the deterministic
        # default grid — the degradation surface is never empty.
        scen = report.degradation["scenarios"]
        names = {s["name"] for s in scen}
        assert {"fraction_dropout_0.10", "fraction_dropout_0.25",
                "fraction_dropout_0.50"} <= names
        assert any(n.startswith("region_dropout_") for n in names)
        for s in scen:
            assert s["n_cases"] >= 0 and "n_dropped" in s


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
        cases = design["cases"]
        kw = _eval_kwargs(design)
        # EVAL-03: power alone no longer promotes a fixture-only call;
        # bind the declaration + baseline evidence for the forecast
        # status.
        from nepal.research_v0._hashing import sha256_canonical
        probs = fx.make_baseline_probs(cases)
        kw["baseline_evidence"] = {
            name: {"digest": sha256_canonical(
                       [round(float(v), 9) for v in v_]),
                   "fit_provenance": "bound"}
            for name, v_ in probs.items()}
        kw["experiment"] = ForecastExperimentDeclaration(
            declaration_id="decl-powered",
            feature_artifact_digest="a" * 64,
            threshold_record={"threshold": 0.5},
            ablations=("model",),
            vintage_lineage=tuple(
                sorted({c.vintage_digest for c in cases})))
        report = evaluate(cases, **kw)
        assert report.power["powered"] is True
        assert report.power["effective_n"] >= report.power["required_n"]
        assert report.status == "FORECAST_EXPERIMENT_ONLY"

    def test_power_report_direct(self):
        design = fx.underpowered_design()
        power = power_report(design["cases"])
        assert power["clustering_unit"] == \
            "event_group_id_else_unit_id+season_cell"
        assert power["powered"] is False
        # n_effective counts independent clusters, never raw cases.
        assert power["n_effective"] == power["n_clusters"]
        assert power["n_effective"] < len(design["cases"])

    def test_event_group_collapses_clusters(self):
        # 4 cases sharing one event group are ONE independent unit.
        cases = fx.planted_cases(seed=2, regions=("region_east",),
                                 seasons=("season_a",), per_cluster=4)
        assert len({c.event_group_id for c in cases}) == 1
        power = power_report(cases)
        assert power["n_effective"] == 1

    def test_unit_season_cell_fallback_when_group_absent(self):
        cases = fx.planted_cases(seed=2, regions=("region_east",),
                                 seasons=("season_a",), per_cluster=4,
                                 event_groups=False)
        assert all(c.event_group_id == "" for c in cases)
        power = power_report(cases)
        assert power["n_effective"] == \
            len({(c.unit_id, c.season) for c in cases})

    def test_raw_case_count_never_effective(self):
        design = fx.powered_design()
        power = power_report(design["cases"])
        assert power["n_effective"] < len(design["cases"])
        assert power["clustering_unit"]


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

    def test_case_order_permutation_invariant_digest(self):
        """Cases are canonically ordered by (case_id, opportunity_id)
        before scoring and hashing — a pure reordering of the case
        list (baseline vectors permuted to stay aligned) yields
        identical metrics and an identical report digest."""
        design = fx.underpowered_design()
        cases = design["cases"]
        n = len(cases)
        kwargs = _eval_kwargs(design)
        reference = evaluate(cases, **kwargs)
        for perm in (list(reversed(range(n))),
                     [(i * 7 + 3) % n for i in range(n)]):
            assert sorted(perm) == list(range(n))
            reordered = [cases[i] for i in perm]
            pk = dict(kwargs)
            pk["baseline_probs"] = {
                name: [vec[i] for i in perm]
                for name, vec in kwargs["baseline_probs"].items()}
            rerouted = evaluate(reordered, **pk)
            assert rerouted.experiment_id == reference.experiment_id
            assert rerouted.metrics == reference.metrics
            assert sha256_canonical(rerouted.to_dict()) == \
                sha256_canonical(reference.to_dict())

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


# --------------------------------------------------------------
# Post-audit residuals: admission-key and lineage probes
# --------------------------------------------------------------

def test_noncanonical_vintage_key_rejected():
    """admitted_vintages keys must recompute to
    sha256_canonical(vintage.to_dict()) — arbitrary keys mean the
    admission map never passed through build_vintage discipline."""
    design = fx.underpowered_design()
    kwargs = _eval_kwargs(design)
    vintages = kwargs["admitted_vintages"]
    kwargs["admitted_vintages"] = {
        "a" * 64: next(iter(vintages.values()))}
    with pytest.raises(ValueError, match="canonical digest"):
        evaluate(design["cases"], **kwargs)


def test_holdout_record_validated_not_duck_typed():
    """A HoldoutPlanV0 carrying contract violations must be rejected
    even when it happens to expose locked test groups."""
    design = fx.underpowered_design()
    bad = dataclasses.replace(design["holdout"], holdout_plan_id="",
                              test_locked=True)
    assert bad.problems()  # the record itself is invalid
    kwargs = _eval_kwargs(design)
    kwargs["holdout"] = bad
    with pytest.raises(ValueError, match="holdout"):
        evaluate(design["cases"], **kwargs)


def test_shared_opportunity_id_rejected():
    design = fx.underpowered_design()
    cases = list(design["cases"])
    cases[1] = dataclasses.replace(
        cases[1], opportunity_id=cases[0].opportunity_id)
    kwargs = _eval_kwargs(design)
    with pytest.raises(ValueError, match="distinct"):
        evaluate(cases, **kwargs)


def test_case_region_outside_declared_names_rejected():
    """A case citing a locked test group that is NOT a declared
    evaluation region rejects — declared regions are not decorative."""
    design = fx.underpowered_design()
    holdout = dataclasses.replace(
        design["holdout"],
        evaluation_region_names=("region_east",))
    kwargs = _eval_kwargs(design)
    kwargs["holdout"] = holdout
    with pytest.raises(ValueError, match="declared evaluation"):
        evaluate(design["cases"], **kwargs)


# --------------------------------------------------------------
# EVAL-C01: denominators count only verified OBSERVED_FULL entries
# --------------------------------------------------------------

class TestVerifiedOpportunityDenominator:
    def test_non_observed_full_excluded_and_counted(self):
        design = fx.underpowered_design()
        cases = design["cases"]
        kwargs = _eval_kwargs(design)
        opps = dict(design["opportunities"])
        unit = cases[0].unit_id
        for j, state in enumerate(
                ("OBSERVED_PARTIAL", "UNOBSERVED", "UNKNOWN")):
            oid = f"degraded-{j}"
            opps[oid] = fx.degraded_opportunity(
                oid, unit, cases[0].valid_start, cases[0].valid_end,
                state=state)
        kwargs["opportunities"] = opps
        report = evaluate(cases, **kwargs)
        derived = len(design["opportunities"])
        assert report.n_opportunities == derived
        assert report.n_censored_opportunities == 3
        assert report.metrics["model"]["n_opportunities"] == derived
        assert report.opportunity_scope["censored_ids"] == \
            ["degraded-0", "degraded-1", "degraded-2"]

    def test_defective_entry_censored_not_rejected(self):
        """An OBSERVED_FULL-claimed record whose problems() is
        non-empty never enters the denominator — it is excluded and
        counted, and does not sink the evaluation."""
        design = fx.underpowered_design()
        cases = design["cases"]
        kwargs = _eval_kwargs(design)
        opps = dict(design["opportunities"])
        opps["defective-0"] = fx.defective_opportunity(
            "defective-0", cases[0].unit_id,
            cases[0].valid_start, cases[0].valid_end)
        kwargs["opportunities"] = opps
        report = evaluate(cases, **kwargs)
        assert report.n_censored_opportunities == 1
        assert report.n_opportunities == \
            len(design["opportunities"])

    def test_out_of_scope_entry_neither_counted_nor_censored(self):
        design = fx.underpowered_design()
        cases = design["cases"]
        kwargs = _eval_kwargs(design)
        opps = dict(design["opportunities"])
        ub = dict(design["unit_basins"])
        ub["ghost-unit"] = "ghost-basin"   # mapped, never claimed
        opps["outside-0"] = fx.synthetic_opportunity(
            "outside-0", "ghost-unit",
            "2021-01-01T06:00:00Z", "2021-01-02T06:00:00Z")
        kwargs["opportunities"] = opps
        kwargs["unit_basins"] = ub
        report = evaluate(cases, **kwargs)
        assert report.n_opportunities == \
            len(design["opportunities"])
        assert report.n_censored_opportunities == 0


# --------------------------------------------------------------
# EVAL-C02: a case's state is never trusted without the verified
# opportunity basis
# --------------------------------------------------------------

class TestOpportunityBasisCensoring:
    def _relinked_design(self, design, state="OBSERVED_PARTIAL"):
        cases = design["cases"]
        target = cases[0]
        opps = dict(design["opportunities"])
        opps[target.opportunity_id] = fx.degraded_opportunity(
            target.opportunity_id, target.unit_id,
            target.valid_start, target.valid_end, state=state)
        return target, opps

    def test_partial_link_censored_not_scored(self):
        design = fx.underpowered_design()
        cases = design["cases"]
        target, opps = self._relinked_design(design)
        kwargs = _eval_kwargs(design)
        kwargs["opportunities"] = opps
        report = evaluate(cases, **kwargs)
        assert report.n_censored_for_opportunity_state == 1
        assert report.metrics["model"]["n"] == len(cases) - 1
        rest = [c for c in cases if c is not target]
        y = [1 if c.y_state == "POSITIVE" else 0 for c in rest]
        assert report.metrics["model"]["brier"] == pytest.approx(
            M.brier_score(y, [c.y_prob for c in rest]))
        # the degraded entry is a censored opportunity as well
        assert report.n_censored_opportunities == 1
        assert report.n_opportunities == \
            len(design["opportunities"]) - 1

    def test_defective_link_censored_not_scored(self):
        design = fx.underpowered_design()
        cases = design["cases"]
        target = cases[0]
        opps = dict(design["opportunities"])
        opps[target.opportunity_id] = fx.defective_opportunity(
            target.opportunity_id, target.unit_id,
            target.valid_start, target.valid_end)
        kwargs = _eval_kwargs(design)
        kwargs["opportunities"] = opps
        report = evaluate(cases, **kwargs)
        assert report.n_censored_for_opportunity_state == 1
        assert report.metrics["model"]["n"] == len(cases) - 1

    @pytest.mark.parametrize("state", ["UNOBSERVED", "UNKNOWN"])
    def test_unobserved_and_unknown_links_censored(self, state):
        design = fx.underpowered_design()
        cases = design["cases"]
        _, opps = self._relinked_design(design, state=state)
        kwargs = _eval_kwargs(design)
        kwargs["opportunities"] = opps
        report = evaluate(cases, **kwargs)
        assert report.n_censored_for_opportunity_state == 1

    def test_out_of_scope_link_censored(self):
        design = fx.underpowered_design()
        cases = design["cases"]
        target = dataclasses.replace(cases[0], unit_id="ghost-unit")
        cases = [target] + list(cases[1:])
        opps = dict(design["opportunities"])
        opps[target.opportunity_id] = dataclasses.replace(
            opps[target.opportunity_id], unit_id="ghost-unit")
        ub = dict(design["unit_basins"])
        ub["ghost-unit"] = "ghost-basin"   # unclaimed by any region
        kwargs = _eval_kwargs(design)
        kwargs["opportunities"] = opps
        kwargs["unit_basins"] = ub
        report = evaluate(cases, **kwargs)
        assert report.n_censored_out_of_scope == 1
        assert report.metrics["model"]["n"] == len(cases) - 1
        assert report.n_opportunities == \
            len(design["opportunities"]) - 1

    def test_censored_state_case_still_counts_in_n_censored(self):
        """A CENSORED_OR_AMBIGUOUS case linked to a degraded entry is
        counted once, under n_censored — not double-counted."""
        design = fx.underpowered_design()
        cases = list(design["cases"])
        cases[0] = dataclasses.replace(
            cases[0], y_state="CENSORED_OR_AMBIGUOUS")
        target, opps = self._relinked_design(
            {"cases": cases, "holdout": design["holdout"],
             "opportunities": design["opportunities"],
             "unit_basins": design["unit_basins"],
             "region_basins": design["region_basins"]})
        kwargs = _eval_kwargs(
            {"cases": cases, "holdout": design["holdout"]})
        kwargs["opportunities"] = opps
        kwargs["unit_basins"] = design["unit_basins"]
        kwargs["region_basins"] = design["region_basins"]
        report = evaluate(cases, **kwargs)
        assert report.n_censored == 1
        assert report.n_censored_for_opportunity_state == 0


# --------------------------------------------------------------
# EVAL-C03: every slice denominator is registry-scoped, never a
# case count
# --------------------------------------------------------------

class TestSliceScopedDenominators:
    def test_region_slice_counts_owned_opportunities(self):
        design = fx.underpowered_design()
        cases = design["cases"]
        east = [c for c in cases if c.region == "region_east"]
        extra = fx.unlinked_opportunities(
            sorted({c.unit_id for c in east}), n=2)
        kwargs = _eval_kwargs(design)
        kwargs["opportunities"] = {
            **design["opportunities"], **extra}
        report = evaluate(cases, **kwargs)
        bundle = report.slices["region"]["region_east"]
        expected = len(east) + len(extra)
        assert bundle["opportunities_scoped"] == expected
        assert bundle["opportunities_scoped"] != bundle["n_cases"]
        assert bundle["scope_rule"] == "region_owned_basins"
        for sc in bundle["scorers"].values():
            assert sc["n_opportunities"] == expected

    def test_season_slice_counts_linked_plus_unlinked(self):
        # EVAL-01: non-meteorological season labels cannot be
        # attributed to opportunities — only opportunities linked by
        # the slice's own cases count; unattributable unlinked
        # opportunities are excluded, never smeared in.
        design = fx.underpowered_design()
        cases = design["cases"]
        extra = fx.unlinked_opportunities(
            sorted({c.unit_id for c in cases}), n=1)
        kwargs = _eval_kwargs(design)
        kwargs["opportunities"] = {
            **design["opportunities"], **extra}
        report = evaluate(cases, **kwargs)
        for season, bundle in report.slices["season"].items():
            slice_cases = [c for c in cases if c.season == season]
            assert bundle["scope_rule"] == \
                "linked_opportunities_only"
            assert bundle["opportunities_scoped"] == \
                len(slice_cases)

    def test_lead_time_bucket_denominators_scoped(self):
        design = fx.underpowered_design()
        cases = design["cases"]
        report = evaluate(cases, **_eval_kwargs(design))
        for h, bundle in report.lead_time.items():
            bucket = [c for c in cases if c.horizon == h]
            assert bundle["opportunities_scoped"] == len(bucket)
            assert "case count" not in bundle["scope_rule"]

    def test_slice_censored_opportunity_counts(self):
        design = fx.underpowered_design()
        cases = design["cases"]
        east_cases = [c for c in cases if c.region == "region_east"]
        opps = dict(design["opportunities"])
        opps["deg-east"] = fx.degraded_opportunity(
            "deg-east", east_cases[0].unit_id,
            east_cases[0].valid_start, east_cases[0].valid_end)
        kwargs = _eval_kwargs(design)
        kwargs["opportunities"] = opps
        report = evaluate(cases, **kwargs)
        assert report.slices["region"]["region_east"][
            "n_censored_opportunities"] == 1
        assert report.slices["region"]["region_west"][
            "n_censored_opportunities"] == 0


# --------------------------------------------------------------
# EVAL-C05: declared missing-feed scenario set
# --------------------------------------------------------------

class TestMissingFeedScenarios:
    def test_default_set_all_five(self):
        design = fx.underpowered_design()
        report = evaluate(design["cases"], **_eval_kwargs(design))
        names = {s["name"] for s in
                 report.missing_feed["scenarios"]}
        assert names == set(DEFAULT_SCENARIOS)

    def test_provider_dropout_drops_model_only(self):
        design = fx.underpowered_design()
        report = evaluate(design["cases"], **_eval_kwargs(design))
        sc = next(s for s in report.missing_feed["scenarios"]
                  if s["name"] == "provider_dropout")
        assert sc["status"] == "EXECUTED"
        assert "model" not in sc["scorers"]
        assert set(sc["scorers"]) == {
            "climatology", "rule", "null", "regularized_supervised"}
        assert sc["scorers"]["null"] == report.metrics["null"]

    def test_variable_dropout_rule_falls_back_to_climatology(self):
        design = fx.underpowered_design()
        report = evaluate(design["cases"], **_eval_kwargs(design))
        sc = next(s for s in report.missing_feed["scenarios"]
                  if s["name"] == "variable_dropout")
        assert sc["status"] == "EXECUTED"
        assert sc["scorers"]["rule"] == \
            report.metrics["climatology"]
        assert sc["scorers"]["rule"] != report.metrics["rule"] \
            or report.metrics["rule"] == \
            report.metrics["climatology"]

    def test_member_truncation_and_latency_not_applicable(self):
        design = fx.underpowered_design()
        report = evaluate(design["cases"], **_eval_kwargs(design))
        by_name = {s["name"]: s for s in
                   report.missing_feed["scenarios"]}
        assert by_name["member_truncation"]["status"] == \
            "NOT_APPLICABLE"
        assert by_name["latency_stress"]["status"] == \
            "NOT_APPLICABLE"
        assert by_name["member_truncation"]["reason"]
        assert by_name["latency_stress"]["reason"]

    def test_missing_opportunity_executes_with_counts(self):
        design = fx.underpowered_design()
        cases = design["cases"]
        target = cases[0]
        opps = dict(design["opportunities"])
        opps[target.opportunity_id] = fx.degraded_opportunity(
            target.opportunity_id, target.unit_id,
            target.valid_start, target.valid_end)
        kwargs = _eval_kwargs(design)
        kwargs["opportunities"] = opps
        report = evaluate(cases, **kwargs)
        sc = next(s for s in report.missing_feed["scenarios"]
                  if s["name"] == "missing_opportunity")
        assert sc["status"] == "EXECUTED"
        assert sc["n_censored_for_opportunity_state"] == 1
        assert sc["scorers"]["model"] == report.metrics["model"]

    def test_scenarios_param_selects_subset(self):
        design = fx.underpowered_design()
        kwargs = _eval_kwargs(design)
        kwargs["scenarios"] = ("provider_dropout",)
        report = evaluate(design["cases"], **kwargs)
        assert [s["name"] for s in
                report.missing_feed["scenarios"]] == \
            ["provider_dropout"]

    def test_unknown_scenario_not_applicable(self):
        design = fx.underpowered_design()
        kwargs = _eval_kwargs(design)
        kwargs["scenarios"] = ("provider_dropout", "bogus_feed")
        report = evaluate(design["cases"], **kwargs)
        by_name = {s["name"]: s for s in
                   report.missing_feed["scenarios"]}
        assert by_name["bogus_feed"]["status"] == "NOT_APPLICABLE"
        assert by_name["provider_dropout"]["status"] == "EXECUTED"


# --------------------------------------------------------------
# EVAL-C06 / FCST-C02: bound experiment declaration
# --------------------------------------------------------------

class TestExperimentDeclaration:
    def test_absent_declaration_is_fixture_only(self):
        design = fx.underpowered_design()
        report = evaluate(design["cases"], **_eval_kwargs(design))
        assert report.declaration["mode"] == "fixture_only"
        assert report.status in NEUTRAL_RESEARCH_STATUSES

    def test_valid_mapping_declared(self):
        design = fx.underpowered_design()
        cases = design["cases"]
        kwargs = _eval_kwargs(design)
        kwargs["experiment"] = fx.experiment_declaration(cases)
        report = evaluate(cases, **kwargs)
        assert report.declaration["mode"] == "declared"
        assert report.declaration["declaration_id"] == \
            "decl-synth-b3"
        assert report.declaration["n_vintage_lineage"] == \
            len({c.vintage_digest for c in cases})

    def test_valid_dataclass_declared(self):
        design = fx.underpowered_design()
        cases = design["cases"]
        decl = ForecastExperimentDeclaration.from_mapping(
            fx.experiment_declaration(cases))
        assert decl.problems() == []
        kwargs = _eval_kwargs(design)
        kwargs["experiment"] = decl
        report = evaluate(cases, **kwargs)
        assert report.declaration["mode"] == "declared"

    def test_missing_keys_reject(self):
        design = fx.underpowered_design()
        cases = design["cases"]
        for key in ("declaration_id", "feature_artifact_digest",
                    "threshold_record", "ablations",
                    "vintage_lineage"):
            payload = fx.experiment_declaration(cases)
            del payload[key]
            kwargs = _eval_kwargs(design)
            kwargs["experiment"] = payload
            with pytest.raises(ValueError, match="experiment"):
                evaluate(cases, **kwargs)

    def test_unknown_keys_reject(self):
        design = fx.underpowered_design()
        cases = design["cases"]
        payload = fx.experiment_declaration(cases)
        payload["surprise"] = 1
        kwargs = _eval_kwargs(design)
        kwargs["experiment"] = payload
        with pytest.raises(ValueError, match="unknown keys"):
            evaluate(cases, **kwargs)

    def test_bad_feature_digest_rejects(self):
        design = fx.underpowered_design()
        cases = design["cases"]
        payload = fx.experiment_declaration(cases)
        payload["feature_artifact_digest"] = "not-a-digest"
        kwargs = _eval_kwargs(design)
        kwargs["experiment"] = payload
        with pytest.raises(ValueError, match="feature_artifact"):
            evaluate(cases, **kwargs)

    def test_lineage_must_cover_case_vintages(self):
        design = fx.underpowered_design()
        cases = design["cases"]
        payload = fx.experiment_declaration(cases)
        payload["vintage_lineage"] = ["0" * 64]
        kwargs = _eval_kwargs(design)
        kwargs["experiment"] = payload
        with pytest.raises(ValueError, match="vintage_lineage"):
            evaluate(cases, **kwargs)

    def test_threshold_mismatch_rejects(self):
        design = fx.underpowered_design()
        cases = design["cases"]
        payload = fx.experiment_declaration(cases)
        payload["threshold_record"] = {"threshold": 0.9}
        kwargs = _eval_kwargs(design)
        kwargs["experiment"] = payload
        with pytest.raises(ValueError, match="threshold"):
            evaluate(cases, **kwargs)

    def test_wrong_experiment_type_rejects(self):
        design = fx.underpowered_design()
        kwargs = _eval_kwargs(design)
        kwargs["experiment"] = 42
        with pytest.raises(ValueError, match="experiment"):
            evaluate(design["cases"], **kwargs)

    def test_declaration_problems_method(self):
        decl = ForecastExperimentDeclaration()
        assert decl.problems()
        assert ForecastExperimentDeclaration.from_mapping(
            fx.experiment_declaration(
                fx.underpowered_design()["cases"])).problems() == []


# --------------------------------------------------------------
# FCST-C01: FitPartition provenance binding
# --------------------------------------------------------------

class TestFitPartitionProvenance:
    def test_unbound_fixture_marked(self):
        cases = fx.underpowered_design()["cases"]
        part = fx.train_partition(cases)
        rates = fit_climatology_rates(part)
        assert rates.fit_provenance["provenance"] == "unbound_fixture"
        assert fit_marginal_rate(part).fit_provenance[
            "provenance"] == "unbound_fixture"
        predict = fit_regularized_supervised(part)
        assert predict.fit_provenance["provenance"] == \
            "unbound_fixture"

    def test_bound_provenance_recorded(self):
        cases = fx.underpowered_design()["cases"]
        part = fx.train_partition(cases, bound=True)
        rates = fit_climatology_rates(part)
        assert rates.fit_provenance["provenance"] == "bound"
        assert rates.fit_provenance["holdout_digest"]
        assert rates.fit_provenance["train_groups"] == \
            ("train_basin_a",)
        predict = fit_regularized_supervised(part)
        assert predict.fit_provenance["provenance"] == "bound"
        assert fit_marginal_rate(part).fit_provenance[
            "provenance"] == "bound"

    def test_unbound_case_like_rows_reject(self):
        """An unbound partition whose rows are scored ForecastCase
        objects is rejected even for fits that ignore rows."""
        cases = fx.planted_cases(seed=3, regions=("train_basin_a",),
                                 per_cluster=6)
        labels = [1 if c.y_state == "POSITIVE" else 0
                  for c in cases]
        with pytest.raises(ValueError, match="scored cases"):
            fit_regularized_supervised(FitPartition(
                partition="TRAIN_ONLY", rows=list(cases),
                labels=labels))
        with pytest.raises(ValueError, match="scored cases"):
            fit_marginal_rate(FitPartition(
                partition="TRAIN_ONLY", rows=list(cases),
                labels=labels))

    def test_unbound_case_payload_rows_reject(self):
        payload = [c.to_dict() for c in fx.planted_cases(
            seed=3, regions=("train_basin_a",), per_cluster=2)]
        with pytest.raises(ValueError, match="scored cases"):
            fit_marginal_rate(FitPartition(
                partition="TRAIN_ONLY", rows=payload,
                labels=[0, 1]))

    def test_bad_provenance_fields_reject(self):
        with pytest.raises(ValueError, match="holdout_digest"):
            fit_marginal_rate(FitPartition(
                partition="TRAIN_ONLY", labels=[0, 1],
                holdout_digest="not-hex"))
        with pytest.raises(ValueError, match="feature_digest"):
            fit_marginal_rate(FitPartition(
                partition="TRAIN_ONLY", labels=[0, 1],
                feature_digest="xyz"))
        with pytest.raises(ValueError, match="cutoff_time"):
            fit_marginal_rate(FitPartition(
                partition="TRAIN_ONLY", labels=[0, 1],
                cutoff_time="yesterday"))
        with pytest.raises(ValueError, match="train_groups"):
            fit_marginal_rate(FitPartition(
                partition="TRAIN_ONLY", labels=[0, 1],
                train_groups="train_basin_a"))

    def test_non_train_only_partition_rejects(self):
        for label in ("TEST", "HELD_OUT", "train"):
            with pytest.raises(ValueError, match="TRAIN_ONLY"):
                fit_marginal_rate(FitPartition(
                    partition=label, labels=[0, 1]))

    def test_fit_partition_provenance_helper(self):
        cases = fx.underpowered_design()["cases"]
        assert fit_partition_provenance(
            fx.train_partition(cases))["provenance"] == \
            "unbound_fixture"
        assert fit_partition_provenance(
            fx.train_partition(cases, bound=True))[
                "provenance"] == "bound"


class TestRound4EvaluationHardening:
    """EVAL-01..05 / FCST-01..02 adversarial probes."""

    def test_baseline_provenance_recorded_unbound(self):
        """FCST-02: caller-supplied baseline vectors are recorded as
        fixture inputs — never silently promoted to bound evidence."""
        design = fx.underpowered_design()
        report = evaluate(design["cases"], **_eval_kwargs(design))
        prov = report.baseline_provenance
        assert prov
        for name in report.metrics:
            if name == "model":
                continue
            assert prov[name]["fit_provenance"] == \
                "caller_supplied_fixture"

    def test_baseline_evidence_digest_recomputes(self):
        """FCST-02: a bound baseline whose declared digest does not
        recompute over the supplied vector is rejected."""
        design = fx.underpowered_design()
        cases = design["cases"]
        probs = fx.make_baseline_probs(cases)
        bad_evidence = {
            name: {"digest": "0" * 64,
                   "fit_provenance": "bound"}
            for name in probs}
        kw = _eval_kwargs(design)
        kw["baseline_evidence"] = bad_evidence
        try:
            evaluate(cases, **kw)
            raise AssertionError("bad baseline digest must reject")
        except ValueError as exc:
            assert "baseline_evidence" in str(exc)

    def test_baseline_evidence_bound_passes(self):
        from nepal.research_v0._hashing import sha256_canonical
        design = fx.underpowered_design()
        cases = design["cases"]
        probs = fx.make_baseline_probs(cases)
        good = {
            name: {"digest": sha256_canonical(
                       [round(float(v), 9) for v in v_]),
                   "fit_provenance": "bound"}
            for name, v_ in probs.items()}
        kw = _eval_kwargs(design)
        kw["baseline_evidence"] = good
        report = evaluate(cases, **kw)
        assert report.baseline_provenance[
            "climatology"]["fit_provenance"] == "bound"

    def test_declaration_power_design_carried(self):
        design = fx.underpowered_design()
        cases = design["cases"]
        decl = ForecastExperimentDeclaration(
            declaration_id="decl-pd",
            feature_artifact_digest="a" * 64,
            threshold_record={"threshold": 0.5},
            ablations=(), vintage_lineage=tuple(
                sorted({c.vintage_digest for c in cases})),
            power_design={"design_digest": "b" * 64,
                          "metric": "pr_auc", "mde": 0.1})
        kw = _eval_kwargs(design)
        kw["threshold"] = 0.5
        kw["experiment"] = decl
        report = evaluate(cases, **kw)
        assert report.declaration["declaration_id"] == "decl-pd"

    def test_fit_partition_row_keys_reject_duplicates(self):
        """FCST-01: canonical row keys must be unique and
        well-formed — a duplicated key can never bind membership."""
        from nepal.experiment_v0.baselines import (
            FitPartition, _require_train_partition)
        with pytest.raises(ValueError, match="row_keys"):
            _require_train_partition(FitPartition(
                partition="TRAIN_ONLY", rows=[[0.1], [0.2]],
                labels=[0, 1],
                row_keys=("u1|2020-01-01", "u1|2020-01-01")))

    def test_fit_partition_row_keys_length_mismatch(self):
        from nepal.experiment_v0.baselines import (
            FitPartition, _require_train_partition)
        with pytest.raises(ValueError, match="row_keys"):
            _require_train_partition(FitPartition(
                partition="TRAIN_ONLY", rows=[[0.1], [0.2]],
                labels=[0, 1],
                row_keys=("u1|2020-01-01",)))

    def test_row_keys_digest_in_fit_provenance(self):
        from nepal.experiment_v0.baselines import (
            FitPartition, fit_partition_provenance)
        p = FitPartition(
            partition="TRAIN_ONLY", rows=[[0.1], [0.2]],
            labels=[0, 1],
            row_keys=("u1|2020-01-01", "u1|2020-01-02"))
        from nepal.experiment_v0.baselines import \
            _require_train_partition
        _require_train_partition(p)
        prov = fit_partition_provenance(p)
        assert len(prov["row_keys_digest"]) == 64

    def test_unlinked_opp_not_attributed_to_non_met_season(self):
        """EVAL-01: unattributed unlinked opportunities cannot enter
        a slice the registry cannot attribute them to."""
        design = fx.underpowered_design()
        cases = design["cases"]
        extra = fx.unlinked_opportunities(
            sorted({c.unit_id for c in cases}), n=1)
        kwargs = _eval_kwargs(design)
        kwargs["opportunities"] = {
            **design["opportunities"], **extra}
        report = evaluate(cases, **kwargs)
        for season, bundle in report.slices["season"].items():
            slice_cases = [c for c in cases if c.season == season]
            assert bundle["opportunities_scoped"] == \
                len(slice_cases)
