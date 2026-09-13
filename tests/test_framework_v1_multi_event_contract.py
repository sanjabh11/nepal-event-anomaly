"""W1 Multi-Event Contract (MEC) tests — synthetic fixtures only."""
from __future__ import annotations

import copy

import pytest

from nepal.framework_v1 import multi_event_contract as mec


def _event(i, group="G1", day=3):
    return {
        "event_id": f"SYNTH-EVT-{i:03d}",
        "event_group_id": group,
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
        "events": [_event(1, "G1", 3), _event(2, "G2", 10),
                   _event(3, "G3", 21), _event(4, "G4", 5)],
        "holdout": {
            "assigned_before_filtering": True,
            "temporal_embargo_days": 30,
            "geographic_holdout": {"min_separation_km": 50.0},
            "event_separation": {"group_disjoint": True},
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
