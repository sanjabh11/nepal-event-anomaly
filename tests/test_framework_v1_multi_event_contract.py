"""W1 Multi-Event Contract (MEC) tests — synthetic fixtures only."""
from __future__ import annotations

import copy
import json

import pytest

from nepal.framework_v1 import multi_event_contract as mec
from nepal.framework_v1.provenance import sha256_canonical, sha256_file

# Region bboxes: [west, south, east, north].  Every fixture event location
# falls strictly inside its own region's bbox.
_BBOXES = {
    "R1": [80, 27, 82, 29],
    "R2": [84, 27, 86, 29],
    "R3": [88, 26, 90, 28],
}
_LOCATIONS = {
    "R1": {"lat": 28.0, "lon": 81.0},
    "R2": {"lat": 28.0, "lon": 85.0},
    "R3": {"lat": 27.0, "lon": 89.0},
}


def _event(i, group="G1", day=3, region=None):
    rid = region or f"R{(i - 1) % 3 + 1}"
    d = f"2015-04-{day:02d}"
    prev = f"2015-04-{day - 1:02d}"
    row_source = {"fixture_row": i, "group": group, "region": rid}
    return {
        "event_id": f"SYNTH-EVT-{i:03d}",
        "event_group_id": group,
        "region_id": rid,
        "synthetic": True,
        "location": dict(_LOCATIONS[rid]),
        "date_spec": {"precision": "day", "date": d,
                      "source": "synthetic_fixture_date"},
        "holdout_group": f"H{i % 2}",
        "windows": {
            # acquisition ends before feature availability, which ends on
            # or before the event date inside the target window.
            "acquisition_window": {"start": "2015-04-01", "end": prev},
            "production_time": "2015-04-25",
            "issue_time": "2015-04-26",
            "publication_time": "2015-05-01",
            "feature_availability_time": {"start": "2015-04-01",
                                          "end": d},
            "target_window": {"start": d, "end": d},
        },
        "row_source": row_source,
        "row_sha256": sha256_canonical(row_source),
    }


def _envelope():
    events = [_event(1, "G1", 3), _event(2, "G2", 10),
              _event(3, "G3", 21), _event(4, "G4", 5)]
    assignment = {ev["event_id"]: ev["holdout_group"] for ev in events}
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
        "row_schema_version": "synthetic-rows-v1",
        "row_serialization": mec.CANONICAL_ROW_SERIALIZATION,
        "event_groups": ["G1", "G2", "G3", "G4"],
        "events": events,
        "holdout": {
            "assigned_before_filtering": True,
            "temporal_embargo_days": 30,
            "geographic_holdout": {"min_separation_km": 50.0},
            "event_separation": {"group_disjoint": True},
            "holdout_groups": ["H0", "H1"],
            "assignment": assignment,
            "assignment_sha256": sha256_canonical(assignment),
        },
        "label_spec": {
            "label_source": "synthetic_fixture_labels",
            "adjudication": {"required": True,
                             "independent_reviewers": 1,
                             "ledger_sha256": "ef" * 32,
                             "reviewer_ids": ["REV-SYNTH-1"]},
            "negative_controls": {"required": True, "n_controls": 1,
                                  "artifact_sha256": "ab" * 32},
        },
        "validation_scope": {
            "scope_id": "synthetic-regional-split",
            "n_geographic_regions": 3,
            "min_events": 4,
            "regions": {rid: {"bbox": list(bbox)}
                        for rid, bbox in _BBOXES.items()},
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
    # event_ref is the interval end; target_window must contain it and
    # feature availability must not extend past it.
    e["events"][0]["windows"]["target_window"] = {
        "start": "2015-04-01", "end": "2015-04-30"}
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


# ---------- MEC-01..09 audit-gap hardening ----------


def _write_packet(tmp_path, name="source_packet.json", **overrides):
    """Write a real-mode source-packet JSON file.  The file never
    contains packet_sha256 — that would be a circular self-reference."""
    data = {"packet_id": "p1", "approved_by": "operator",
            "human_approved": True, "approved_at": "2026-09-13"}
    data.update(overrides)
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _real_envelope(tmp_path, packet=True):
    """Real-mode envelope.  ``packet=True`` binds a valid packet file
    under tmp_path; a dict is used verbatim; None omits source_packet."""
    e = _envelope()
    e["mode"] = "REAL_SOURCE_DESIGN"
    e["synthetic_fixture"] = False
    # MEC-REAL-02R: the row schema allowlist; every event row_source
    # below uses only these fields.
    e["row_fields"] = ["fixture_row", "group", "region"]
    # MEC-REAL-04: single declared timezone policy for the envelope.
    e["timezone_policy"] = "UTC"
    # MEC-REAL-03: typed asset provenance records; asset_ids must equal
    # the records' id set.
    e["source_catalog"]["asset_ids"] = ["ASSET-REAL-1"]
    e["source_catalog"]["asset_records"] = [{
        "asset_id": "ASSET-REAL-1",
        "source_url": "https://data.example.org/assets/asset-1.csv",
        "version": "v1",
        "retrieved_at": "2026-09-01",
        "byte_count": 128,
        "sha256": "ab" * 32,
    }]
    for i, ev in enumerate(e["events"], start=1):
        ev["event_id"] = f"EVT-REAL-{i:03d}"
        ev["synthetic"] = False
        ev["date_spec"]["timezone"] = "UTC"
    e["holdout"]["assignment"] = {
        ev["event_id"]: ev["holdout_group"] for ev in e["events"]}
    e["holdout"]["assignment_sha256"] = sha256_canonical(
        e["holdout"]["assignment"])
    if packet is True:
        pfile = _write_packet(tmp_path)
        e["source_packet"] = {
            "packet_id": "p1",
            "packet_sha256": sha256_file(pfile),
            "approved_by": "operator",
            "human_approved": True,
            "approved_at": "2026-09-13",
            "packet_relpath": pfile.name,
        }
    elif packet is not None:
        e["source_packet"] = packet
    return e


def test_real_mode_without_packet_fails_closed(tmp_path):
    ok, problems = mec.verify_mec_envelope(
        _real_envelope(tmp_path, packet=None), packet_root=tmp_path)
    assert not ok and any("source_packet" in p for p in problems)


def test_real_mode_partial_packet_fails_closed(tmp_path):
    ok, problems = mec.verify_mec_envelope(
        _real_envelope(tmp_path, {"packet_id": "p1",
                                  "packet_sha256": "ab" * 32}),
        packet_root=tmp_path)
    assert not ok


def test_real_mode_unapproved_packet_fails_closed(tmp_path):
    pfile = _write_packet(tmp_path)
    ok, problems = mec.verify_mec_envelope(_real_envelope(tmp_path, {
        "packet_id": "p1", "packet_sha256": sha256_file(pfile),
        "approved_by": "operator", "human_approved": False,
        "approved_at": "2026-09-13",
        "packet_relpath": pfile.name}), packet_root=tmp_path)
    assert not ok and any("human_approved" in p for p in problems)


def test_real_mode_synthetic_ids_rejected(tmp_path):
    e = _real_envelope(tmp_path)
    e["events"][0]["event_id"] = "SYNTH-EVT-001"
    e["events"][0]["synthetic"] = True
    ok, _ = mec.verify_mec_envelope(e, packet_root=tmp_path)
    assert not ok


def test_real_mode_full_packet_schema_verifies(tmp_path):
    """Schema-only real-mode design verifies with a complete approved
    packet bound to a real file under packet_root."""
    env = mec.build_mec_envelope(_real_envelope(tmp_path),
                                 packet_root=tmp_path)
    ok, problems = mec.verify_mec_envelope(env, packet_root=tmp_path)
    assert ok, problems


def test_real_mode_requires_packet_root(tmp_path):
    e = _real_envelope(tmp_path)
    ok, problems = mec.verify_mec_envelope(e)  # packet_root withheld
    assert not ok and any("packet_root" in p for p in problems)


def test_real_mode_packet_relpath_escape_rejected(tmp_path):
    e = _real_envelope(tmp_path)
    e["source_packet"]["packet_relpath"] = "../outside.json"
    ok, problems = mec.verify_mec_envelope(e, packet_root=tmp_path)
    assert not ok and any("packet_relpath" in p for p in problems)


def test_real_mode_absolute_packet_relpath_rejected(tmp_path):
    e = _real_envelope(tmp_path)
    e["source_packet"]["packet_relpath"] = "/etc/passwd"
    ok, problems = mec.verify_mec_envelope(e, packet_root=tmp_path)
    assert not ok


def test_real_mode_missing_packet_file_rejected(tmp_path):
    e = _real_envelope(tmp_path)
    e["source_packet"]["packet_relpath"] = "nonexistent.json"
    ok, problems = mec.verify_mec_envelope(e, packet_root=tmp_path)
    assert not ok and any("packet" in p for p in problems)


def test_real_mode_packet_symlink_rejected(tmp_path):
    real = _write_packet(tmp_path, name="real_packet.json")
    link = tmp_path / "link_packet.json"
    link.symlink_to(real)
    e = _real_envelope(tmp_path)
    e["source_packet"]["packet_relpath"] = link.name
    e["source_packet"]["packet_sha256"] = sha256_file(real)
    ok, problems = mec.verify_mec_envelope(e, packet_root=tmp_path)
    assert not ok and any("symlink" in p for p in problems)


def test_real_mode_packet_self_hash_in_file_rejected(tmp_path):
    """A packet file containing packet_sha256 is a circular
    self-reference and must be rejected."""
    pfile = _write_packet(tmp_path, name="tampered_packet.json",
                          packet_sha256="0" * 64)
    e = _real_envelope(tmp_path)
    e["source_packet"]["packet_relpath"] = pfile.name
    e["source_packet"]["packet_sha256"] = sha256_file(pfile)
    ok, problems = mec.verify_mec_envelope(e, packet_root=tmp_path)
    assert not ok and any("packet_sha256" in p for p in problems)


def test_real_mode_packet_hash_mismatch_rejected(tmp_path):
    e = _real_envelope(tmp_path)
    e["source_packet"]["packet_sha256"] = "ab" * 32
    ok, problems = mec.verify_mec_envelope(e, packet_root=tmp_path)
    assert not ok and any("packet_sha256" in p for p in problems)


def test_real_mode_packet_field_mismatch_rejected(tmp_path):
    e = _real_envelope(tmp_path)
    e["source_packet"]["approved_by"] = "someone-else"
    ok, problems = mec.verify_mec_envelope(e, packet_root=tmp_path)
    assert not ok and any("approved_by" in p for p in problems)


def test_synthetic_mode_ignores_packet_root(tmp_path):
    env = mec.build_mec_envelope(_envelope())
    ok, problems = mec.verify_mec_envelope(env, packet_root=tmp_path)
    assert ok, problems


# MEC-02: real mode binds actual row bytes


def test_real_mode_requires_row_source(tmp_path):
    e = _real_envelope(tmp_path)
    del e["events"][0]["row_source"]  # caller-only hash: not verifiable
    ok, problems = mec.verify_mec_envelope(e, packet_root=tmp_path)
    assert not ok and any("row_source" in p for p in problems)


# MEC-03: canonical row schema version


def test_row_source_without_schema_version_rejected():
    e = _envelope()
    del e["row_schema_version"]
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("row_schema_version" in p for p in problems)


def test_empty_row_schema_version_rejected():
    e = _envelope()
    e["row_schema_version"] = ""
    ok, _ = mec.verify_mec_envelope(e)
    assert not ok


# MEC-REAL-02: row_serialization pins canonical-json-v1


def test_canonical_row_serialization_constant():
    assert mec.CANONICAL_ROW_SERIALIZATION == "canonical-json-v1"


def test_row_source_without_serialization_rejected():
    e = _envelope()
    del e["row_serialization"]
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("row_serialization" in p for p in problems)


def test_wrong_row_serialization_rejected():
    e = _envelope()
    e["row_serialization"] = "pretty-json-v2"
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("row_serialization" in p for p in problems)


def test_non_string_row_serialization_rejected():
    e = _envelope()
    e["row_serialization"] = 1
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("row_serialization" in p for p in problems)


def test_row_serialization_without_row_source_rejected():
    """Declaring a serialization no event uses is misleading — reject."""
    e = _envelope()
    for ev in e["events"]:
        del ev["row_source"]
        # caller-pinned row_sha256 remains a valid lowercase SHA-256
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("row_serialization" in p for p in problems)


def test_empty_row_source_mapping_rejected():
    e = _envelope()
    e["events"][0]["row_source"] = {}
    e["events"][0]["row_sha256"] = sha256_canonical({})
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("row_source" in p for p in problems)


def test_row_source_non_string_key_rejected():
    e = _envelope()
    row = {1: "x"}  # non-string key — not a canonical JSON object key
    e["events"][0]["row_source"] = row
    e["events"][0]["row_sha256"] = sha256_canonical(row)
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("row_source" in p for p in problems)


# MEC-04: all six windows, consistently ordered


def test_missing_window_key_rejected():
    e = _envelope()
    del e["events"][0]["windows"]["issue_time"]
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("issue_time" in p for p in problems)


def test_window_time_ordering_rejected():
    e = _envelope()
    # issue before production violates production <= issue <= publication
    e["events"][0]["windows"]["issue_time"] = "2015-04-24"
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("issue_time" in p for p in problems)


def test_event_outside_target_window_rejected():
    e = _envelope()
    e["events"][0]["windows"]["target_window"] = {
        "start": "2015-04-10", "end": "2015-04-11"}
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("target_window" in p for p in problems)


def test_acquisition_after_feature_availability_rejected():
    e = _envelope()
    # acquisition ends 04-10, after feature availability end 04-03
    e["events"][0]["windows"]["acquisition_window"] = {
        "start": "2015-04-01", "end": "2015-04-10"}
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("acquisition_window" in p for p in problems)


# MEC-05: unique asset ids, well-formed asset records


def test_duplicate_asset_id_rejected():
    e = _envelope()
    e["source_catalog"]["asset_ids"] = ["SYNTH-ASSET-1", "SYNTH-ASSET-1"]
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("duplicate" in p for p in problems)


def test_asset_record_bad_hash_rejected():
    e = _envelope()
    e["source_catalog"]["asset_ids"] = [
        {"asset_id": "SYNTH-ASSET-1", "asset_sha256": "xyz", "bytes": 10}]
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("asset_sha256" in p for p in problems)


def test_asset_record_negative_bytes_rejected():
    e = _envelope()
    e["source_catalog"]["asset_ids"] = [
        {"asset_id": "SYNTH-ASSET-1", "asset_sha256": "ab" * 32,
         "bytes": -1}]
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("bytes" in p for p in problems)


def test_asset_record_accepted():
    e = _envelope()
    e["source_catalog"]["asset_ids"] = [
        {"asset_id": "SYNTH-ASSET-1", "asset_sha256": "ab" * 32,
         "bytes": 128},
        "SYNTH-ASSET-2"]
    ok, problems = mec.verify_mec_envelope(mec.build_mec_envelope(e))
    assert ok, problems


# MEC-06: materialized, hashed holdout assignment


def test_missing_holdout_assignment_rejected():
    e = _envelope()
    del e["holdout"]["assignment"]
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("assignment" in p for p in problems)


def test_holdout_assignment_hash_mismatch_rejected():
    e = _envelope()
    e["holdout"]["assignment_sha256"] = "ab" * 32
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("assignment_sha256" in p for p in problems)


def test_holdout_assignment_group_mismatch_rejected():
    e = _envelope()
    e["holdout"]["assignment"]["SYNTH-EVT-001"] = "H0"  # event says H1
    e["holdout"]["assignment_sha256"] = sha256_canonical(
        e["holdout"]["assignment"])
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok


def test_holdout_assignment_extra_event_rejected():
    e = _envelope()
    e["holdout"]["assignment"]["SYNTH-EVT-999"] = "H0"
    e["holdout"]["assignment_sha256"] = sha256_canonical(
        e["holdout"]["assignment"])
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("assignment" in p for p in problems)


# MEC-07: event geometry bound to declared region bboxes


def test_missing_location_rejected():
    e = _envelope()
    del e["events"][0]["location"]
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("location" in p for p in problems)


def test_location_outside_region_bbox_rejected():
    e = _envelope()
    # R1 bbox is lon 80..82; lon 89 belongs to R3's territory
    e["events"][0]["location"] = {"lat": 28.0, "lon": 89.0}
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("location" in p or "bbox" in p
                          for p in problems)


def test_single_distinct_bbox_rejected():
    e = _envelope()
    # Three region names but one physical box — a renamed single-box
    # design must still fail.
    for spec in e["validation_scope"]["regions"].values():
        spec["bbox"] = [80, 27, 82, 29]
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("bbox" in p for p in problems)


def test_missing_regions_map_rejected():
    e = _envelope()
    del e["validation_scope"]["regions"]
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("regions" in p for p in problems)


# MEC-08: label evidence fields


def test_adjudication_ledger_missing_rejected():
    e = _envelope()
    del e["label_spec"]["adjudication"]["ledger_sha256"]
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("ledger_sha256" in p for p in problems)


def test_reviewer_ids_empty_rejected():
    e = _envelope()
    e["label_spec"]["adjudication"]["reviewer_ids"] = []
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("reviewer_ids" in p for p in problems)


def test_reviewer_ids_duplicate_rejected():
    e = _envelope()
    e["label_spec"]["adjudication"]["reviewer_ids"] = ["REV-1", "REV-1"]
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("reviewer_ids" in p for p in problems)


def test_negative_controls_artifact_missing_rejected():
    e = _envelope()
    del e["label_spec"]["negative_controls"]["artifact_sha256"]
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("artifact_sha256" in p for p in problems)


# MEC-09: day-15 nuance


def test_day15_with_support_flag_accepted():
    e = _envelope()
    e["events"][0]["date_spec"]["date"] = "2015-04-15"
    e["events"][0]["date_spec"]["exact_day15_supported"] = True
    e["events"][0]["windows"]["target_window"] = {
        "start": "2015-04-14", "end": "2015-04-16"}
    ok, problems = mec.verify_mec_envelope(mec.build_mec_envelope(e))
    assert ok, problems


def test_day15_flag_with_fallback_source_rejected():
    e = _envelope()
    e["events"][0]["date_spec"]["date"] = "2015-04-15"
    e["events"][0]["date_spec"]["exact_day15_supported"] = True
    e["events"][0]["date_spec"]["source"] = "imputed_fallback"
    e["events"][0]["windows"]["target_window"] = {
        "start": "2015-04-14", "end": "2015-04-16"}
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("source" in p for p in problems)


def test_day15_flag_false_still_rejected():
    e = _envelope()
    e["events"][0]["date_spec"]["date"] = "2015-04-15"
    e["events"][0]["date_spec"]["exact_day15_supported"] = False
    e["events"][0]["windows"]["target_window"] = {
        "start": "2015-04-14", "end": "2015-04-16"}
    ok, problems = mec.verify_mec_envelope(e)
    assert not ok and any("day-15" in p for p in problems)


# MEC-REAL-02R: real-mode row_fields allowlist


def test_real_mode_requires_row_fields(tmp_path):
    e = _real_envelope(tmp_path)
    del e["row_fields"]
    ok, problems = mec.verify_mec_envelope(e, packet_root=tmp_path)
    assert not ok and any("row_fields" in p for p in problems)


def test_real_mode_row_fields_not_string_list_rejected(tmp_path):
    e = _real_envelope(tmp_path)
    e["row_fields"] = "fixture_row"
    ok, problems = mec.verify_mec_envelope(e, packet_root=tmp_path)
    assert not ok and any("row_fields" in p for p in problems)


def test_real_mode_row_fields_duplicates_rejected(tmp_path):
    e = _real_envelope(tmp_path)
    e["row_fields"] = ["fixture_row", "group", "region", "group"]
    ok, problems = mec.verify_mec_envelope(e, packet_root=tmp_path)
    assert not ok and any("duplicate" in p for p in problems)


def test_real_mode_row_source_extra_field_rejected(tmp_path):
    e = _real_envelope(tmp_path)
    row = dict(e["events"][0]["row_source"])
    row["undeclared_field"] = "x"
    e["events"][0]["row_source"] = row
    e["events"][0]["row_sha256"] = sha256_canonical(row)
    ok, problems = mec.verify_mec_envelope(e, packet_root=tmp_path)
    assert not ok and any("row_fields" in p for p in problems)


def test_real_mode_row_source_non_mapping_rejected(tmp_path):
    e = _real_envelope(tmp_path)
    row = ["a", "b"]  # a list is legal synthetic row bytes, not a real row
    e["events"][0]["row_source"] = row
    e["events"][0]["row_sha256"] = sha256_canonical(row)
    ok, problems = mec.verify_mec_envelope(e, packet_root=tmp_path)
    assert not ok and any("row_source" in p for p in problems)


def test_real_mode_row_source_non_json_value_rejected(tmp_path):
    e = _real_envelope(tmp_path)
    row = dict(e["events"][0]["row_source"])
    row["group"] = float("nan")  # canonical JSON cannot encode NaN
    e["events"][0]["row_source"] = row
    e["events"][0]["row_sha256"] = "ab" * 32
    ok, problems = mec.verify_mec_envelope(e, packet_root=tmp_path)
    assert not ok and any("row_source" in p for p in problems)


# MEC-REAL-03: typed asset records in real mode


def test_real_mode_requires_asset_records(tmp_path):
    """String-only asset_ids without typed provenance records reject."""
    e = _real_envelope(tmp_path)
    del e["source_catalog"]["asset_records"]
    ok, problems = mec.verify_mec_envelope(e, packet_root=tmp_path)
    assert not ok and any("asset_records" in p for p in problems)


def test_real_mode_asset_record_incomplete_rejected(tmp_path):
    e = _real_envelope(tmp_path)
    del e["source_catalog"]["asset_records"][0]["sha256"]
    ok, problems = mec.verify_mec_envelope(e, packet_root=tmp_path)
    assert not ok and any("asset_records" in p for p in problems)


def test_real_mode_asset_record_bad_retrieved_at_rejected(tmp_path):
    e = _real_envelope(tmp_path)
    e["source_catalog"]["asset_records"][0]["retrieved_at"] = \
        "last Tuesday"
    ok, problems = mec.verify_mec_envelope(e, packet_root=tmp_path)
    assert not ok and any("retrieved_at" in p for p in problems)


def test_real_mode_asset_record_extra_field_rejected(tmp_path):
    e = _real_envelope(tmp_path)
    e["source_catalog"]["asset_records"][0]["operator"] = "x"
    ok, problems = mec.verify_mec_envelope(e, packet_root=tmp_path)
    assert not ok and any("asset_records" in p for p in problems)


def test_real_mode_asset_ids_diverge_from_records_rejected(tmp_path):
    e = _real_envelope(tmp_path)
    e["source_catalog"]["asset_ids"] = ["ASSET-REAL-1", "ASSET-REAL-2"]
    ok, problems = mec.verify_mec_envelope(e, packet_root=tmp_path)
    assert not ok and any("asset_ids" in p for p in problems)


def test_real_mode_asset_id_record_form_rejected(tmp_path):
    """The synthetic-mode {asset_id, asset_sha256, bytes} record form is
    not a valid real-mode asset_ids entry — typed provenance lives on
    asset_records."""
    e = _real_envelope(tmp_path)
    e["source_catalog"]["asset_ids"] = [
        {"asset_id": "ASSET-REAL-1", "asset_sha256": "ab" * 32,
         "bytes": 128}]
    ok, problems = mec.verify_mec_envelope(e, packet_root=tmp_path)
    assert not ok and any("asset_ids" in p for p in problems)


def test_real_mode_asset_record_datetime_retrieved_at_ok(tmp_path):
    e = _real_envelope(tmp_path)
    e["source_catalog"]["asset_records"][0]["retrieved_at"] = \
        "2026-09-01T12:00:00Z"
    env = mec.build_mec_envelope(e, packet_root=tmp_path)
    ok, problems = mec.verify_mec_envelope(env, packet_root=tmp_path)
    assert ok, problems


# MEC-REAL-04: UTC timezone policy in real mode


def test_real_mode_requires_timezone_policy(tmp_path):
    e = _real_envelope(tmp_path)
    del e["timezone_policy"]
    ok, problems = mec.verify_mec_envelope(e, packet_root=tmp_path)
    assert not ok and any("timezone_policy" in p for p in problems)


def test_real_mode_non_utc_timezone_policy_rejected(tmp_path):
    e = _real_envelope(tmp_path)
    e["timezone_policy"] = "Asia/Kathmandu"
    ok, problems = mec.verify_mec_envelope(e, packet_root=tmp_path)
    assert not ok and any("timezone_policy" in p for p in problems)


def test_real_mode_non_utc_window_timezone_rejected(tmp_path):
    e = _real_envelope(tmp_path)
    e["events"][0]["windows"]["acquisition_window"]["timezone"] = \
        "Asia/Kathmandu"
    ok, problems = mec.verify_mec_envelope(e, packet_root=tmp_path)
    assert not ok and any("timezone" in p for p in problems)


def test_real_mode_missing_date_spec_timezone_rejected(tmp_path):
    e = _real_envelope(tmp_path)
    del e["events"][0]["date_spec"]["timezone"]
    ok, problems = mec.verify_mec_envelope(e, packet_root=tmp_path)
    assert not ok and any("timezone" in p for p in problems)


def test_real_mode_non_utc_date_spec_timezone_rejected(tmp_path):
    e = _real_envelope(tmp_path)
    e["events"][0]["date_spec"]["timezone"] = "Asia/Kathmandu"
    ok, problems = mec.verify_mec_envelope(e, packet_root=tmp_path)
    assert not ok and any("timezone" in p for p in problems)


# MEC-REAL-05: optional approval-record file binding


def _write_approval(tmp_path, name="approval_record.json", **overrides):
    """Write an approval-record JSON file under the packet root."""
    data = {"packet_id": "p1", "approved_by": "operator",
            "human_approved": True, "approved_at": "2026-09-13",
            "record_type": "source_packet_approval"}
    data.update(overrides)
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_real_mode_approval_record_bound_verifies(tmp_path):
    e = _real_envelope(tmp_path)
    afile = _write_approval(tmp_path)
    e["source_packet"]["approval_record_relpath"] = afile.name
    e["source_packet"]["approval_record_sha256"] = sha256_file(afile)
    env = mec.build_mec_envelope(e, packet_root=tmp_path)
    ok, problems = mec.verify_mec_envelope(env, packet_root=tmp_path)
    assert ok, problems


def test_real_mode_approval_record_tamper_rejected(tmp_path):
    e = _real_envelope(tmp_path)
    afile = _write_approval(tmp_path)
    e["source_packet"]["approval_record_relpath"] = afile.name
    e["source_packet"]["approval_record_sha256"] = sha256_file(afile)
    # Tamper with the record after the digest was declared.
    afile.write_text(json.dumps({"packet_id": "p1",
                                 "approved_by": "forger"}),
                     encoding="utf-8")
    ok, problems = mec.verify_mec_envelope(e, packet_root=tmp_path)
    assert not ok and any("approval_record" in p for p in problems)


def test_real_mode_approval_record_field_mismatch_rejected(tmp_path):
    e = _real_envelope(tmp_path)
    afile = _write_approval(tmp_path, approved_by="someone-else")
    e["source_packet"]["approval_record_relpath"] = afile.name
    e["source_packet"]["approval_record_sha256"] = sha256_file(afile)
    ok, problems = mec.verify_mec_envelope(e, packet_root=tmp_path)
    assert not ok and any("approved_by" in p for p in problems)


def test_real_mode_approval_record_missing_file_rejected(tmp_path):
    e = _real_envelope(tmp_path)
    e["source_packet"]["approval_record_relpath"] = "no_such_record.json"
    e["source_packet"]["approval_record_sha256"] = "ab" * 32
    ok, problems = mec.verify_mec_envelope(e, packet_root=tmp_path)
    assert not ok and any("approval_record" in p for p in problems)


def test_real_mode_approval_record_relpath_alone_rejected(tmp_path):
    """Declaring the record path without its digest fails closed."""
    e = _real_envelope(tmp_path)
    afile = _write_approval(tmp_path)
    e["source_packet"]["approval_record_relpath"] = afile.name
    ok, problems = mec.verify_mec_envelope(e, packet_root=tmp_path)
    assert not ok and any("approval_record" in p for p in problems)


def test_real_mode_approval_record_escape_rejected(tmp_path):
    e = _real_envelope(tmp_path)
    e["source_packet"]["approval_record_relpath"] = "../outside.json"
    e["source_packet"]["approval_record_sha256"] = "ab" * 32
    ok, problems = mec.verify_mec_envelope(e, packet_root=tmp_path)
    assert not ok and any("approval_record" in p for p in problems)
