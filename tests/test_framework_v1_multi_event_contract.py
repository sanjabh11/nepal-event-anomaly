"""W1 Multi-Event Contract (MEC) tests — synthetic fixtures only."""
from __future__ import annotations

import copy

import pytest

from nepal.framework_v1 import multi_event_contract as mec


def _event(i, group="G1", day=3, region=None):
    return {
        "event_id": f"SYNTH-EVT-{i:03d}",
        "event_group_id": group,
        "region_id": region or f"R{(i - 1) % 3 + 1}",
        "synthetic": True,
        "date_spec": {"precision": "day", "date": f"2015-04-{day:02d}",
                      "source": "synthetic_fixture_date"},
        "holdout_group": f"H{i % 2}",
        "windows": {
            "acquisition_window": {"start": "2015-04-01",
                                   "end": "2015-04-10"},
            "publication_time": "2015-05-01",
            "feature_availability_time": {"start": "2015-04-01",
                                          "end": "2015-04-02"},
            "target_window": {"start": "2015-04-03", "end": "2015-04-03"},
        },
        "row_sha256": f"{i:064x}"[-64:],
    }


def _envelope():
    return {
        "envelope_type": mec.MEC_ENVELOPE_TYPE,
        "profile_id": mec.MEC_PROFILE_ID,
        "research_only": True,
        "research_diagnostic_only": True,
        "synthetic_fixture": True,
        "source_catalog": {
            "catalog_id": "synthetic-catalog-v0",
            "source_sha256": "ab" * 32,
            "asset_ids": ["SYNTH-ASSET-1"],
            "processing_script_sha256": "cd" * 32,
        },
        "mode": "SYNTHETIC_FIXTURE",
        "event_groups": ["G1", "G2", "G3", "G4"],
        "events": [_event(1, "G1", 3), _event(2, "G2", 10),
                   _event(3, "G3", 21), _event(4, "G4", 5)],
        "holdout": {
            "assigned_before_filtering": True,
            "temporal_embargo_days": 30,
            "geographic_holdout": {"min_separation_km": 50.0},
            "event_separation": {"group_disjoint": True},
            "holdout_groups": ["H0", "H1"],
        },
        "label_spec": {
            "label_source": "synthetic_fixture_labels",
            "adjudication": {"required": True,
                             "independent_reviewers": 1},
            "negative_controls": {"required": True, "n_controls": 1},
        },
        "validation_scope": {
            "scope_id": "synthetic-regional-split",
            "n_geographic_regions": 3,
            "min_events": 4,
        },
        "claim_scope": "synthetic contract exercise only",
        "promotion_eligible": False,
        "production_authorized": False,
    }


def test_valid_synthetic_envelope_verifies():
    env = mec.build_mec_envelope(_envelope())
    ok, problems = mec.verify_mec_envelope(env)
    assert ok, problems
    assert "artifact_sha256" in env


def test_tamper_rejected():
    env = mec.build_mec_envelope(_envelope())
    env["claim_scope"] = "mutated after hashing"
    ok, _ = mec.verify_mec_envelope(env)
    assert not ok


def test_missing_source_digest_rejected():
    e = _envelope()
    del e["source_catalog"]["source_sha256"]
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("source_sha256" in p for p in problems)


def test_missing_processing_hash_rejected():
    e = _envelope()
    del e["source_catalog"]["processing_script_sha256"]
    ok, _ = mec.verify_mec_envelope(e)
    assert not ok


def test_duplicate_event_id_rejected():
    e = _envelope()
    e["events"][1]["event_id"] = e["events"][0]["event_id"]
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("duplicate" in p for p in problems)


def test_empty_event_id_rejected():
    e = _envelope()
    e["events"][0]["event_id"] = ""
    ok, _ = mec.verify_mec_envelope(e)
    assert not ok


def test_non_synthetic_event_id_rejected():
    e = _envelope()
    e["events"][0]["event_id"] = "LANGTANG-2015-04-25"
    e["events"][0]["synthetic"] = False
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok


def test_real_catalog_id_rejected():
    e = _envelope()
    e["source_catalog"]["catalog_id"] = "hma_events_all"
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok


def test_day15_artificial_date_rejected():
    e = _envelope()
    e["events"][0]["date_spec"]["date"] = "2015-04-15"
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("day-15" in p or "artificial" in p
                          for p in problems)


def test_missing_date_source_rejected():
    e = _envelope()
    del e["events"][0]["date_spec"]["source"]
    ok, _ = mec.verify_mec_envelope(e)
    assert not ok


def test_fallback_date_rejected():
    e = _envelope()
    e["events"][0]["date_spec"]["source"] = "fallback_default"
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok


def test_interval_date_spec_accepted():
    e = _envelope()
    e["events"][0]["date_spec"] = {
        "precision": "interval",
        "interval": {"start": "2015-04-01", "end": "2015-04-30"},
        "source": "synthetic_fixture_interval"}
    ok, problems = mec.verify_mec_envelope(mec.build_mec_envelope(e))
    assert ok, problems


def test_inverted_interval_rejected():
    e = _envelope()
    e["events"][0]["windows"]["acquisition_window"] = {
        "start": "2015-05-01", "end": "2015-04-01"}
    ok, _ = mec.verify_mec_envelope(e)
    assert not ok


def test_post_event_feature_availability_rejected():
    e = _envelope()
    e["events"][0]["windows"]["feature_availability_time"] = {
        "start": "2015-04-01", "end": "2015-04-20"}  # after 04-03 event
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok


def test_holdout_after_filtering_rejected():
    e = _envelope()
    e["holdout"]["assigned_before_filtering"] = False
    ok, _ = mec.verify_mec_envelope(e)
    assert not ok


def test_missing_holdout_rejected():
    e = _envelope()
    del e["holdout"]
    ok, _ = mec.verify_mec_envelope(e)
    assert not ok


def test_missing_event_group_rejected():
    e = _envelope()
    del e["events"][0]["event_group_id"]
    ok, _ = mec.verify_mec_envelope(e)
    assert not ok


def test_langtang_30km_scope_rejected():
    e = _envelope()
    e["validation_scope"]["scope_id"] = "LANGTANG_30KM"
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok


def test_single_box_scope_rejected():
    e = _envelope()
    e["validation_scope"]["n_geographic_regions"] = 1
    ok, _ = mec.verify_mec_envelope(e)
    assert not ok


def test_one_event_scope_rejected():
    e = _envelope()
    e["validation_scope"]["min_events"] = 1
    ok, _ = mec.verify_mec_envelope(e)
    assert not ok


def test_absolute_path_rejected():
    e = _envelope()
    e["source_catalog"]["catalog_path"] = \
        "/Users/sanjayb/nepal-event-anomaly/data/x.csv"
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("absolute" in p for p in problems)


def test_envelope_type_required():
    e = _envelope()
    e["envelope_type"] = "SOMETHING_ELSE"
    ok, _ = mec.verify_mec_envelope(e)
    assert not ok


def test_claim_scope_required():
    e = _envelope()
    del e["claim_scope"]
    ok, _ = mec.verify_mec_envelope(e)
    assert not ok


def test_missing_row_hash_rejected():
    e = _envelope()
    del e["events"][0]["row_sha256"]
    ok, _ = mec.verify_mec_envelope(e)
    assert not ok


# ---------- N2 validator hardening ----------


def test_feb31_rejected():
    e = _envelope()
    e["events"][0]["date_spec"]["date"] = "2015-02-31"
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("date" in p for p in problems)


def test_apr31_window_rejected():
    e = _envelope()
    e["events"][0]["windows"]["acquisition_window"] = {
        "start": "2015-04-01", "end": "2015-04-31"}
    ok, _ = mec.verify_mec_envelope(e)
    assert not ok


def test_invalid_timezone_rejected():
    e = _envelope()
    e["events"][0]["date_spec"]["timezone"] = "Not/AZone"
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("timezone" in p for p in problems)


def test_valid_timezone_accepted():
    e = _envelope()
    e["events"][0]["date_spec"]["timezone"] = "UTC"
    ok, problems = mec.verify_mec_envelope(mec.build_mec_envelope(e))
    assert ok, problems


def test_caller_min_events_mismatch_rejected():
    e = _envelope()
    e["validation_scope"]["min_events"] = 7
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("min_events" in p for p in problems)


def test_caller_region_count_mismatch_rejected():
    e = _envelope()
    e["validation_scope"]["n_geographic_regions"] = 5
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("n_geographic_regions" in p for p in problems)


def test_missing_region_id_rejected():
    e = _envelope()
    del e["events"][0]["region_id"]
    ok, _ = mec.verify_mec_envelope(e)
    assert not ok


def test_orphan_event_group_rejected():
    e = _envelope()
    e["events"][0]["event_group_id"] = "G-UNDECLARED"
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("orphan" in p for p in problems)


def test_orphan_holdout_group_rejected():
    e = _envelope()
    e["events"][0]["holdout_group"] = "H-UNDECLARED"
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("orphan" in p for p in problems)


def test_row_source_hash_mismatch_rejected():
    e = _envelope()
    e["events"][0]["row_source"] = {"raw": "row-bytes"}
    # caller hash does not match canonical row bytes
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("row_sha256" in p for p in problems)


def test_row_source_hash_match_accepted():
    from nepal.framework_v1.provenance import sha256_canonical
    e = _envelope()
    row = {"raw": "row-bytes", "v": 1}
    e["events"][0]["row_source"] = row
    e["events"][0]["row_sha256"] = sha256_canonical(row)
    ok, problems = mec.verify_mec_envelope(mec.build_mec_envelope(e))
    assert ok, problems


def test_missing_label_spec_rejected():
    e = _envelope()
    del e["label_spec"]
    ok, _ = mec.verify_mec_envelope(e)
    assert not ok


def test_label_spec_no_adjudication_rejected():
    e = _envelope()
    e["label_spec"]["adjudication"] = {"required": False,
                                       "independent_reviewers": 0}
    ok, _ = mec.verify_mec_envelope(e)
    assert not ok


def _real_envelope(packet=None):
    e = _envelope()
    e["mode"] = "REAL_SOURCE_DESIGN"
    e["synthetic_fixture"] = False
    for i, ev in enumerate(e["events"], start=1):
        ev["event_id"] = f"EVT-REAL-{i:03d}"
        ev["synthetic"] = False
    if packet is not None:
        e["source_packet"] = packet
    return e


def test_real_mode_without_packet_fails_closed():
    ok, problems = mec.verify_mec_envelope(_real_envelope())
    assert not ok and any("source_packet" in p for p in problems)


def test_real_mode_partial_packet_fails_closed():
    ok, problems = mec.verify_mec_envelope(
        _real_envelope({"packet_id": "p1",
                        "packet_sha256": "ab" * 32}))
    assert not ok


def test_real_mode_unapproved_packet_fails_closed():
    ok, problems = mec.verify_mec_envelope(_real_envelope({
        "packet_id": "p1", "packet_sha256": "ab" * 32,
        "approved_by": "operator", "human_approved": False,
        "approved_at": "2026-09-13"}))
    assert not ok and any("human_approved" in p for p in problems)


def test_real_mode_synthetic_ids_rejected():
    e = _real_envelope({"packet_id": "p1", "packet_sha256": "ab" * 32,
                        "approved_by": "operator", "human_approved": True,
                        "approved_at": "2026-09-13"})
    e["events"][0]["event_id"] = "SYNTH-EVT-001"
    e["events"][0]["synthetic"] = True
    ok, _ = mec.verify_mec_envelope(e)
    assert not ok


def test_real_mode_full_packet_schema_verifies():
    """Schema-only real-mode design verifies with a complete approved
    packet — no data access occurs; this proves the schema exists."""
    env = mec.build_mec_envelope(_real_envelope({
        "packet_id": "p1", "packet_sha256": "ab" * 32,
        "approved_by": "operator", "human_approved": True,
        "approved_at": "2026-09-13"}))
    ok, problems = mec.verify_mec_envelope(env)
    assert ok, problems
