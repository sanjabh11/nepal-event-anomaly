"""Engineering-proof tests for the additive research_v0 namespace.

These tests prove the v0 policy contracts are executable and fail-closed.
They are contract-layer evidence only — they say nothing about real-data
scientific validity.
"""
from __future__ import annotations

import math

import pytest

from nepal.research_v0 import gates, policy
from nepal.research_v0.policy import (
    EventTimeClass, ForecastDataClass, TargetState,
    assign_target_state, classify_event_time, cutoff_order_problems,
    eligible_horizons, embargo_seconds, parse_strict_utc)

H = 3600
D = 86400


class TestStrictTime:
    def test_epoch_and_rfc3339_z(self):
        assert parse_strict_utc(1234.5) == 1234.5
        assert parse_strict_utc("2026-08-26T02:52:00Z") is not None
        assert parse_strict_utc("2026-08-26T02:52:00+00:00") is not None

    def test_naive_and_offset_rejected(self):
        assert parse_strict_utc("2026-08-26T02:52:00") is None
        assert parse_strict_utc("2026-08-26T02:52:00+05:45") is None

    def test_nan_inf_bool_none_rejected(self):
        assert parse_strict_utc(float("nan")) is None
        assert parse_strict_utc(float("inf")) is None
        assert parse_strict_utc(True) is None
        assert parse_strict_utc(None) is None
        assert parse_strict_utc("garbage") is None


class TestEventTimeClassification:
    def test_exact_timestamp(self):
        assert classify_event_time(30 * 60) is EventTimeClass.EXACT_TIMESTAMP
        assert classify_event_time(H) is EventTimeClass.EXACT_TIMESTAMP

    def test_exact_day(self):
        assert classify_event_time(H + 1) is EventTimeClass.EXACT_DAY
        assert classify_event_time(D) is EventTimeClass.EXACT_DAY

    def test_interval_classes(self):
        assert classify_event_time(7 * D) is EventTimeClass.INTERVAL_LE_7D
        assert classify_event_time(12 * D) is EventTimeClass.INTERVAL_8_30D
        assert classify_event_time(30 * D) is EventTimeClass.INTERVAL_8_30D

    def test_coarse_and_unresolved(self):
        for bad in (31 * D, None, -1, float("nan"), float("inf")):
            assert classify_event_time(bad) is \
                EventTimeClass.COARSE_OR_UNRESOLVED


class TestHorizonEligibility:
    def test_exact_timestamp_all_horizons(self):
        assert eligible_horizons(
            EventTimeClass.EXACT_TIMESTAMP,
            event_uncertainty_seconds=H,
            observation_latency_seconds=H,
            processing_latency_seconds=H) == list(policy.HORIZON_ORDER)

    def test_exact_day_bars_sub_day(self):
        out = eligible_horizons(
            EventTimeClass.EXACT_DAY,
            event_uncertainty_seconds=D,
            observation_latency_seconds=0,
            processing_latency_seconds=0)
        assert out == ["48h", "72h", "7d", "14d", "30d"]

    def test_sentinel1_twelve_day_interval(self):
        out = eligible_horizons(
            EventTimeClass.INTERVAL_8_30D,
            event_uncertainty_seconds=12 * D,
            observation_latency_seconds=0,
            processing_latency_seconds=0)
        assert out == ["30d"]

    def test_interval_le_7d_bars_short_horizons(self):
        out = eligible_horizons(
            EventTimeClass.INTERVAL_LE_7D,
            event_uncertainty_seconds=6 * D,
            observation_latency_seconds=0,
            processing_latency_seconds=0)
        assert out == ["7d", "14d", "30d"]

    def test_latency_gate_can_eliminate_horizons(self):
        out = eligible_horizons(
            EventTimeClass.EXACT_TIMESTAMP,
            event_uncertainty_seconds=H,
            observation_latency_seconds=25 * H,
            processing_latency_seconds=0)
        assert "24h" not in out and "48h" in out

    def test_unverified_latency_blocks_all(self):
        assert eligible_horizons(
            EventTimeClass.EXACT_TIMESTAMP,
            event_uncertainty_seconds=H,
            observation_latency_seconds=None,
            processing_latency_seconds=0) == []

    def test_negative_nan_inf_components_block(self):
        for bad in (-1.0, float("nan"), float("inf")):
            assert eligible_horizons(
                EventTimeClass.EXACT_TIMESTAMP,
                event_uncertainty_seconds=bad,
                observation_latency_seconds=0,
                processing_latency_seconds=0) == []
            assert eligible_horizons(
                EventTimeClass.EXACT_TIMESTAMP,
                event_uncertainty_seconds=H,
                observation_latency_seconds=bad,
                processing_latency_seconds=0) == []


class TestTargetStates:
    WS, WE = 1000.0, 2000.0

    def test_positive_requires_contained_adjudicated(self):
        ev = [{"start": 1100, "end": 1500, "adjudicated": True}]
        assert assign_target_state(
            self.WS, self.WE, ev,
            opportunity_state="OBSERVED_FULL") is TargetState.POSITIVE

    def test_negative_requires_full_observation_no_overlap(self):
        assert assign_target_state(
            self.WS, self.WE,
            [{"start": 3000, "end": 4000, "adjudicated": True}],
            opportunity_state="OBSERVED_FULL") is TargetState.NEGATIVE

    def test_partial_and_unknown_observation_censored(self):
        for state in ("OBSERVED_PARTIAL", "UNOBSERVED", "UNKNOWN"):
            assert assign_target_state(
                self.WS, self.WE, [], opportunity_state=state) is \
                TargetState.CENSORED_OR_AMBIGUOUS

    def test_boundary_overlap_is_censored(self):
        ev = [{"start": 500, "end": 1500, "adjudicated": True}]
        assert assign_target_state(
            self.WS, self.WE, ev,
            opportunity_state="OBSERVED_FULL") is \
            TargetState.CENSORED_OR_AMBIGUOUS

    def test_unresolved_contained_interval_is_censored(self):
        ev = [{"start": 1100, "end": 1500, "adjudicated": False}]
        assert assign_target_state(
            self.WS, self.WE, ev,
            opportunity_state="OBSERVED_FULL") is \
            TargetState.CENSORED_OR_AMBIGUOUS

    def test_malformed_window_and_intervals_censored(self):
        assert assign_target_state(
            2000.0, 1000.0, [], opportunity_state="OBSERVED_FULL") is \
            TargetState.CENSORED_OR_AMBIGUOUS
        ev = [{"start": float("nan"), "end": 1500, "adjudicated": True}]
        assert assign_target_state(
            self.WS, self.WE, ev,
            opportunity_state="OBSERVED_FULL") is \
            TargetState.CENSORED_OR_AMBIGUOUS
        ev = [{"start": 1100, "end": 900, "adjudicated": True}]  # inverted
        assert assign_target_state(
            self.WS, self.WE, ev,
            opportunity_state="OBSERVED_FULL") is \
            TargetState.CENSORED_OR_AMBIGUOUS


class TestEmbargo:
    def test_max_of_components(self):
        assert embargo_seconds(
            max_horizon_seconds=7 * D,
            max_label_interval_seconds=12 * D,
            max_observation_latency_seconds=2 * D,
            max_cascade_seconds=30 * D) == 30 * D

    def test_unknown_or_invalid_component_blocks(self):
        for bad in (None, -1.0, float("nan"), float("inf")):
            assert embargo_seconds(
                max_horizon_seconds=7 * D,
                max_label_interval_seconds=bad,
                max_observation_latency_seconds=2 * D,
                max_cascade_seconds=0) is None


class TestCutoffOrder:
    def _ok(self):
        return {
            "source_observation_end": 100, "source_processing_complete": 200,
            "source_publication": 300, "feature_availability": 400,
            "forecast_initialization": 450, "forecast_issue": 500,
            "forecast_valid_start": 600, "forecast_valid_end": 700,
        }

    def test_valid_chain(self):
        assert cutoff_order_problems(self._ok()) == []

    def test_initialization_required_in_chain(self):
        bad = self._ok()
        del bad["forecast_initialization"]
        assert any("forecast_initialization" in p
                   for p in cutoff_order_problems(bad))

    def test_inversion_flagged(self):
        bad = self._ok()
        bad["feature_availability"] = 250
        assert any("cutoff order violated" in p
                   for p in cutoff_order_problems(bad))

    def test_missing_and_nonfinite_flagged(self):
        bad = self._ok()
        del bad["forecast_issue"]
        bad["source_publication"] = float("nan")
        problems = cutoff_order_problems(bad)
        assert any("forecast_issue" in p for p in problems)
        assert any("source_publication" in p for p in problems)

    def test_naive_timestamp_rejected(self):
        bad = self._ok()
        bad["forecast_issue"] = "2026-08-25T00:00:00"  # no UTC marker
        assert cutoff_order_problems(bad)

    def test_archive_and_retrieval_rules(self):
        ok = self._ok()
        ok["archive_availability"] = 800
        ok["local_retrieval_time"] = 900
        assert cutoff_order_problems(ok) == []
        # archive before issue is impossible
        bad = self._ok()
        bad["archive_availability"] = 400
        assert any("archive" in p for p in cutoff_order_problems(bad))
        # retrieval before archive is impossible
        bad2 = self._ok()
        bad2["archive_availability"] = 800
        bad2["local_retrieval_time"] = 700
        assert any("retrieval" in p for p in cutoff_order_problems(bad2))

    def test_iso_strings_accepted(self):
        ok = {
            "source_observation_end": "2026-08-20T00:00:00Z",
            "source_processing_complete": "2026-08-21T00:00:00Z",
            "source_publication": "2026-08-22T00:00:00Z",
            "feature_availability": "2026-08-23T00:00:00Z",
            "forecast_initialization": "2026-08-24T00:00:00Z",
            "forecast_issue": "2026-08-24T01:00:00Z",
            "forecast_valid_start": "2026-08-25T00:00:00Z",
            "forecast_valid_end": "2026-08-26T00:00:00Z",
        }
        assert cutoff_order_problems(ok) == []
