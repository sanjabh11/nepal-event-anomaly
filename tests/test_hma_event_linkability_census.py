"""Contract tests for the HMA event-linkability census (dormant branch)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Point, Polygon

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hma_event_linkability_census as census  # noqa: E402
from p5_safe_io import ExistingEvidenceError  # noqa: E402

EVIDENCE = Path("/Users/sanjayb/nepal-event-anomaly-evidence")
NEEDS_EVIDENCE = pytest.mark.skipif(
    not (EVIDENCE / "india-phase0-source-intake"
         / "HMA_EVENT_LINKABILITY_CENSUS_V0.json.sha256").is_file()
    and not (EVIDENCE / "india-phase0-source-intake"
             / "INDIA_EVENT_ADJUDICATION_V1.json.sha256").is_file(),
    reason="operator-local india-phase0 evidence lane absent (CI checkout)",
)


def _canonical(report: dict) -> str:
    stripped = {k: v for k, v in report.items() if k != "generated_utc"}
    return json.dumps(stripped, sort_keys=True)


@pytest.fixture(scope="module")
def report() -> dict:
    return census.build_census(EVIDENCE)


def test_nearest_matches_keeps_equidistant_ties():
    polys = gpd.GeoDataFrame(
        {"feature_id": ["GH:1990:00001", "GH:2000:00001"],
         "epoch": [1990, 2000]},
        geometry=[Polygon([(0, 0), (2, 0), (2, 2), (0, 2)]),
                  Polygon([(4, 0), (6, 0), (6, 2), (4, 2)])],
        crs="EPSG:32633")
    pts = gpd.GeoDataFrame({"event_ordinal": [0]},
                           geometry=[Point(3, 1)], crs="EPSG:32633")

    matches = census.nearest_matches(pts, polys)

    assert matches[0]["distance_km"] == pytest.approx(0.001)
    assert matches[0]["tie_count"] == 2
    assert matches[0]["matched_feature_ids"] == [
        "GH:1990:00001", "GH:2000:00001"]
    assert matches[0]["matched_epochs"] == [1990, 2000]


def test_parse_point_rejects_bad_values():
    assert census._parse_point("35.17", "77.70") == (35.17, 77.70)
    assert census._parse_point("NA", "77.70") is None
    assert census._parse_point("95.0", "77.70") is None
    assert census._parse_point("", "") is None


@NEEDS_EVIDENCE
def test_rerun_is_exact_match(report):
    rerun = census.build_census(EVIDENCE)
    assert _canonical(rerun) == _canonical(report)


@NEEDS_EVIDENCE
def test_counts_coherent_subsets_within_supersets(report):
    head = report["headline"]
    census_block = report["lake_point_census"]
    assert head["catalog_rows"] == 768
    assert census_block["points_total"] == head["catalog_rows"]
    assert census_block["parseable_points"] <= census_block["points_total"]
    within = census_block["within_km_counts"]
    assert within["2"] <= within["5"] <= within["10"]
    assert within["10"] <= census_block["parseable_points"]
    assert head["any_inventory_match_le_5km"] == within["5"]
    assert (head["le_5km_matched_lake_on_ge5_epoch_path"]
            <= head["le_5km_matched_lake_on_ge3_epoch_path"]
            <= head["any_inventory_match_le_5km"])
    assert head["event_year_in_1990_2020_inclusive"] <= head["catalog_rows"]
    assert head["joint_all_ceiling_conditions"] <= min(
        head["any_inventory_match_le_5km"],
        head["le_5km_matched_lake_on_ge3_epoch_path"],
        head["event_year_in_1990_2020_inclusive"])
    assert head["binding_ceiling_min_of_marginals"] == min(
        head["any_inventory_match_le_5km"],
        head["le_5km_matched_lake_on_ge3_epoch_path"],
        head["event_year_in_1990_2020_inclusive"])
    assert head["joint_all_ceiling_conditions"] <= (
        head["binding_ceiling_min_of_marginals"])


@NEEDS_EVIDENCE
def test_parse_audit_and_splits_partition_all_rows(report):
    audit = report["parse_audit"]
    assert audit["catalog_rows"] == 768
    assert (audit["lake_point"]["parseable"]
            + audit["lake_point"]["unparseable"] == 768)
    assert (audit["impact_point"]["parseable"]
            + audit["impact_point"]["unparseable"] == 768)
    assert sum(audit["event_year"].values()) == 768
    assert len(report["per_event_records"]) == 768
    for split_name, buckets in report["splits"].items():
        assert sum(b["rows"] for b in buckets.values()) == 768, split_name
        for label, bucket in buckets.items():
            assert (bucket["year_in_window"] + bucket["year_outside_window"]
                    + bucket["year_unparseable"] == bucket["rows"]), (
                split_name, label)
            assert (bucket["lake_point_le_5km_path_ge5"]
                    <= bucket["lake_point_le_5km_path_ge3"]
                    <= bucket["lake_point_le_5km"] <= bucket["rows"])
            assert (bucket["joint_all_ceiling_conditions"]
                    <= bucket["lake_point_le_5km_path_ge3"])


@NEEDS_EVIDENCE
def test_dormancy_and_authority_flags(report):
    assert report["event_association_branch"] == "DORMANT"
    assert report["dormancy_statement"] == (
        "census only; no event-lake identity or association claim")
    assert all(v is False for v in report["authority"].values())
    assert all(v is False for v in report["conclusions"].values())


@NEEDS_EVIDENCE
def test_no_write_on_overwrite(tmp_path, report):
    out = tmp_path / "census.json"
    census.write_once_json(out, report)
    census.write_once_sidecar(out)
    sealed = out.read_bytes()
    with pytest.raises(ExistingEvidenceError):
        census.write_once_json(out, report)
    assert out.read_bytes() == sealed


def test_no_write_on_overwrite_synthetic(tmp_path):
    out = tmp_path / "synthetic.json"
    census.write_once_json(out, {"a": 1})
    census.write_once_sidecar(out)
    with pytest.raises(ExistingEvidenceError):
        census.write_once_json(out, {"a": 2})
    assert json.loads(out.read_text()) == {"a": 1}
