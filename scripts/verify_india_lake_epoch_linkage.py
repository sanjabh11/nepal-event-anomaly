"""Fail-closed verifier and deterministic replay for a candidate linkage report."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import india_lake_epoch_linkage as linkage


RELATIONS = {"POSITIVE_AREA_OVERLAP_CANDIDATE", "TOUCH_ONLY_CANDIDATE"}
POINT_STATUSES = {
    "NO_VALID_POLYGONS", "ONE_SPATIAL_CANDIDATE",
    "MULTIPLE_SPATIAL_CANDIDATES", "NO_INTERSECTION_CANDIDATE",
}
EXPECTED_NRSC_ROWS = 2433
FRACTION_ROUNDOFF_TOLERANCE = 1e-9


def _finite_nonnegative(value: Any) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and value >= 0)


def _first_differences(expected: Any, actual: Any, path: str = "$",
                       limit: int = 12) -> list[str]:
    """Return a bounded set of JSON paths where deterministic replay differs."""
    differences: list[str] = []

    def visit(left: Any, right: Any, current: str) -> None:
        if len(differences) >= limit:
            return
        if isinstance(left, dict) and isinstance(right, dict):
            if left.keys() != right.keys():
                differences.append(f"{current}: object keys differ")
                return
            for key in left:
                visit(left[key], right[key], f"{current}.{key}")
                if len(differences) >= limit:
                    return
            return
        if isinstance(left, list) and isinstance(right, list):
            if len(left) != len(right):
                differences.append(f"{current}: list lengths {len(left)} != {len(right)}")
                return
            for index, (left_item, right_item) in enumerate(zip(left, right)):
                visit(left_item, right_item, f"{current}[{index}]")
                if len(differences) >= limit:
                    return
            return
        if left != right:
            differences.append(f"{current}: {left!r} != {right!r}")

    visit(expected, actual, path)
    return differences


def validate_report_document(document: object) -> dict[str, Any]:
    """Validate row coverage, graph references, summaries, and claim ceiling."""
    if not isinstance(document, dict):
        raise ValueError("report must be a JSON object")
    if document.get("schema") != linkage.SCHEMA \
            or document.get("status") != "CANDIDATE_LINKAGE_ONLY":
        raise ValueError("unexpected report schema or status")
    authority = document.get("authority")
    if authority != linkage.AUTHORITY_FLAGS or any(authority.values()):
        raise ValueError("authority flags must be present and false")
    conclusions = document.get("conclusions")
    if not isinstance(conclusions, dict) or not conclusions \
            or any(value is not False for value in conclusions.values()):
        raise ValueError("all conclusion/authority claims must remain false")

    epochs = document.get("epoch_feature_inventory")
    if not isinstance(epochs, list) or len(epochs) != len(linkage.EPOCHS):
        raise ValueError("epoch inventory must contain all five frozen snapshots")
    features_by_epoch: dict[int, dict[str, dict[str, Any]]] = {}
    valid_ids_by_epoch: dict[int, set[str]] = {}
    for profile in epochs:
        if not isinstance(profile, dict):
            raise ValueError("epoch profile must be an object")
        epoch = profile.get("epoch")
        if epoch not in linkage.EXPECTED_FEATURE_COUNTS or epoch in features_by_epoch:
            raise ValueError(f"unexpected or duplicate epoch: {epoch}")
        features = profile.get("features")
        if not isinstance(features, list) \
                or len(features) != linkage.EXPECTED_FEATURE_COUNTS[epoch] \
                or profile.get("feature_count") != len(features):
            raise ValueError(f"epoch {epoch} feature inventory count mismatch")
        by_id: dict[str, dict[str, Any]] = {}
        for ordinal, feature in enumerate(features, 1):
            if not isinstance(feature, dict):
                raise ValueError(f"epoch {epoch} feature must be an object")
            expected_id = f"GH:{epoch}:{ordinal:05d}"
            if feature.get("feature_id") != expected_id \
                    or feature.get("source_feature_ordinal") != ordinal:
                raise ValueError(f"epoch {epoch} feature order/id mismatch")
            if expected_id in by_id:
                raise ValueError(f"duplicate feature id: {expected_id}")
            by_id[expected_id] = feature
        valid_ids = {feature_id for feature_id, feature in by_id.items()
                     if feature.get("geometry_status") == "VALID_POLYGON"}
        excluded = len(by_id) - len(valid_ids)
        if profile.get("valid_polygon_count") != len(valid_ids) \
                or profile.get("excluded_geometry_count") != excluded:
            raise ValueError(f"epoch {epoch} geometry summary mismatch")
        if any(feature.get("geometry_status") not in {
                "VALID_POLYGON", "EXCLUDED_NULL_GEOMETRY",
                "EXCLUDED_EMPTY_GEOMETRY", "EXCLUDED_NON_POLYGON",
                "EXCLUDED_INVALID_GEOMETRY"}
               for feature in by_id.values()):
            raise ValueError(f"epoch {epoch} has unknown geometry status")
        features_by_epoch[epoch] = by_id
        valid_ids_by_epoch[epoch] = valid_ids
    if set(features_by_epoch) != set(linkage.EPOCHS):
        raise ValueError("epoch inventory differs from the frozen epoch set")

    pairs = document.get("adjacent_epoch_relations")
    expected_pairs = list(zip(linkage.EPOCHS, linkage.EPOCHS[1:]))
    if not isinstance(pairs, list) or len(pairs) != len(expected_pairs):
        raise ValueError("adjacent-epoch relation inventory is incomplete")
    pair_summaries = []
    for pair, (left_epoch, right_epoch) in zip(pairs, expected_pairs):
        if not isinstance(pair, dict):
            raise ValueError("adjacent-epoch pair must be an object")
        if pair.get("from_epoch") != left_epoch or pair.get("to_epoch") != right_epoch:
            raise ValueError("adjacent-epoch pair ordering differs from protocol")
        edges = pair.get("overlap_candidates")
        if not isinstance(edges, list):
            raise ValueError("overlap_candidates must be a list")
        edge_keys: set[tuple[str, str]] = set()
        intersecting_left: set[str] = set()
        positive_left_degree: dict[str, int] = {}
        positive_right_degree: dict[str, int] = {}
        for edge in edges:
            if not isinstance(edge, dict):
                raise ValueError("overlap edge must be an object")
            left_id, right_id = edge.get("from_feature_id"), edge.get("to_feature_id")
            if not isinstance(left_id, str) or not isinstance(right_id, str):
                raise ValueError("overlap edge feature ids must be strings")
            key = (left_id, right_id)
            if key in edge_keys:
                raise ValueError(f"duplicate overlap edge: {key}")
            edge_keys.add(key)
            if left_id not in valid_ids_by_epoch[left_epoch] \
                    or right_id not in valid_ids_by_epoch[right_epoch]:
                raise ValueError("overlap edge references missing/excluded feature")
            if edge.get("identity_claim") is not False or edge.get("relation") not in RELATIONS:
                raise ValueError("overlap edge exceeds candidate-only claim ceiling")
            area = edge.get("intersection_area_m2")
            iou = edge.get("intersection_over_union")
            from_fraction = edge.get("fraction_of_from_area")
            to_fraction = edge.get("fraction_of_to_area")
            if not all(_finite_nonnegative(v) for v in (area, iou, from_fraction, to_fraction)):
                raise ValueError("overlap edge has invalid metrics")
            if any(v > 1 + FRACTION_ROUNDOFF_TOLERANCE
                   for v in (iou, from_fraction, to_fraction)):
                raise ValueError(
                    f"overlap fraction exceeds one beyond numeric tolerance: {key}")
            intersecting_left.add(left_id)
            if edge["relation"] == "TOUCH_ONLY_CANDIDATE":
                if area != 0 or iou != 0 \
                        or edge.get("overlap_pattern") != "TOUCH_ONLY":
                    raise ValueError("touch-only edge has positive-area metrics")
            else:
                if area <= 0 or edge.get("overlap_pattern") not in {
                        "ONE_TO_ONE_OVERLAP_CANDIDATE", "POSSIBLE_SPLIT",
                        "POSSIBLE_MERGE", "MANY_TO_MANY_OVERLAP"}:
                    raise ValueError("positive-area overlap edge metrics/pattern invalid")
                positive_left_degree[left_id] = positive_left_degree.get(left_id, 0) + 1
                positive_right_degree[right_id] = positive_right_degree.get(right_id, 0) + 1
        for edge in edges:
            if edge["relation"] != "POSITIVE_AREA_OVERLAP_CANDIDATE":
                continue
            left_degree = positive_left_degree[edge["from_feature_id"]]
            right_degree = positive_right_degree[edge["to_feature_id"]]
            expected_pattern = (
                "MANY_TO_MANY_OVERLAP" if left_degree > 1 and right_degree > 1 else
                "POSSIBLE_SPLIT" if left_degree > 1 else
                "POSSIBLE_MERGE" if right_degree > 1 else
                "ONE_TO_ONE_OVERLAP_CANDIDATE")
            if edge.get("overlap_pattern") != expected_pattern:
                raise ValueError("overlap pattern contradicts recomputed graph degree")
        overlap_count = sum(edge["relation"] == "POSITIVE_AREA_OVERLAP_CANDIDATE"
                            for edge in edges)
        touch_count = sum(edge["relation"] == "TOUCH_ONLY_CANDIDATE"
                          for edge in edges)
        if pair.get("overlap_candidate_count") != overlap_count \
                or pair.get("touch_only_candidate_count") != touch_count:
            raise ValueError("adjacent-epoch edge summaries do not match rows")
        nearest = pair.get("nearest_only_diagnostics")
        if not isinstance(nearest, list):
            raise ValueError("nearest-only diagnostics must be a list")
        if any(not isinstance(item, dict) for item in nearest):
            raise ValueError("nearest-only diagnostic must be an object")
        nearest_from = [item.get("from_feature_id") for item in nearest]
        if len(nearest_from) != len(set(nearest_from)):
            raise ValueError("duplicate nearest-only source feature")
        for item in nearest:
            if item.get("from_feature_id") not in valid_ids_by_epoch[left_epoch] \
                    or item.get("from_feature_id") in intersecting_left:
                raise ValueError("nearest-only diagnostic conflicts with intersection")
            if item.get("relation") != "NEAREST_ONLY_DIAGNOSTIC_NOT_IDENTITY_EVIDENCE" \
                    or not _finite_nonnegative(item.get("distance_m")):
                raise ValueError("nearest-only diagnostic has invalid semantics/distance")
            if not item.get("nearest_feature_ids") or not set(
                    item["nearest_feature_ids"]) <= valid_ids_by_epoch[right_epoch]:
                raise ValueError("nearest-only diagnostic references invalid features")
            if len(item["nearest_feature_ids"]) != len(set(item["nearest_feature_ids"])):
                raise ValueError("nearest-only diagnostic repeats a target feature")
        if pair.get("positive_overlap_from_features_with_any_candidate") != len(positive_left_degree) \
                or pair.get("positive_overlap_degree_gt_one_from_features") != sum(
                    degree > 1 for degree in positive_left_degree.values()) \
                or pair.get("positive_overlap_degree_gt_one_to_features") != sum(
                    degree > 1 for degree in positive_right_degree.values()):
            raise ValueError("overlap topology summaries do not match edge graph")
        pair_summaries.append({"from_epoch": left_epoch, "to_epoch": right_epoch,
                               "positive_area_candidates": overlap_count,
                               "touch_only_candidates": touch_count,
                               "nearest_only_features": len(nearest)})

    spatial = document.get("nrsc_t68_spatial_relations")
    if not isinstance(spatial, dict):
        raise ValueError("NRSC spatial relations block missing")
    rows = spatial.get("comparisons")
    if not isinstance(rows, list) or len(rows) != EXPECTED_NRSC_ROWS \
            or spatial.get("row_count") != EXPECTED_NRSC_ROWS:
        raise ValueError("NRSC Table 68 comparison denominator mismatch")
    row_keys = [row.get("nrsc_row_key") for row in rows if isinstance(row, dict)]
    if len(row_keys) != EXPECTED_NRSC_ROWS \
            or len(set(row_keys)) != EXPECTED_NRSC_ROWS:
        raise ValueError("NRSC row keys are missing or duplicated")
    point_counts: dict[str, dict[str, int]] = {str(epoch): {} for epoch in linkage.EPOCHS}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("NRSC comparison row must be an object")
        by_epoch = row.get("relations_by_epoch")
        if not isinstance(by_epoch, dict) or set(by_epoch) != set(point_counts):
            raise ValueError("NRSC row lacks one or more frozen epochs")
        for epoch, result in by_epoch.items():
            if not isinstance(result, dict):
                raise ValueError("point relation must be an object")
            candidates = result.get("candidates")
            if not isinstance(candidates, list):
                raise ValueError("point candidates must be a list")
            if result.get("status") not in POINT_STATUSES:
                raise ValueError("unknown point relation status")
            if any(not isinstance(candidate, dict)
                   or candidate.get("identity_claim") is not False
                   or candidate.get("feature_id") not in valid_ids_by_epoch[int(epoch)]
                   or candidate.get("relation") not in {
                       "POINT_IN_POLYGON", "POINT_ON_POLYGON_BOUNDARY"}
                   for candidate in candidates):
                raise ValueError("point candidate reference/claim is invalid")
            nearest = result.get("nearest_only_diagnostic")
            expected_status = (
                "ONE_SPATIAL_CANDIDATE" if len(candidates) == 1 else
                "MULTIPLE_SPATIAL_CANDIDATES" if len(candidates) > 1 else
                "NO_INTERSECTION_CANDIDATE" if nearest is not None else
                "NO_VALID_POLYGONS")
            if result["status"] != expected_status:
                raise ValueError("point status contradicts candidate/nearest rows")
            candidate_ids = [candidate["feature_id"] for candidate in candidates]
            if len(candidate_ids) != len(set(candidate_ids)):
                raise ValueError("point relation repeats a candidate feature")
            if nearest is not None:
                if not isinstance(nearest, dict) or candidates or nearest.get("relation") != \
                        "NEAREST_ONLY_DIAGNOSTIC_NOT_IDENTITY_EVIDENCE" \
                        or not _finite_nonnegative(nearest.get("distance_m")) \
                        or not nearest.get("feature_ids") \
                        or not set(nearest["feature_ids"]) <= valid_ids_by_epoch[int(epoch)]:
                    raise ValueError("point nearest-only diagnostic is invalid")
            point_counts[epoch][result["status"]] = \
                point_counts[epoch].get(result["status"], 0) + 1

    return {"artifact_status": document["status"],
            "epoch_feature_counts": {str(k): len(features_by_epoch[k])
                                     for k in linkage.EPOCHS},
            "epoch_excluded_geometry_counts": {
                str(k): len(features_by_epoch[k]) - len(valid_ids_by_epoch[k])
                for k in linkage.EPOCHS},
            "adjacent_epoch_summary": pair_summaries,
            "nrsc_t68_rows": len(rows),
            "nrsc_point_candidate_status_counts": point_counts}


def verify_report(report_path: Path, evidence_root: Path,
                  register_path: Path, registry_path: Path) -> dict[str, Any]:
    report_path = report_path.resolve()
    sidecar_path = Path(f"{report_path}.sha256")
    if not report_path.is_file() or not sidecar_path.is_file():
        raise ValueError("report or SHA-256 sidecar is missing")
    raw = report_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    lines = sidecar_path.read_text(encoding="utf-8").splitlines()
    if lines != [f"{digest}  {report_path.name}"]:
        raise ValueError("report SHA-256 sidecar mismatch")
    document = json.loads(raw)
    summary = validate_report_document(document)
    current_generator_hash = linkage.sha256_file(Path(linkage.__file__))
    if document.get("analysis_code_sha256") != current_generator_hash:
        raise ValueError("report generator SHA-256 differs from current generator bytes")

    replay = linkage.create_report(
        evidence_root.resolve(), register_path.resolve(), registry_path.resolve(),
        repository_head=document.get("repository_head"), profile_only=False)
    # Compare the JSON representation, not Python container implementation
    # details such as tuples returned by pyproj authority metadata.
    replay = json.loads(json.dumps(replay, allow_nan=False))
    replay["generated_utc"] = document.get("generated_utc")
    if replay != document:
        differences = _first_differences(document, replay)
        raise ValueError("deterministic source replay differs from sealed report: "
                         + "; ".join(differences))
    return {"report_path": str(report_path), "report_sha256": digest,
            "replay": "EXACT_MATCH", **summary}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--register", type=Path,
                        default=linkage.ROOT / "docs/science/INDIA_EVIDENCE_REGISTER_V3.json")
    parser.add_argument("--nrsc-registry", type=Path,
                        default=linkage.ROOT / "docs/science/INDIA_INVENTORY_REGISTRY_V4.json")
    args = parser.parse_args(argv)
    try:
        result = verify_report(args.report, args.evidence_root,
                               args.register, args.nrsc_registry)
    except (OSError, ValueError, KeyError, TypeError, AttributeError,
            json.JSONDecodeError) as exc:
        print(f"LAKE_EPOCH_LINKAGE_VERIFY_BLOCKED: {exc}")
        return 1
    print("LAKE_EPOCH_LINKAGE_VERIFY_OK")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
