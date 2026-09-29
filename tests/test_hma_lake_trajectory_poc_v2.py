"""Focused contract tests for the V2 append-only trajectory successor."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hma_lake_trajectory_poc_v2 as poc  # noqa: E402


def test_every_epoch_slot_gets_an_explicit_missingness_flag():
    document = {"trajectories": [{
        "candidate_path_id": "candidate-a",
        "observations": [
            {"epoch": 1990, "source_feature_present": True,
             "coverage_flag": "OBSERVED_AREA_AND_DATE", "feature_id": "a", "area_m2": 1.0},
            {"epoch": 2000, "source_feature_present": False,
             "feature_id": None, "area_m2": None},
            {"epoch": 2010, "source_feature_present": True,
             "coverage_flag": "OBSERVED_FEATURE_INCOMPLETE_MEASUREMENT", "feature_id": "b", "area_m2": None},
            {"epoch": 2015, "source_feature_present": False,
             "feature_id": None, "area_m2": None},
            {"epoch": 2020, "source_feature_present": True,
             "coverage_flag": "OBSERVED_AREA_AND_DATE", "feature_id": "c", "area_m2": 3.0},
        ],
    }]}

    audit = poc._coverage_flags(document)

    assert audit["trajectory_epoch_slots"] == 5
    assert audit["counts_by_epoch_and_flag"]["2000"][poc.MISSING_EPOCH_FLAG] == 1
    assert document["trajectories"][0]["observations"][1]["coverage_flag"] == poc.MISSING_EPOCH_FLAG
    assert document["trajectories"][0]["observations"][3]["coverage_flag"] == poc.MISSING_EPOCH_FLAG


def test_coverage_flag_contract_rejects_false_missing_measurement():
    document = {"trajectories": [{
        "candidate_path_id": "candidate-a",
        "observations": [
            {"epoch": epoch, "source_feature_present": False,
             "feature_id": None, "area_m2": None}
            for epoch in poc.v1.linkage.EPOCHS
        ],
    }]}
    document["trajectories"][0]["observations"][2]["area_m2"] = 4.0
    with pytest.raises(ValueError, match="contradicts a measured area"):
        poc._coverage_flags(document)


def test_v2_plan_keeps_numeric_gate_and_event_branch_dormant():
    plan = poc._make_plan(
        {"trajectories": [], "coverage_flag_audit": {"contract": {
            "one_explicit_flag_per_candidate_path_and_epoch": True,
            "missing_epoch_is_not_zero_area": True,
            "missing_source_feature_flag": poc.MISSING_EPOCH_FLAG,
        }}},
        "a" * 64,
        "b" * 64,
        "c" * 64,
        {"v1.json": "d" * 64},
    )

    criterion = plan["stability_design"]["numeric_reproducible_structure_criterion"]
    assert criterion["selected_full_sample_silhouette_strictly_greater_than"] == 0.0
    assert criterion["resamples_selecting_full_sample_k_fraction_min"] == 0.80
    assert criterion["every_full_sample_cluster_mean_best_match_jaccard_min"] == 0.75
    assert plan["not_run_or_not_authorized"]["event_label_association"] == "DORMANT_SEPARATE_BRANCH"
    assert plan["event_association_branch"] == "DORMANT"
    assert plan["not_run_or_not_authorized"]["new_data_acquisition"] is False


def test_metadata_review_does_not_authorize_acquisition_or_overstate_domain():
    review = poc.HIGHER_FREQUENCY_ASSESSMENT
    assert review["payload_acquired"] is False
    assert review["decision"]["new_acquisition_authorized"] is False
    assert review["decision"]["post_result_source_switch_prohibited"] is True
    assert "not all HMA" in review["candidates"][1]["reported_study_domain"]
    assert all(row["payload_or_network_request_made"] is False
               for row in review["candidates"])


def test_inventory_scope_is_not_overclaimed_as_full_hma():
    trajectory_doc = {"trajectories": [{"observations": [
        {"source_feature_present": True, "region_source": "Central Himalaya"},
        {"source_feature_present": False, "region_source": None},
    ]}]}

    domain = poc._inventory_domain(trajectory_doc)

    assert domain["source_inventory_name"] == "Greater Himalaya glacial lake inventory"
    assert domain["observed_source_region_labels"] == ["Central Himalaya"]
    assert domain["full_hma_coverage_claimed"] is False
