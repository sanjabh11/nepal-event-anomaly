"""Territory spatial-relation contract tests (artifact V1).

Live-evidence tests skip outside the evidence-root environment; the
contract tests run everywhere on synthetic geometry.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import india_territory_classify as tc  # noqa: E402
import validate_india_source_intake as intake  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
E = Path("/Users/sanjayb/nepal-event-anomaly-evidence")
_BOUNDARY = E / "india-phase0-source-intake" / "territory-boundary"
_ARTIFACT = E / "india-phase0-source-intake" / "INDIA_LAKE_TERRITORY_V1.json"
_PRESENT = _BOUNDARY.is_dir() and _ARTIFACT.is_file()


def _doc_template():
    return {"schema": tc.SCHEMA, "version": 1,
            "claim_scope": tc.CLAIM_SCOPE,
            "meaning": tc.CLAIM_MEANING,
            "states": list(tc.STATES),
            "proximity_rule": tc.PROXIMITY_RULE,
            "records": [], "summary": {},
            "sources": {"lake_frame": "x", "lake_frame_sha256": "0" * 64,
                        "boundary_dir": tc.BOUNDARY_DIRNAME,
                        "component_digests": {},
                        "provenance": tc.PROVENANCE,
                        "overlay_sources": tc.OVERLAY_SOURCES,
                        "crs_note": tc.CRS_NOTE},
            "authority": dict(intake.AUTHORITY_FLAGS)}


def _record(sid="a", lat=5.0, lon=5.0, state="INSIDE_SOI_CLAIM",
            membership=None):
    return {"source_record_id": sid, "source_lake_id": f"lake-{sid}",
            "basin": "Test Basin", "latitude": lat, "longitude": lon,
            "spatial_relation": state,
            "overlay_membership": membership or
            {k: False for k in tc.OVERLAY_FILES},
            "proximity_to_boundary_m_lt": None, "basis": tc.RECORD_BASIS}


def _summary(recs):
    live = {s: 0 for s in tc.STATES}
    for r in recs:
        live[r["spatial_relation"]] += 1
    return {"atlas_rows": len(recs),
            "inside_soi_claim_rows": live["INSIDE_SOI_CLAIM"],
            "inside_disputed_overlay_rows": live["INSIDE_DISPUTED_OVERLAY"],
            "outside_soi_claim_rows": live["OUTSIDE_SOI_CLAIM"],
            "unassessed_rows": live["UNASSESSED"],
            "proximity_flagged_rows": sum(
                r.get("proximity_to_boundary_m_lt") is not None for r in recs)}


def _full_doc(recs):
    d = _doc_template()
    d["records"] = recs
    d["summary"] = _summary(recs)
    d["sources"]["component_digests"] = {
        n: {"sha256": "0" * 64, "size_bytes": 1}
        for n in list(tc.SHP_COMPONENTS) + list(tc.OVERLAY_FILES.values())}
    return d


def _write_sidecar(path):
    import hashlib
    path.with_suffix(path.suffix + ".sha256").write_text(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n")


def test_classification_partitions_synthetic_geometry():
    from shapely.geometry import box
    from shapely.ops import unary_union
    soi = box(0, 0, 10, 10)
    overlays = {"pok": box(8, 8, 10, 10), "shaksgam": box(0, 0, 0, 0),
                "lsib_disputed": box(0, 0, 0, 0)}
    frame = {"records": [
        {"source_record_id": "a", "lake": {"source_lake_id": "A"},
         "location": {"latitude": 5, "longitude": 5}},
        {"source_record_id": "b", "lake": {"source_lake_id": "B"},
         "location": {"latitude": 9, "longitude": 9}},
        {"source_record_id": "c", "lake": {"source_lake_id": "C"},
         "location": {"latitude": 20, "longitude": 20}},
        {"source_record_id": "d", "lake": {"source_lake_id": "D"},
         "location": {"latitude": None, "longitude": None}},
    ]}
    out = tc.classify(frame, soi, overlays)
    states = {r["source_record_id"]: (r["spatial_relation"],
                                      r["overlay_membership"]) for r in out}
    assert states["a"][0] == "INSIDE_SOI_CLAIM"
    assert states["b"][0] == "INSIDE_DISPUTED_OVERLAY"
    assert states["b"][1]["pok"] is True
    assert states["b"][1]["lsib_disputed"] is False
    assert states["c"][0] == "OUTSIDE_SOI_CLAIM"
    assert states["d"][0] == "UNASSESSED"


@pytest.mark.parametrize("bad", [
    (float("nan"), 5.0), (5.0, float("inf")), (91.0, 5.0),
    (5.0, 181.0), ("x", 5.0), (True, 5.0)])
def test_coordinate_edge_cases_rejected(bad):
    assert tc._valid_coord(bad[0], bad[1]) is False


def test_boundary_state_language_is_source_relative():
    assert set(tc.STATES) == {"INSIDE_SOI_CLAIM", "INSIDE_DISPUTED_OVERLAY",
                              "OUTSIDE_SOI_CLAIM", "UNASSESSED"}
    assert "IN_COUNTRY" not in tc.STATES
    assert "DISPUTED_REVIEW" not in tc.STATES


def test_forged_record_summary_mismatch():
    doc = _full_doc([_record()])
    doc["summary"]["inside_soi_claim_rows"] = 7
    assert any("inside_soi_claim_rows" in p
               for p in tc.validate_artifact(doc))


def test_forged_overlay_membership_rejected():
    r = _record(state="INSIDE_DISPUTED_OVERLAY",
                membership={k: False for k in tc.OVERLAY_FILES})
    doc = _full_doc([r])
    doc["summary"]["inside_disputed_overlay_rows"] = 1
    doc["summary"]["inside_soi_claim_rows"] = 0
    assert any("no overlay true" in p
               for p in tc.validate_artifact(doc))


def test_duplicate_row_ids_rejected():
    doc = _full_doc([_record("dup"), _record("dup")])
    doc["summary"]["inside_soi_claim_rows"] = 2
    doc["summary"]["atlas_rows"] = 2
    assert any("duplicate" in p for p in tc.validate_artifact(doc))


def test_missing_component_binding_rejected():
    doc = _full_doc([_record()])
    doc["sources"]["component_digests"].pop("IndiaBoundary.prj")
    assert any("component" in p for p in tc.validate_artifact(doc))


def test_bad_authority_rejected():
    doc = _full_doc([_record()])
    doc["authority"]["forecast_authorized"] = True
    assert tc.validate_artifact(doc)


@pytest.mark.parametrize("field,value", [
    ("claim_scope", "administrative_territory"),
    ("meaning", "These are verified Indian territories."),
    ("states", ["IN_COUNTRY", "OUTSIDE"]),
    ("version", 2),
])
def test_source_relative_claim_contract_cannot_be_promoted(field, value):
    doc = _full_doc([_record()])
    doc[field] = value
    assert any("claim" in problem or "source-relative" in problem
               or "states" in problem or "version" in problem
               for problem in tc.validate_artifact(doc))


def test_territory_decision_fields_cannot_be_added_to_artifact_or_row():
    doc = _full_doc([_record()])
    doc["territory_status"] = "IN_COUNTRY"
    assert any("artifact fields" in problem
               for problem in tc.validate_artifact(doc))
    doc = _full_doc([_record()])
    doc["records"][0]["territory_status"] = "IN_COUNTRY"
    assert any("record" in problem and "fields" in problem
               for problem in tc.validate_artifact(doc))


def test_non_boolean_overlay_membership_fails_without_crashing():
    doc = _full_doc([_record(membership={
        "pok": "false", "shaksgam": False, "lsib_disputed": False})])
    assert any("values must be booleans" in problem
               for problem in tc.validate_artifact(doc))


def test_proximity_count_is_recomputed_structurally():
    doc = _full_doc([_record()])
    doc["summary"]["proximity_flagged_rows"] = 1
    assert any("proximity_flagged_rows" in problem
               for problem in tc.validate_artifact(doc))


@pytest.mark.parametrize("field,value", [
    ("latitude", float("nan")),
    ("longitude", float("inf")),
    ("latitude", 91.0),
])
def test_nonfinite_or_out_of_range_record_coordinates_rejected(field, value):
    doc = _full_doc([_record(state="UNASSESSED", lat=None, lon=None)])
    doc["records"][0][field] = value
    assert any("coordinates must be finite" in problem
               for problem in tc.validate_artifact(doc))


def test_basis_cannot_claim_administrative_determination():
    doc = _full_doc([_record()])
    doc["records"][0]["basis"] = "administrative determination"
    assert any("basis" in problem for problem in tc.validate_artifact(doc))


@pytest.mark.parametrize("field,value", [
    ("source_lake_id", "forged-lake"),
    ("basin", "forged-basin"),
    ("proximity_to_boundary_m_lt", 1000.0),
])
def test_verify_recomputes_all_record_fields(tmp_path, monkeypatch, field, value):
    from shapely.geometry import box

    boundary_dir = tmp_path / "boundary"
    boundary_dir.mkdir()
    for name in list(tc.SHP_COMPONENTS) + list(tc.OVERLAY_FILES.values()):
        (boundary_dir / name).write_bytes(name.encode("utf-8"))
    frame = {"records": [{
        "source_record_id": "record-1",
        "lake": {"source_lake_id": "lake-1"},
        "location": {"basin": "Basin A", "latitude": 5.0,
                     "longitude": 5.0},
    }]}
    frame_path = tmp_path / "frame.json"
    frame_path.write_text(json.dumps(frame))
    soi = box(0, 0, 10, 10)
    overlays = {name: box(20, 20, 21, 21) for name in tc.OVERLAY_FILES}
    monkeypatch.setattr(tc, "load_geometries", lambda _path: (soi, overlays))
    records = tc.classify(frame, soi, overlays)
    doc = _full_doc(records)
    doc["sources"]["lake_frame"] = frame_path.name
    doc["sources"]["lake_frame_sha256"] = tc.sha256_file(frame_path)
    doc["sources"]["component_digests"] = tc.component_manifest(boundary_dir)
    doc["summary"] = {
        "atlas_rows": 1, "inside_soi_claim_rows": 1,
        "inside_disputed_overlay_rows": 0, "outside_soi_claim_rows": 0,
        "unassessed_rows": 0, "proximity_flagged_rows": 0,
    }
    if field == "proximity_to_boundary_m_lt":
        # Forged record carries a proximity flag; keep the summary
        # consistent so the failure is the record recomputation, not
        # the summary count check (which returns early).
        doc["summary"]["proximity_flagged_rows"] = 1
    artifact_path = tmp_path / "territory.json"
    doc["records"][0][field] = value
    artifact_path.write_text(json.dumps(doc))
    _write_sidecar(artifact_path)

    problems = tc.verify_artifact(artifact_path, boundary_dir)
    assert problems
    assert any("recomputation mismatch" in problem for problem in problems)


def test_geometry_contract_rejects_line_geometry():
    from shapely.geometry import LineString
    with pytest.raises(ValueError, match="polygonal"):
        tc._require_polygonal_geometries(
            [LineString([(0, 0), (1, 1)])], "synthetic overlay")


@pytest.mark.skipif(not _PRESENT, reason="external evidence root unavailable")
def test_live_verify_recomputes_from_bound_bytes():
    assert tc.verify_artifact(_ARTIFACT) == []


@pytest.mark.skipif(not _PRESENT, reason="external evidence root unavailable")
def test_live_summary_partitions_rows():
    doc = json.loads(_ARTIFACT.read_text())
    s = doc["summary"]
    assert s["atlas_rows"] == 2433
    assert (s["inside_soi_claim_rows"] + s["inside_disputed_overlay_rows"]
            + s["outside_soi_claim_rows"] + s["unassessed_rows"]) == 2433
    assert s["proximity_flagged_rows"] > 0
    # rows are rows, not lakes, and not a territory adjudication
    assert "not" in doc["meaning"].lower()


@pytest.mark.skipif(not _PRESENT, reason="external evidence root unavailable")
def test_live_component_corruption_detected(tmp_path):
    doc = json.loads(_ARTIFACT.read_text())
    doc["sources"]["component_digests"]["IndiaBoundary.shp"]["sha256"] = "0" * 64
    forged = tmp_path / "INDIA_LAKE_TERRITORY_V1.json"
    forged.write_text(json.dumps(doc))
    forged.with_suffix(".json.sha256").write_text(
        f"{__import__('hashlib').sha256(forged.read_bytes()).hexdigest()}"
        f"  {forged.name}\n")
    problems = tc.verify_artifact(forged)
    assert any("component digest mismatch" in p for p in problems)


def test_frame_rejects_in_country_without_typed_decision():
    import india_lake_frame as lf
    record = {
        "source_record_id": "X", "lake": {"source_lake_id": "x",
                                          "identity_status": "UNRECONCILED",
                                          "canonical_lake_id": None,
                                          "name": ""},
        "location": {"basin": "Teesta", "latitude": 27.9,
                     "longitude": 88.5, "state": "",
                     "territory_status": "IN_COUNTRY"},
        "attributes": {}, "observation": {"status": "UNKNOWN",
            "completeness": "UNKNOWN", "control_eligible": False,
            "evidence_refs": [], "at_risk_start": None,
            "at_risk_end": None, "observed_years": [],
            "first_observed_year": None},
        "event_linkage": {"linkage_status": "UNASSESSED",
                          "known_breach_ids": []},
        "source": {"name": "NRSC_GLA_IHR"},
    }
    frame = {"schema": "NRSC_LAKE_FRAME_V0", "records": [record]}
    problems = lf.validate_frame(frame)
    assert any("IN_COUNTRY requires" in p for p in problems)


def test_frame_accepts_unassessed():
    import india_lake_frame as lf
    record = {
        "source_record_id": "X", "lake": {"source_lake_id": "x",
                                          "identity_status": "UNRECONCILED",
                                          "canonical_lake_id": None,
                                          "name": ""},
        "location": {"basin": "Teesta", "latitude": 27.9,
                     "longitude": 88.5, "state": "",
                     "territory_status": "UNASSESSED"},
        "attributes": {}, "observation": {"status": "UNKNOWN",
            "completeness": "UNKNOWN", "control_eligible": False,
            "evidence_refs": [], "at_risk_start": None,
            "at_risk_end": None, "observed_years": [],
            "first_observed_year": None},
        "event_linkage": {"linkage_status": "UNASSESSED",
                          "known_breach_ids": []},
        "source": {"name": "NRSC_GLA_IHR"},
    }
    frame = {"schema": "NRSC_LAKE_FRAME_V0", "records": [record]}
    problems = lf.validate_frame(frame)
    assert not any("territory" in p.lower() for p in problems)


def test_adjudication_rejects_in_country_without_typed_decision():
    import india_event_adjudication as ea
    doc = {"schema": ea.SCHEMA, "records": []}
    rec = {"source_record_id": "HMAGLOFDB:1",
           "adjudication": {
               "review_state": "COMPLETED", "reviewer_ids": ["r1"],
               "reviewed_utc": "2026-09-28T00:00:00Z",
               "location_confirmed": True,
               "territory_status": "IN_COUNTRY",
               "mechanism": "moraine", "mechanism_certainty": "CONFIRMED",
               "evidence_citations": ["EV:X"], "eligibility": "ELIGIBLE"},
           "episode": {"independence_status": "INDEPENDENT",
                       "candidate_episode_id": "E1"}}
    problems = ea.validate_adjudication(
        {"records": [rec]}, {"records": []}, "0" * 64)
    assert any("territory_evidence" in p for p in problems)
