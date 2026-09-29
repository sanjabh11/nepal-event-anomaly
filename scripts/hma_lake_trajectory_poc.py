"""Build and evaluate a candidate-only HMA lake-area trajectory PoC.

This offline PoC links only threshold-approved adjacent-epoch overlap
candidates. It preserves every source feature, leaves every unapproved edge
unresolved, and makes no physical-identity, event, territory, risk, or
operational claim. The 2000 Error-field unit discrepancy is retained as a
limitation; Error is excluded from trajectory features and clustering.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
from dateutil import parser as date_parser
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import india_lake_epoch_linkage as linkage  # noqa: E402
import verify_india_lake_epoch_linkage as source_verifier  # noqa: E402
from p5_safe_io import write_once_json, write_once_sidecar  # noqa: E402

EVIDENCE_ROOT = Path("/Users/sanjayb/nepal-event-anomaly-evidence")
INTAKE_ROOT = EVIDENCE_ROOT / "india-phase0-source-intake"
OUTPUT_ROOT = INTAKE_ROOT / "hma-lake-trajectory-poc-v1"
PRIOR_OUTPUT_ROOT = INTAKE_ROOT / "hma-lake-trajectory-poc"
LINKAGE_PATH = INTAKE_ROOT / "INDIA_LAKE_EPOCH_CANDIDATE_LINKAGE_V0.json"
REVIEW_PATH = INTAKE_ROOT / "INDIA_LAKE_LINKAGE_REVIEW_V0.json"
SCOPE_PATH = INTAKE_ROOT / "INDIA_POC_SCOPE_DECISION_V0.json"
ADMIN_CONTRACT_PATH = INTAKE_ROOT / "INDIA_ADMIN_TERRITORY_CONTRACT_V0.json"
REGISTER_PATH = ROOT / "docs/science/INDIA_EVIDENCE_REGISTER_V3.json"
REGISTRY_PATH = ROOT / "docs/science/INDIA_INVENTORY_REGISTRY_V4.json"
READ_ME_REL = "Glacial_Lake_Inventory/Readme.txt"
ERROR_XML_REL = "Glacial_Lake_Inventory/GlacialLake_20220726/GlacialLake_2000.shp.xml"
SCHEMA_PREFLIGHT = "HMA_LAKE_TRAJECTORY_PREFLIGHT_V1"
SCHEMA_TRAJECTORIES = "HMA_LAKE_TRAJECTORIES_V1"
SCHEMA_PLAN = "FROZEN_EVAL_PLAN_V1"
SCHEMA_RESULT = "HMA_LAKE_CLUSTER_STABILITY_V1"
APPROVED = "APPROVED_IDENTITY_CANDIDATE"
UNCONFIRMED = "UNCONFIRMED_CANDIDATE"
AMBIGUOUS_PATTERNS = {"POSSIBLE_SPLIT", "POSSIBLE_MERGE", "MANY_TO_MANY_OVERLAP"}
SEED = 20260929
RESAMPLES = 200
SAMPLE_FRACTION = 0.8
JACCARD_MIN = 0.75
K_SELECTION_FRACTION_MIN = 0.80
K_MIN = 2
K_MAX = 6
KMEANS_N_INIT = 10


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_sidecar(path: Path) -> str:
    path = Path(path)
    sidecar = Path(f"{path}.sha256")
    if not path.is_file() or not sidecar.is_file():
        raise ValueError(f"required artifact or sidecar missing: {path}")
    digest = sha256_file(path)
    expected = f"{digest}  {path.name}\n"
    if sidecar.read_text(encoding="utf-8") != expected:
        raise ValueError(f"SHA-256 sidecar mismatch: {sidecar}")
    return digest


def _read_bound_json(path: Path) -> tuple[dict[str, Any], str]:
    digest = verify_sidecar(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"artifact must be a JSON object: {path}")
    return document, digest


def _numeric(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _parse_date(value: Any) -> date | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date_parser.parse(text, fuzzy=False).date()
    except (ValueError, TypeError, OverflowError):
        return None


def _iso_day(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _false_authority(document: dict[str, Any], label: str) -> None:
    if document.get("authority") != linkage.AUTHORITY_FLAGS:
        raise ValueError(f"{label} authority flags differ from the all-false contract")
    if any(document["authority"].values()):
        raise ValueError(f"{label} contains an enabled authority flag")


def _load_and_validate_inputs(*, replay_source: bool) -> dict[str, Any]:
    link_doc, link_sha = _read_bound_json(LINKAGE_PATH)
    review_doc, review_sha = _read_bound_json(REVIEW_PATH)
    scope_doc, scope_sha = _read_bound_json(SCOPE_PATH)
    admin_doc, admin_sha = _read_bound_json(ADMIN_CONTRACT_PATH)

    if link_doc.get("schema") != linkage.SCHEMA \
            or link_doc.get("status") != "CANDIDATE_LINKAGE_ONLY":
        raise ValueError("candidate linkage artifact schema/status mismatch")
    _false_authority(link_doc, "linkage artifact")
    _false_authority(review_doc, "linkage review")
    _false_authority(scope_doc, "scope decision")
    _false_authority(admin_doc, "territory contract")
    if review_doc.get("schema") != "INDIA_LAKE_LINKAGE_REVIEW_V0" \
            or review_doc.get("linkage_sha256") != link_sha \
            or review_doc.get("linkage_artifact") != LINKAGE_PATH.name:
        raise ValueError("linkage review does not bind the exact candidate report")
    if scope_doc.get("schema") != "INDIA_POC_SCOPE_DECISION_V0" \
            or scope_doc.get("decision_state") != "REGION_AGNOSTIC_POC":
        raise ValueError("only the sealed REGION_AGNOSTIC_POC scope is supported")
    if admin_doc.get("schema") != "INDIA_ADMIN_TERRITORY_CONTRACT_V0" \
            or admin_doc.get("decision_state") != "DEFER":
        raise ValueError("territory contract must remain DEFER")
    if review_doc.get("detail") != {
            "mode": "THRESHOLD", "iou_min": 0.8, "from_frac_min": 0.8}:
        raise ValueError("linkage review threshold contract changed")

    if replay_source:
        source_verifier.verify_report(LINKAGE_PATH, EVIDENCE_ROOT,
                                      REGISTER_PATH, REGISTRY_PATH)

    # Recompute the owner's threshold disposition from every candidate edge.
    approved: list[dict[str, Any]] = []
    unconfirmed: list[dict[str, Any]] = []
    verdict_counts: Counter[str] = Counter()
    for relation in link_doc.get("adjacent_epoch_relations", []):
        left_epoch, right_epoch = relation.get("from_epoch"), relation.get("to_epoch")
        for edge in relation.get("overlap_candidates", []):
            if edge.get("relation") != "POSITIVE_AREA_OVERLAP_CANDIDATE":
                continue
            decision = (
                edge.get("overlap_pattern") == "ONE_TO_ONE_OVERLAP_CANDIDATE"
                and _numeric(edge.get("intersection_over_union")) is not None
                and _numeric(edge.get("intersection_over_union")) >= 0.8
                and _numeric(edge.get("fraction_of_from_area")) is not None
                and _numeric(edge.get("fraction_of_from_area")) >= 0.8
            )
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
                "identity_claim": False,
                "disposition": APPROVED if decision else UNCONFIRMED,
            }
            verdict_counts[row["disposition"]] += 1
            (approved if decision else unconfirmed).append(row)
    if review_doc.get("total_pairs") != sum(verdict_counts.values()) \
            or review_doc.get("verdict_counts") != dict(verdict_counts):
        raise ValueError("linkage review aggregate counts do not match recomputed edges")

    if replay_source:
        # Existing verifier checks source archive/member bytes, all report rows,
        # the current generator digest, and exact deterministic source replay.
        source_replay_status = "EXACT_MATCH"
    else:
        source_replay_status = "NOT_RUN_THIS_COMMAND"
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
        "source_replay": source_replay_status,
    }


def _feature_index(link_doc: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    features: dict[str, dict[str, Any]] = {}
    epochs: dict[str, int] = {}
    for profile in link_doc["epoch_feature_inventory"]:
        epoch = int(profile["epoch"])
        for feature in profile["features"]:
            feature_id = feature["feature_id"]
            if feature_id in features:
                raise ValueError(f"duplicate source feature ID: {feature_id}")
            attrs = feature.get("attributes")
            if not isinstance(attrs, dict):
                raise ValueError(f"source feature lacks attributes: {feature_id}")
            features[feature_id] = feature
            epochs[feature_id] = epoch
    expected = sum(linkage.EXPECTED_FEATURE_COUNTS.values())
    if len(features) != expected:
        raise ValueError(f"source-node conservation failed: {len(features)} != {expected}")
    return features, epochs


def _resolve_paths(inputs: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    link_doc = inputs["linkage"]
    features, epochs = _feature_index(link_doc)
    outgoing: dict[str, str] = {}
    incoming: dict[str, str] = {}
    approved_edges = inputs["approved_edges"]
    for edge in approved_edges:
        left, right = edge["from_feature_id"], edge["to_feature_id"]
        if left not in features or right not in features:
            raise ValueError("approved candidate edge references a missing source node")
        if epochs[right] != epochs[left] + 10 and not (
                (epochs[left], epochs[right]) in {(2010, 2015), (2015, 2020)}):
            raise ValueError(f"candidate edge is not adjacent in the frozen epoch order: {left}->{right}")
        if left in outgoing or right in incoming:
            raise ValueError("threshold-approved candidate graph branches; refusing path promotion")
        outgoing[left] = right
        incoming[right] = left

    node_order = sorted(features, key=lambda key: (epochs[key], key))
    roots = [node for node in node_order if node not in incoming]
    paths: list[list[str]] = []
    visited: set[str] = set()
    for root in roots:
        path: list[str] = []
        current = root
        while current:
            if current in visited:
                raise ValueError("approved candidate graph contains a cycle or duplicate path")
            visited.add(current)
            path.append(current)
            current = outgoing.get(current, "")
        paths.append(path)
    if visited != set(features):
        raise ValueError("not every source feature was conserved in candidate paths")

    ambiguous_edges = [edge for edge in inputs["unconfirmed_edges"]
                       if edge.get("overlap_pattern") in AMBIGUOUS_PATTERNS]
    groups = _group_ambiguous_edges(ambiguous_edges)
    ambiguity_by_node: dict[str, list[str]] = defaultdict(list)
    for group in groups:
        for feature_id in group["source_feature_ids"] + group["target_feature_ids"]:
            ambiguity_by_node[feature_id].append(group["ambiguity_record_id"])

    return [
        {
            "candidate_path_id": "CANDIDATE_PATH:" + hashlib.sha256(
                "\n".join(path).encode("utf-8")).hexdigest()[:20],
            "source_feature_ids": path,
            "approved_candidate_edge_ids": [
                f"{a}->{b}" for a, b in zip(path, path[1:])
            ],
            "epochs_observed": [epochs[node] for node in path],
            "source_feature_count": len(path),
            "all_five_epochs_present": len(path) == len(linkage.EPOCHS),
            "ambiguity_record_ids": sorted({record_id for node in path
                                             for record_id in ambiguity_by_node.get(node, [])}),
            "observations": _path_observations(path, features, epochs),
        }
        for path in paths
    ], {"outgoing": outgoing, "incoming": incoming,
        "feature_epochs": epochs, "ambiguity_records": groups,
        "ambiguous_edge_count": len(ambiguous_edges)}


def _vertical_slice(paths: list[dict[str, Any]], graph: dict[str, Any],
                    inputs: dict[str, Any]) -> dict[str, Any]:
    """Exercise one actual five-epoch path and one unresolved multi-link case."""
    full_paths = sorted((row for row in paths if len(row["source_feature_ids"]) == 5),
                        key=lambda row: row["candidate_path_id"])
    if not full_paths:
        raise ValueError("vertical slice cannot find a five-epoch candidate path")
    if not graph["ambiguity_records"]:
        raise ValueError("vertical slice cannot find an ambiguous multi-link component")
    path = full_paths[0]
    ambiguous = sorted(graph["ambiguity_records"],
                       key=lambda row: row["ambiguity_record_id"])[0]
    approved_refs = {f"{edge['from_feature_id']}->{edge['to_feature_id']}"
                     for edge in inputs["approved_edges"]}
    unconfirmed_refs = {f"{edge['from_feature_id']}->{edge['to_feature_id']}"
                        for edge in inputs["unconfirmed_edges"]}
    path_refs = {f"{left}->{right}" for left, right in zip(
        path["source_feature_ids"], path["source_feature_ids"][1:])}
    ambiguity_refs = set(ambiguous["edge_references"])
    if len(path_refs) != 4 or not path_refs <= approved_refs:
        raise ValueError("vertical slice path is not exactly four approved adjacent candidates")
    if not ambiguity_refs or not ambiguity_refs <= unconfirmed_refs \
            or ambiguity_refs & approved_refs or ambiguous["identity_assigned"] is not False:
        raise ValueError("vertical slice ambiguity was promoted or lost")
    if [row["epoch"] for row in path["observations"]] != list(linkage.EPOCHS):
        raise ValueError("vertical slice does not preserve all five ordered epochs")
    payload = {
        "candidate_path_id": path["candidate_path_id"],
        "path_feature_ids": path["source_feature_ids"],
        "path_edge_references": sorted(path_refs),
        "observation_coverage": [
            {"epoch": row["epoch"], "feature_id": row["feature_id"],
             "area_available": row["area_m2"] is not None,
             "date_available": row["date_iso"] is not None}
            for row in path["observations"]
        ],
        "ambiguous_record_id": ambiguous["ambiguity_record_id"],
        "ambiguous_edge_references": sorted(ambiguity_refs),
        "ambiguous_identity_assigned": False,
    }
    serialized_a = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    serialized_b = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    if serialized_a != serialized_b:
        raise ValueError("vertical slice serialization is not deterministic")
    return {
        "status": "PASS",
        "actual_five_epoch_candidate_path_exercised": True,
        "actual_ambiguous_multilink_component_exercised": True,
        "all_path_links_threshold_approved_candidates": True,
        "ambiguous_links_remain_unconfirmed": True,
        "source_features_in_slice": len(set(path["source_feature_ids"])
                                          | set(ambiguous["source_feature_ids"])
                                          | set(ambiguous["target_feature_ids"])),
        "deterministic_slice_sha256": hashlib.sha256(serialized_a.encode()).hexdigest(),
        "example": payload,
    }


def _prior_artifact_digests() -> dict[str, str]:
    prior_names = (
        "HMA_LAKE_TRAJECTORY_PREFLIGHT_V0.json",
        "HMA_LAKE_TRAJECTORIES_V0.json",
        "FROZEN_EVAL_PLAN_V0.json",
        "HMA_LAKE_CLUSTER_STABILITY_V0.json",
    )
    digests = {}
    for name in prior_names:
        path = PRIOR_OUTPUT_ROOT / name
        digests[name] = verify_sidecar(path)
    return digests


def _group_ambiguous_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_interval: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        by_interval[(int(edge["from_epoch"]), int(edge["to_epoch"]))].append(edge)
    groups: list[dict[str, Any]] = []
    for interval, interval_edges in sorted(by_interval.items()):
        adjacency: dict[str, set[str]] = defaultdict(set)
        incident: dict[str, list[int]] = defaultdict(list)
        for i, edge in enumerate(interval_edges):
            left, right = edge["from_feature_id"], edge["to_feature_id"]
            adjacency[left].add(right)
            adjacency[right].add(left)
            incident[left].append(i)
            incident[right].append(i)
        seen: set[str] = set()
        for node in sorted(adjacency):
            if node in seen:
                continue
            stack, nodes, edge_indexes = [node], set(), set()
            while stack:
                current = stack.pop()
                if current in seen:
                    continue
                seen.add(current)
                nodes.add(current)
                edge_indexes.update(incident[current])
                stack.extend(sorted(adjacency[current] - seen, reverse=True))
            component_edges = [interval_edges[i] for i in sorted(edge_indexes)]
            source_ids = sorted({e["from_feature_id"] for e in component_edges})
            target_ids = sorted({e["to_feature_id"] for e in component_edges})
            digest_payload = f"{interval[0]}:{interval[1]}\n" + "\n".join(
                f"{e['from_feature_id']}->{e['to_feature_id']}" for e in component_edges)
            groups.append({
                "ambiguity_record_id": "AMBIGUOUS_MULTI_LINK:" + hashlib.sha256(
                    digest_payload.encode("utf-8")).hexdigest()[:20],
                "from_epoch": interval[0], "to_epoch": interval[1],
                "source_feature_ids": source_ids,
                "target_feature_ids": target_ids,
                "edge_count": len(component_edges),
                "topology_types": sorted({str(e["overlap_pattern"])
                                           for e in component_edges}),
                "edge_references": [
                    f"{e['from_feature_id']}->{e['to_feature_id']}"
                    for e in component_edges
                ],
                "identity_assigned": False,
                "status": "AMBIGUOUS_MULTI_LINK_CANDIDATE_NOT_LINKED",
            })
    return groups


def _path_observations(path: list[str], features: dict[str, dict[str, Any]],
                       epochs: dict[str, int]) -> list[dict[str, Any]]:
    by_epoch = {epochs[node]: node for node in path}
    rows = []
    for epoch in linkage.EPOCHS:
        feature_id = by_epoch.get(epoch)
        if feature_id is None:
            rows.append({"epoch": epoch, "source_feature_present": False,
                         "feature_id": None, "area_km2_source": None,
                         "area_m2": None, "area_error_raw": None,
                         "area_error_used_in_model": False,
                         "date_source": None, "date_iso": None,
                         "region_source": None, "geometry_status": None})
            continue
        feature = features[feature_id]
        attrs = feature["attributes"]
        area_km2 = _numeric(attrs.get("Area"))
        area_error = _numeric(attrs.get("Error"))
        observed_date = _parse_date(attrs.get("Date"))
        region_value = attrs.get("Region")
        region = (str(region_value).strip() if region_value is not None else "") or "UNKNOWN"
        rows.append({
            "epoch": epoch,
            "source_feature_present": True,
            "feature_id": feature_id,
            "area_km2_source": area_km2,
            "area_m2": area_km2 * 1_000_000.0 if area_km2 is not None and area_km2 > 0 else None,
            "area_error_raw": area_error,
            "area_error_used_in_model": False,
            "date_source": attrs.get("Date"),
            "date_iso": _iso_day(observed_date),
            "region_source": region,
            "geometry_status": feature.get("geometry_status"),
            "coverage_flag": (
                "OBSERVED_AREA_AND_DATE" if area_km2 is not None and area_km2 > 0
                and observed_date is not None else
                "OBSERVED_FEATURE_INCOMPLETE_MEASUREMENT"),
        })
    return rows


def _safe_relative_member_digest(relative: str) -> dict[str, str]:
    manifest = INTAKE_ROOT / "observation-inventory-figshare" / "SHA256SUMS.txt"
    expected = linkage.parse_sha256_manifest(manifest)
    member = manifest.parent / relative
    archive_path = manifest.parent / "Glacial_Lake_Inventory.zip"
    if not member.is_file() or not archive_path.is_file():
        raise ValueError(f"source unit-evidence member or archive missing: {relative}")
    extracted = member.read_bytes()
    actual = hashlib.sha256(extracted).hexdigest()
    with zipfile.ZipFile(archive_path) as archive:
        if relative not in archive.namelist():
            raise ValueError(f"source unit-evidence member absent from pinned archive: {relative}")
        archived = archive.read(relative)
    archive_digest = hashlib.sha256(archived).hexdigest()
    if archived != extracted or archive_digest != actual:
        raise ValueError(f"source unit-evidence extracted bytes differ from archive: {relative}")
    manifest_binding = expected.get(relative)
    if manifest_binding is not None and manifest_binding != actual:
        raise ValueError(f"source unit-evidence checksum manifest mismatch: {relative}")
    return {
        "member": relative,
        "sha256": actual,
        "archive_member_sha256": archive_digest,
        "member_manifest_binding": "PRESENT_MATCH" if manifest_binding else "ABSENT_ARCHIVE_BYTES_VERIFIED",
    }


def _input_summary(inputs: dict[str, Any]) -> dict[str, Any]:
    link_doc = inputs["linkage"]
    readme = _safe_relative_member_digest(READ_ME_REL)
    error_xml = _safe_relative_member_digest(ERROR_XML_REL)
    profiles = link_doc["epoch_feature_inventory"]
    missingness: dict[str, Any] = {}
    error_anomaly: dict[str, Any] = {}
    for profile in profiles:
        epoch = int(profile["epoch"])
        features = profile["features"]
        areas, errors, dates = [], [], []
        area_missing = date_missing = error_missing = 0
        area_le_error = 0
        for feature in features:
            attrs = feature["attributes"]
            area, error = _numeric(attrs.get("Area")), _numeric(attrs.get("Error"))
            parsed = _parse_date(attrs.get("Date"))
            if area is None or area <= 0:
                area_missing += 1
            else:
                areas.append(area)
            if error is None or error < 0:
                error_missing += 1
            else:
                errors.append(error)
            if parsed is None:
                date_missing += 1
            else:
                dates.append(parsed)
            if area is not None and error is not None and area <= error:
                area_le_error += 1
        missingness[str(epoch)] = {
            "source_features": len(features),
            "valid_positive_area": len(areas), "missing_invalid_area": area_missing,
            "parseable_dates": len(dates), "missing_invalid_dates": date_missing,
            "valid_nonnegative_error_values": len(errors),
            "missing_invalid_error": error_missing,
            "area_le_error_count_diagnostic_only": area_le_error,
            "area_km2_min_median_max": [min(areas), float(np.median(areas)), max(areas)]
            if areas else None,
            "date_min_max": [_iso_day(min(dates)), _iso_day(max(dates))]
            if dates else None,
        }
        if epoch == 2000:
            error_anomaly = {
                "status": "UNRESOLVED_SOURCE_UNIT_CONFLICT",
                "readme_statement": "Error area uncertainty in km^2",
                "readme_member_sha256": readme["sha256"],
                "2000_metadata_lineage_statement": (
                    "GlacialLake_2000.shp.xml records Error=10.308*Perimeter; "
                    "no /1,000,000 conversion is recorded in the 2000 lineage"),
                "metadata_member_sha256": error_xml["sha256"],
                "area_le_error_count": area_le_error,
                "error_value_use": "RAW_ONLY_EXCLUDED_FROM_MODEL",
                "no_unit_conversion_applied": True,
            }

    # Source dates and errors in each epoch remain in the raw feature table.
    interval_dates = {}
    # Calculated later over actual approved paths; here preserve epoch coverage.
    for epoch in linkage.EPOCHS:
        interval_dates[str(epoch)] = {
            "valid_area_count": missingness[str(epoch)]["valid_positive_area"],
            "valid_date_count": missingness[str(epoch)]["parseable_dates"],
        }
    return {
        "linkage_report_sha256": inputs["linkage_sha256"],
        "linkage_review_sha256": inputs["review_sha256"],
        "scope_decision_sha256": inputs["scope_sha256"],
        "territory_contract_sha256": inputs["admin_contract_sha256"],
        "source_replay": inputs["source_replay"],
        "source_feature_counts": {str(k): v for k, v in linkage.EXPECTED_FEATURE_COUNTS.items()},
        "total_source_features": sum(linkage.EXPECTED_FEATURE_COUNTS.values()),
        "epoch_measurement_missingness": missingness,
        "epoch_area_and_date_coverage": interval_dates,
        "area_unit_evidence": {
            "readme": readme,
            "declared_unit": "km^2",
            "trajectory_output_unit": "m^2; source Area value multiplied by 1,000,000",
        },
        "area_error_unit_evidence": error_anomaly,
        "approved_candidate_edge_counts_by_interval": dict(Counter(
            f"{e['from_epoch']}-{e['to_epoch']}" for e in inputs["approved_edges"])),
        "unconfirmed_candidate_edge_counts_by_interval_and_pattern": _edge_counts(
            inputs["unconfirmed_edges"]),
        "approved_candidate_edges": len(inputs["approved_edges"]),
        "unconfirmed_candidate_edges": len(inputs["unconfirmed_edges"]),
    }


def _edge_counts(edges: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    result: dict[str, Counter[str]] = defaultdict(Counter)
    for edge in edges:
        result[f"{edge['from_epoch']}-{edge['to_epoch']}"][str(edge["overlap_pattern"])] += 1
    return {key: dict(counts) for key, counts in sorted(result.items())}


def _preflight_and_trajectory(inputs: dict[str, Any],
                             prior_digests: dict[str, str]
                             ) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    summary = _input_summary(inputs)
    paths, graph = _resolve_paths(inputs)
    length_counts = Counter(str(len(path["source_feature_ids"])) for path in paths)
    complete = [path for path in paths if path["all_five_epochs_present"]]
    complete_valid = [path for path in complete if _trajectory_vector(path) is not None]
    if not complete_valid:
        raise ValueError("no complete five-snapshot area/date candidate trajectories")
    vertical_slice = _vertical_slice(paths, graph, inputs)
    preflight = {
        "schema": SCHEMA_PREFLIGHT,
        "supersedes": prior_digests,
        "status": "REVISE_SCOPE",
        "allowed_continuation_scope": "AREA_DATE_DESCRIPTIVE_ONLY",
        "reasons": [
            "The source README declares Error in km^2, but the 2000 shapefile lineage does not record the later /1,000,000 conversion; do not use Error as an uncertainty magnitude.",
            "Threshold-approved spatial overlaps are candidate links only, not confirmed physical lake identities.",
        ],
        "source_integrity": "PASS_EXACT_SOURCE_REPLAY",
        "scope_decision": "REGION_AGNOSTIC_POC",
        "territory_status": "UNASSESSED_DEFERRED",
        "authority": dict(linkage.AUTHORITY_FLAGS),
        "source_profile": summary,
        "candidate_path_count": len(paths),
        "candidate_path_length_distribution": dict(sorted(length_counts.items(), key=lambda kv: int(kv[0]))),
        "complete_five_epoch_candidate_paths": len(complete),
        "area_date_evaluable_complete_paths": len(complete_valid),
        "vertical_slice": vertical_slice,
        "approved_candidate_graph": {
            "nodes_conserved": len(graph["feature_epochs"]),
            "approved_edges": len(inputs["approved_edges"]),
            "unconfirmed_edges": len(inputs["unconfirmed_edges"]),
            "ambiguous_multilink_records": len(graph["ambiguity_records"]),
            "branching_approved_graph": False,
            "identity_claims": 0,
        },
        "stop_conditions": {
            "source_replay_must_be_exact_match": True,
            "all_source_nodes_must_be_conserved": True,
            "area_error_uncertainty_claims": "BLOCKED_UNRESOLVED_UNIT_CONFLICT",
        },
        "decision_note": "Continue only with the explicitly narrowed area-and-date descriptive analysis; no Error-based perturbation, event association, territory analysis, or causal/risk claim.",
    }
    _false_authority(preflight, "preflight")

    trajectories = []
    for path in paths:
        vector = _trajectory_vector(path)
        trajectories.append({
            **path,
            "trajectory_semantics": "candidate path through threshold-approved adjacent-snapshot overlaps; not a physical lake identity",
            "cluster_evaluation_eligible": vector is not None,
            "area_change_rate_features": vector,
            "area_change_feature_status": (
                "FOUR_ADJACENT_SNAPSHOT_LOG_AREA_RATES" if vector is not None else
                "NOT_ELIGIBLE_INCOMPLETE_OR_INVALID_AREA_DATE"),
        })
    cadence = _cadence_summary(trajectories)
    trajectory_doc = {
        "schema": SCHEMA_TRAJECTORIES,
        "status": "CANDIDATE_TRAJECTORIES_ONLY",
        "claim_scope": "research_only_region_agnostic_descriptive_poc",
        "authority": dict(linkage.AUTHORITY_FLAGS),
        "conclusions": {
            "physical_lake_identity_established": False,
            "event_or_recurrence_identity_established": False,
            "verified_non_event_intervals_established": False,
            "administrative_territory_assigned": False,
            "risk_or_causal_claim_authorized": False,
            "operational_authority": False,
        },
        "inputs": {
            "linkage_report": {"path": LINKAGE_PATH.name, "sha256": inputs["linkage_sha256"]},
            "linkage_review": {"path": REVIEW_PATH.name, "sha256": inputs["review_sha256"]},
            "scope_decision": {"path": SCOPE_PATH.name, "sha256": inputs["scope_sha256"]},
            "territory_contract": {"path": ADMIN_CONTRACT_PATH.name, "sha256": inputs["admin_contract_sha256"]},
        },
        "unit_contract": {
            "area_source_field": "Area",
            "area_source_unit": "km^2 per source Readme.txt",
            "area_m2_conversion": "area_km2_source * 1,000,000 exactly; no geometry-derived substitute",
            "error_source_field": "Error",
            "error_use": "raw value preserved; excluded from features and sensitivity analysis due unresolved 2000 unit conflict",
        },
        "candidate_link_rule": {
            "accepted_review_disposition": APPROVED,
            "threshold": {"overlap_pattern": "ONE_TO_ONE_OVERLAP_CANDIDATE", "iou_min": 0.8, "fraction_of_from_area_min": 0.8},
            "unconfirmed_links": "preserved separately; never used to join trajectory paths",
            "identity_claim": False,
        },
        "source_feature_conservation": {
            "expected_total_nodes": summary["total_source_features"],
            "observed_total_nodes": len(trajectories) and sum(
                len(path["source_feature_ids"]) for path in trajectories),
            "every_source_node_once": True,
            "candidate_trajectory_count": len(trajectories),
            "path_length_distribution": dict(sorted(length_counts.items(), key=lambda kv: int(kv[0]))),
        },
        "unconfirmed_candidate_links": inputs["unconfirmed_edges"],
        "ambiguous_multilink_records": graph["ambiguity_records"],
        "cadence_limits": cadence,
        "trajectories": trajectories,
        "limitations": [
            "Five snapshots are not continuous observation; event occurrence and duration inside snapshot gaps are not identified.",
            "Candidate links are thresholded spatial relations, not adjudicated lake identity.",
            "Area uncertainty is not propagated because the source Error field has an unresolved 2000 unit discrepancy.",
            "The evaluated complete-path subset is selected by high-overlap linkage and complete measurements; it is not a representative sample of all HMA lakes.",
        ],
    }
    _false_authority(trajectory_doc, "trajectory artifact")
    if trajectory_doc["source_feature_conservation"]["observed_total_nodes"] != summary["total_source_features"]:
        raise ValueError("trajectory output failed source-node conservation")
    return preflight, trajectory_doc, trajectories


def _trajectory_vector(path: dict[str, Any]) -> list[float] | None:
    observations = path.get("observations", [])
    if len(observations) != len(linkage.EPOCHS) \
            or [row.get("epoch") for row in observations] != list(linkage.EPOCHS):
        return None
    parsed = []
    for row in observations:
        area_m2 = _numeric(row.get("area_m2"))
        when = _parse_date(row.get("date_iso"))
        if area_m2 is None or area_m2 <= 0 or when is None:
            return None
        parsed.append((area_m2, when))
    vector = []
    for (area_a, date_a), (area_b, date_b) in zip(parsed, parsed[1:]):
        elapsed_days = (date_b - date_a).days
        if elapsed_days <= 0:
            return None
        years = elapsed_days / 365.2425
        vector.append(math.log(area_b / area_a) / years)
    return vector


def _cadence_summary(trajectories: list[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for left, right in zip(linkage.EPOCHS, linkage.EPOCHS[1:]):
        gaps = []
        for trajectory in trajectories:
            observations = trajectory["observations"]
            a = next(row for row in observations if row["epoch"] == left)
            b = next(row for row in observations if row["epoch"] == right)
            da, db = _parse_date(a["date_iso"]), _parse_date(b["date_iso"])
            if da is not None and db is not None and (db - da).days > 0:
                gaps.append((db - da).days)
        result[f"{left}-{right}"] = {
            "nominal_epoch_gap_years": right - left,
            "candidate_paths_with_both_parseable_dates": len(gaps),
            "actual_acquisition_gap_days_min_median_max": [
                min(gaps), float(np.median(gaps)), max(gaps)] if gaps else None,
            "duration_interpretation": "snapshot gaps bound when observed area differences arose; no event duration or event date is resolved within a gap",
        }
    return result


def frozen_plan(trajectory_doc: dict[str, Any], trajectory_sha: str,
                preflight_sha: str) -> dict[str, Any]:
    cohort = [row for row in trajectory_doc["trajectories"]
              if row["cluster_evaluation_eligible"]]
    cohort_ids = sorted(row["candidate_path_id"] for row in cohort)
    return {
        "schema": SCHEMA_PLAN,
        "version": 1,
        "status": "FROZEN_BEFORE_CLUSTERING",
        "preflight_artifact": {"schema": SCHEMA_PREFLIGHT,
                                "sha256": preflight_sha},
        "trajectory_artifact": {"schema": SCHEMA_TRAJECTORIES,
                                 "sha256": trajectory_sha},
        "analysis_code_sha256": sha256_file(Path(__file__)),
        "scope": "descriptive reproducibility of area-trajectory partitions only; no event-label association",
        "cohort": {
            "definition": "candidate paths with all five epochs and positive finite Area plus parseable, strictly increasing actual acquisition dates at every epoch",
            "candidate_path_count": len(cohort),
            "candidate_path_ids_sha256": hashlib.sha256("\n".join(cohort_ids).encode()).hexdigest(),
            "units_are_candidate_paths_not_confirmed_lakes": True,
            "region_stratifier": "source Region value from earliest snapshot (1990); missing values assigned UNKNOWN",
        },
        "features": {
            "fields": ["ln(area_m2[t+1]/area_m2[t]) / (actual_acquisition_days/365.2425)"],
            "adjacent_intervals_only": True,
            "missing_epoch_bridging": False,
            "region_used_as_model_feature": False,
            "error_field_used": False,
            "error_reason": "unresolved 2000 Error-field unit conflict; no conversion or uncertainty propagation",
        },
        "clustering": {
            "algorithm": "scikit-learn KMeans (Lloyd), n_init=10",
            "scaling": "per-feature median centering and IQR scaling; zero IQR uses scale 1",
            "candidate_k": list(range(K_MIN, K_MAX + 1)),
            "selection": "maximum full-sample silhouette; exact ties select smaller k",
            "seed": SEED,
        },
        "stability_design": {
            "resampling": "200 deterministic without-replacement 80% subsamples, stratified by earliest-snapshot Region; each stratum samples ceil(0.8*n), with at least one item",
            "k_reselected_in_each_resample": True,
            "cluster_matching": "for each full-data cluster, maximum Jaccard against any resample cluster on the same sampled candidate-path IDs; an unsampled full-data cluster scores 0",
        "numeric_reproducible_structure_criterion": {
                "selected_full_sample_silhouette_strictly_greater_than": 0.0,
                "resamples_selecting_full_sample_k_fraction_min": K_SELECTION_FRACTION_MIN,
                "every_full_sample_cluster_mean_best_match_jaccard_min": JACCARD_MIN,
                "jaccard_mean_denominator": RESAMPLES,
            "pass_rule": "ALL numeric conditions must pass; otherwise no reproducible structure is detected under this design",
            "method_reference": "Hennig (2007), Cluster-wise assessment of cluster stability, Computational Statistics & Data Analysis 52:258-271, doi:10.1016/j.csda.2006.11.025; Jaccard threshold is an operational heuristic, not a universal scientific boundary",
        },
            "criterion_scope": "algorithmic subsampling stability only; not evidence that clusters are natural lake classes, event predictors, or scientifically validated groups",
        },
        "not_run_or_not_authorized": {
            "measurement_error_sensitivity": "BLOCKED_UNRESOLVED_2000_ERROR_UNITS",
            "event_label_association": "DORMANT_SEPARATE_BRANCH",
            "permutation_test": "NOT_IN_SCOPE_NO_EVENT_LABEL_JOIN",
            "new_data_acquisition": False,
            "territory_or_administrative_claim": False,
            "risk_forecast_warning_or_operational_claim": False,
        },
    }


def _robust_scale(x: np.ndarray) -> np.ndarray:
    center = np.median(x, axis=0)
    q25, q75 = np.percentile(x, [25, 75], axis=0, method="linear")
    scale = q75 - q25
    scale = np.where(scale > 0, scale, 1.0)
    return (x - center) / scale


def _fit_best_k(x: np.ndarray, seed: int) -> tuple[int, np.ndarray, float]:
    if x.ndim != 2 or x.shape[0] < 3 or x.shape[1] == 0:
        raise ValueError("clustering matrix has insufficient dimensions")
    distinct = len(np.unique(x, axis=0))
    max_k = min(K_MAX, x.shape[0] - 1, distinct - 1)
    if max_k < K_MIN:
        raise ValueError("fewer than two distinct cluster candidates")
    candidates = []
    for k in range(K_MIN, max_k + 1):
        model = KMeans(n_clusters=k, random_state=seed + k, n_init=KMEANS_N_INIT,
                       algorithm="lloyd")
        labels = model.fit_predict(x)
        if len(set(labels.tolist())) != k:
            continue
        score = float(silhouette_score(x, labels, metric="euclidean"))
        candidates.append((score, k, labels))
    if not candidates:
        raise ValueError("no valid k/silhouette result")
    score, k, labels = sorted(candidates, key=lambda item: (-item[0], item[1]))[0]
    return k, labels, score


def _stratified_subsample(regions: list[str], fraction: float,
                          rng: np.random.Generator) -> np.ndarray:
    groups: dict[str, list[int]] = defaultdict(list)
    for i, region in enumerate(regions):
        groups[region].append(i)
    sampled = []
    for region in sorted(groups):
        values = np.asarray(groups[region], dtype=int)
        count = max(1, int(math.ceil(len(values) * fraction)))
        sampled.extend(rng.choice(values, size=count, replace=False).tolist())
    return np.asarray(sorted(sampled), dtype=int)


def _jaccard(a: set[int], b: set[int]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 1.0


def _json_sha256(document: dict[str, Any]) -> str:
    payload = json.dumps(document, indent=2, sort_keys=True) + "\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def evaluate(trajectory_doc: dict[str, Any], plan: dict[str, Any],
             *, resamples: int = RESAMPLES) -> dict[str, Any]:
    cohort = [row for row in trajectory_doc["trajectories"]
              if row["cluster_evaluation_eligible"]]
    x_raw = np.asarray([row["area_change_rate_features"] for row in cohort], dtype=float)
    if not np.isfinite(x_raw).all():
        raise ValueError("non-finite model feature")
    regions = []
    for row in cohort:
        earliest = next(item for item in row["observations"] if item["epoch"] == linkage.EPOCHS[0])
        region = earliest.get("region_source")
        regions.append(str(region).strip() if region else "UNKNOWN")
    x = _robust_scale(x_raw)
    k_full, full_labels, full_silhouette = _fit_best_k(x, SEED)
    full_clusters = sorted(set(full_labels.tolist()))
    jaccard_scores: dict[int, list[float]] = {int(c): [] for c in full_clusters}
    selected_k_counts: Counter[int] = Counter()
    rng = np.random.default_rng(SEED)
    for iteration in range(resamples):
        chosen = _stratified_subsample(regions, SAMPLE_FRACTION, rng)
        sample_x = _robust_scale(x_raw[chosen])
        k_sample, sample_labels, _ = _fit_best_k(sample_x, SEED + 100_003 * (iteration + 1))
        selected_k_counts[k_sample] += 1
        for cluster in full_clusters:
            full_members = {int(i) for i in chosen if int(full_labels[i]) == cluster}
            if not full_members:
                jaccard_scores[int(cluster)].append(0.0)
                continue
            best = 0.0
            for sample_cluster in set(sample_labels.tolist()):
                sample_members = {int(chosen[j]) for j, label in enumerate(sample_labels)
                                  if int(label) == int(sample_cluster)}
                best = max(best, _jaccard(full_members, sample_members))
            jaccard_scores[int(cluster)].append(best)
    if sum(selected_k_counts.values()) != resamples:
        raise ValueError("stability resample count mismatch")
    k_fraction = selected_k_counts[k_full] / resamples
    cluster_results = []
    for cluster in full_clusters:
        scores = jaccard_scores[int(cluster)]
        mean_jaccard = float(np.mean(scores))
        cluster_results.append({
            "full_sample_cluster": int(cluster),
            "candidate_path_count": int(np.sum(full_labels == cluster)),
            "mean_best_match_jaccard": mean_jaccard,
            "meets_numeric_jaccard_threshold": mean_jaccard >= JACCARD_MIN,
            "minimum_resample_jaccard": float(min(scores)),
            "maximum_resample_jaccard": float(max(scores)),
        })
    checks = {
        "full_sample_silhouette_positive": full_silhouette > 0.0,
        "selected_k_resample_fraction_at_least_0_80": k_fraction >= K_SELECTION_FRACTION_MIN,
        "every_cluster_mean_jaccard_at_least_0_75": all(
            row["meets_numeric_jaccard_threshold"] for row in cluster_results),
    }
    passed = all(checks.values())
    return {
        "schema": SCHEMA_RESULT,
        "status": "ALGORITHMIC_STABILITY_PASS" if passed else "NO_ROBUST_STRUCTURE_UNDER_THIS_DESIGN",
        "claim_scope": "descriptive algorithmic resampling stability only",
        "authority": dict(linkage.AUTHORITY_FLAGS),
        "trajectory_sha256": plan["trajectory_artifact"]["sha256"],
        "frozen_eval_plan_sha256": _json_sha256(plan),
        "analysis_code_sha256": sha256_file(Path(__file__)),
        "cohort_candidate_path_count": len(cohort),
        "candidate_paths_are_not_confirmed_lakes": True,
        "full_sample": {
            "selected_k": k_full,
            "silhouette": full_silhouette,
            "cluster_sizes": {str(cluster): int(np.sum(full_labels == cluster))
                              for cluster in full_clusters},
        },
        "stability": {
            "resamples_completed": resamples,
            "selected_k_counts": {str(k): count for k, count in sorted(selected_k_counts.items())},
            "fraction_selecting_full_sample_k": k_fraction,
            "numeric_criterion": plan["stability_design"]["numeric_reproducible_structure_criterion"],
            "clusters": cluster_results,
            "checks": checks,
        },
        "interpretation_limits": [
            "A pass indicates resampling stability of this forced KMeans partition under this frozen candidate-path cohort only.",
            "It does not establish natural lake classes, physical identity, event association, hazard, causality, prediction, or transferability.",
            "Measurement-error sensitivity was not run because 2000 Error units are unresolved.",
            "No event labels or catalog-unlisted-as-control assumption were used.",
        ],
        "event_association_branch": "DORMANT",
    }


def _publish_or_match(path: Path, document: dict[str, Any]) -> str:
    if path.exists():
        digest = verify_sidecar(path)
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != document:
            raise FileExistsError(f"write-once artifact exists with different content: {path}")
        return digest
    write_once_json(path, document)
    return write_once_sidecar(path)


def _load_existing_exact(path: Path, expected: dict[str, Any]) -> str:
    doc, digest = _read_bound_json(path)
    if doc != expected:
        raise ValueError(f"artifact differs from independent recomputation: {path}")
    return digest


def _artifact_paths(output_root: Path) -> dict[str, Path]:
    return {
        "preflight": output_root / "HMA_LAKE_TRAJECTORY_PREFLIGHT_V1.json",
        "trajectories": output_root / "HMA_LAKE_TRAJECTORIES_V1.json",
        "plan": output_root / "FROZEN_EVAL_PLAN_V1.json",
        "result": output_root / "HMA_LAKE_CLUSTER_STABILITY_V1.json",
    }


def run(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    prior_digests = _prior_artifact_digests()
    inputs = _load_and_validate_inputs(replay_source=True)
    preflight, trajectory_doc, _ = _preflight_and_trajectory(inputs, prior_digests)
    paths = _artifact_paths(output_root)
    preflight_sha = _publish_or_match(paths["preflight"], preflight)
    trajectory_sha = _publish_or_match(paths["trajectories"], trajectory_doc)
    plan = frozen_plan(trajectory_doc, trajectory_sha, preflight_sha)
    plan_sha = _publish_or_match(paths["plan"], plan)
    # The write-once frozen contract is published before any clustering fit.
    result = evaluate(trajectory_doc, plan)
    result["frozen_eval_plan_sha256"] = plan_sha
    result_sha = _publish_or_match(paths["result"], result)
    return {
        "status": "HMA_LAKE_TRAJECTORY_POC_COMPLETE",
        "source_replay": "EXACT_MATCH",
        "preflight": preflight["status"],
        "candidate_trajectories": trajectory_doc["source_feature_conservation"]["candidate_trajectory_count"],
        "complete_evaluation_paths": plan["cohort"]["candidate_path_count"],
        "numeric_stability_result": result["status"],
        "artifacts": {key: {"path": str(path), "sha256": digest}
                      for key, path, digest in (
                          ("preflight", paths["preflight"], preflight_sha),
                          ("trajectories", paths["trajectories"], trajectory_sha),
                          ("frozen_eval_plan", paths["plan"], plan_sha),
                          ("stability_result", paths["result"], result_sha),
                      )},
    }


def verify(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    prior_digests = _prior_artifact_digests()
    inputs = _load_and_validate_inputs(replay_source=True)
    preflight, trajectory_doc, _ = _preflight_and_trajectory(inputs, prior_digests)
    paths = _artifact_paths(output_root)
    preflight_sha = _load_existing_exact(paths["preflight"], preflight)
    trajectory_sha = _load_existing_exact(paths["trajectories"], trajectory_doc)
    plan = frozen_plan(trajectory_doc, trajectory_sha, preflight_sha)
    plan_sha = _load_existing_exact(paths["plan"], plan)
    expected_result = evaluate(trajectory_doc, plan)
    expected_result["frozen_eval_plan_sha256"] = plan_sha
    result_sha = _load_existing_exact(paths["result"], expected_result)
    return {
        "status": "HMA_LAKE_TRAJECTORY_VERIFY_OK",
        "source_replay": "EXACT_MATCH",
        "replay": "EXACT_MATCH",
        "preflight_sha256": preflight_sha,
        "trajectory_sha256": trajectory_sha,
        "frozen_eval_plan_sha256": plan_sha,
        "stability_result_sha256": result_sha,
        "numeric_stability_result": expected_result["status"],
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
        print(f"HMA_LAKE_TRAJECTORY_{args.command.upper()}_BLOCKED: {exc}")
        return 1
    print(result["status"])
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
