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
    ONE_STATION_CLAIM_SCOPE, ONE_STATION_RECEIPT_TYPE,
    ONE_STATION_SCIENTIFIC_STATUSES, ONE_STATION_STATUS_MAP,
    ONE_STATION_STATUSES, OneStationReceiptV1,
    one_station_receipt_from_dict, one_station_receipt_skeleton)

_AUTHORITY_FLAGS = (
    "promotion_eligible", "production_authorized",
    "warning_path_authorized", "detector_authorized",
    "locator_authorized")

_SCIENTIFIC = ("OBSERVABILITY_PASS", "CANDIDATE_ANOMALIES_ONLY",
               "NO_QUALIFIED_SIGNAL")


def _good(**kw) -> OneStationReceiptV1:
    """A fully-qualified OBSERVABILITY_PASS receipt — mutate via kw."""
    base = dict(
        status="OBSERVABILITY_PASS",
        station_id="374",
        window_start="2023-04-10T00:00:00Z",
        window_end="2023-04-20T00:00:00Z",
        event_anchor={"date": "2023-04-15",
                      "source": "documented catalog entry"},
        waveform_digest="a" * 64,
        stationxml_digest="b" * 64)
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
        r = _good(event_anchor={"date": "2023-05-01",
                                "source": "documented"})
        assert any("outside the declared window" in p
                   for p in r.problems())

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
            window_end="2023-05-09T00:00:00Z",
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

    def test_authorized_list_may_narrow(self):
        r = _good(authorized_stations=("374",))
        assert r.problems() == []

    def test_authorized_list_may_never_widen(self):
        r = _good(authorized_stations=("374", "999"))
        assert any("exceed" in p for p in r.problems())

    def test_window_outside_authorized_window_rejected(self):
        r = _good(window_start="2023-06-01T00:00:00Z",
                  window_end="2023-06-10T00:00:00Z",
                  event_anchor={"date": "2023-06-05",
                                "source": "documented"})
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
            window_end="2023-05-09T00:00:00Z",
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
                    window_end="2023-05-09T00:00:00Z",
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
