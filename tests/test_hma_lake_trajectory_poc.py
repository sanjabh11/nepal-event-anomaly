"""Focused synthetic/adversarial tests for the offline HMA trajectory PoC."""
from __future__ import annotations

import sys
import hashlib
import zipfile
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hma_lake_trajectory_poc as poc  # noqa: E402


def _feature(epoch: int, ordinal: int, *, area: float = 1.0,
             error: float = 0.01, day: str = "2000-01-01",
             region: str = "Central Himalaya"):
    return {
        "feature_id": f"GH:{epoch}:{ordinal:05d}",
        "source_feature_ordinal": ordinal,
        "geometry_status": "VALID_POLYGON",
        "attributes": {"Area": area, "Error": error, "Date": day,
                       "Region": region},
    }


def _link_doc():
    epochs = (1990, 2000)
    profiles = []
    for epoch in epochs:
        profiles.append({
            "epoch": epoch,
            "features": [_feature(epoch, 1), _feature(epoch, 2)],
        })
    edge = {
        "from_feature_id": "GH:1990:00001", "to_feature_id": "GH:2000:00001",
        "overlap_pattern": "ONE_TO_ONE_OVERLAP_CANDIDATE",
        "relation": "POSITIVE_AREA_OVERLAP_CANDIDATE",
        "intersection_area_m2": 1.0, "intersection_over_union": 0.9,
        "fraction_of_from_area": 0.9, "fraction_of_to_area": 0.9,
        "identity_claim": False,
    }
    return {"epoch_feature_inventory": profiles}, edge


def test_resolve_paths_conserves_all_nodes_and_links_only_approved(monkeypatch):
    monkeypatch.setattr(poc.linkage, "EPOCHS", (1990, 2000))
    monkeypatch.setattr(poc.linkage, "EXPECTED_FEATURE_COUNTS", {1990: 2, 2000: 2})
    link_doc, edge = _link_doc()
    approved = [{**edge, "from_epoch": 1990, "to_epoch": 2000}]
    unconfirmed = [{
        "from_epoch": 1990, "to_epoch": 2000,
        "from_feature_id": "GH:1990:00002", "to_feature_id": "GH:2000:00002",
        "overlap_pattern": "POSSIBLE_SPLIT", "identity_claim": False,
    }]
    paths, graph = poc._resolve_paths({"linkage": link_doc,
                                       "approved_edges": approved,
                                       "unconfirmed_edges": unconfirmed})
    assert sum(len(row["source_feature_ids"]) for row in paths) == 4
    assert len(paths) == 3
    assert {tuple(row["source_feature_ids"]) for row in paths} == {
        ("GH:1990:00001", "GH:2000:00001"),
        ("GH:1990:00002",),
        ("GH:2000:00002",),
    }
    assert graph["ambiguous_edge_count"] == 1
    assert len(graph["ambiguity_records"]) == 1
    assert graph["ambiguity_records"][0]["identity_assigned"] is False


def test_approved_candidate_branch_fails_closed(monkeypatch):
    monkeypatch.setattr(poc.linkage, "EPOCHS", (1990, 2000))
    monkeypatch.setattr(poc.linkage, "EXPECTED_FEATURE_COUNTS", {1990: 2, 2000: 2})
    link_doc, edge = _link_doc()
    edges = [
        {**edge, "from_epoch": 1990, "to_epoch": 2000,
         "to_feature_id": "GH:2000:00001"},
        {**edge, "from_epoch": 1990, "to_epoch": 2000,
         "to_feature_id": "GH:2000:00002"},
    ]
    with pytest.raises(ValueError, match="branches"):
        poc._resolve_paths({"linkage": link_doc, "approved_edges": edges,
                           "unconfirmed_edges": []})


def test_vertical_slice_checks_real_five_epoch_path_and_ambiguous_link():
    ids = [f"GH:{epoch}:00001" for epoch in poc.linkage.EPOCHS]
    path = {
        "candidate_path_id": "CANDIDATE_PATH:sample",
        "source_feature_ids": ids,
        "observations": [
            {"epoch": epoch, "feature_id": feature_id,
             "area_m2": 1_000_000.0, "date_iso": f"{epoch}-07-01"}
            for epoch, feature_id in zip(poc.linkage.EPOCHS, ids)
        ],
    }
    path_refs = [f"{left}->{right}" for left, right in zip(ids, ids[1:])]
    ambiguous_ref = "GH:2010:00002->GH:2015:00002"
    ambiguity = {
        "ambiguity_record_id": "AMBIGUOUS_MULTI_LINK:test",
        "source_feature_ids": ["GH:2010:00002"],
        "target_feature_ids": ["GH:2015:00002"],
        "edge_references": [ambiguous_ref],
        "identity_assigned": False,
    }
    inputs = {
        "approved_edges": [{"from_feature_id": ref.split("->")[0],
                             "to_feature_id": ref.split("->")[1]}
                            for ref in path_refs],
        "unconfirmed_edges": [{"from_feature_id": "GH:2010:00002",
                               "to_feature_id": "GH:2015:00002"}],
    }
    graph = {"ambiguity_records": [ambiguity]}

    result = poc._vertical_slice([path], graph, inputs)

    assert result["status"] == "PASS"
    assert result["actual_five_epoch_candidate_path_exercised"] is True
    assert result["actual_ambiguous_multilink_component_exercised"] is True
    assert len(result["example"]["path_feature_ids"]) == 5
    inputs["approved_edges"].append({"from_feature_id": "GH:2010:00002",
                                     "to_feature_id": "GH:2015:00002"})
    with pytest.raises(ValueError, match="promoted or lost"):
        poc._vertical_slice([path], graph, inputs)


def test_area_conversion_preserves_error_raw_and_never_uses_it(monkeypatch):
    monkeypatch.setattr(poc.linkage, "EPOCHS", (1990, 2000))
    feature = _feature(1990, 1, area=0.25, error=25_652.0, day="1991/10/16")
    rows = poc._path_observations([feature["feature_id"]],
        {feature["feature_id"]: feature}, {feature["feature_id"]: 1990})
    first = rows[0]
    assert first["area_m2"] == pytest.approx(250_000.0)
    assert first["area_error_raw"] == 25_652.0
    assert first["area_error_used_in_model"] is False
    assert first["date_iso"] == "1991-10-16"
    assert rows[1]["source_feature_present"] is False


def test_trajectory_features_use_actual_adjacent_dates_without_bridging(monkeypatch):
    monkeypatch.setattr(poc.linkage, "EPOCHS", (1990, 2000, 2010, 2015, 2020))
    obs = []
    for epoch, area, day in zip(poc.linkage.EPOCHS,
                                (1, 2, 4, 4, 8),
                                ("1991-10-16", "2001-11-14", "2010-08-13",
                                 "2016-07-01", "2019-06-02")):
        obs.append({"epoch": epoch, "area_m2": area * 1_000_000,
                    "date_iso": day})
    vector = poc._trajectory_vector({"observations": obs})
    assert vector is not None
    assert len(vector) == 4
    assert vector[0] == pytest.approx(np.log(2) / ((date_days("2001-11-14", "1991-10-16")) / 365.2425))
    obs[2]["date_iso"] = None
    assert poc._trajectory_vector({"observations": obs}) is None


def date_days(a: str, b: str) -> int:
    from datetime import date
    return (date.fromisoformat(a) - date.fromisoformat(b)).days


def _synthetic_trajectory_doc():
    trajectories = []
    rng = np.random.default_rng(71)
    for group, region in ((-1.0, "Western Himalaya"), (1.0, "Central Himalaya")):
        for i in range(30):
            vector = (rng.normal(group, 0.03, 4)).tolist()
            trajectories.append({
                "candidate_path_id": f"path-{group}-{i}",
                "cluster_evaluation_eligible": True,
                "area_change_rate_features": vector,
                "observations": [{"epoch": 1990, "region_source": region}],
            })
    return {"trajectories": trajectories}


def test_frozen_contract_has_numeric_reproducibility_threshold(monkeypatch):
    monkeypatch.setattr(poc, "RESAMPLES", 20)
    plan = poc.frozen_plan(_synthetic_trajectory_doc(), "a" * 64, "c" * 64)
    criteria = plan["stability_design"]["numeric_reproducible_structure_criterion"]
    assert criteria["every_full_sample_cluster_mean_best_match_jaccard_min"] == 0.75
    assert criteria["resamples_selecting_full_sample_k_fraction_min"] == 0.80
    assert criteria["jaccard_mean_denominator"] == 20
    assert plan["not_run_or_not_authorized"]["event_label_association"] == "DORMANT_SEPARATE_BRANCH"
    assert plan["features"]["error_field_used"] is False


def test_stability_evaluation_is_deterministic_and_honors_numeric_gate(monkeypatch):
    monkeypatch.setattr(poc, "RESAMPLES", 24)
    monkeypatch.setattr(poc, "JACCARD_MIN", 0.75)
    monkeypatch.setattr(poc, "K_SELECTION_FRACTION_MIN", 0.80)
    trajectories = _synthetic_trajectory_doc()
    plan = poc.frozen_plan(trajectories, "b" * 64, "d" * 64)
    first = poc.evaluate(trajectories, plan, resamples=24)
    second = poc.evaluate(trajectories, plan, resamples=24)
    assert first == second
    assert first["stability"]["resamples_completed"] == 24
    assert all(isinstance(value, bool) for value in first["stability"]["checks"].values())
    assert first["event_association_branch"] == "DORMANT"
    assert all(row["mean_best_match_jaccard"] >= 0.75
               for row in first["stability"]["clusters"])


def test_write_once_publication_refuses_different_successor(tmp_path):
    path = tmp_path / "artifact.json"
    first = {"schema": "TEST", "value": 1}
    digest = poc._publish_or_match(path, first)
    assert poc.verify_sidecar(path) == digest
    assert poc._publish_or_match(path, first) == digest
    with pytest.raises(FileExistsError, match="different content"):
        poc._publish_or_match(path, {"schema": "TEST", "value": 2})


def test_unlisted_readme_is_still_bound_to_exact_archive_member(tmp_path, monkeypatch):
    bundle = tmp_path / "observation-inventory-figshare"
    bundle.mkdir()
    extracted = bundle / "docs" / "Readme.txt"
    extracted.parent.mkdir()
    raw = b"Area in km^2; Error metadata requires review.\n"
    extracted.write_bytes(raw)
    archive_path = bundle / "Glacial_Lake_Inventory.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("docs/Readme.txt", raw)
    unrelated = hashlib.sha256(b"other").hexdigest()
    (bundle / "SHA256SUMS.txt").write_text(f"{unrelated}  other.bin\n")
    monkeypatch.setattr(poc, "INTAKE_ROOT", tmp_path)

    result = poc._safe_relative_member_digest("docs/Readme.txt")

    assert result["sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["member_manifest_binding"] == "ABSENT_ARCHIVE_BYTES_VERIFIED"


def test_frozen_plan_hash_matches_write_once_json_encoding(tmp_path):
    plan = {"schema": "PLAN", "threshold": 0.75}
    path = tmp_path / "plan.json"
    from p5_safe_io import write_once_json
    write_once_json(path, plan)
    assert poc._json_sha256(plan) == hashlib.sha256(path.read_bytes()).hexdigest()


def test_numeric_missingness_is_not_coerced_to_zero():
    assert poc._numeric(None) is None
    assert poc._numeric("not-a-number") is None
    assert poc._numeric(float("nan")) is None
    assert poc._numeric(0.0) == 0.0
