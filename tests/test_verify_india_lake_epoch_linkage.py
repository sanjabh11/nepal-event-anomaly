"""Adversarial tests for the candidate-linkage artifact verifier."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import india_lake_epoch_linkage as linkage  # noqa: E402
import verify_india_lake_epoch_linkage as verifier  # noqa: E402


def _document(monkeypatch):
    monkeypatch.setattr(linkage, "EPOCHS", (1990, 2000))
    monkeypatch.setattr(linkage, "EXPECTED_FEATURE_COUNTS", {1990: 1, 2000: 1})
    monkeypatch.setattr(verifier, "EXPECTED_NRSC_ROWS", 1)
    profiles = []
    for epoch in linkage.EPOCHS:
        feature_id = f"GH:{epoch}:00001"
        profiles.append({
            "epoch": epoch,
            "feature_count": 1,
            "valid_polygon_count": 1,
            "excluded_geometry_count": 0,
            "features": [{
                "feature_id": feature_id,
                "source_feature_ordinal": 1,
                "geometry_status": "VALID_POLYGON",
            }],
        })
    edge = {
        "from_feature_id": "GH:1990:00001",
        "to_feature_id": "GH:2000:00001",
        "relation": "POSITIVE_AREA_OVERLAP_CANDIDATE",
        "intersection_area_m2": 1.0,
        "intersection_over_union": 1.0,
        "fraction_of_from_area": 1.0,
        "fraction_of_to_area": 1.0,
        "identity_claim": False,
        "overlap_pattern": "ONE_TO_ONE_OVERLAP_CANDIDATE",
    }
    pair = {
        "from_epoch": 1990,
        "to_epoch": 2000,
        "overlap_candidates": [edge],
        "overlap_candidate_count": 1,
        "touch_only_candidate_count": 0,
        "positive_overlap_from_features_with_any_candidate": 1,
        "positive_overlap_degree_gt_one_from_features": 0,
        "positive_overlap_degree_gt_one_to_features": 0,
        "nearest_only_diagnostics": [],
    }
    point_relations = {
        str(epoch): {
            "status": "ONE_SPATIAL_CANDIDATE",
            "candidates": [{
                "feature_id": f"GH:{epoch}:00001",
                "relation": "POINT_IN_POLYGON",
                "identity_claim": False,
            }],
            "nearest_only_diagnostic": None,
        }
        for epoch in linkage.EPOCHS
    }
    return {
        "schema": linkage.SCHEMA,
        "status": "CANDIDATE_LINKAGE_ONLY",
        "authority": dict(linkage.AUTHORITY_FLAGS),
        "conclusions": {
            "physical_lake_identity_established": False,
            "event_or_recurrence_identity_established": False,
            "verified_non_event_intervals_established": False,
            "administrative_territory_assigned": False,
            "event_weather_or_risk_claim_authorized": False,
            "operational_authority": False,
        },
        "epoch_feature_inventory": profiles,
        "adjacent_epoch_relations": [pair],
        "nrsc_t68_spatial_relations": {
            "row_count": 1,
            "comparisons": [{
                "nrsc_row_key": "NRSC:T68:1:lake-a",
                "relations_by_epoch": point_relations,
            }],
        },
    }


def test_valid_candidate_only_report_passes(monkeypatch):
    document = _document(monkeypatch)

    summary = verifier.validate_report_document(document)

    assert summary["nrsc_t68_rows"] == 1
    assert summary["adjacent_epoch_summary"][0]["positive_area_candidates"] == 1


def test_authority_promotion_is_rejected(monkeypatch):
    document = _document(monkeypatch)
    document["authority"]["weather_download_authorized"] = True

    with pytest.raises(ValueError, match="authority flags"):
        verifier.validate_report_document(document)


def test_overlap_claim_or_bad_reference_is_rejected(monkeypatch):
    document = _document(monkeypatch)
    edge = document["adjacent_epoch_relations"][0]["overlap_candidates"][0]
    edge["identity_claim"] = True

    with pytest.raises(ValueError, match="claim ceiling"):
        verifier.validate_report_document(document)

    edge["identity_claim"] = False
    edge["to_feature_id"] = "GH:2000:99999"
    with pytest.raises(ValueError, match="missing/excluded"):
        verifier.validate_report_document(document)

    document = _document(monkeypatch)
    document["adjacent_epoch_relations"][0]["overlap_candidates"][0][
        "overlap_pattern"] = "POSSIBLE_SPLIT"
    with pytest.raises(ValueError, match="graph degree"):
        verifier.validate_report_document(document)


def test_summary_count_and_duplicate_row_tampering_are_rejected(monkeypatch):
    document = _document(monkeypatch)
    document["adjacent_epoch_relations"][0]["overlap_candidate_count"] = 2

    with pytest.raises(ValueError, match="summaries do not match"):
        verifier.validate_report_document(document)

    document = _document(monkeypatch)
    document["nrsc_t68_spatial_relations"]["comparisons"].append(
        document["nrsc_t68_spatial_relations"]["comparisons"][0])
    document["nrsc_t68_spatial_relations"]["row_count"] = 2
    with pytest.raises(ValueError, match="denominator mismatch"):
        verifier.validate_report_document(document)

    document = _document(monkeypatch)
    candidates = document["nrsc_t68_spatial_relations"]["comparisons"][0][
        "relations_by_epoch"]["1990"]["candidates"]
    candidates.append(dict(candidates[0]))
    document["nrsc_t68_spatial_relations"]["comparisons"][0][
        "relations_by_epoch"]["1990"]["status"] = "MULTIPLE_SPATIAL_CANDIDATES"
    with pytest.raises(ValueError, match="repeats a candidate"):
        verifier.validate_report_document(document)
