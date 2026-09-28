"""Create a provenance-bound, candidate-only lake inventory linkage.

This is a local, offline analysis of the already approved five-epoch Greater
Himalaya inventory and the bounded NRSC Table 68 extraction.  Spatial overlap
and point containment are candidate relations only: this module never assigns
physical lake identity, territory, events, non-events, risk, or operational
status, and it never downloads data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import geopandas as gpd  # noqa: E402
from pyproj import CRS, Transformer  # noqa: E402
from shapely.geometry import Point  # noqa: E402
from shapely.strtree import STRtree  # noqa: E402
from shapely.validation import explain_validity  # noqa: E402

import india_evidence_register as evidence_register  # noqa: E402
import india_source_registry as source_registry  # noqa: E402
from p5_safe_io import write_once_json, write_once_sidecar  # noqa: E402


SCHEMA = "INDIA_LAKE_EPOCH_CANDIDATE_LINKAGE_V0"
EPOCHS = (1990, 2000, 2010, 2015, 2020)
EXPECTED_FEATURE_COUNTS = {
    1990: 7334,
    2000: 7361,
    2010: 7833,
    2015: 8143,
    2020: 9208,
}
AUTHORITY_FLAGS = dict(evidence_register.AUTHORITY_FLAGS)
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SHA_LINE = re.compile(r"^([0-9a-f]{64})  (.+)$")
FEATURE_ATTRIBUTES = (
    "Id", "GL_ID", "Source", "Date", "Area", "Type", "Region",
    "Error", "Not_RGI", "Elevation", "MG_ID", "MG_ID1", "MG_ID2",
    "NewPro", "PR",
)
REQUIRED_SHAPE_SUFFIXES = (".shp", ".shx", ".dbf", ".prj")


@dataclass(frozen=True)
class SpatialFeature:
    feature_id: str
    ordinal: int
    geometry: Any
    attributes: dict[str, Any]
    geometry_status: str
    geometry_reason: str | None = None

    def as_record(self) -> dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "source_feature_ordinal": self.ordinal,
            "geometry_status": self.geometry_status,
            "geometry_reason": self.geometry_reason,
            "attributes": self.attributes,
        }


@dataclass(frozen=True)
class PolygonSpatialIndex:
    """Reusable immutable tree over valid polygons in source order."""

    features: tuple[SpatialFeature, ...]
    tree: STRtree | None

    @classmethod
    def from_features(cls, features: list[SpatialFeature]) -> "PolygonSpatialIndex":
        valid = tuple(feature for feature in features if _is_valid_polygon(feature))
        return cls(valid, STRtree([feature.geometry for feature in valid])
                   if valid else None)


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json_value(value: Any) -> Any:
    """Convert common GeoPandas/NumPy scalars into strict JSON values."""
    if hasattr(value, "item"):
        try:
            value = value.item()
        except (ValueError, TypeError):
            pass
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except (ValueError, TypeError):
            pass
    return str(value)


def _verify_sidecar(path: Path) -> str:
    sidecar = Path(f"{path}.sha256")
    if not path.is_file() or not sidecar.is_file():
        raise ValueError(f"required artifact or SHA-256 sidecar missing: {path}")
    lines = sidecar.read_text(encoding="utf-8").splitlines()
    if len(lines) != 1:
        raise ValueError(f"invalid sidecar line count: {sidecar}")
    match = SHA_LINE.fullmatch(lines[0])
    if not match or match.group(2) != path.name:
        raise ValueError(f"invalid sidecar format or filename: {sidecar}")
    actual = sha256_file(path)
    if match.group(1) != actual:
        raise ValueError(f"sidecar digest mismatch: {path}")
    return actual


def parse_sha256_manifest(path: Path) -> dict[str, str]:
    """Parse a strict sha256sum manifest, rejecting duplicate/traversal paths."""
    expected: dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line:
            continue
        match = SHA_LINE.fullmatch(line)
        if not match:
            raise ValueError(f"invalid SHA256SUMS line {number}")
        digest, name = match.groups()
        member = PurePosixPath(name)
        if member.is_absolute() or ".." in member.parts or name in expected:
            raise ValueError(f"unsafe or duplicate SHA256SUMS path: {name}")
        expected[name] = digest
    if not expected:
        raise ValueError("SHA256SUMS is empty")
    return expected


def verify_figshare_bundle(evidence_root: Path, register_path: Path,
                           registry_path: Path) -> dict[str, Any]:
    """Validate approvals, pinned source bytes, archive members, and NRSC rows."""
    register_path = register_path.resolve()
    if not register_path.is_file():
        raise ValueError(f"evidence register missing: {register_path}")
    register_doc = json.loads(register_path.read_text(encoding="utf-8"))
    register_problems = evidence_register.validate_register(register_doc)
    if register_problems:
        raise ValueError("evidence register invalid: " + "; ".join(register_problems))
    register_hash = sha256_file(register_path)
    manifest_path = ROOT / "docs/science/ARTIFACT_MANIFEST_V0.json"
    manifest_doc = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_entry = next((item for item in manifest_doc.get("files", [])
                           if item.get("relpath") == str(register_path.relative_to(ROOT))), None)
    if not isinstance(manifest_entry, dict):
        raise ValueError("current artifact manifest does not bind the evidence register")
    if manifest_entry.get("sha256") != register_hash \
            or manifest_entry.get("size_bytes") != register_path.stat().st_size:
        raise ValueError("artifact manifest does not match evidence register bytes")

    evidence_id = "EV:GREATER_HIMALAYA_FIGSHARE_21708590_ZIP"
    records = evidence_register.verified_records(register_doc)
    source_record = records.get(evidence_id)
    if not isinstance(source_record, dict):
        raise ValueError("approved Figshare source is not BYTES_VERIFIED")
    payload = source_record.get("payload")
    if not isinstance(payload, dict) or payload.get("kind") != "LOCAL_PATH_LABEL":
        raise ValueError("Figshare payload is not a resolvable local payload")
    registered_root = Path(str(payload.get("evidence_root", ""))).resolve()
    if registered_root != evidence_root.resolve():
        raise ValueError("provided evidence root differs from registered root")
    relative_payload = Path(str(payload.get("path", "")))
    if relative_payload.is_absolute() or ".." in relative_payload.parts:
        raise ValueError("registered payload path escapes evidence root")
    zip_path = (registered_root / relative_payload).resolve()
    if registered_root not in zip_path.parents:
        raise ValueError("registered payload path escapes evidence root")
    zip_hash = sha256_file(zip_path)
    if zip_hash != source_record.get("sha256") \
            or zip_path.stat().st_size != payload.get("size_bytes"):
        raise ValueError("Figshare archive does not match the evidence register")

    intake_dir = evidence_root / "india-phase0-source-intake"
    intake_decision_path = intake_dir / "INDIA_OBSERVATION_INTAKE_DECISION_V0.json"
    intake_decision_hash = _verify_sidecar(intake_decision_path)
    intake_decision = json.loads(intake_decision_path.read_text(encoding="utf-8"))
    if intake_decision.get("schema") != "INDIA_OBSERVATION_INTAKE_DECISION_V0" \
            or intake_decision.get("decision") != "INTAKE_PAYLOAD" \
            or intake_decision.get("candidate_id") != "GREATER_HIMALAYA_FIGSHARE_21708590" \
            or intake_decision.get("authority") != AUTHORITY_FLAGS:
        raise ValueError("Figshare payload intake decision is absent or out of scope")

    territory_path = intake_dir / "INDIA_ADMIN_TERRITORY_CONTRACT_V0.json"
    territory_hash = _verify_sidecar(territory_path)
    territory_decision = json.loads(territory_path.read_text(encoding="utf-8"))
    if territory_decision.get("decision_state") != "DEFER" \
            or territory_decision.get("authority") != AUTHORITY_FLAGS:
        raise ValueError("territory contract no longer records the required DEFER state")

    archive_dir = zip_path.parent
    checksum_path = archive_dir / "SHA256SUMS.txt"
    member_hashes = parse_sha256_manifest(checksum_path)
    extracted_members: dict[str, str] = {}
    archive_names: set[str] = set()
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = PurePosixPath(info.filename).as_posix()
            if name in archive_names:
                raise ValueError(f"duplicate archive member: {name}")
            archive_names.add(name)
            if name in member_hashes:
                digest = hashlib.sha256(archive.read(info)).hexdigest()
                if digest != member_hashes[name]:
                    raise ValueError(f"archive/member manifest mismatch: {name}")
                local_path = archive_dir / name
                if not local_path.is_file() or sha256_file(local_path) != digest:
                    raise ValueError(f"extracted member mismatch: {name}")
                extracted_members[name] = digest
    missing_members = sorted(set(member_hashes) - archive_names)
    if missing_members:
        raise ValueError("manifest members absent from archive: "
                         + ", ".join(missing_members[:10]))
    if set(extracted_members) != set(member_hashes):
        raise ValueError("not all SHA256SUMS members were verified against archive bytes")

    registry_path = registry_path.resolve()
    registry_hash = _verify_sidecar(registry_path)
    registry_doc = json.loads(registry_path.read_text(encoding="utf-8"))
    registry_problems = source_registry.validate_registry(registry_doc)
    if registry_problems:
        raise ValueError("NRSC inventory registry invalid: "
                         + "; ".join(registry_problems))
    nrsc_entry = next((item for item in registry_doc.get("sources", [])
                       if item.get("id") == "NRSC_GLA_IHR"), None)
    if not isinstance(nrsc_entry, dict):
        raise ValueError("NRSC source record missing from registry")
    nrsc_artifact = intake_dir / "NRSC_GLA_IHR_TABLE_EXTRACTION_V0.json"
    nrsc_hash = _verify_sidecar(nrsc_artifact)
    bounded = nrsc_entry.get("bounded_extraction", {})
    if bounded.get("artifact_sha256") != nrsc_hash:
        raise ValueError("NRSC extraction digest differs from registry V4")
    nrsc_doc = json.loads(nrsc_artifact.read_text(encoding="utf-8"))
    if nrsc_doc.get("authority") != AUTHORITY_FLAGS:
        raise ValueError("NRSC extraction authority flags are not all false")
    nrsc_rows = [row for row in nrsc_doc.get("records", [])
                 if row.get("source_table") == "table_68_ge10ha"]
    if len(nrsc_rows) != 2433 or nrsc_doc.get("extraction", {}).get(
            "tables", {}).get("table_68_ge10ha") != len(nrsc_rows):
        raise ValueError("NRSC Table 68 row count differs from the pinned extraction")
    row_keys = [_nrsc_key(row) for row in nrsc_rows]
    if len(row_keys) != len(set(row_keys)):
        raise ValueError("NRSC Table 68 row keys are not unique")

    return {
        "figshare": {
            "evidence_id": evidence_id,
            "source_version": source_record.get("source_version"),
            "locator": source_record.get("locator"),
            "archive_path": str(zip_path),
            "archive_sha256": zip_hash,
            "archive_size_bytes": zip_path.stat().st_size,
            "sha256sums_path": str(checksum_path),
            "sha256sums_sha256": sha256_file(checksum_path),
            "verified_member_count": len(extracted_members),
            "member_sha256": dict(sorted(extracted_members.items())),
            "unmanifested_archive_members": sorted(archive_names - set(member_hashes)),
        },
        "owner_intake_decision": {
            "path": str(intake_decision_path),
            "sha256": intake_decision_hash,
            "decision": intake_decision["decision"],
            "candidate_id": intake_decision["candidate_id"],
            "decided_by": intake_decision.get("decided_by"),
            "decided_utc": intake_decision.get("decided_utc"),
        },
        "territory_decision": {
            "path": str(territory_path),
            "sha256": territory_hash,
            "decision_state": territory_decision["decision_state"],
        },
        "evidence_register": {
            "path": str(register_path),
            "sha256": register_hash,
            "artifact_manifest_sha256": manifest_entry["sha256"],
            "artifact_manifest_path": str(manifest_path),
        },
        "nrsc": {
            "registry_path": str(registry_path),
            "registry_sha256": registry_hash,
            "extraction_path": str(nrsc_artifact),
            "extraction_sha256": nrsc_hash,
            "table_68_rows": len(nrsc_rows),
            "rows": nrsc_rows,
        },
    }


def _nrsc_key(row: dict[str, Any]) -> str:
    serial = row.get("serial_no")
    compact_id = str(row.get("glacial_lake_id_compact", "")).strip()
    table = str(row.get("source_table", "")).strip()
    if table != "table_68_ge10ha" or not compact_id or serial is None:
        raise ValueError("NRSC Table 68 row lacks its table, serial, or compact lake ID")
    return f"NRSC:T68:{serial}:{compact_id}"


def _shape_component_hashes(epoch: int, member_hashes: dict[str, str]) -> dict[str, str]:
    prefix = f"Glacial_Lake_Inventory/GlacialLake_20220726/GlacialLake_{epoch}"
    selected = {name: digest for name, digest in member_hashes.items()
                if name.startswith(prefix + ".")}
    required = {prefix + suffix for suffix in REQUIRED_SHAPE_SUFFIXES}
    if not required <= set(selected):
        raise ValueError(f"epoch {epoch} lacks a required shape component digest")
    return dict(sorted(selected.items()))


def _feature_attributes(row: Any) -> dict[str, Any]:
    fields = set(row.index)
    return {name: _json_value(row[name]) for name in FEATURE_ATTRIBUTES
            if name in fields}


def load_epoch_features(evidence_root: Path, epoch: int,
                        member_hashes: dict[str, str],
                        expected_crs: CRS | None = None) -> tuple[list[SpatialFeature], CRS, dict[str, Any]]:
    shp = (evidence_root / "india-phase0-source-intake"
           / "observation-inventory-figshare" / "Glacial_Lake_Inventory"
           / "GlacialLake_20220726" / f"GlacialLake_{epoch}.shp")
    gdf = gpd.read_file(shp, engine="pyogrio")
    if len(gdf) != EXPECTED_FEATURE_COUNTS[epoch]:
        raise ValueError(f"epoch {epoch} feature count changed: {len(gdf)}")
    if gdf.crs is None:
        raise ValueError(f"epoch {epoch} has no declared CRS")
    crs = CRS.from_user_input(gdf.crs)
    if not crs.is_projected:
        raise ValueError(f"epoch {epoch} CRS is not projected")
    axes = crs.axis_info
    if not axes or any(axis.unit_conversion_factor is None
                       or not math.isclose(axis.unit_conversion_factor, 1.0)
                       or axis.unit_name.casefold() not in {"metre", "meter"}
                       for axis in axes[:2]):
        raise ValueError(f"epoch {epoch} CRS axes are not metre units")
    if expected_crs is not None and not crs.equals(expected_crs):
        raise ValueError(f"epoch {epoch} CRS differs from other snapshots")
    component_hashes = _shape_component_hashes(epoch, member_hashes)
    features: list[SpatialFeature] = []
    valid_count = 0
    invalid_counts: dict[str, int] = {}
    for ordinal, (_, row) in enumerate(gdf.iterrows(), 1):
        geom = row[gdf.geometry.name]
        attributes = _feature_attributes(row)
        feature_id = f"GH:{epoch}:{ordinal:05d}"
        status = "VALID_POLYGON"
        reason: str | None = None
        if geom is None:
            status, reason = "EXCLUDED_NULL_GEOMETRY", "null geometry"
        elif geom.is_empty:
            status, reason = "EXCLUDED_EMPTY_GEOMETRY", "empty geometry"
        elif geom.geom_type not in {"Polygon", "MultiPolygon"}:
            status, reason = "EXCLUDED_NON_POLYGON", geom.geom_type
        elif not geom.is_valid:
            status, reason = "EXCLUDED_INVALID_GEOMETRY", explain_validity(geom)
        else:
            valid_count += 1
        if reason:
            invalid_counts[status] = invalid_counts.get(status, 0) + 1
        features.append(SpatialFeature(feature_id, ordinal, geom, attributes,
                                       status, reason))
    profile = {
        "epoch": epoch,
        "inventory_period_label": str(epoch),
        "feature_count": len(features),
        "valid_polygon_count": valid_count,
        "excluded_geometry_count": len(features) - valid_count,
        "excluded_geometry_reasons": dict(sorted(invalid_counts.items())),
        "crs_name": crs.name,
        "crs_authority": crs.to_authority(),
        "crs_units": [axis.unit_name for axis in axes[:2]],
        "crs_wkt": crs.to_wkt(),
        "source_component_sha256": component_hashes,
        "feature_id_semantics": "epoch + one-based source feature ordinal; stable only within these exact bound source bytes",
        "features": [feature.as_record() for feature in features],
    }
    return features, crs, profile


def _is_valid_polygon(feature: SpatialFeature) -> bool:
    return (feature.geometry_status == "VALID_POLYGON"
            and feature.geometry is not None
            and not feature.geometry.is_empty
            and feature.geometry.is_valid
            and feature.geometry.geom_type in {"Polygon", "MultiPolygon"})


def build_epoch_relations(left_features: list[SpatialFeature],
                          right_features: list[SpatialFeature]) -> dict[str, Any]:
    """Return topological candidates and unthresholded nearest diagnostics."""
    left_valid = [feature for feature in left_features if _is_valid_polygon(feature)]
    right_valid = [feature for feature in right_features if _is_valid_polygon(feature)]
    left_excluded = [feature.feature_id for feature in left_features
                     if not _is_valid_polygon(feature)]
    right_excluded = [feature.feature_id for feature in right_features
                      if not _is_valid_polygon(feature)]
    if not right_valid:
        return {
            "left_valid_features": len(left_valid),
            "right_valid_features": 0,
            "left_excluded_feature_ids": left_excluded,
            "right_excluded_feature_ids": right_excluded,
            "overlap_candidate_count": 0,
            "touch_only_candidate_count": 0,
            "overlap_candidates": [],
            "nearest_only_diagnostics": [],
        }

    right_geometries = [feature.geometry for feature in right_valid]
    tree = STRtree(right_geometries)
    right_index = {index: feature for index, feature in enumerate(right_valid)}
    raw_edges: list[dict[str, Any]] = []
    positive_left_degree: dict[str, int] = {}
    positive_right_degree: dict[str, int] = {}
    left_positive: set[str] = set()
    left_intersects: set[str] = set()
    for left in left_valid:
        indexes = tree.query(left.geometry, predicate="intersects")
        if len(indexes):
            left_intersects.add(left.feature_id)
        for index in sorted((int(i) for i in indexes),
                            key=lambda i: right_index[i].feature_id):
            right = right_index[index]
            intersection_area = float(left.geometry.intersection(right.geometry).area)
            if not math.isfinite(intersection_area) or intersection_area < 0:
                raise ValueError("non-finite or negative polygon intersection area")
            left_area = float(left.geometry.area)
            right_area = float(right.geometry.area)
            union_area = left_area + right_area - intersection_area
            if not math.isfinite(union_area) or union_area <= 0:
                raise ValueError("invalid union area in polygon overlap")
            is_area_overlap = intersection_area > 0.0
            raw_edges.append({
                "from_feature_id": left.feature_id,
                "to_feature_id": right.feature_id,
                "relation": ("POSITIVE_AREA_OVERLAP_CANDIDATE" if is_area_overlap
                             else "TOUCH_ONLY_CANDIDATE"),
                "intersection_area_m2": intersection_area,
                "intersection_over_union": (intersection_area / union_area
                                             if is_area_overlap else 0.0),
                "fraction_of_from_area": (intersection_area / left_area
                                           if is_area_overlap and left_area > 0 else 0.0),
                "fraction_of_to_area": (intersection_area / right_area
                                         if is_area_overlap and right_area > 0 else 0.0),
                "identity_claim": False,
            })
            if is_area_overlap:
                left_positive.add(left.feature_id)
                positive_left_degree[left.feature_id] = (
                    positive_left_degree.get(left.feature_id, 0) + 1)
                positive_right_degree[right.feature_id] = (
                    positive_right_degree.get(right.feature_id, 0) + 1)
    for edge in raw_edges:
        if edge["relation"] != "POSITIVE_AREA_OVERLAP_CANDIDATE":
            edge["overlap_pattern"] = "TOUCH_ONLY"
            continue
        left_degree = positive_left_degree[edge["from_feature_id"]]
        right_degree = positive_right_degree[edge["to_feature_id"]]
        if left_degree > 1 and right_degree > 1:
            pattern = "MANY_TO_MANY_OVERLAP"
        elif left_degree > 1:
            pattern = "POSSIBLE_SPLIT"
        elif right_degree > 1:
            pattern = "POSSIBLE_MERGE"
        else:
            pattern = "ONE_TO_ONE_OVERLAP_CANDIDATE"
        edge["overlap_pattern"] = pattern
    raw_edges.sort(key=lambda item: (item["from_feature_id"],
                                     item["to_feature_id"]))

    nearest_only: list[dict[str, Any]] = []
    for left in left_valid:
        if left.feature_id in left_intersects:
            continue
        indexes, distances = tree.query_nearest(
            left.geometry, all_matches=True, return_distance=True)
        paired = sorted(
            ((right_index[int(index)], float(distance))
             for index, distance in zip(indexes, distances)),
            key=lambda item: item[0].feature_id,
        )
        nearest_only.append({
            "from_feature_id": left.feature_id,
            "nearest_feature_ids": [item[0].feature_id for item in paired],
            "distance_m": paired[0][1] if paired else None,
            "relation": "NEAREST_ONLY_DIAGNOSTIC_NOT_IDENTITY_EVIDENCE",
        })
    return {
        "left_valid_features": len(left_valid),
        "right_valid_features": len(right_valid),
        "left_excluded_feature_ids": left_excluded,
        "right_excluded_feature_ids": right_excluded,
        "overlap_candidate_count": sum(
            edge["relation"] == "POSITIVE_AREA_OVERLAP_CANDIDATE"
            for edge in raw_edges),
        "touch_only_candidate_count": sum(
            edge["relation"] == "TOUCH_ONLY_CANDIDATE" for edge in raw_edges),
        "positive_overlap_from_features_with_any_candidate": len(left_positive),
        "positive_overlap_degree_gt_one_from_features": sum(
            degree > 1 for degree in positive_left_degree.values()),
        "positive_overlap_degree_gt_one_to_features": sum(
            degree > 1 for degree in positive_right_degree.values()),
        "overlap_candidates": raw_edges,
        "nearest_only_diagnostics": nearest_only,
    }


def build_point_relations(point: Point,
                          features: list[SpatialFeature] | PolygonSpatialIndex
                          ) -> dict[str, Any]:
    """Relate one rounded atlas point to polygons; nearest is diagnostics only."""
    spatial_index = (features if isinstance(features, PolygonSpatialIndex)
                     else PolygonSpatialIndex.from_features(features))
    if not spatial_index.features or spatial_index.tree is None:
        return {"status": "NO_VALID_POLYGONS", "candidates": [],
                "nearest_only_diagnostic": None}
    tree_indexes = sorted(
        (int(i) for i in spatial_index.tree.query(point, predicate="intersects")),
        key=lambda i: spatial_index.features[i].feature_id)
    candidates = []
    for tree_index in tree_indexes:
        feature = spatial_index.features[tree_index]
        inside = bool(feature.geometry.contains(point))
        candidates.append({
            "feature_id": feature.feature_id,
            "relation": "POINT_IN_POLYGON" if inside else "POINT_ON_POLYGON_BOUNDARY",
            "identity_claim": False,
        })
    if candidates:
        status = ("ONE_SPATIAL_CANDIDATE" if len(candidates) == 1
                  else "MULTIPLE_SPATIAL_CANDIDATES")
        nearest = None
    else:
        status = "NO_INTERSECTION_CANDIDATE"
        indexes_near, distances = spatial_index.tree.query_nearest(
            point, all_matches=True, return_distance=True)
        nearest_pairs = sorted(
            ((spatial_index.features[int(tree_index)], float(distance))
             for tree_index, distance in zip(indexes_near, distances)),
            key=lambda item: item[0].feature_id,
        )
        nearest = {
            "feature_ids": [item[0].feature_id for item in nearest_pairs],
            "distance_m": nearest_pairs[0][1] if nearest_pairs else None,
            "relation": "NEAREST_ONLY_DIAGNOSTIC_NOT_IDENTITY_EVIDENCE",
        }
    return {"status": status, "candidates": candidates,
            "nearest_only_diagnostic": nearest}


def _feature_by_id(features: list[SpatialFeature]) -> dict[str, SpatialFeature]:
    return {feature.feature_id: feature for feature in features}


def _point_crosswalk(rows: list[dict[str, Any]], epoch_features: dict[int, list[SpatialFeature]],
                     target_crs: CRS) -> dict[str, Any]:
    transformer = Transformer.from_crs("EPSG:4326", target_crs, always_xy=True)
    spatial_indexes = {
        epoch: PolygonSpatialIndex.from_features(epoch_features[epoch])
        for epoch in EPOCHS
    }
    comparisons: list[dict[str, Any]] = []
    for row in rows:
        key = _nrsc_key(row)
        try:
            latitude = float(row["latitude"])
            longitude = float(row["longitude"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{key}: invalid NRSC coordinates") from exc
        if not math.isfinite(latitude) or not math.isfinite(longitude) \
                or not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            raise ValueError(f"{key}: NRSC coordinates out of range")
        x, y = transformer.transform(longitude, latitude)
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError(f"{key}: CRS transformation returned non-finite point")
        point = Point(x, y)
        by_epoch: dict[str, Any] = {}
        for epoch in EPOCHS:
            relations = build_point_relations(point, spatial_indexes[epoch])
            by_epoch[str(epoch)] = relations
        comparisons.append({
            "nrsc_row_key": key,
            "source_record_id": row.get("source_record_id"),
            "source_serial_no": row.get("serial_no"),
            "source_glacial_lake_id": row.get("glacial_lake_id"),
            "source_glacial_lake_id_compact": row.get("glacial_lake_id_compact"),
            "source_subbasin": row.get("subbasin"),
            "input_point_wgs84": {"latitude": latitude, "longitude": longitude},
            "coordinate_precision_note": "NRSC Table 68 coordinates are printed to 0.001 degree; positional accuracy is not provided or inferred",
            "relations_by_epoch": by_epoch,
        })
    return {
        "row_count": len(comparisons),
        "coordinate_semantics": "printed atlas point transformed from EPSG:4326 to the inventory CRS; no territory classification",
        "candidate_semantics": "topological point-in-polygon relation only; not identity, containment of full lake, or a match decision",
        "nearest_semantics": "unthresholded distance diagnostic only; never promoted to a candidate identity",
        "comparisons": comparisons,
    }


def create_report(evidence_root: Path, register_path: Path,
                  registry_path: Path, repository_head: str | None = None,
                  profile_only: bool = False) -> dict[str, Any]:
    source_bundle = verify_figshare_bundle(evidence_root, register_path,
                                           registry_path)
    member_hashes = source_bundle["figshare"]["member_sha256"]
    epoch_features: dict[int, list[SpatialFeature]] = {}
    epoch_profiles: list[dict[str, Any]] = []
    common_crs: CRS | None = None
    for epoch in EPOCHS:
        features, common_crs, profile = load_epoch_features(
            evidence_root, epoch, member_hashes, common_crs)
        epoch_features[epoch] = features
        epoch_profiles.append(profile)
    if common_crs is None:
        raise ValueError("no epoch CRS was loaded")

    if profile_only:
        profile_inputs = dict(source_bundle)
        profile_inputs["nrsc"] = {
            key: value for key, value in source_bundle["nrsc"].items()
            if key != "rows"
        }
        return {
            "schema": SCHEMA,
            "status": "PROFILE_ONLY_NO_OUTPUT_WRITTEN",
            "repository_head": repository_head,
            "analysis_code_sha256": sha256_file(Path(__file__)),
            "inputs": profile_inputs,
            "epochs": [{key: value for key, value in profile.items()
                        if key != "features"}
                       for profile in epoch_profiles],
            "nrsc_t68_rows": len(source_bundle["nrsc"]["rows"]),
            "profile_scope": "CRS and geometry validity only; no spatial linkage computed",
        }

    epoch_pair_relations = []
    for left_epoch, right_epoch in zip(EPOCHS, EPOCHS[1:]):
        relations = build_epoch_relations(epoch_features[left_epoch],
                                          epoch_features[right_epoch])
        epoch_pair_relations.append({
            "from_epoch": left_epoch,
            "to_epoch": right_epoch,
            "temporal_semantics": "two inventory snapshots; not continuous observation",
            **relations,
        })
    nrsc_rows = source_bundle["nrsc"].pop("rows")
    point_relations = _point_crosswalk(nrsc_rows, epoch_features, common_crs)

    registry_doc = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    registry_source_ids = {source.get("id") for source in registry_doc.get("sources", [])
                           if isinstance(source, dict)}
    registry_gap = ("Registry V4 predates the Figshare intake and has no source row for it; this result binds the V3 evidence register and the sealed owner intake decision directly. A successor registry should reconcile this before readiness claims."
                    if "GREATER_HIMALAYA_FIGSHARE_21708590" not in registry_source_ids
                    else None)
    return {
        "schema": SCHEMA,
        "version": 0,
        "status": "CANDIDATE_LINKAGE_ONLY",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "repository_head": repository_head,
        "analysis_code_sha256": sha256_file(Path(__file__)),
        "claim_scope": "research_only_phase0_spatial_candidate_linkage",
        "authority": dict(AUTHORITY_FLAGS),
        "inputs": source_bundle,
        "protocol": {
            "snapshot_epochs": list(EPOCHS),
            "expected_feature_counts": {str(k): v for k, v in EXPECTED_FEATURE_COUNTS.items()},
            "feature_id_rule": "GH:<epoch>:<one-based source feature ordinal>; meaningful only with bound archive/member hashes",
            "epoch_link_rule": "adjacent-epoch polygon topological intersects; positive area and zero-area touch are separate candidate relations",
            "overlap_metrics": ["intersection area m2", "intersection-over-union", "fraction of each polygon area"],
            "overlap_pattern_labels": "one-to-one/split/merge/many-to-many describe graph degree patterns only; they do not establish lake identity or physical splitting/merging",
            "point_link_rule": "NRSC printed coordinate point transformed from WGS84 and tested against each valid epoch polygon; no arbitrary distance cutoff",
            "nearest_rule": "nearest geometry is reported only when no topological intersection candidate exists; distances never create links",
            "invalid_geometry_rule": "null, empty, non-polygon, and invalid geometries are preserved in the feature inventory and excluded from spatial operations; no repair or imputation",
            "attribute_use": "Area, Error, MG_ID, and source identifiers are descriptive only and do not determine linkage",
            "territory_rule": "administrative territory remains UNASSESSED; the sealed owner territory contract is DEFER",
        },
        "epoch_feature_inventory": epoch_profiles,
        "adjacent_epoch_relations": epoch_pair_relations,
        "nrsc_t68_spatial_relations": point_relations,
        "unresolved_registry_note": registry_gap,
        "conclusions": {
            "physical_lake_identity_established": False,
            "event_or_recurrence_identity_established": False,
            "verified_non_event_intervals_established": False,
            "administrative_territory_assigned": False,
            "event_weather_or_risk_claim_authorized": False,
            "operational_authority": False,
        },
        "limitations": [
            "The Figshare layers are five epoch snapshots, not continuous annual observation; absence from an epoch cannot establish a non-event interval.",
            "The atlas coordinates are rounded printed points with unknown positional accuracy; polygon containment is only a candidate spatial relation.",
            "Feature ordinals are not durable physical lake identifiers; the same glacier anchor does not make a stable lake ID.",
            "The NRSC rows span a transboundary basin domain and must not be described as an India-administered denominator.",
            "Eleven invalid 2020 geometries, if confirmed by the pinned bytes, are excluded from spatial operations and remain listed with reasons.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--output", type=Path,
                        help="write-once JSON output path, required unless --profile-only")
    parser.add_argument("--repository-head",
                        help="optional git HEAD recorded by the caller")
    parser.add_argument("--register", type=Path,
                        default=ROOT / "docs/science/INDIA_EVIDENCE_REGISTER_V3.json")
    parser.add_argument("--nrsc-registry", type=Path,
                        default=ROOT / "docs/science/INDIA_INVENTORY_REGISTRY_V4.json")
    parser.add_argument("--profile-only", action="store_true",
                        help="verify inputs and print epoch profile; do not write an artifact")
    args = parser.parse_args(argv)
    try:
        report = create_report(args.evidence_root.resolve(), args.register,
                               args.nrsc_registry, args.repository_head,
                               profile_only=args.profile_only)
        if args.profile_only:
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0
        if args.output is None:
            parser.error("--output is required unless --profile-only is set")
        output = args.output.resolve()
        root = args.evidence_root.resolve()
        if root not in output.parents:
            raise ValueError("output must be located under the approved evidence root")
        sidecar = Path(f"{output}.sha256")
        if output.exists() or sidecar.exists():
            raise FileExistsError(f"write-once output already exists: {output}")
        digest = write_once_json(output, report)
        sidecar_digest = write_once_sidecar(output)
        if digest != sidecar_digest:
            raise ValueError("output sidecar did not bind the published report")
        print(f"LAKE_EPOCH_LINKAGE_OK: {output}")
        print(f"sha256={digest}")
        print(f"table68_rows={report['nrsc_t68_spatial_relations']['row_count']}")
        return 0
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        print(f"LAKE_EPOCH_LINKAGE_BLOCKED: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
