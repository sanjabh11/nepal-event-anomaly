"""Focused contract tests for the V3 containment-mode trajectory engine."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hma_lake_trajectory_poc as v1  # noqa: E402
import hma_lake_trajectory_poc_v3 as poc  # noqa: E402

SEALED_AVAILABLE = poc.REVIEW_V1_PATH.is_file() and v1.LINKAGE_PATH.is_file()
V3_SEALED = (poc.OUTPUT_ROOT / f"{poc.SCHEMA_TRAJECTORIES}.json").is_file()


def _edge(**overrides):
    edge = {
        "overlap_pattern": "ONE_TO_ONE_OVERLAP_CANDIDATE",
        "relation": "POSITIVE_AREA_OVERLAP_CANDIDATE",
        "intersection_over_union": 0.9,
        "fraction_of_from_area": 0.9,
        "fraction_of_to_area": 0.9,
    }
    edge.update(overrides)
    return edge


def test_edge_channels_strict_and_containment():
    # Strict passes when iou and from-fraction both clear the threshold.
    strict_ok, contain_ok, ratio = poc._edge_channels(_edge())
    assert strict_ok and contain_ok and ratio == pytest.approx(1.0)

    # Nested shrink: strict IoU fails but containment admits within ratio bound.
    strict_ok, contain_ok, ratio = poc._edge_channels(_edge(
        intersection_over_union=0.4, fraction_of_from_area=0.9,
        fraction_of_to_area=0.45))
    assert strict_ok is False and contain_ok is True
    assert ratio == pytest.approx(2.0)

    # Ratio beyond the sealed 4.0 bound stays unconfirmed.
    strict_ok, contain_ok, ratio = poc._edge_channels(_edge(
        intersection_over_union=0.1, fraction_of_from_area=0.9,
        fraction_of_to_area=0.1))
    assert strict_ok is False and contain_ok is False
    assert ratio == pytest.approx(9.0)

    # Ambiguous multi-link patterns never approve on either channel.
    strict_ok, contain_ok, _ = poc._edge_channels(_edge(
        overlap_pattern="POSSIBLE_SPLIT"))
    assert strict_ok is False and contain_ok is False


def test_degree_one_assertion_fires_on_synthetic_branching_edge():
    branching_out = [
        {"from_feature_id": "GH:1990:00001", "to_feature_id": "GH:2000:00001"},
        {"from_feature_id": "GH:1990:00001", "to_feature_id": "GH:2000:00002"},
    ]
    with pytest.raises(ValueError, match="degree 1"):
        poc._assert_approved_degree_one(branching_out)
    branching_in = [
        {"from_feature_id": "GH:1990:00001", "to_feature_id": "GH:2000:00001"},
        {"from_feature_id": "GH:1990:00002", "to_feature_id": "GH:2000:00001"},
    ]
    with pytest.raises(ValueError, match="degree 1"):
        poc._assert_approved_degree_one(branching_in)
    poc._assert_approved_degree_one(branching_out[:1])


def _feature(epoch: int, ordinal: int, **attrs):
    base = {"Area": 1.0, "Error": 0.01, "Date": f"{epoch}-07-01",
            "Region": "Central Himalaya", "Type": "Pro-glacial lakes",
            "Elevation": 5000, "PR": "123456", "MG_ID1": None, "MG_ID2": None}
    base.update(attrs)
    return {"feature_id": f"GH:{epoch}:{ordinal:05d}",
            "source_feature_ordinal": ordinal,
            "geometry_status": "VALID_POLYGON",
            "attributes": base}


def test_conservation_every_source_node_exactly_once(monkeypatch):
    monkeypatch.setattr(v1.linkage, "EPOCHS", (1990, 2000))
    monkeypatch.setattr(v1.linkage, "EXPECTED_FEATURE_COUNTS", {1990: 2, 2000: 2})
    features = [_feature(1990, 1), _feature(1990, 2),
                _feature(2000, 1), _feature(2000, 2)]
    link_doc = {"epoch_feature_inventory": [
        {"epoch": 1990, "features": features[:2]},
        {"epoch": 2000, "features": features[2:]}]}
    edge = {"from_epoch": 1990, "to_epoch": 2000,
            "from_feature_id": "GH:1990:00001",
            "to_feature_id": "GH:2000:00001",
            "overlap_pattern": "ONE_TO_ONE_OVERLAP_CANDIDATE",
            "relation": "POSITIVE_AREA_OVERLAP_CANDIDATE",
            "approval_channel": "STRICT", "identity_claim": False}
    paths, _ = poc._resolve_paths({"linkage": link_doc,
                                   "approved_edges": [edge],
                                   "unconfirmed_edges": []})
    seen = [fid for path in paths for fid in path["source_feature_ids"]]
    assert sorted(seen) == [f["feature_id"] for f in features]
    assert len(paths) == 3


def test_coverage_flag_contract_fills_missing_epoch_slots():
    document = {"trajectories": [{
        "candidate_path_id": "candidate-a",
        "observations": [
            {"epoch": epoch, "source_feature_present": False,
             "feature_id": None, "area_m2": None}
            for epoch in v1.linkage.EPOCHS
        ],
    }]}
    audit = poc._coverage_flags(document)
    assert audit["trajectory_epoch_slots"] == len(v1.linkage.EPOCHS)
    assert all(row["coverage_flag"] == poc.v2.MISSING_EPOCH_FLAG
               for row in document["trajectories"][0]["observations"])


def test_earliest_observation_covariates_from_first_epoch_feature():
    f1 = _feature(2000, 1, Type="Moraine-dammed lakes", Elevation=5123,
                  Region="Eastern Himalaya", PR="998877", MG_ID1=7, MG_ID2=3)
    f2 = _feature(2010, 1)
    features = {f1["feature_id"]: f1, f2["feature_id"]: f2}
    epochs = {f1["feature_id"]: 2000, f2["feature_id"]: 2010}
    covariates = poc._earliest_covariates(
        [f2["feature_id"], f1["feature_id"]], features, epochs)
    assert covariates == {
        "type_source": "Moraine-dammed lakes",
        "elevation_m": 5123.0,
        "region_source": "Eastern Himalaya",
        "pr": "998877",
        "mg_id1": 7,
        "mg_id2": 3,
    }


def test_write_once_rerun_semantics(tmp_path):
    path = tmp_path / "artifact.json"
    document = {"schema": "TEST_V3", "value": 1}
    digest = poc._publish_or_match(path, document)
    assert poc.v1.verify_sidecar(path) == digest
    # Identical rerun is a no-op returning the same digest.
    assert poc._publish_or_match(path, document) == digest
    with pytest.raises(FileExistsError, match="different content"):
        poc._publish_or_match(path, {"schema": "TEST_V3", "value": 2})


@pytest.mark.skipif(not SEALED_AVAILABLE,
                    reason="sealed intake evidence root not mounted")
def test_verdict_recompute_equals_sealed_review_counts():
    inputs = poc._load_and_validate_inputs(replay_source=False)
    assert inputs["verdict_counts"] == {
        "APPROVED_IDENTITY_CANDIDATE": 26098,
        "UNCONFIRMED_CANDIDATE": 1540,
    }
    assert inputs["channel_counts"] == {"STRICT": 12695, "CONTAINMENT": 13403}
    assert len(inputs["approved_edges"]) == 26098
    assert len(inputs["unconfirmed_edges"]) == 1540


@pytest.mark.skipif(not V3_SEALED,
                    reason="V3 artifacts are not sealed on this host")
def test_verify_subcommand_returns_exact_match():
    result = poc.verify()
    assert result["status"] == "HMA_LAKE_TRAJECTORY_V3_VERIFY_OK"
    assert result["replay"] == "EXACT_MATCH"
    assert result["source_replay"] == "EXACT_MATCH"
