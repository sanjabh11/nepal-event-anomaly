"""Territory-classification contract tests.

The boundary bytes live under the external evidence root; tests that
touch them skip when the root is absent.  Contract tests on a synthetic
fixture run everywhere.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import india_territory_classify as tc  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
E = Path("/Users/sanjayb/nepal-event-anomaly-evidence")
_BOUNDARY = E / "india-phase0-source-intake" / "territory-boundary"
_FRAME = E / "india-phase0-source-intake" / "NRSC_LAKE_FRAME_V0.json"
_ARTIFACT = E / "india-phase0-source-intake" / "INDIA_LAKE_TERRITORY_V0.json"
_PRESENT = _BOUNDARY.is_dir() and _FRAME.is_file()


def _polygon_union_shapely():
    shapely = pytest.importorskip("shapely")
    return shapely


def test_synthetic_classification_partitions():
    from shapely.geometry import box
    from shapely.ops import unary_union
    soi = box(0, 0, 10, 10)
    disputed = unary_union([box(8, 8, 10, 10)])
    frame = {"records": [
        {"source_record_id": "a", "lake": {"source_lake_id": "A"},
         "location": {"latitude": 5, "longitude": 5, "basin": "in"}},
        {"source_record_id": "b", "lake": {"source_lake_id": "B"},
         "location": {"latitude": 9, "longitude": 9, "basin": "disp"}},
        {"source_record_id": "c", "lake": {"source_lake_id": "C"},
         "location": {"latitude": 20, "longitude": 20, "basin": "out"}},
        {"source_record_id": "d", "lake": {"source_lake_id": "D"},
         "location": {"latitude": None, "longitude": None, "basin": "na"}},
    ]}
    out = tc.classify(frame, soi, disputed)["records"]
    states = {r["source_record_id"]: r["territory_state"] for r in out}
    assert states == {"a": "IN_COUNTRY", "b": "DISPUTED_REVIEW",
                      "c": "OUTSIDE", "d": "UNASSESSED"}


def test_validator_rejects_state_inconsistency():
    doc = {"schema": tc.SCHEMA, "version": 0,
           "records": [{"source_record_id": "x",
                        "latitude": 1, "longitude": 1,
                        "territory_state": "IN_COUNTRY"}],
           "summary": {"lake_rows": 1, "in_country": 0,
                       "disputed_review": 0, "outside": 0,
                       "unassessed": 0},
           "sources": {"lake_frame_sha256": "0" * 64,
                       "boundary_sha256": "0" * 64},
           "authority": dict(__import__("validate_india_source_intake")
                             .AUTHORITY_FLAGS)}
    problems = tc.validate_artifact(doc)
    assert any("in_country" in p for p in problems)


def test_validator_rejects_missing_coordinates_claimed_in_country():
    doc = {"schema": tc.SCHEMA, "version": 0,
           "records": [{"source_record_id": "x",
                        "latitude": None, "longitude": None,
                        "territory_state": "IN_COUNTRY"}],
           "summary": {"lake_rows": 1, "in_country": 1,
                       "disputed_review": 0, "outside": 0,
                       "unassessed": 0},
           "sources": {"lake_frame_sha256": "0" * 64,
                       "boundary_sha256": "0" * 64},
           "authority": dict(__import__("validate_india_source_intake")
                             .AUTHORITY_FLAGS)}
    problems = tc.validate_artifact(doc)
    assert any("UNASSESSED" in p for p in problems)


@pytest.mark.skipif(not _PRESENT, reason="external evidence root unavailable")
def test_live_artifact_partitions_all_rows():
    doc = json.loads(_ARTIFACT.read_text())
    assert tc.validate_artifact(doc) == []
    s = doc["summary"]
    assert s["lake_rows"] == 2433
    assert (s["in_country"] + s["disputed_review"]
            + s["outside"] + s["unassessed"]) == 2433
    assert s["in_country"] == 496
    assert s["disputed_review"] == 145
    assert s["outside"] == 1792
