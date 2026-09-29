"""HMA event-linkability census (formal closure of the dormant branch).

CENSUS ONLY.  This script counts how many catalog GLOF events *could ever be
linked* to a lake trajectory in the pinned Greater Himalaya inventory.  It is a
structural ceiling on any future event-association test: census only; no
event-lake identity or association claim is made for any row.  The
event-association branch remains DORMANT and every authority flag is false.

Predeclared protocol (frozen before computation; deviations would be POST_HOC):

Inputs (all SHA-256 verified before use):
  * Catalog ``HMAGLOFDB.csv`` (``p5-glof-2026-09-19/glof-events``), verified
    against its ``.sha256`` sidecar; expected 768 data rows.
  * ``INDIA_HMAGLOFDB_CROSSWALK_V2.json`` (sidecar-verified): authoritative
    ``source_record_id`` -> ``row_ordinal`` keys.  Every record's
    ``source.row_sha256`` is recomputed from the CSV row at ``row_ordinal``
    (sha256 of ``json.dumps(row, sort_keys=True, ensure_ascii=False)``) and
    must match exactly -- a byte-level binding of catalog rows to record keys.
  * ``INDIA_EVENT_ADJUDICATION_V1.json`` (sidecar-verified): authoritative
    ``mechanism_certainty``, ``eligibility``, ``independence_status`` per
    ``source_record_id``.  Its key set must equal the crosswalk key set.
  * ``INDIA_LAKE_EPOCH_CANDIDATE_LINKAGE_V0.json`` (sidecar-verified): epoch
    feature IDs/counts and the pinned shapefile member digests
    (``inputs.figshare.member_sha256``).  Every ``.shp/.shx/.dbf/.prj`` of
    epochs {1990,2000,2010,2015,2020} is hashed and must match those digests.
  * ``HMA_LAKE_TRAJECTORIES_V2.json`` (sidecar-verified): candidate paths with
    ``source_feature_ids``/``epochs_observed`` for the coverage-depth join.

Census rules:
  1. Event point = ``Lat_lake``/``Lon_lake`` (catalog lake coordinate, WGS84).
     Rows with a parseable finite pair are counted separately from
     unparseable rows -- no silent drops.  A secondary census repeats every
     step on ``Lat_impact``/``Lon_impact`` and is reported separately; the
     headline ceiling uses the lake point only.
  2. Event year = the crosswalk's authoritative ``date.year`` (exact
     ``Year_exact`` integer else integer ``Year_approx`` else unknown).
     "Inside window" = year in [1990, 2020] inclusive; unparseable years are
     counted as UNPARSEABLE, never dropped.
  3. Points are transformed EPSG:4326 -> the inventory CRS (Asia North Albers
     Equal Area Conic, ESRI:102025, metre axes; verified per epoch by
     ``india_lake_epoch_linkage.load_epoch_features``).  Nearest valid
     inventory polygon (any epoch) is found with ``sjoin_nearest``;
     equidistant ties keep *all* matched feature IDs and the qualifier "any
     matched feature satisfies the condition" is applied.
  4. Distance thresholds (kilometres): {2, 5, 10}; the primary linkage
     threshold is 5 km.
  5. Coverage join: a matched feature ID maps to the candidate paths listing
     it in ``source_feature_ids``; its coverage depth = max
     ``len(epochs_observed)`` over those paths (0 if in no path).  For events
     <=5 km we count whether any matched feature has depth >=3 and >=5.
  6. Splits: adjudication ``mechanism_certainty``, adjudication
     ``independence_status``, adjudication ``eligibility``, and catalog
     ``Country`` (a catalog string, never a territory classification).
  7. Headline ceiling = min over the three marginal ceilings: rows with any
     inventory match <=5 km; rows <=5 km whose matched lake is on a >=3-epoch
     path; rows inside the 1990-2020 window.  The joint count satisfying all
     conditions simultaneously is also reported (it is <= the min).

Output: write-once JSON + .sha256 sidecar via ``p5_safe_io``.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import geopandas as gpd  # noqa: E402
from pyproj import Transformer  # noqa: E402
from shapely.geometry import Point  # noqa: E402

import india_evidence_register as evidence_register  # noqa: E402
import india_lake_epoch_linkage as linkage  # noqa: E402
from p5_safe_io import write_once_json, write_once_sidecar  # noqa: E402

SCHEMA = "HMA_EVENT_LINKABILITY_CENSUS_V0"
EVIDENCE_ROOT = Path("/Users/sanjayb/nepal-event-anomaly-evidence")
INTAKE_ROOT = EVIDENCE_ROOT / "india-phase0-source-intake"
CATALOG_PATH = (
    EVIDENCE_ROOT / "p5-glof-2026-09-19" / "glof-events" / "HMAGLOFDB.csv"
)
CROSSWALK_PATH = INTAKE_ROOT / "INDIA_HMAGLOFDB_CROSSWALK_V2.json"
ADJUDICATION_PATH = INTAKE_ROOT / "INDIA_EVENT_ADJUDICATION_V1.json"
LINKAGE_PATH = INTAKE_ROOT / "INDIA_LAKE_EPOCH_CANDIDATE_LINKAGE_V0.json"
TRAJECTORY_PATH = (
    INTAKE_ROOT / "hma-lake-trajectory-poc-v2" / "HMA_LAKE_TRAJECTORIES_V2.json"
)
SHAPE_MEMBER_DIR = (
    Path("observation-inventory-figshare") / "Glacial_Lake_Inventory"
    / "GlacialLake_20220726"
)
EPOCHS = linkage.EPOCHS
EXPECTED_CATALOG_ROWS = 768
KM_THRESHOLDS = (2.0, 5.0, 10.0)
PRIMARY_THRESHOLD_KM = 5.0
DEPTH_THRESHOLDS = (3, 5)
TEMPORAL_WINDOW = (1990, 2020)
AUTHORITY_FLAGS = dict(evidence_register.AUTHORITY_FLAGS)
EVENT_BRANCH_STATUS = "DORMANT"
DORMANCY_STATEMENT = (
    "census only; no event-lake identity or association claim"
)
SHAPE_COMPONENT_SUFFIXES = (".shp", ".shx", ".dbf", ".prj")
SHAPE_MEMBER_PREFIX = (
    "Glacial_Lake_Inventory/GlacialLake_20220726/GlacialLake_{epoch}{suffix}"
)


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


SHA_LINE = re.compile(r"^([0-9a-f]{64})(  (.+))?$")


def verify_sidecar(path: Path) -> str:
    """Verify a ``<hex>  <name>`` or bare ``<hex>`` sidecar; fail on drift."""
    path = Path(path)
    sidecar = Path(f"{path}.sha256")
    if not path.is_file() or not sidecar.is_file():
        raise ValueError(f"required artifact or SHA-256 sidecar missing: {path}")
    lines = sidecar.read_text(encoding="utf-8").splitlines()
    if len(lines) != 1:
        raise ValueError(f"invalid sidecar line count: {sidecar}")
    match = SHA_LINE.fullmatch(lines[0])
    if not match:
        raise ValueError(f"invalid sidecar format: {sidecar}")
    if match.group(3) is not None and match.group(3) != path.name:
        raise ValueError(f"sidecar filename mismatch: {sidecar}")
    actual = sha256_file(path)
    if match.group(1) != actual:
        raise ValueError(f"sidecar digest mismatch: {path}")
    return actual


def _row_digest(row: dict[str, Any]) -> str:
    """Canonical catalog-row digest (identical to india_event_crosswalk)."""
    return hashlib.sha256(
        json.dumps(row, sort_keys=True, ensure_ascii=False)
        .encode("utf-8")).hexdigest()


def _load_json_sidecar_verified(path: Path) -> tuple[dict[str, Any], str]:
    digest = verify_sidecar(path)
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    return doc, digest


def load_catalog(csv_path: Path) -> list[dict[str, str]]:
    with Path(csv_path).open(encoding="cp1252", newline="") as handle:
        return list(csv.DictReader(handle))


def bind_catalog_rows(
    rows: list[dict[str, str]], crosswalk: dict[str, Any]
) -> list[dict[str, Any]]:
    """Re-derive every crosswalk row key from catalog bytes; fail on drift."""
    records = crosswalk.get("records")
    if not isinstance(records, list) or len(records) != len(rows):
        raise ValueError("crosswalk record count differs from catalog rows")
    if len(rows) != EXPECTED_CATALOG_ROWS:
        raise ValueError(f"catalog row count changed: {len(rows)}")
    bound: list[dict[str, Any]] = []
    for record in records:
        source = record.get("source") or {}
        ordinal = source.get("row_ordinal")
        sid = record.get("source_record_id")
        if not isinstance(ordinal, int) or not 1 <= ordinal <= len(rows):
            raise ValueError(f"crosswalk row_ordinal out of range: {ordinal}")
        row = rows[ordinal - 1]
        if _row_digest(row) != source.get("row_sha256"):
            raise ValueError(f"catalog row digest mismatch at ordinal {ordinal}")
        if source.get("record_id") != (row.get("GF_ID") or "").strip():
            raise ValueError(f"crosswalk GF_ID mismatch at ordinal {ordinal}")
        bound.append({"source_record_id": sid, "row_ordinal": ordinal,
                      "row": row, "crosswalk": record})
    sids = [b["source_record_id"] for b in bound]
    if len(sids) != len(set(sids)):
        raise ValueError("crosswalk source_record_id keys are not unique")
    return bound


def bind_adjudication(
    adjudication: dict[str, Any], record_ids: set[str]
) -> dict[str, dict[str, Any]]:
    records = adjudication.get("records")
    if not isinstance(records, list):
        raise ValueError("adjudication artifact lacks a record list")
    by_id: dict[str, dict[str, Any]] = {}
    for record in records:
        sid = record.get("source_record_id")
        if sid in by_id:
            raise ValueError(f"duplicate adjudication key: {sid}")
        by_id[sid] = record
    if set(by_id) != record_ids:
        missing = sorted(record_ids - set(by_id))[:5]
        extra = sorted(set(by_id) - record_ids)[:5]
        raise ValueError(
            f"adjudication key set differs from crosswalk: "
            f"missing={missing} extra={extra}")
    return by_id


def verify_shape_members(
    evidence_root: Path, member_hashes: dict[str, str]
) -> dict[str, dict[str, str]]:
    """Hash every required shape component against the pinned digests."""
    verified: dict[str, dict[str, str]] = {}
    intake_root = evidence_root / "india-phase0-source-intake"
    for epoch in EPOCHS:
        components: dict[str, str] = {}
        for suffix in SHAPE_COMPONENT_SUFFIXES:
            member = SHAPE_MEMBER_PREFIX.format(epoch=epoch, suffix=suffix)
            expected = member_hashes.get(member)
            if not expected:
                raise ValueError(f"pinned member digest absent: {member}")
            local = intake_root / SHAPE_MEMBER_DIR / f"GlacialLake_{epoch}{suffix}"
            if not local.is_file():
                raise ValueError(f"shapefile component missing: {local}")
            actual = sha256_file(local)
            if actual != expected:
                raise ValueError(f"shapefile digest mismatch: {local}")
            components[member] = actual
        verified[str(epoch)] = components
    return verified


def load_inventory(
    evidence_root: Path, linkage_doc: dict[str, Any]
) -> tuple[gpd.GeoDataFrame, Any, dict[str, Any]]:
    """Load all valid epoch polygons and bind them to the linkage artifact."""
    member_hashes = (linkage_doc.get("inputs") or {}).get(
        "figshare", {}).get("member_sha256")
    if not isinstance(member_hashes, dict):
        raise ValueError("linkage artifact lacks pinned member digests")
    shape_digests = verify_shape_members(evidence_root, member_hashes)

    artifact_features: dict[int, set[str]] = {}
    for entry in linkage_doc.get("epoch_feature_inventory") or []:
        epoch = entry.get("epoch")
        ids = {f.get("feature_id") for f in entry.get("features") or []}
        artifact_features[epoch] = ids
        if entry.get("feature_count") != len(entry.get("features") or []):
            raise ValueError(f"linkage epoch {epoch} feature_count mismatch")

    rows_out: list[dict[str, Any]] = []
    common_crs = None
    epoch_profiles: list[dict[str, Any]] = []
    for epoch in EPOCHS:
        features, common_crs, profile = linkage.load_epoch_features(
            evidence_root, epoch, member_hashes, common_crs)
        loaded_ids = {f.feature_id for f in features}
        if artifact_features.get(epoch) != loaded_ids:
            raise ValueError(
                f"loaded epoch {epoch} feature IDs differ from linkage artifact")
        valid = [f for f in features
                 if f.geometry_status == "VALID_POLYGON"]
        epoch_profiles.append({
            "epoch": epoch,
            "feature_count": len(features),
            "valid_polygon_count": len(valid),
            "excluded_geometry_count": len(features) - len(valid),
            "source_component_sha256": shape_digests[str(epoch)],
        })
        for f in valid:
            rows_out.append({"feature_id": f.feature_id, "epoch": epoch,
                             "geometry": f.geometry})
    if not rows_out or common_crs is None:
        raise ValueError("inventory yielded no valid polygons")
    gdf = gpd.GeoDataFrame(
        rows_out, geometry="geometry", crs=common_crs.to_wkt())
    profile = {"epochs": epoch_profiles,
               "valid_polygon_total": len(rows_out),
               "crs_name": common_crs.name,
               "crs_authority": list(common_crs.to_authority() or [])}
    return gdf, common_crs, profile


def trajectory_depth(trajectory_doc: dict[str, Any]) -> tuple[dict[str, int], dict[str, Any]]:
    """feature_id -> max len(epochs_observed) over candidate paths."""
    trajectories = trajectory_doc.get("trajectories")
    if not isinstance(trajectories, list):
        raise ValueError("trajectory artifact lacks a trajectory list")
    depth: dict[str, int] = {}
    path_depth_counts: Counter[int] = Counter()
    for path in trajectories:
        observed = path.get("epochs_observed") or []
        if not isinstance(observed, list):
            raise ValueError("trajectory epochs_observed is not a list")
        n = len(set(observed))
        path_depth_counts[n] += 1
        for fid in path.get("source_feature_ids") or []:
            if depth.get(fid, 0) < n:
                depth[fid] = n
    summary = {
        "candidate_path_count": len(trajectories),
        "path_depth_distribution": dict(sorted(
            (str(k), v) for k, v in path_depth_counts.items())),
        "features_on_any_path": len(depth),
        "features_on_ge3_epoch_paths": sum(
            1 for v in depth.values() if v >= 3),
        "features_on_ge5_epoch_paths": sum(
            1 for v in depth.values() if v >= 5),
    }
    return depth, summary


def _parse_point(lat_raw: Any, lon_raw: Any) -> tuple[float, float] | None:
    try:
        lat = float(str(lat_raw).strip())
        lon = float(str(lon_raw).strip())
    except (ValueError, TypeError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon)):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return (lat, lon)


def _event_year(crosswalk_record: dict[str, Any]) -> tuple[int | None, str]:
    date_block = crosswalk_record.get("date") or {}
    year = date_block.get("year")
    basis = date_block.get("basis") or "unknown"
    if isinstance(year, bool) or not isinstance(year, int):
        return None, "unparseable"
    return year, str(basis)


def nearest_matches(
    points: gpd.GeoDataFrame, polygons: gpd.GeoDataFrame
) -> dict[int, dict[str, Any]]:
    """sjoin_nearest; equidistant ties retain every matched feature ID."""
    joined = points.sjoin_nearest(polygons, distance_col="distance_m")
    result: dict[int, dict[str, Any]] = {}
    for event_idx, group in joined.groupby(level=0):
        dist_min = float(group["distance_m"].min())
        tied = group[group["distance_m"] == group["distance_m"].min()]
        result[int(event_idx)] = {
            "distance_m": dist_min,
            "distance_km": dist_min / 1000.0,
            "matched_feature_ids": sorted(str(v) for v in tied["feature_id"]),
            "matched_epochs": sorted(int(v) for v in tied["epoch"].unique()),
            "tie_count": int(len(tied)),
        }
    return result


def census_point_set(
    events: list[dict[str, Any]],
    polygons: gpd.GeoDataFrame,
    crs: Any,
    depth: dict[str, int],
    lat_col: str,
    lon_col: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Run the nearest-polygon census over one coordinate pair column set."""
    transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    parseable: list[int] = []
    per_event: list[dict[str, Any] | None] = [None] * len(events)
    geometries: list[Point] = []
    for idx, event in enumerate(events):
        point = _parse_point(event["row"].get(lat_col),
                             event["row"].get(lon_col))
        if point is None:
            continue
        lat, lon = point
        x, y = transformer.transform(lon, lat)
        geometries.append(Point(x, y))
        parseable.append(idx)
    gdf = gpd.GeoDataFrame(
        {"event_ordinal": parseable}, geometry=geometries, crs=crs.to_wkt())
    matches = nearest_matches(gdf, polygons) if len(gdf) else {}

    within = {str(int(t)): 0 for t in KM_THRESHOLDS}
    depth_within_primary = {str(d): 0 for d in DEPTH_THRESHOLDS}
    epoch_touch: Counter[int] = Counter()
    distances: list[float] = []
    for position, event_idx in enumerate(parseable):
        match = matches.get(position)
        if match is None:
            raise ValueError("sjoin_nearest returned no candidate for a point")
        distances.append(match["distance_km"])
        event = events[event_idx]
        for threshold in KM_THRESHOLDS:
            if match["distance_km"] <= threshold:
                within[str(int(threshold))] += 1
        for epoch in match["matched_epochs"]:
            epoch_touch[epoch] += 1
        matched_depth = max(
            (depth.get(fid, 0) for fid in match["matched_feature_ids"]),
            default=0)
        record = {
            "distance_km": round(match["distance_km"], 6),
            "matched_feature_ids": match["matched_feature_ids"],
            "matched_epochs": match["matched_epochs"],
            "tie_count": match["tie_count"],
            "within_km": {str(int(t)): match["distance_km"] <= t
                          for t in KM_THRESHOLDS},
            "matched_max_path_epochs": matched_depth,
        }
        if match["distance_km"] <= PRIMARY_THRESHOLD_KM:
            for d in DEPTH_THRESHOLDS:
                if matched_depth >= d:
                    depth_within_primary[str(d)] += 1
        per_event[event_idx] = record

    ordered = sorted(distances)
    n = len(ordered)
    summary = {
        "coordinate_columns": [lat_col, lon_col],
        "points_total": len(events),
        "parseable_points": n,
        "unparseable_points": len(events) - n,
        "unparseable_source_record_ids": sorted(
            events[i]["source_record_id"]
            for i, r in enumerate(per_event) if r is None),
        "nearest_distance_km": {
            "min": round(ordered[0], 6) if n else None,
            "median": round(ordered[n // 2], 6) if n else None,
            "max": round(ordered[-1], 6) if n else None,
        },
        "within_km_counts": within,
        "events_whose_nearest_includes_epoch": dict(sorted(
            (str(k), v) for k, v in epoch_touch.items())),
        f"within_{int(PRIMARY_THRESHOLD_KM)}km_path_depth_counts":
            depth_within_primary,
    }
    return summary, per_event


def _split_bucket(
    events: list[dict[str, Any]],
    lake_records: list[dict[str, Any] | None],
    keyfunc,
) -> dict[str, dict[str, int]]:
    buckets: dict[str, dict[str, int]] = {}

    def _slot(label: str) -> dict[str, int]:
        return buckets.setdefault(label, {
            "rows": 0,
            "year_in_window": 0,
            "year_outside_window": 0,
            "year_unparseable": 0,
            "lake_point_le_5km": 0,
            "lake_point_le_5km_path_ge3": 0,
            "lake_point_le_5km_path_ge5": 0,
            "joint_all_ceiling_conditions": 0,
        })

    for event, rec in zip(events, lake_records):
        labels = keyfunc(event)
        year = event["event_year"]
        in_window = year is not None and TEMPORAL_WINDOW[0] <= year <= TEMPORAL_WINDOW[1]
        le5 = rec is not None and rec["within_km"]["5"]
        ge3 = le5 and rec["matched_max_path_epochs"] >= 3
        ge5 = le5 and rec["matched_max_path_epochs"] >= 5
        joint = bool(le5 and ge3 and in_window)
        for label in labels:
            slot = _slot(label)
            slot["rows"] += 1
            if year is None:
                slot["year_unparseable"] += 1
            elif in_window:
                slot["year_in_window"] += 1
            else:
                slot["year_outside_window"] += 1
            if le5:
                slot["lake_point_le_5km"] += 1
            if ge3:
                slot["lake_point_le_5km_path_ge3"] += 1
            if ge5:
                slot["lake_point_le_5km_path_ge5"] += 1
            if joint:
                slot["joint_all_ceiling_conditions"] += 1
    return dict(sorted(buckets.items()))


def build_census(evidence_root: Path) -> dict[str, Any]:
    evidence_root = Path(evidence_root).resolve()

    intake_root = evidence_root / "india-phase0-source-intake"
    catalog_path = (evidence_root / "p5-glof-2026-09-19" / "glof-events"
                    / "HMAGLOFDB.csv")
    crosswalk_path = intake_root / CROSSWALK_PATH.name
    adjudication_path = intake_root / ADJUDICATION_PATH.name
    linkage_path = intake_root / LINKAGE_PATH.name
    trajectory_path = (intake_root / "hma-lake-trajectory-poc-v2"
                       / TRAJECTORY_PATH.name)

    catalog_sha = verify_sidecar(catalog_path)
    crosswalk, crosswalk_sha = _load_json_sidecar_verified(crosswalk_path)
    adjudication, adjudication_sha = _load_json_sidecar_verified(adjudication_path)
    linkage_doc, linkage_sha = _load_json_sidecar_verified(linkage_path)
    trajectory_doc, trajectory_sha = _load_json_sidecar_verified(trajectory_path)
    for doc, name in ((adjudication, "adjudication"),
                      (linkage_doc, "lake-epoch linkage"),
                      (trajectory_doc, "trajectories")):
        if doc.get("authority") != AUTHORITY_FLAGS:
            raise ValueError(f"{name} authority flags are not all false")
    if trajectory_doc.get("event_association_branch") != EVENT_BRANCH_STATUS:
        raise ValueError("trajectory artifact no longer marks the "
                         "event-association branch DORMANT")

    rows = load_catalog(catalog_path)
    bound = bind_catalog_rows(rows, crosswalk)
    adjud_by_id = bind_adjudication(
        adjudication, {b["source_record_id"] for b in bound})

    polygons, crs, inventory_profile = load_inventory(
        evidence_root, linkage_doc)
    depth, trajectory_summary = trajectory_depth(trajectory_doc)

    events: list[dict[str, Any]] = []
    for b in bound:
        record = b["crosswalk"]
        adjud = adjud_by_id[b["source_record_id"]]["adjudication"]
        episode = adjud_by_id[b["source_record_id"]]["episode"]
        year, year_basis = _event_year(record)
        events.append({
            "source_record_id": b["source_record_id"],
            "row_ordinal": b["row_ordinal"],
            "row": b["row"],
            "gf_id": (b["row"].get("GF_ID") or "").strip(),
            "country": (b["row"].get("Country") or "").strip(),
            "catalog_mechanism": (b["row"].get("Mechanism") or "").strip(),
            "event_year": year,
            "event_year_basis": year_basis,
            "mechanism_certainty": adjud.get("mechanism_certainty"),
            "eligibility": adjud.get("eligibility"),
            "independence_status": episode.get("independence_status"),
        })

    lake_summary, lake_records = census_point_set(
        events, polygons, crs, depth, "Lat_lake", "Lon_lake")
    impact_summary, impact_records = census_point_set(
        events, polygons, crs, depth, "Lat_impact", "Lon_impact")

    parse_audit = {
        "catalog_rows": len(events),
        "lake_point": {"parseable": lake_summary["parseable_points"],
                       "unparseable": lake_summary["unparseable_points"]},
        "impact_point": {"parseable": impact_summary["parseable_points"],
                         "unparseable": impact_summary["unparseable_points"]},
        "event_year": dict(sorted(Counter(
            e["event_year_basis"] for e in events).items())),
    }

    le5_flags = []
    ge3_flags = []
    ge5_flags = []
    window_flags = []
    joint_flags = []
    for event, rec in zip(events, lake_records):
        in_window = (event["event_year"] is not None
                     and TEMPORAL_WINDOW[0] <= event["event_year"]
                     <= TEMPORAL_WINDOW[1])
        le5 = rec is not None and rec["within_km"]["5"]
        ge3 = le5 and rec["matched_max_path_epochs"] >= 3
        ge5 = le5 and rec["matched_max_path_epochs"] >= 5
        joint = bool(le5 and ge3 and in_window)
        le5_flags.append(le5)
        ge3_flags.append(ge3)
        ge5_flags.append(ge5)
        window_flags.append(in_window)
        joint_flags.append(joint)

    n_le5 = sum(le5_flags)
    n_ge3 = sum(ge3_flags)
    n_ge5 = sum(ge5_flags)
    n_window = sum(window_flags)
    n_joint = sum(joint_flags)
    binding_ceiling = min(n_le5, n_ge3, n_window)
    headline = {
        "catalog_rows": len(events),
        "lake_point_parseable": lake_summary["parseable_points"],
        "any_inventory_match_le_5km": n_le5,
        "le_5km_matched_lake_on_ge3_epoch_path": n_ge3,
        "le_5km_matched_lake_on_ge5_epoch_path": n_ge5,
        "event_year_in_1990_2020_inclusive": n_window,
        "le_5km_and_year_in_window": sum(
            1 for a, w in zip(le5_flags, window_flags) if a and w),
        "joint_all_ceiling_conditions": n_joint,
        "binding_ceiling_min_of_marginals": binding_ceiling,
        "binding_ceiling_rule": (
            "min(any match <=5km, <=5km with >=3-epoch path coverage, "
            "year inside 1990-2020); a structural ceiling only"),
    }

    splits = {
        "by_mechanism_certainty": _split_bucket(
            events, lake_records,
            lambda e: [str(e["mechanism_certainty"])]),
        "by_independence_status": _split_bucket(
            events, lake_records,
            lambda e: [str(e["independence_status"])]),
        "by_eligibility": _split_bucket(
            events, lake_records,
            lambda e: [str(e["eligibility"])]),
        "by_catalog_country_string": _split_bucket(
            events, lake_records,
            lambda e: [str(e["country"]) or "EMPTY"]),
        "by_catalog_mechanism_string": _split_bucket(
            events, lake_records,
            lambda e: [str(e["catalog_mechanism"]) or "EMPTY"]),
    }

    per_event = []
    for event, lake_rec, impact_rec in zip(events, lake_records, impact_records):
        per_event.append({
            "source_record_id": event["source_record_id"],
            "row_ordinal": event["row_ordinal"],
            "gf_id": event["gf_id"],
            "country": event["country"],
            "event_year": event["event_year"],
            "event_year_basis": event["event_year_basis"],
            "mechanism_certainty": event["mechanism_certainty"],
            "eligibility": event["eligibility"],
            "independence_status": event["independence_status"],
            "lake_point": lake_rec,
            "impact_point": impact_rec,
        })

    return {
        "schema": SCHEMA,
        "version": 0,
        "status": "CENSUS_COMPLETE_NO_ASSOCIATION_CLAIM",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "claim_scope": "research_only_structural_linkability_ceiling",
        "event_association_branch": EVENT_BRANCH_STATUS,
        "dormancy_statement": DORMANCY_STATEMENT,
        "authority": dict(AUTHORITY_FLAGS),
        "analysis_code_sha256": sha256_file(Path(__file__)),
        "inputs": {
            "catalog": {"path": str(catalog_path), "sha256": catalog_sha},
            "crosswalk": {"path": str(crosswalk_path), "sha256": crosswalk_sha},
            "adjudication": {"path": str(adjudication_path),
                             "sha256": adjudication_sha},
            "lake_epoch_linkage": {"path": str(linkage_path),
                                   "sha256": linkage_sha},
            "trajectories": {"path": str(trajectory_path),
                             "sha256": trajectory_sha},
        },
        "protocol": {
            "event_point_rule": (
                "Lat_lake/Lon_lake (WGS84) transformed into the pinned "
                "inventory CRS; Lat_impact/Lon_impact reported as a separate "
                "secondary census only"),
            "event_year_rule": (
                "crosswalk date.year (Year_exact integer else Year_approx "
                "integer else UNPARSEABLE); no silent drops"),
            "nearest_rule": (
                "geopandas sjoin_nearest to valid inventory polygons of all "
                "five epochs; equidistant ties keep all matched feature IDs "
                "and the 'any matched feature' qualifier applies"),
            "distance_thresholds_km": list(KM_THRESHOLDS),
            "primary_threshold_km": PRIMARY_THRESHOLD_KM,
            "coverage_depth_rule": (
                "matched feature_id -> candidate paths via "
                "source_feature_ids; depth = max len(epochs_observed) over "
                "paths containing it; counted at >=3 and >=5"),
            "temporal_window_inclusive": list(TEMPORAL_WINDOW),
            "catalog_country_rule": (
                "Country is a catalog string used for descriptive splits; it "
                "is never a territory classification"),
            "ceiling_rule": (
                "binding ceiling = min of the three marginal ceilings; the "
                "joint count is the tighter realized bound"),
        },
        "inventory": inventory_profile,
        "trajectory_coverage": trajectory_summary,
        "parse_audit": parse_audit,
        "lake_point_census": lake_summary,
        "impact_point_census": impact_summary,
        "splits": splits,
        "headline": headline,
        "per_event_records": per_event,
        "limitations": [
            "Spatial proximity is a structural ceiling only: a catalog lake "
            "coordinate within 5 km of an inventory polygon is not an "
            "event-lake association, and no identity claim is made.",
            "Catalog coordinates have unknown positional accuracy; many rows "
            "are approximate lake or impact locations.",
            "Candidate paths are threshold-approved overlap chains, not "
            "physical lake identities; coverage depth counts observed epochs, "
            "not confirmed lake continuity.",
            "The 1990-2020 window refers to inventory snapshot labels; events "
            "inside the window are not necessarily observed by any epoch.",
            "Rows with UNPARSEABLE years are excluded from the window "
            "marginal, so the reported ceiling is not tightened by them; "
            "they remain counted separately.",
        ],
        "conclusions": {
            "physical_lake_identity_established": False,
            "event_or_recurrence_identity_established": False,
            "event_lake_association_established": False,
            "administrative_territory_assigned": False,
            "event_weather_or_risk_claim_authorized": False,
            "operational_authority": False,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-root", type=Path, default=EVIDENCE_ROOT)
    parser.add_argument("--output", type=Path, required=True,
                        help="write-once JSON output path")
    args = parser.parse_args(argv)
    try:
        report = build_census(args.evidence_root.resolve())
        output = args.output.resolve()
        root = args.evidence_root.resolve()
        if root not in output.parents:
            raise ValueError("output must be located under the evidence root")
        sidecar = Path(f"{output}.sha256")
        if output.exists() or sidecar.exists():
            raise FileExistsError(
                f"write-once output already exists: {output}")
        digest = write_once_json(output, report)
        sidecar_digest = write_once_sidecar(output)
        if digest != sidecar_digest:
            raise ValueError("output sidecar did not bind the published census")
        print(f"HMA_EVENT_LINKABILITY_CENSUS_OK: {output}")
        print(f"sha256={digest}")
        print(json.dumps(report["headline"], indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(f"HMA_EVENT_LINKABILITY_CENSUS_BLOCKED: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
