"""Contract tests for the one-station seismic observability lane (V1).

Design surface only — authorized by P5_AMENDMENT_V4 for one-station
benchmark path DESIGN.  These tests prove the vocabulary, the
fail-closed receipt gates, the authority-flag floor, and the
serialize round-trip.  No waveform retrieval, no network, no ObsPy.
"""
from __future__ import annotations

import dataclasses
import json

import pytest

from nepal.seismic_sidecar.contracts import RECEIPT_STATUSES
from nepal.seismic_sidecar.one_station_contract import (
    AUTHORIZED_STATION_IDS, AUTHORIZED_WAVEFORM_WINDOW,
    ONE_STATION_CLAIM_SCOPE, ONE_STATION_MAX_WINDOW_S,
    ONE_STATION_RECEIPT_TYPE,
    ONE_STATION_SCIENTIFIC_STATUSES, ONE_STATION_STATUS_MAP,
    ONE_STATION_STATUSES, OneStationReceiptV1,
    one_station_receipt_from_dict, one_station_receipt_skeleton)

_AUTHORITY_FLAGS = (
    "promotion_eligible", "production_authorized",
    "warning_path_authorized", "detector_authorized",
    "locator_authorized")

_SCIENTIFIC = ("OBSERVABILITY_PASS", "CANDIDATE_ANOMALIES_ONLY",
               "NO_QUALIFIED_SIGNAL")

_NON_SCIENTIFIC = ("RUN_ERROR", "NOT_OPERATIONAL", "BLOCKED")

#: Every declared byte-evidence digest field on the receipt.
_DIGEST_FIELDS = (
    "waveform_digest", "stationxml_digest", "source_digest",
    "decoder_environment_digest", "feature_contract_digest",
    "windowing_digest", "evaluation_digest",
    "timing_verification_digest", "storage_receipt_digest",
    "config_digest")

#: The digest chain a scientific terminal MUST bind.
_REQUIRED_SCIENTIFIC_DIGESTS = (
    "source_digest", "waveform_digest", "stationxml_digest",
    "decoder_environment_digest", "feature_contract_digest",
    "windowing_digest", "evaluation_digest",
    "timing_verification_digest", "storage_receipt_digest",
    "config_digest")


def _full_anchor(**kw) -> dict:
    """A complete documented in-window event anchor — mutate via kw."""
    anchor = {
        "date": "2023-04-15",
        "source": "documented catalog entry",
        "source_id": "catalog:NEP-2023-04-15",
        "event_utc": "2023-04-15T06:11:25Z",
        "timing_tolerance_s": 30.0,
        "relation_to_window": "inside",
        "source_digest": "c" * 64,
    }
    anchor.update(kw)
    return anchor


def _good(**kw) -> OneStationReceiptV1:
    """A fully-qualified OBSERVABILITY_PASS receipt — mutate via kw."""
    base = dict(
        status="OBSERVABILITY_PASS",
        station_id="374",
        # Exactly 24h — the maximum admissible span, inclusive of
        # endpoints.
        window_start="2023-04-15T00:00:00Z",
        window_end="2023-04-16T00:00:00Z",
        event_anchor=_full_anchor(),
        waveform_digest="a" * 64,
        stationxml_digest="b" * 64,
        source_digest="1" * 64,
        decoder_environment_digest="2" * 64,
        feature_contract_digest="3" * 64,
        windowing_digest="d" * 64,
        evaluation_digest="e" * 64,
        timing_verification_digest="f" * 64,
        storage_receipt_digest="7" * 64,
        config_digest="8" * 64)
    base.update(kw)
    return OneStationReceiptV1(**base)


class TestStatusVocabulary:
    def test_statuses_exact_vocabulary(self):
        assert ONE_STATION_STATUSES == frozenset({
            "OBSERVABILITY_PASS", "CANDIDATE_ANOMALIES_ONLY",
            "NO_QUALIFIED_SIGNAL", "NOT_OPERATIONAL",
            "RUN_ERROR", "BLOCKED"})

    def test_scientific_terminal_set_exact(self):
        assert ONE_STATION_SCIENTIFIC_STATUSES == frozenset(
            _SCIENTIFIC)
        assert not (ONE_STATION_SCIENTIFIC_STATUSES &
                    {"NOT_OPERATIONAL", "RUN_ERROR", "BLOCKED"})

    def test_status_map_covers_every_v0_status(self):
        """Mapping completeness: every v0 sidecar receipt status has
        exactly one declared one-station analog, and every analog is
        a declared V1 status."""
        assert set(ONE_STATION_STATUS_MAP) == set(RECEIPT_STATUSES)
        assert set(ONE_STATION_STATUS_MAP.values()) <= \
            ONE_STATION_STATUSES

    def test_unobservable_maps_to_blocked_preflight(self):
        assert ONE_STATION_STATUS_MAP["UNOBSERVABLE"] == "BLOCKED"
        assert ONE_STATION_STATUS_MAP["CANDIDATE_ONLY"] == \
            "CANDIDATE_ANOMALIES_ONLY"
        assert ONE_STATION_STATUS_MAP["DESCRIPTIVE_REGIME_ONLY"] == \
            "OBSERVABILITY_PASS"
        assert ONE_STATION_STATUS_MAP["RUN_ERROR"] == "RUN_ERROR"

    def test_authorized_ceilings_match_amendment(self):
        assert set(AUTHORIZED_STATION_IDS) == \
            {"374", "312", "302", "1158"}
        assert AUTHORIZED_WAVEFORM_WINDOW == \
            ("2023-04-01", "2023-05-09")

    def test_claim_scope_fixed(self):
        assert ONE_STATION_CLAIM_SCOPE == \
            "research_only_no_operational_authorization"


class TestReceiptShape:
    def test_record_type_and_defaults(self):
        r = _good()
        assert r.problems() == []
        d = r.to_dict()
        assert d["record_type"] == ONE_STATION_RECEIPT_TYPE
        for flag in _AUTHORITY_FLAGS:
            assert d[flag] is False

    def test_record_type_must_be_exact(self):
        r = _good(record_type="ONE_STATION_OBSERVABILITY_RECEIPT_V0")
        assert any("record_type" in p for p in r.problems())

    def test_status_must_be_declared(self):
        r = _good(status="SUCCESS")
        assert any("status" in p for p in r.problems())

    def test_claim_scope_immutable(self):
        r = _good(claim_scope="research_only_post_initiation_detection")
        assert any("claim_scope" in p for p in r.problems())

    def test_to_dict_is_json_serializable(self):
        json.dumps(_good().to_dict())


class TestAuthorityFlags:
    @pytest.mark.parametrize("flag", _AUTHORITY_FLAGS)
    def test_flag_true_rejected(self, flag):
        r = _good(**{flag: True})
        assert any(flag in p for p in r.problems())

    @pytest.mark.parametrize("flag", _AUTHORITY_FLAGS)
    def test_flag_truthy_nonbool_rejected(self, flag):
        # "exactly False" — a truthy non-bool is not False.
        r = _good(**{flag: "yes"})
        assert any(flag in p for p in r.problems())

    @pytest.mark.parametrize("flag", _AUTHORITY_FLAGS)
    def test_flag_absent_from_payload_rejected(self, flag):
        d = _good().to_dict()
        d.pop(flag)
        with pytest.raises(ValueError):
            one_station_receipt_from_dict(d)


class TestScientificTerminals:
    @pytest.mark.parametrize("status", _SCIENTIFIC)
    def test_each_scientific_status_admitted(self, status):
        assert _good(status=status).problems() == []

    @pytest.mark.parametrize("status", _SCIENTIFIC)
    def test_scientific_status_requires_event_anchor(self, status):
        r = _good(status=status, event_anchor={})
        assert any("event_anchor" in p for p in r.problems())

    @pytest.mark.parametrize("status", _SCIENTIFIC)
    def test_scientific_status_requires_waveform_digest(self, status):
        r = _good(status=status, waveform_digest="")
        assert any("waveform_digest" in p for p in r.problems())

    @pytest.mark.parametrize("status", _SCIENTIFIC)
    def test_scientific_status_requires_stationxml_digest(self,
                                                          status):
        r = _good(status=status, stationxml_digest="")
        assert any("stationxml_digest" in p for p in r.problems())

    @pytest.mark.parametrize("status", _SCIENTIFIC)
    def test_scientific_status_requires_window(self, status):
        r = _good(status=status, window_start="", window_end="")
        assert any("window" in p for p in r.problems())

    def test_event_anchor_requires_date_and_source(self):
        assert any("event_anchor.date" in p for p in
                   _good(event_anchor={"source": "s"}).problems())
        assert any("event_anchor.source" in p for p in
                   _good(event_anchor={"date": "2023-04-15"})
                   .problems())
        assert any("event_anchor" in p for p in
                   _good(event_anchor={"date": "not-a-date",
                                       "source": "s"}).problems())

    def test_event_anchor_must_be_in_window(self):
        r = _good(event_anchor=_full_anchor(
            date="2023-05-01", event_utc="2023-05-01T00:00:00Z",
            relation_to_window="lead"))
        assert any("outside the declared window" in p
                   for p in r.problems())

    @pytest.mark.parametrize("anchor", [False, 0])
    def test_falsey_event_anchor_is_not_absent(self, anchor):
        r = _good(event_anchor=anchor)
        assert any("event_anchor" in p for p in r.problems())

    def test_digests_must_be_sha256_when_present(self):
        assert any("waveform_digest" in p for p in
                   _good(waveform_digest="nothex").problems())
        assert any("stationxml_digest" in p for p in
                   _good(stationxml_digest="nothex").problems())


class TestBlockedTerminal:
    def _blocked(self, **kw):
        base = dict(
            status="BLOCKED",
            station_id="312",
            window_start="2023-04-01T00:00:00Z",
            window_end="2023-04-02T00:00:00Z",
            blocked_reason="no documented event overlaps the "
                           "declared window")
        base.update(kw)
        return OneStationReceiptV1(**base)

    def test_blocked_admitted(self):
        assert self._blocked().problems() == []

    def test_blocked_requires_blocked_reason(self):
        assert any("blocked_reason" in p
                   for p in self._blocked(blocked_reason="").problems())

    def test_blocked_rejects_waveform_digest(self):
        """No bytes exist at preflight — a BLOCKED receipt carrying a
        waveform digest is inadmissible."""
        r = self._blocked(waveform_digest="a" * 64)
        assert any("waveform_digest" in p for p in r.problems())

    def test_blocked_rejects_event_anchor(self):
        """Preflight terminal binds no event."""
        r = self._blocked(event_anchor={"date": "2023-04-15",
                                        "source": "doc"})
        assert any("event_anchor" in p for p in r.problems())

    def test_non_blocked_rejects_blocked_reason(self):
        r = _good(blocked_reason="should not be here")
        assert any("blocked_reason" in p for p in r.problems())


class TestNonScientificTerminals:
    def test_run_error_requires_reason(self):
        r = OneStationReceiptV1(station_id="374")
        assert r.status == "RUN_ERROR"
        assert any("reason" in p for p in r.problems())
        ok = OneStationReceiptV1(station_id="374",
                                 reason="config rejected")
        assert ok.problems() == []

    def test_not_operational_requires_reason(self):
        r = OneStationReceiptV1(status="NOT_OPERATIONAL",
                                station_id="374")
        assert any("reason" in p for p in r.problems())
        ok = OneStationReceiptV1(
            status="NOT_OPERATIONAL", station_id="374",
            reason="claim ceiling marker — design artifact only")
        assert ok.problems() == []


class TestStationAndWindow:
    def test_station_id_required(self):
        assert any("station_id" in p
                   for p in _good(station_id="").problems())

    def test_station_outside_authorized_list_rejected(self):
        r = _good(station_id="999")
        assert any("outside the declared authorized station" in p
                   for p in r.problems())

    def test_empty_authorized_station_list_rejected(self):
        r = _good(authorized_stations=[])
        assert any("authorized_stations" in p for p in r.problems())

    def test_station_id_must_belong_to_declared_authorized_list(self):
        r = _good(authorized_stations=("312",))
        assert any("station_id" in p and "outside" in p
                   for p in r.problems())

    def test_authorized_list_may_narrow(self):
        r = _good(authorized_stations=("374",))
        assert r.problems() == []

    def test_authorized_list_may_never_widen(self):
        r = _good(authorized_stations=("374", "999"))
        assert any("exceed" in p for p in r.problems())

    def test_window_outside_authorized_window_rejected(self):
        r = _good(window_start="2023-06-01T00:00:00Z",
                  window_end="2023-06-02T00:00:00Z",
                  event_anchor=_full_anchor(
                      date="2023-06-01",
                      event_utc="2023-06-01T00:00:00Z"))
        assert any("outside the authorized" in p
                   for p in r.problems())

    def test_authorized_window_may_never_widen(self):
        r = _good(authorized_window=("2023-01-01", "2023-05-09"))
        assert any("exceed" in p for p in r.problems())

    def test_authorized_window_may_narrow(self):
        r = _good(authorized_window=("2023-04-01", "2023-04-30"))
        assert r.problems() == []

    def test_inverted_window_rejected(self):
        r = _good(window_start="2023-04-20T00:00:00Z",
                  window_end="2023-04-10T00:00:00Z")
        assert any("inverted" in p for p in r.problems())

    def test_naive_window_rejected(self):
        r = _good(window_start="2023-04-10 00:00:00",
                  window_end="2023-04-20 00:00:00")
        assert any("explicit-UTC" in p for p in r.problems())


class TestForbiddenClaimVocabulary:
    @pytest.mark.parametrize("text", [
        "this feed could support early warning",
        "operational forecast for the basin",
        "alert issued to downstream settlements",
        "acts as a detector for regional events",
        "serves as a locator for epicenters",
        "predicts the next event window",
        "real-time bulletin output",
        "warning path is live",
    ])
    def test_forbidden_terms_in_reason_rejected(self, text):
        r = OneStationReceiptV1(station_id="374", reason=text)
        assert any("forbidden claim vocabulary" in p
                   for p in r.problems())

    def test_forbidden_terms_in_blocked_reason_rejected(self):
        r = OneStationReceiptV1(
            status="BLOCKED", station_id="312",
            window_start="2023-04-01T00:00:00Z",
            window_end="2023-04-02T00:00:00Z",
            blocked_reason="retrieval would enable an operational "
                           "warning product")
        assert any("forbidden claim vocabulary" in p
                   for p in r.problems())

    def test_forbidden_terms_in_notes_rejected(self):
        r = _good(notes=("metadata probe ok",
                         "suitable for warning dissemination"))
        assert any("forbidden claim vocabulary" in p
                   for p in r.problems())

    def test_clean_free_text_admitted(self):
        r = _good(reason="byte-qualified windows bound",
                  notes=("station metadata verified",
                         "contrast predeclared"))
        assert r.problems() == []


class TestSerialization:
    def test_round_trip_every_status(self):
        for status in ONE_STATION_STATUSES:
            if status in _SCIENTIFIC:
                r = _good(status=status)
            elif status == "BLOCKED":
                r = OneStationReceiptV1(
                    status="BLOCKED", station_id="312",
                    window_start="2023-04-01T00:00:00Z",
                    window_end="2023-04-02T00:00:00Z",
                    blocked_reason="preflight gate unmet")
            else:
                r = OneStationReceiptV1(
                    status=status, station_id="374",
                    reason="terminal reason")
            rt = one_station_receipt_from_dict(r.to_dict())
            assert rt == r
            assert rt.problems() == []

    def test_from_dict_rejects_wrong_record_type(self):
        d = _good().to_dict()
        d["record_type"] = "SEISMIC_DETECTION_RECEIPT_V0"
        with pytest.raises(ValueError):
            one_station_receipt_from_dict(d)

    def test_from_dict_rejects_missing_and_extra_fields(self):
        d = _good().to_dict()
        missing = {k: v for k, v in d.items() if k != "notes"}
        with pytest.raises(ValueError):
            one_station_receipt_from_dict(missing)
        with pytest.raises(ValueError):
            one_station_receipt_from_dict(dict(d, sneaky=1))

    def test_from_dict_rejects_non_mapping(self):
        with pytest.raises(ValueError):
            one_station_receipt_from_dict(["not", "a", "mapping"])

    def test_json_round_trip(self):
        r = _good()
        payload = json.loads(json.dumps(r.to_dict()))
        assert one_station_receipt_from_dict(payload) == r

    def test_skeleton_is_fail_closed(self):
        skel = one_station_receipt_skeleton()
        assert skel["record_type"] == ONE_STATION_RECEIPT_TYPE
        assert skel["status"] == "RUN_ERROR"
        assert skel["claim_scope"] == ONE_STATION_CLAIM_SCOPE
        for flag in _AUTHORITY_FLAGS:
            assert skel[flag] is False
        # Deserialization requires the full surface — the skeleton
        # carries every field, and the record stays inadmissible
        # until a runner promotes it through the gates.
        rt = one_station_receipt_from_dict(skel)
        assert rt.problems()

    def test_frozen_dataclass(self):
        r = _good()
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.status = "OBSERVABILITY_PASS"  # noqa: DC01


class TestWindowDurationBound:
    """R-12: a declared analysis window spans at most 86400 s (24h),
    inclusive of endpoints."""

    def test_max_window_constant_is_24h(self):
        assert ONE_STATION_MAX_WINDOW_S == 86400

    def test_exactly_24h_window_admitted(self):
        # _good's default window is exactly 24h — the inclusive bound.
        assert _good().problems() == []

    def test_one_second_over_24h_rejected(self):
        r = _good(window_start="2023-04-15T00:00:00Z",
                  window_end="2023-04-16T00:00:01Z")
        assert any("86400" in p for p in r.problems())

    def test_multi_day_window_rejected(self):
        r = _good(window_start="2023-04-10T00:00:00Z",
                  window_end="2023-04-20T00:00:00Z")
        assert any("86400" in p for p in r.problems())

    def test_malformed_window_bound_rejected(self):
        r = _good(window_end="not-a-timestamp")
        assert any("explicit-UTC" in p for p in r.problems())

    def test_non_string_window_bound_rejected(self):
        # Numeric epochs are not ISO-8601 strings — bounded problem.
        r = _good(window_start=1681430400.0)
        assert any("explicit-UTC" in p for p in r.problems())

    def test_partial_window_rejected(self):
        r = _good(window_end="")
        assert any("window" in p for p in r.problems())


class TestEventAnchorStrictness:
    """R-15: a scientific event anchor carries the full documented
    field surface — each missing/malformed field is a distinct
    problem."""

    @pytest.mark.parametrize("name", [
        "date", "source", "source_id", "event_utc",
        "timing_tolerance_s", "relation_to_window", "source_digest"])
    def test_each_missing_anchor_field_rejected(self, name):
        anchor = _full_anchor()
        anchor.pop(name)
        r = _good(event_anchor=anchor)
        assert any(f"event_anchor.{name}" in p
                   for p in r.problems()), name

    @pytest.mark.parametrize("bad", [
        {"date": "15-04-2023"},
        {"date": "2023-04-15T06:11:25Z"},      # not a calendar date
        {"source": ""},
        {"source": "   "},
        {"source": 7},
        {"source_id": ""},
        {"source_id": "   "},
        {"event_utc": "2023-04-15"},           # date only
        {"event_utc": "2023-04-15T06:11Z"},    # no seconds
        {"event_utc": "2023-04-15 06:11:25"},  # naive
        {"event_utc": 1681438285},             # not an ISO string
        {"timing_tolerance_s": 0},
        {"timing_tolerance_s": -5},
        {"timing_tolerance_s": "30"},          # string, not number
        {"timing_tolerance_s": float("nan")},
        {"timing_tolerance_s": float("inf")},
        {"timing_tolerance_s": True},
        {"relation_to_window": "before"},
        {"relation_to_window": "INSIDE"},      # case-sensitive
        {"relation_to_window": ["inside"]},
        {"source_digest": "zz" * 32},
        {"source_digest": "c" * 63},
        {"source_digest": "C" * 64},           # lowercase hex only
    ])
    def test_malformed_anchor_fields_rejected(self, bad):
        r = _good(event_anchor=_full_anchor(**bad))
        assert r.problems(), bad

    @pytest.mark.parametrize("rel", ["inside", "edge", "lead"])
    def test_relation_semantics_admitted(self, rel):
        if rel == "inside":
            window_start = "2023-04-15T00:00:00Z"
            window_end = "2023-04-16T00:00:00Z"
            event_utc = "2023-04-15T06:11:25Z"
        elif rel == "edge":
            window_start = "2023-04-15T00:00:00Z"
            window_end = "2023-04-16T00:00:00Z"
            event_utc = "2023-04-15T00:00:00Z"
        else:
            window_start = "2023-04-15T06:00:00Z"
            window_end = "2023-04-15T07:00:00Z"
            event_utc = "2023-04-15T05:59:45Z"
        r = _good(
            window_start=window_start,
            window_end=window_end,
            event_anchor=_full_anchor(
                event_utc=event_utc, relation_to_window=rel))
        assert r.problems() == []

    @pytest.mark.parametrize("rel,event_utc,window_start,window_end", [
        ("lead", "2023-04-15T06:11:25Z",
         "2023-04-15T00:00:00Z", "2023-04-16T00:00:00Z"),
        ("edge", "2023-04-15T06:11:25Z",
         "2023-04-15T00:00:00Z", "2023-04-16T00:00:00Z"),
        ("inside", "2023-04-15T05:59:45Z",
         "2023-04-15T06:00:00Z", "2023-04-15T07:00:00Z"),
    ])
    def test_relation_must_match_event_position(self, rel, event_utc,
                                                window_start, window_end):
        r = _good(
            window_start=window_start,
            window_end=window_end,
            event_anchor=_full_anchor(
                event_utc=event_utc, relation_to_window=rel))
        assert any("relation_to_window" in p for p in r.problems())

    def test_lead_must_stay_within_timing_tolerance(self):
        r = _good(
            window_start="2023-04-15T06:00:00Z",
            window_end="2023-04-15T07:00:00Z",
            event_anchor=_full_anchor(
                event_utc="2023-04-15T05:59:29Z",
                relation_to_window="lead"))
        assert any("outside the declared window" in p
                   for p in r.problems())

    @pytest.mark.parametrize("event_utc,date,needle", [
        ("2023-04-16T00:01:00Z", "2023-04-16", "declared window"),
        ("2023-05-10T00:00:00Z", "2023-05-10", "authorized window"),
    ])
    def test_event_utc_must_be_within_declared_and_authorized_windows(
            self, event_utc, date, needle):
        r = _good(event_anchor=_full_anchor(
            date=date, event_utc=event_utc))
        assert any("event_utc" in p and needle in p
                   for p in r.problems())

    def test_anchor_date_must_match_event_utc_date(self):
        r = _good(event_anchor=_full_anchor(date="2023-04-16"))
        assert any("date" in p and "event_utc" in p
                   for p in r.problems())

    def test_anchor_extra_keys_admitted(self):
        r = _good(event_anchor=_full_anchor(magnitude=4.9))
        assert r.problems() == []


class TestExecutionDigestSurface:
    """The declared execution-binding digests are optional only for
    non-scientific terminals; scientific terminals bind the full chain."""

    def test_all_digest_fields_on_skeleton(self):
        skel = one_station_receipt_skeleton()
        for name in _DIGEST_FIELDS:
            assert name in skel

    def test_non_scientific_digests_may_stay_absent(self):
        r = OneStationReceiptV1(
            status="RUN_ERROR", station_id="374",
            reason="config rejected")
        assert r.problems() == []

    @pytest.mark.parametrize("name", _DIGEST_FIELDS)
    def test_valid_digest_admitted(self, name):
        r = _good(**{name: "0" * 64})
        assert not any(name in p for p in r.problems())

    @pytest.mark.parametrize("name", _DIGEST_FIELDS)
    @pytest.mark.parametrize("bad", ["nothex", "A" * 64, 64,
                                     ["a" * 64]])
    def test_malformed_digest_rejected(self, name, bad):
        r = _good(**{name: bad})
        assert any(name in p for p in r.problems())

    @pytest.mark.parametrize("name", _DIGEST_FIELDS)
    @pytest.mark.parametrize("bad", [False, 0])
    def test_falsey_digest_rejected_even_when_optional(self, name, bad):
        r = OneStationReceiptV1(
            status="RUN_ERROR", station_id="374",
            reason="config rejected", **{name: bad})
        assert any(name in p for p in r.problems())

    @pytest.mark.parametrize("status", _SCIENTIFIC)
    @pytest.mark.parametrize("name", _REQUIRED_SCIENTIFIC_DIGESTS)
    def test_scientific_requires_full_chain(self, status, name):
        r = _good(status=status, **{name: None})
        assert any(name in p for p in r.problems())

    def test_new_fields_round_trip(self):
        r = _good(source_digest="1" * 64,
                  decoder_environment_digest="2" * 64,
                  feature_contract_digest="3" * 64,
                  storage_receipt_digest="4" * 64)
        assert r.problems() == []
        rt = one_station_receipt_from_dict(r.to_dict())
        assert rt == r

    def test_digest_fields_required_in_payload(self):
        """The exact-field deserializer: a serialized receipt missing
        a declared digest field rejects."""
        d = _good().to_dict()
        d.pop("windowing_digest")
        with pytest.raises(ValueError):
            one_station_receipt_from_dict(d)


class TestNonScientificTerminalsBindNothing:
    """R-13/R-14/R-16: BLOCKED, NOT_OPERATIONAL, and RUN_ERROR carry
    no byte evidence of any kind and no event anchor."""

    @pytest.mark.parametrize("status", _NON_SCIENTIFIC)
    @pytest.mark.parametrize("name", _DIGEST_FIELDS)
    def test_no_digest_binds(self, status, name):
        kw = {"station_id": "374"}
        if status == "BLOCKED":
            kw.update(window_start="2023-04-01T00:00:00Z",
                      window_end="2023-04-02T00:00:00Z",
                      blocked_reason="preflight gate unmet")
        else:
            kw["reason"] = "terminal reason"
        r = OneStationReceiptV1(status=status, **kw,
                                **{name: "a" * 64})
        assert any(name in p for p in r.problems())

    @pytest.mark.parametrize("status", _NON_SCIENTIFIC)
    def test_no_event_anchor_binds(self, status):
        kw = {"station_id": "374"}
        if status == "BLOCKED":
            kw.update(window_start="2023-04-01T00:00:00Z",
                      window_end="2023-04-02T00:00:00Z",
                      blocked_reason="preflight gate unmet")
        else:
            kw["reason"] = "terminal reason"
        r = OneStationReceiptV1(status=status,
                                event_anchor=_full_anchor(), **kw)
        assert any("event_anchor" in p for p in r.problems())

    def test_not_operational_metadata_only_admitted(self):
        """R-14: NOT_OPERATIONAL is a metadata-only claim-ceiling
        marker — reason required, no evidence bound."""
        r = OneStationReceiptV1(
            status="NOT_OPERATIONAL", station_id="374",
            reason="claim ceiling marker — design artifact only")
        assert r.problems() == []

    def test_run_error_binds_no_evidence(self):
        r = OneStationReceiptV1(
            station_id="374", reason="config rejected",
            waveform_digest="a" * 64)
        assert any("waveform_digest" in p for p in r.problems())


class TestBoundedProblems:
    """R-17: .problems() never raises on malformed field types —
    every defect is a bounded problem string."""

    @pytest.mark.parametrize("anchor", [
        "scalar", 42, ["date", "source"], None, ()])
    def test_non_mapping_anchor_bounded(self, anchor):
        problems = _good(event_anchor=anchor).problems()
        assert isinstance(problems, list) and problems

    def test_anchor_with_missing_keys_bounded(self):
        assert _good(event_anchor={"date": "2023-04-15"}).problems()
        assert _good(event_anchor={"unexpected": 1}).problems()

    @pytest.mark.parametrize("kw", [
        {"status": ["RUN_ERROR"]},
        {"status": {"x": 1}},
        {"status": 42},
        {"waveform_digest": 12345},
        {"windowing_digest": ["a" * 64]},
        {"evaluation_digest": object()},
        {"station_id": 374},
        {"window_start": 1681430400},
        {"window_end": ["2023-04-16T00:00:00Z"]},
        {"notes": "not a tuple"},
        {"notes": 7},
        {"authorized_stations": "374"},
        {"authorized_stations": ({"x": 1},)},
        {"authorized_window": "2023-04-01"},
        {"reason": 5},
        {"blocked_reason": object()},
    ])
    def test_malformed_field_types_never_raise(self, kw):
        problems = _good(**kw).problems()
        assert isinstance(problems, list) and problems, kw

    def test_from_dict_malformed_field_types_bounded(self):
        """Deserialization of malformed field types yields a receipt
        whose .problems() flags the defect — never a type error."""
        d = _good().to_dict()
        d["event_anchor"] = "scalar-not-mapping"
        r = one_station_receipt_from_dict(d)
        assert any("event_anchor" in p for p in r.problems())
        d["notes"] = "free text"
        r = one_station_receipt_from_dict(d)
        assert any("notes" in p for p in r.problems())
        d["authorized_stations"] = "374"
        r = one_station_receipt_from_dict(d)
        assert any("authorized_stations" in p for p in r.problems())
