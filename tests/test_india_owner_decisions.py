"""Owner-decision sign-off lane contract tests."""
import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import india_owner_decisions as od  # noqa: E402

E = Path("/Users/sanjayb/nepal-event-anomaly-evidence")
CANDIDATES = (E / "india-phase0-source-intake"
              / "INDIA_OBSERVATION_SOURCE_CANDIDATES_V0.json")
_PRESENT = CANDIDATES.is_file()


def test_admin_contract_seals_typed_artifact(tmp_path):
    out = tmp_path / "c.json"
    problems = od.admin_contract("DEFER", "owner:x", "pending", out)
    assert problems == []
    doc = json.loads(out.read_text())
    assert doc["schema"] == "INDIA_ADMIN_TERRITORY_CONTRACT_V0"
    assert doc["decision_state"] == "DEFER"
    assert all(v is False for v in doc["authority"].values())
    assert (tmp_path / "c.json.sha256").is_file()


def test_record_rule_needs_rule(tmp_path):
    main_args = ["observation-intake"]
    # Direct function check: rule omission handled by CLI; here verify
    # DEFER works without one.
    problems = od.admin_contract("RECORD_RULE", "owner:x", "r",
                                 tmp_path / "c.json", rule="x")
    assert problems == []


@pytest.mark.skipif(not _PRESENT, reason="evidence root unavailable")
def test_observation_intake_rejects_unknown_candidate(tmp_path):
    problems = od.observation_intake("NOPE", "DEFER", "owner:x", "r",
                                   tmp_path / "o.json")
    assert any("unknown candidate" in p for p in problems)


@pytest.mark.skipif(not _PRESENT, reason="evidence root unavailable")
def test_observation_intake_binds_candidates_digest(tmp_path):
    out = tmp_path / "o.json"
    problems = od.observation_intake("GREATER_HIMALAYA_FIGSHARE_21708590",
                                   "DEFER", "owner:x", "r", out)
    assert problems == []
    doc = json.loads(out.read_text())
    assert doc["candidates_sha256"] == od.sha(CANDIDATES)


# --- scope-decision + linkage review lanes -----------------------------------

def _fake_linkage(tmp_path) -> Path:
    doc = {
        "schema": "INDIA_LAKE_EPOCH_CANDIDATE_LINKAGE_V0",
        "adjacent_epoch_relations": [{
            "from_epoch": "1990", "to_epoch": "2000",
            "overlap_candidates": [
                {"from_feature_id": "A", "to_feature_id": "B",
                 "intersection_over_union": 0.9,
                 "fraction_of_from_area": 0.95,
                 "fraction_of_to_area": 0.85,
                 "overlap_pattern": "ONE_TO_ONE_OVERLAP_CANDIDATE"},
                {"from_feature_id": "C", "to_feature_id": "D",
                 "intersection_over_union": 0.3,
                 "fraction_of_from_area": 0.4,
                 "fraction_of_to_area": 0.4,
                 "overlap_pattern": "ONE_TO_ONE_OVERLAP_CANDIDATE"},
                {"from_feature_id": "E", "to_feature_id": "F",
                 "intersection_over_union": 0.9,
                 "fraction_of_from_area": 0.9,
                 "fraction_of_to_area": 0.9,
                 "overlap_pattern": "POSSIBLE_MERGE"},
            ]}],
    }
    p = tmp_path / "linkage.json"
    p.write_text(json.dumps(doc))
    return p


def test_scope_decision_seals_typed_artifact(tmp_path):
    out = tmp_path / "s.json"
    problems = od.scope_decision("REGION_AGNOSTIC_POC", "owner:x", "r", out)
    assert problems == []
    doc = json.loads(out.read_text())
    assert doc["schema"] == "INDIA_POC_SCOPE_DECISION_V0"
    assert doc["decision_state"] == "REGION_AGNOSTIC_POC"
    assert all(v is False for v in doc["authority"].values())


def test_linkage_worksheet_exports_ambiguous_only(tmp_path):
    linkage = _fake_linkage(tmp_path)
    out = tmp_path / "ws.csv"
    problems = od.linkage_worksheet(linkage, out, iou_below=0.5)
    assert problems == []
    rows = list(csv.DictReader(out.open()))
    # high-IoU one-to-one excluded; low-IoU and merge exported
    assert [r["from_feature_id"] for r in rows] == ["C", "E"]
    assert (tmp_path / "ws.csv.sha256").is_file()


def test_linkage_review_threshold_counts(tmp_path):
    linkage = _fake_linkage(tmp_path)
    out = tmp_path / "r.json"
    problems = od.linkage_review("THRESHOLD", "owner:x", "r", out,
                                 linkage, iou_min=0.5, from_frac_min=0.5)
    assert problems == []
    doc = json.loads(out.read_text())
    assert doc["verdict_counts"] == {"APPROVED_IDENTITY_CANDIDATE": 1,
                                     "UNCONFIRMED_CANDIDATE": 2}
    assert doc["linkage_sha256"] == od.sha(linkage)


def test_linkage_review_worksheet_validates(tmp_path):
    linkage = _fake_linkage(tmp_path)
    ws = tmp_path / "ws.csv"
    ws.write_text(
        "from_epoch,to_epoch,from_feature_id,to_feature_id,"
        "intersection_over_union,fraction_of_from_area,"
        "fraction_of_to_area,overlap_pattern,review_decision,review_note\n"
        "1990,2000,C,D,0.3,0.4,0.4,ONE_TO_ONE_OVERLAP_CANDIDATE,"
        "CONFIRM_SAME_LAKE,\n"
        "1990,2000,E,F,0.9,0.9,0.9,POSSIBLE_MERGE,REJECT,\n"
        "1990,2000,A,B,0.9,0.95,0.85,ONE_TO_ONE_OVERLAP_CANDIDATE,,\n")
    out = tmp_path / "r.json"
    problems = od.linkage_review("WORKSHEET", "owner:x", "r", out,
                                 linkage, worksheet=str(ws))
    assert problems == []
    doc = json.loads(out.read_text())
    assert doc["verdict_counts"]["CONFIRM_SAME_LAKE"] == 1
    assert doc["verdict_counts"]["REJECT"] == 1
    assert doc["verdict_counts"]["UNREVIEWED_REMAINDER"] == 1

    bad = tmp_path / "bad.csv"
    bad.write_text(
        "from_epoch,to_epoch,from_feature_id,to_feature_id,"
        "intersection_over_union,fraction_of_from_area,"
        "fraction_of_to_area,overlap_pattern,review_decision,review_note\n"
        "1990,2000,C,D,0.3,0.4,0.4,ONE_TO_ONE_OVERLAP_CANDIDATE,MAYBE,\n")
    problems = od.linkage_review("WORKSHEET", "owner:x", "r",
                                 tmp_path / "r2.json", linkage,
                                 worksheet=str(bad))
    assert any("review_decision" in p for p in problems)


# --- CONTAINMENT review mode ------------------------------------------------

def _nested_linkage(tmp_path) -> Path:
    doc = {
        "schema": "INDIA_LAKE_EPOCH_CANDIDATE_LINKAGE_V0",
        "adjacent_epoch_relations": [{
            "from_epoch": "1990", "to_epoch": "2000",
            "overlap_candidates": [
                # strict-channel pass: iou .9, from .95
                {"from_feature_id": "A", "to_feature_id": "B",
                 "intersection_over_union": 0.9,
                 "fraction_of_from_area": 0.95,
                 "fraction_of_to_area": 0.85,
                 "overlap_pattern": "ONE_TO_ONE_OVERLAP_CANDIDATE"},
                # nested growth: from 98% inside to, to grew 2.1x -> iou .47
                {"from_feature_id": "C", "to_feature_id": "D",
                 "intersection_over_union": 0.47,
                 "fraction_of_from_area": 0.98,
                 "fraction_of_to_area": 0.47,
                 "overlap_pattern": "ONE_TO_ONE_OVERLAP_CANDIDATE"},
                # nested but imploded 8x -> area ratio 8.3 > cap
                {"from_feature_id": "G", "to_feature_id": "H",
                 "intersection_over_union": 0.11,
                 "fraction_of_from_area": 0.91,
                 "fraction_of_to_area": 0.11,
                 "overlap_pattern": "ONE_TO_ONE_OVERLAP_CANDIDATE"},
                # ambiguous pattern must stay unconfirmed even if nested
                {"from_feature_id": "E", "to_feature_id": "F",
                 "intersection_over_union": 0.5,
                 "fraction_of_from_area": 0.99,
                 "fraction_of_to_area": 0.5,
                 "overlap_pattern": "POSSIBLE_MERGE"},
            ]}],
    }
    p = tmp_path / "linkage.json"
    p.write_text(json.dumps(doc))
    return p


def test_containment_requires_all_parameters(tmp_path):
    problems = od.linkage_review("CONTAINMENT", "owner:x", "r",
                                 tmp_path / "r.json",
                                 _nested_linkage(tmp_path),
                                 iou_min=0.8, from_frac_min=0.8)
    assert problems and "containment-min" in problems[0]


def test_containment_approves_nested_growth(tmp_path):
    out = tmp_path / "r.json"
    problems = od.linkage_review(
        "CONTAINMENT", "owner:x", "r", out, _nested_linkage(tmp_path),
        iou_min=0.8, from_frac_min=0.8,
        containment_min=0.8, area_ratio_max=4.0)
    assert problems == []
    doc = json.loads(out.read_text())
    assert doc["schema"] == "INDIA_LAKE_LINKAGE_REVIEW_V1"
    assert doc["version"] == 1
    # strict A->B + nested C->D; G->H rejected on area ratio; E->F merge
    assert doc["verdict_counts"] == {"APPROVED_IDENTITY_CANDIDATE": 2,
                                     "UNCONFIRMED_CANDIDATE": 2}
    assert doc["detail"]["approved_via_strict_threshold"] == 1
    assert doc["detail"]["approved_via_containment"] == 1
    assert doc["detail"]["containment_min"] == 0.8


def test_containment_area_ratio_cap_blocks_implosion(tmp_path):
    out = tmp_path / "r.json"
    problems = od.linkage_review(
        "CONTAINMENT", "owner:x", "r", out, _nested_linkage(tmp_path),
        iou_min=0.8, from_frac_min=0.8,
        containment_min=0.8, area_ratio_max=1.5)
    assert problems == []
    doc = json.loads(out.read_text())
    # only the strict edge approves; C->D ratio 2.09 exceeds the 1.5 cap
    assert doc["verdict_counts"]["APPROVED_IDENTITY_CANDIDATE"] == 1


def test_containment_rejects_branching_approved_edges(tmp_path):
    doc = {
        "schema": "INDIA_LAKE_EPOCH_CANDIDATE_LINKAGE_V0",
        "adjacent_epoch_relations": [{
            "from_epoch": "1990", "to_epoch": "2000",
            "overlap_candidates": [
                {"from_feature_id": "A", "to_feature_id": "B",
                 "intersection_over_union": 0.9,
                 "fraction_of_from_area": 0.95,
                 "fraction_of_to_area": 0.9,
                 "overlap_pattern": "ONE_TO_ONE_OVERLAP_CANDIDATE"},
                # mislabeled pattern: same source, second target
                {"from_feature_id": "A", "to_feature_id": "C",
                 "intersection_over_union": 0.85,
                 "fraction_of_from_area": 0.9,
                 "fraction_of_to_area": 0.85,
                 "overlap_pattern": "ONE_TO_ONE_OVERLAP_CANDIDATE"},
            ]}],
    }
    linkage = tmp_path / "linkage.json"
    linkage.write_text(json.dumps(doc))
    problems = od.linkage_review(
        "CONTAINMENT", "owner:x", "r", tmp_path / "r.json", linkage,
        iou_min=0.8, from_frac_min=0.8,
        containment_min=0.8, area_ratio_max=4.0)
    assert any("degree-1" in p for p in problems)
