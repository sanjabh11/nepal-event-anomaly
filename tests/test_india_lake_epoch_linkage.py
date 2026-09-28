"""Synthetic contract tests for candidate-only India lake spatial linkage."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest
from pyproj import CRS
from shapely.geometry import Point, Polygon

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import india_lake_epoch_linkage as linkage  # noqa: E402


def _feature(feature_id: str, geometry: Polygon,
             status: str = "VALID_POLYGON") -> linkage.SpatialFeature:
    return linkage.SpatialFeature(feature_id, 1, geometry, {}, status)


def test_adjacent_epoch_overlap_reports_split_topology_not_identity():
    left = [_feature("left", Polygon([(0, 0), (2, 0), (2, 1), (0, 1)]))]
    right = [
        _feature("right-a", Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])),
        _feature("right-b", Polygon([(1, 0), (2, 0), (2, 1), (1, 1)])),
    ]

    result = linkage.build_epoch_relations(left, right)

    assert result["overlap_candidate_count"] == 2
    assert {edge["overlap_pattern"] for edge in result["overlap_candidates"]} == {
        "POSSIBLE_SPLIT"
    }
    assert all(edge["identity_claim"] is False
               for edge in result["overlap_candidates"])
    assert not result["nearest_only_diagnostics"]


def test_adjacent_epoch_overlap_reports_merge_topology_not_identity():
    left = [
        _feature("left-a", Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])),
        _feature("left-b", Polygon([(1, 0), (2, 0), (2, 1), (1, 1)])),
    ]
    right = [_feature("right", Polygon([(0, 0), (2, 0), (2, 1), (0, 1)]))]

    result = linkage.build_epoch_relations(left, right)

    assert result["overlap_candidate_count"] == 2
    assert {edge["overlap_pattern"] for edge in result["overlap_candidates"]} == {
        "POSSIBLE_MERGE"
    }


def test_touching_polygons_are_not_positive_area_overlap():
    left = [_feature("left", Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]))]
    right = [_feature("right", Polygon([(1, 0), (2, 0), (2, 1), (1, 1)]))]

    result = linkage.build_epoch_relations(left, right)

    assert result["overlap_candidate_count"] == 0
    assert result["touch_only_candidate_count"] == 1
    assert result["overlap_candidates"][0]["overlap_pattern"] == "TOUCH_ONLY"
    assert result["nearest_only_diagnostics"] == []


def test_invalid_geometry_is_excluded_without_repair():
    invalid = Polygon([(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)])
    left = [_feature("invalid", invalid, "EXCLUDED_INVALID_GEOMETRY")]
    right = [_feature("valid", Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]))]

    result = linkage.build_epoch_relations(left, right)

    assert result["left_excluded_feature_ids"] == ["invalid"]
    assert result["overlap_candidates"] == []
    assert result["nearest_only_diagnostics"] == []


def test_nearest_polygon_is_diagnostic_only_and_has_no_cutoff():
    left = [_feature("left", Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]))]
    right = [_feature("far", Polygon([(100, 0), (101, 0), (101, 1), (100, 1)]))]

    result = linkage.build_epoch_relations(left, right)

    assert result["overlap_candidates"] == []
    assert result["nearest_only_diagnostics"][0]["nearest_feature_ids"] == ["far"]
    assert result["nearest_only_diagnostics"][0]["distance_m"] == pytest.approx(99)
    assert result["nearest_only_diagnostics"][0]["relation"].endswith(
        "NOT_IDENTITY_EVIDENCE"
    )


def test_point_relations_distinguish_interior_boundary_and_no_candidate():
    feature = _feature("lake", Polygon([(0, 0), (2, 0), (2, 2), (0, 2)]))
    features = [feature]

    inside = linkage.build_point_relations(Point(1, 1), features)
    boundary = linkage.build_point_relations(Point(0, 1), features)
    outside = linkage.build_point_relations(Point(5, 1), features)

    assert inside["status"] == "ONE_SPATIAL_CANDIDATE"
    assert inside["candidates"][0]["relation"] == "POINT_IN_POLYGON"
    assert inside["candidates"][0]["identity_claim"] is False
    assert boundary["candidates"][0]["relation"] == "POINT_ON_POLYGON_BOUNDARY"
    assert outside["status"] == "NO_INTERSECTION_CANDIDATE"
    assert outside["candidates"] == []
    assert outside["nearest_only_diagnostic"]["distance_m"] == pytest.approx(3)
    assert "NOT_IDENTITY_EVIDENCE" in outside["nearest_only_diagnostic"]["relation"]


def test_point_crosswalk_builds_one_spatial_tree_per_epoch(monkeypatch):
    real_tree = linkage.STRtree
    tree_builds = 0

    def counted_tree(geometries):
        nonlocal tree_builds
        tree_builds += 1
        return real_tree(geometries)

    monkeypatch.setattr(linkage, "STRtree", counted_tree)
    epochs = {
        epoch: [_feature(f"GH:{epoch}:00001",
                         Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]))]
        for epoch in linkage.EPOCHS
    }
    rows = [
        {"source_table": "table_68_ge10ha", "serial_no": serial,
         "glacial_lake_id_compact": f"lake-{serial}",
         "latitude": 0.5, "longitude": 0.5}
        for serial in range(1, 4)
    ]

    result = linkage._point_crosswalk(rows, epochs, CRS.from_epsg(4326))

    assert result["row_count"] == 3
    assert tree_builds == len(linkage.EPOCHS)


def test_nrsc_key_preserves_duplicate_printed_serials_as_distinct_rows():
    first = {"source_table": "table_68_ge10ha", "serial_no": 2001,
             "glacial_lake_id_compact": "lake-a"}
    second = {"source_table": "table_68_ge10ha", "serial_no": 2001,
              "glacial_lake_id_compact": "lake-b"}

    assert linkage._nrsc_key(first) != linkage._nrsc_key(second)
    with pytest.raises(ValueError, match="lacks its table"):
        linkage._nrsc_key({"source_table": "table_68_ge10ha", "serial_no": 1})


def test_checksum_manifest_rejects_duplicate_and_path_traversal(tmp_path):
    digest = hashlib.sha256(b"x").hexdigest()
    duplicate = tmp_path / "duplicate.txt"
    duplicate.write_text(f"{digest}  a.shp\n{digest}  a.shp\n")
    traversal = tmp_path / "traversal.txt"
    traversal.write_text(f"{digest}  ../outside.shp\n")

    with pytest.raises(ValueError, match="duplicate"):
        linkage.parse_sha256_manifest(duplicate)
    with pytest.raises(ValueError, match="unsafe"):
        linkage.parse_sha256_manifest(traversal)


def test_manifest_sidecar_verification_fails_closed(tmp_path):
    artifact = tmp_path / "input.json"
    artifact.write_text("{}\n")
    artifact.with_name(artifact.name + ".sha256").write_text(
        f"{'0' * 64}  {artifact.name}\n"
    )

    with pytest.raises(ValueError, match="digest mismatch"):
        linkage._verify_sidecar(artifact)


def test_authority_ceiling_is_all_false():
    assert linkage.AUTHORITY_FLAGS
    assert all(value is False for value in linkage.AUTHORITY_FLAGS.values())
