"""V3 trajectory engine for the owner-sealed CONTAINMENT linkage review.

V1 (``hma_lake_trajectory_poc``) is imported unchanged as the deterministic
helper engine; V2 is imported only for its small coverage-flag and
inventory-domain contracts.  This engine binds the sealed
INDIA_LAKE_LINKAGE_REVIEW_V1 decision (mode CONTAINMENT: strict threshold OR
bounded nested-containment channel), recomputes every edge disposition
independently from the raw candidate linkage report, resolves degree-1
candidate path chains with the V1 chain semantics, and emits write-once V3
preflight and trajectory artifacts.

Containment-admitted edges are candidates, not physical identities.  No
event, territory, risk, causal, or operational claim is made; all authority
flags remain false.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import hma_lake_trajectory_poc as v1  # noqa: E402
import hma_lake_trajectory_poc_v2 as v2  # noqa: E402

OUTPUT_ROOT = v1.INTAKE_ROOT / "hma-lake-trajectory-poc-v3"
REVIEW_V1_PATH = v1.INTAKE_ROOT / "INDIA_LAKE_LINKAGE_REVIEW_V1.json"
SCHEMA_PREFLIGHT = "HMA_LAKE_TRAJECTORY_PREFLIGHT_V3"
SCHEMA_TRAJECTORIES = "HMA_LAKE_TRAJECTORIES_V3"
SCHEMA_REVIEW = "INDIA_LAKE_LINKAGE_REVIEW_V1"
APPROVED = v1.APPROVED
UNCONFIRMED = v1.UNCONFIRMED
EVENT_BRANCH_STATUS = "DORMANT"
DECISION_NOTE = ("REVISED_COHORT_NESTED_CANDIDATES: containment-admitted "
                 "edges are candidates, not identities.")
LINK_RULE: dict[str, Any] = {
    "mode": "CONTAINMENT",
    "params": {
        "iou_min": 0.8,
        "from_frac_min": 0.8,
        "containment_min": 0.8,
        "area_ratio_max": 4.0,
    },
}
REFERENCE_COMPLETE_PATH_COUNT = 5087  # owner expectation; recorded, never forced


def _sha256_file(path: Path) -> str:
    return v1.sha256_file(path)


def _edge_channels(edge: dict[str, Any]) -> tuple[bool, bool, float | None]:
    """Independently recompute the dual-channel CONTAINMENT approval.

    Returns ``(strict_ok, containment_ok, implied_area_ratio)`` where the
    implied ratio is ``max(from_frac/to_frac, to_frac/from_frac)`` derived
    from the shared intersection area, or ``None`` when either fraction is
    missing or non-positive.
    """
    strict_ok = containment_ok = False
    implied_ratio: float | None = None
    ff = v1._numeric(edge.get("fraction_of_from_area"))
    tf = v1._numeric(edge.get("fraction_of_to_area"))
    if ff is not None and tf is not None and ff > 0 and tf > 0:
        implied_ratio = max(ff / tf, tf / ff)
    if edge.get("overlap_pattern") == "ONE_TO_ONE_OVERLAP_CANDIDATE":
        params = LINK_RULE["params"]
        iou = v1._numeric(edge.get("intersection_over_union"))
        strict_ok = (iou is not None and iou >= params["iou_min"]
                     and ff is not None and ff >= params["from_frac_min"])
        containment_ok = (implied_ratio is not None
                          and max(ff, tf) >= params["containment_min"]
                          and implied_ratio <= params["area_ratio_max"])
    return strict_ok, containment_ok, implied_ratio


def _assert_approved_degree_one(approved_edges: list[dict[str, Any]]) -> None:
    """Belt-and-suspenders degree<=1 check on the approved candidate graph."""
    out_degree = Counter(edge["from_feature_id"] for edge in approved_edges)
    in_degree = Counter(edge["to_feature_id"] for edge in approved_edges)
    if any(degree > 1 for degree in out_degree.values()) \
            or any(degree > 1 for degree in in_degree.values()):
        raise ValueError(
            "containment-approved candidate graph exceeds degree 1; "
            "refusing path promotion")


def _load_and_validate_inputs(*, replay_source: bool) -> dict[str, Any]:
    link_doc, link_sha = v1._read_bound_json(v1.LINKAGE_PATH)
    review_doc, review_sha = v1._read_bound_json(REVIEW_V1_PATH)
    scope_doc, scope_sha = v1._read_bound_json(v1.SCOPE_PATH)
    admin_doc, admin_sha = v1._read_bound_json(v1.ADMIN_CONTRACT_PATH)

    if link_doc.get("schema") != v1.linkage.SCHEMA \
            or link_doc.get("status") != "CANDIDATE_LINKAGE_ONLY":
        raise ValueError("candidate linkage artifact schema/status mismatch")
    v1._false_authority(link_doc, "linkage artifact")
    v1._false_authority(review_doc, "linkage review V1")
    v1._false_authority(scope_doc, "scope decision")
    v1._false_authority(admin_doc, "territory contract")
    if review_doc.get("schema") != SCHEMA_REVIEW \
            or review_doc.get("version") != 1 \
            or review_doc.get("linkage_sha256") != link_sha \
            or review_doc.get("linkage_artifact") != v1.LINKAGE_PATH.name:
        raise ValueError("linkage review V1 does not bind the exact candidate report")
    detail = review_doc.get("detail")
    if not isinstance(detail, dict) or detail.get("mode") != LINK_RULE["mode"]:
        raise ValueError("linkage review V1 is not a CONTAINMENT-mode decision")
    if {key: detail.get(key) for key in LINK_RULE["params"]} != LINK_RULE["params"]:
        raise ValueError(
            "linkage review V1 containment parameters differ from the sealed contract")
    if scope_doc.get("schema") != "INDIA_POC_SCOPE_DECISION_V0" \
            or scope_doc.get("decision_state") != "REGION_AGNOSTIC_POC":
        raise ValueError("only the sealed REGION_AGNOSTIC_POC scope is supported")
    if admin_doc.get("schema") != "INDIA_ADMIN_TERRITORY_CONTRACT_V0" \
            or admin_doc.get("decision_state") != "DEFER":
        raise ValueError("territory contract must remain DEFER")

    if replay_source:
        # Existing verifier checks source archive/member bytes, all report
        # rows, the current generator digest, and exact deterministic replay.
        v1.source_verifier.verify_report(v1.LINKAGE_PATH, v1.EVIDENCE_ROOT,
                                         v1.REGISTER_PATH, v1.REGISTRY_PATH)

    # Recompute the owner's dual-channel disposition from every candidate edge.
    approved: list[dict[str, Any]] = []
    unconfirmed: list[dict[str, Any]] = []
    verdict_counts: Counter[str] = Counter()
    channel_counts: Counter[str] = Counter()
    for relation in link_doc.get("adjacent_epoch_relations", []):
        left_epoch, right_epoch = relation.get("from_epoch"), relation.get("to_epoch")
        for edge in relation.get("overlap_candidates", []):
            if edge.get("relation") != "POSITIVE_AREA_OVERLAP_CANDIDATE":
                continue
            strict_ok, containment_ok, implied_ratio = _edge_channels(edge)
            channel = ("STRICT" if strict_ok
                       else "CONTAINMENT" if containment_ok else None)
            row = {
                "from_epoch": left_epoch,
                "to_epoch": right_epoch,
                "from_feature_id": edge["from_feature_id"],
                "to_feature_id": edge["to_feature_id"],
                "overlap_pattern": edge.get("overlap_pattern"),
                "relation": edge.get("relation"),
                "intersection_area_m2": edge.get("intersection_area_m2"),
                "intersection_over_union": edge.get("intersection_over_union"),
                "fraction_of_from_area": edge.get("fraction_of_from_area"),
                "fraction_of_to_area": edge.get("fraction_of_to_area"),
                "implied_area_ratio": implied_ratio,
                "strict_channel": strict_ok,
                "containment_channel": containment_ok,
                "approval_channel": channel,
                "identity_claim": False,
                "disposition": APPROVED if channel else UNCONFIRMED,
            }
            verdict_counts[row["disposition"]] += 1
            if channel is not None:
                channel_counts[channel] += 1
            (approved if channel else unconfirmed).append(row)

    strict_n = channel_counts["STRICT"]
    containment_n = channel_counts["CONTAINMENT"]
    if review_doc.get("total_pairs") != sum(verdict_counts.values()) \
            or review_doc.get("verdict_counts") != dict(verdict_counts):
        raise ValueError(
            "linkage review V1 aggregate counts do not match recomputed edges")
    if detail.get("approved_via_strict_threshold") != strict_n \
            or detail.get("approved_via_containment") != containment_n:
        raise ValueError(
            "sealed review V1 channel counts do not match recomputed edges")
    _assert_approved_degree_one(approved)
    return {
        "linkage": link_doc,
        "linkage_sha256": link_sha,
        "review": review_doc,
        "review_sha256": review_sha,
        "scope": scope_doc,
        "scope_sha256": scope_sha,
        "admin_contract": admin_doc,
        "admin_contract_sha256": admin_sha,
        "approved_edges": approved,
        "unconfirmed_edges": unconfirmed,
        "verdict_counts": dict(verdict_counts),
        "channel_counts": {"STRICT": strict_n, "CONTAINMENT": containment_n},
        "source_replay": "EXACT_MATCH" if replay_source else "NOT_RUN_THIS_COMMAND",
    }


def _resolve_paths(inputs: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """V1 chain resolution over the containment-approved candidate edges."""
    _assert_approved_degree_one(inputs["approved_edges"])
    return v1._resolve_paths(inputs)


def _earliest_covariates(source_feature_ids: list[str],
                         features: dict[str, dict[str, Any]],
                         epochs: dict[str, int]) -> dict[str, Any]:
    """Pre-outcome stratifiers from the earliest observed epoch's feature."""
    first = min(source_feature_ids, key=lambda fid: (epochs[fid], fid))
    attrs = features[first].get("attributes")
    if not isinstance(attrs, dict):
        raise ValueError(f"earliest observed feature lacks attributes: {first}")
    region_value = attrs.get("Region")
    region = (str(region_value).strip()
              if region_value is not None else "") or "UNKNOWN"
    return {
        "type_source": attrs.get("Type"),
        "elevation_m": v1._numeric(attrs.get("Elevation")),
        "region_source": region,
        "pr": attrs.get("PR"),
        "mg_id1": attrs.get("MG_ID1"),
        "mg_id2": attrs.get("MG_ID2"),
    }


def _coverage_flags(trajectory_doc: dict[str, Any]) -> dict[str, Any]:
    """V2 coverage-flag contract: one explicit flag per path-epoch slot."""
    return v2._coverage_flags(trajectory_doc)


def _vertical_slice(paths: list[dict[str, Any]], graph: dict[str, Any],
                    inputs: dict[str, Any]) -> dict[str, Any]:
    """V2 slice contract plus an actual containment-channel edge exercise."""
    base = v1._vertical_slice(paths, graph, inputs)
    edge_by_ref = {
        f"{edge['from_feature_id']}->{edge['to_feature_id']}": edge
        for edge in inputs["approved_edges"]
    }
    containment_paths = sorted(
        (path for path in paths
         if any(edge_by_ref[ref]["approval_channel"] == "CONTAINMENT"
                for ref in path["approved_candidate_edge_ids"])),
        key=lambda path: path["candidate_path_id"])
    if not containment_paths:
        raise ValueError(
            "vertical slice cannot find a containment-channel approved edge in a path")
    chosen = containment_paths[0]
    edge_ref = sorted(ref for ref in chosen["approved_candidate_edge_ids"]
                      if edge_by_ref[ref]["approval_channel"] == "CONTAINMENT")[0]
    edge = edge_by_ref[edge_ref]
    ratio = edge["implied_area_ratio"]
    bound = LINK_RULE["params"]["area_ratio_max"]
    if edge["strict_channel"] or not edge["containment_channel"] \
            or ratio is None or ratio > bound:
        raise ValueError(
            "selected containment-channel edge does not satisfy the sealed bounds")
    example = dict(base["example"])
    example["containment_channel_edge"] = {
        "candidate_path_id": chosen["candidate_path_id"],
        "edge_reference": edge_ref,
        "from_epoch": edge["from_epoch"],
        "to_epoch": edge["to_epoch"],
        "strict_channel": False,
        "containment_channel": True,
        "intersection_over_union": edge["intersection_over_union"],
        "fraction_of_from_area": edge["fraction_of_from_area"],
        "fraction_of_to_area": edge["fraction_of_to_area"],
        "implied_area_ratio": ratio,
        "area_ratio_bound": bound,
        "implied_area_ratio_within_bound": ratio <= bound,
        "channel_semantics": ("containment-admitted candidate link only; "
                              "not a physical lake identity"),
    }
    serialized = json.dumps(example, sort_keys=True, separators=(",", ":"))
    return {
        "status": "PASS",
        "link_rule_mode": LINK_RULE["mode"],
        "actual_five_epoch_candidate_path_exercised": True,
        "actual_ambiguous_multilink_component_exercised": True,
        "actual_containment_only_channel_edge_exercised": True,
        "all_path_links_threshold_approved_candidates": True,
        "ambiguous_links_remain_unconfirmed": True,
        "containment_admitted_edges_are_candidates_not_identities": True,
        "source_features_in_slice": base["source_features_in_slice"],
        "deterministic_slice_sha256": hashlib.sha256(serialized.encode()).hexdigest(),
        "example": example,
    }


def _verified_v2_bindings() -> dict[str, str]:
    expected_schemas = {
        "preflight": v2.SCHEMA_PREFLIGHT,
        "trajectories": v2.SCHEMA_TRAJECTORIES,
        "higher_frequency": v2.SCHEMA_HIGH_FREQUENCY,
        "plan": v2.SCHEMA_PLAN,
        "result": v2.SCHEMA_RESULT,
    }
    bindings: dict[str, str] = {}
    for key, path in v2._artifact_paths(v2.OUTPUT_ROOT).items():
        document, digest = v1._read_bound_json(path)
        if document.get("schema") != expected_schemas[key]:
            raise ValueError(f"unexpected V2 predecessor schema: {path.name}")
        bindings[path.name] = digest
    if len(bindings) != len(expected_schemas):
        raise ValueError("V2 predecessor artifact set is incomplete")
    return bindings


def _artifact_paths(output_root: Path) -> dict[str, Path]:
    return {
        "preflight": output_root / f"{SCHEMA_PREFLIGHT}.json",
        "trajectories": output_root / f"{SCHEMA_TRAJECTORIES}.json",
    }


def _prepare(output_root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    v2_bindings = _verified_v2_bindings()
    v0_bindings = v1._prior_artifact_digests()
    inputs = _load_and_validate_inputs(replay_source=True)
    base_preflight, base_trajectories, _ = v1._preflight_and_trajectory(
        inputs, v0_bindings)
    preflight = copy.deepcopy(base_preflight)
    trajectory_doc = copy.deepcopy(base_trajectories)

    # Per-path stratifiers and channel provenance over V1 path rows.
    features, epochs = v1._feature_index(inputs["linkage"])
    channel_by_ref = {
        f"{edge['from_feature_id']}->{edge['to_feature_id']}": edge["approval_channel"]
        for edge in inputs["approved_edges"]
    }
    for trajectory in trajectory_doc["trajectories"]:
        trajectory["earliest_observation_covariates"] = _earliest_covariates(
            trajectory["source_feature_ids"], features, epochs)
        trajectory["containment_only_edge_ids"] = [
            ref for ref in trajectory["approved_candidate_edge_ids"]
            if channel_by_ref.get(ref) == "CONTAINMENT"]

    coverage = _coverage_flags(trajectory_doc)
    domain = v2._inventory_domain(trajectory_doc)
    paths, graph = _resolve_paths(inputs)
    vertical_slice = _vertical_slice(paths, graph, inputs)

    review_doc = inputs["review"]
    channel_counts = inputs["channel_counts"]
    code_binding = {
        "analysis_code_sha256": _sha256_file(Path(__file__)),
        "engine_code_sha256": _sha256_file(Path(v1.__file__)),
        "v2_wrapper_code_sha256": _sha256_file(Path(v2.__file__)),
    }
    link_rule = {
        "mode": LINK_RULE["mode"],
        "params": dict(LINK_RULE["params"]),
        "rule_text": review_doc["detail"].get("rule_text"),
    }
    channel_edge_counts = {
        "strict_approved": channel_counts["STRICT"],
        "containment_only_approved": channel_counts["CONTAINMENT"],
        "approved_total": len(inputs["approved_edges"]),
        "unconfirmed_total": len(inputs["unconfirmed_edges"]),
    }
    expected_counts = {
        "linkage_review_sha256": inputs["review_sha256"],
        "total_pairs": review_doc["total_pairs"],
        "verdict_counts": dict(review_doc["verdict_counts"]),
        "approved_via_strict_threshold": review_doc["detail"]["approved_via_strict_threshold"],
        "approved_via_containment": review_doc["detail"]["approved_via_containment"],
        "recompute_status": "EXACT_MATCH",
    }

    trajectory_doc.update({
        "schema": SCHEMA_TRAJECTORIES,
        "version": 3,
        "coverage_flag_audit": coverage,
        "inventory_domain": domain,
        "link_rule": link_rule,
        "channel_edge_counts": channel_edge_counts,
        "candidate_link_rule": {
            "mode": LINK_RULE["mode"],
            "params": dict(LINK_RULE["params"]),
            "accepted_review_disposition": APPROVED,
            "approved_channel_counts": {
                "strict": channel_counts["STRICT"],
                "containment_only": channel_counts["CONTAINMENT"],
            },
            "unconfirmed_links": "preserved separately; never used to join trajectory paths",
            "containment_admitted_edges_are_candidates_not_identities": True,
            "identity_claim": False,
        },
        "event_association_branch": EVENT_BRANCH_STATUS,
        "successor_of": v2_bindings,
        **code_binding,
    })
    trajectory_doc["inputs"]["linkage_review"] = {
        "path": REVIEW_V1_PATH.name, "sha256": inputs["review_sha256"]}

    preflight.update({
        "schema": SCHEMA_PREFLIGHT,
        "version": 3,
        "coverage_flag_audit": coverage,
        "inventory_domain": domain,
        "link_rule": link_rule,
        "channel_edge_counts": channel_edge_counts,
        "expected_counts_from_sealed_review": expected_counts,
        "vertical_slice": vertical_slice,
        "event_association_branch": EVENT_BRANCH_STATUS,
        "successor_of": v2_bindings,
        "decision_note": DECISION_NOTE,
        **code_binding,
    })
    return preflight, trajectory_doc, v2_bindings


def _publish_or_match(path: Path, document: dict[str, Any]) -> str:
    return v1._publish_or_match(path, document)


def _load_existing_exact(path: Path, expected: dict[str, Any]) -> str:
    document, digest = v1._read_bound_json(path)
    canonical = (json.dumps(expected, indent=2, sort_keys=True)
                 + "\n").encode("utf-8")
    if path.read_bytes() != canonical or document != expected:
        raise ValueError(
            f"artifact differs from independent recomputation: {path}")
    return digest


def run(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    preflight, trajectory_doc, _ = _prepare(output_root)
    paths = _artifact_paths(output_root)
    preflight_sha = _publish_or_match(paths["preflight"], preflight)
    trajectory_sha = _publish_or_match(paths["trajectories"], trajectory_doc)
    return {
        "status": "HMA_LAKE_TRAJECTORY_POC_V3_COMPLETE",
        "source_replay": "EXACT_MATCH",
        "preflight": preflight["status"],
        "link_rule": preflight["link_rule"],
        "channel_edge_counts": preflight["channel_edge_counts"],
        "candidate_trajectories": trajectory_doc["source_feature_conservation"]
                                                ["candidate_trajectory_count"],
        "candidate_path_length_distribution":
            preflight["candidate_path_length_distribution"],
        "complete_five_epoch_candidate_paths":
            preflight["complete_five_epoch_candidate_paths"],
        "area_date_evaluable_complete_paths":
            preflight["area_date_evaluable_complete_paths"],
        "expected_complete_five_epoch_paths_reference_only":
            REFERENCE_COMPLETE_PATH_COUNT,
        "event_association_branch": EVENT_BRANCH_STATUS,
        "artifacts": {
            key: {"path": str(path), "sha256": digest}
            for key, path, digest in (
                ("preflight", paths["preflight"], preflight_sha),
                ("trajectories", paths["trajectories"], trajectory_sha),
            )
        },
    }


def verify(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    preflight, trajectory_doc, _ = _prepare(output_root)
    paths = _artifact_paths(output_root)
    preflight_sha = _load_existing_exact(paths["preflight"], preflight)
    trajectory_sha = _load_existing_exact(paths["trajectories"], trajectory_doc)
    if preflight["event_association_branch"] != EVENT_BRANCH_STATUS \
            or trajectory_doc["event_association_branch"] != EVENT_BRANCH_STATUS:
        raise ValueError("event-association branch must remain dormant")
    return {
        "status": "HMA_LAKE_TRAJECTORY_V3_VERIFY_OK",
        "source_replay": "EXACT_MATCH",
        "replay": "EXACT_MATCH",
        "preflight_sha256": preflight_sha,
        "trajectory_sha256": trajectory_sha,
        "link_rule_mode": LINK_RULE["mode"],
        "channel_edge_counts": preflight["channel_edge_counts"],
        "complete_five_epoch_candidate_paths":
            preflight["complete_five_epoch_candidate_paths"],
        "event_association_branch": EVENT_BRANCH_STATUS,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("run", "verify"):
        child = sub.add_parser(command)
        child.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    args = parser.parse_args(argv)
    try:
        result = run(args.output_root) if args.command == "run" else verify(args.output_root)
    except (OSError, ValueError, KeyError, TypeError, IndexError,
            json.JSONDecodeError, ArithmeticError) as exc:
        print(f"HMA_LAKE_TRAJECTORY_V3_{args.command.upper()}_BLOCKED: {exc}")
        return 1
    print(result["status"])
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
