"""Adversarial contract tests for the India Phase-0 feasibility machinery."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import india_event_crosswalk as ec
import india_event_adjudication as ea
import india_evidence_register as er
import india_feasibility_report as fr
import india_lake_frame as lf


FIELDS = [
    "GF_ID", "Year_approx", "Year_exact", "Month", "Day", "Lake_name",
    "Glacier_name", "GL_ID", "Country", "Province", "River_Basin",
    "Lat_lake", "Lon_lake", "Driver_lake", "Driver_GLOF", "Mechanism",
    "Repeat", "Sat_evidence", "Ref_scientific", "Ref_scientific_full", "Ref_other",
]

GEOGRAPHY = {"boundary_source": "test-boundary", "boundary_version": "v1",
             "crs": "EPSG:4326"}


def _write_catalog(path: Path, n_india: int = 3) -> None:
    rows = [
        {"GF_ID": str(i + 1), "Year_approx": "", "Year_exact": str(2001 + i),
         "Month": "6", "Day": "17",
         "Lake_name": f"Lake{i}", "Glacier_name": "", "GL_ID": f"GL:{i}",
         "Country": "India", "Province": "Uttarakhand", "River_Basin": "Ganga",
         "Lat_lake": "30.7", "Lon_lake": "79.1", "Driver_lake": "",
         "Driver_GLOF": "rain", "Mechanism": "moraine", "Repeat": "",
         "Sat_evidence": "yes", "Ref_scientific": "paper",
         "Ref_scientific_full": "", "Ref_other": ""}
        for i in range(n_india)
    ]
    rows.append(
        {"GF_ID": "9000", "Year_approx": "1998", "Year_exact": "", "Month": "",
         "Day": "", "Lake_name": "Old", "Glacier_name": "", "GL_ID": "L4",
         "Country": "Nepal", "Province": "", "River_Basin": "", "Lat_lake": "",
         "Lon_lake": "", "Driver_lake": "", "Driver_GLOF": "", "Mechanism": "",
         "Repeat": "", "Sat_evidence": "", "Ref_scientific": "",
         "Ref_scientific_full": "", "Ref_other": ""})
    with path.open("w", newline="", encoding="cp1252") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _write_bound(path: Path, doc) -> Path:
    """Write a governed JSON input plus its .sha256 sidecar."""
    path.write_text(json.dumps(doc), encoding="utf-8")
    lf.write_once_sidecar(path)
    return path


def _register_doc(coverage=("1900-01-01", "2100-12-31"),
                  state="BYTES_VERIFIED") -> dict:
    digest = "a" * 64 if state == "BYTES_VERIFIED" else None
    return {
        "schema": er.SCHEMA, "version": 0,
        "claim_scope": "research_only_no_operational_authorization",
        "authority": dict(er.AUTHORITY_FLAGS), "register_state": "OPEN",
        "records": [{
            "evidence_id": "EV:TEST", "source_name": "test-src",
            "source_version": "1", "locator_type": "LOCAL_PATH_LABEL",
            "locator": "test-bytes", "sha256": digest,
            "verification_state": state, "access_terms": "test",
            "coverage": {"temporal_start": coverage[0],
                         "temporal_end": coverage[1],
                         "spatial_scope": "test"},
            "limitations": ["test fixture"],
        }],
    }


def _reviewed_decision(crosswalk_path: Path, crosswalk: dict,
                       *, eligible=True, evidence="evidence:EV:TEST",
                       territory="IN_COUNTRY") -> dict:
    intake = ea.build_intake(crosswalk_path)
    decision = json.loads(json.dumps(intake))
    decision["status"] = "REVIEWED"
    decision["geography"] = dict(GEOGRAPHY)
    for index, record in enumerate(decision["records"]):
        record["episode"].update({
            "candidate_episode_id": f"IND:EP:{index}",
            "independence_status": "INDEPENDENT" if eligible else "NOT_INDEPENDENT",
        })
        record["adjudication"].update({
            "eligibility": "ELIGIBLE" if eligible else "INELIGIBLE",
            "review_state": "COMPLETED",
            "reviewer_ids": ["reviewer-1"],
            "reviewed_utc": "2026-09-27T00:00:00+00:00",
            "location_confirmed": eligible,
            "territory_status": territory,
            "mechanism": "moraine" if eligible else None,
            "mechanism_certainty": "CONFIRMED" if eligible else "UNKNOWN",
            "evidence_citations": [evidence],
        })
    return decision


def _paths(tmp_path: Path, n_india: int = 3):
    csv_path = tmp_path / "HMAGLOFDB.csv"
    _write_catalog(csv_path, n_india)
    crosswalk = ec.build_crosswalk(csv_path, "test-1.0")
    cw_path = _write_bound(tmp_path / "cw.json", crosswalk)
    inventory = tmp_path / "inventory.json"
    inventory.write_text("[]", encoding="utf-8")
    frame = lf.build_frame(inventory, "NRSC", "test-1")
    lf_path = _write_bound(tmp_path / "lf.json", frame)
    reg_path = _write_bound(tmp_path / "register.json", _register_doc())
    return crosswalk, cw_path, lf_path, reg_path


def test_crosswalk_retains_rows_without_adjudicating(tmp_path):
    source = tmp_path / "HMAGLOFDB.csv"
    _write_catalog(source, 3)
    doc = ec.build_crosswalk(source, "test-1.0")
    assert doc["summary"] == {
        "n_target_country_rows": 3,
        "n_exact_day_rows": 3,
        "n_exact_day_post_1979_rows": 3,
        "n_post_1979_rows_with_source_lake_id": 3,
        "n_unique_post_1979_source_lake_ids": 3,
        "post_1979_source_lake_ids": ["GL:0", "GL:1", "GL:2"],
        "n_unreviewed": 3,
        "n_analysis_eligible": 0,
        "n_independent_episodes": 0,
    }
    assert ec.validate_crosswalk(doc) == []
    assert doc["authority"] == ec.AUTHORITY_FLAGS
    assert doc["geography"]["classification_basis"] == "CATALOG_COUNTRY_FIELD_ONLY"
    assert all(r["adjudication"]["eligibility"] == "UNREVIEWED"
               for r in doc["records"])
    assert all(r["location"]["territory_status"] == "UNASSESSED"
               for r in doc["records"])


def test_crosswalk_rejects_implicit_episode_or_eligibility(tmp_path):
    source = tmp_path / "HMAGLOFDB.csv"
    _write_catalog(source)
    doc = ec.build_crosswalk(source, "test-1.0")
    doc["records"][0]["adjudication"]["eligibility"] = "ELIGIBLE"
    assert any("cannot silently" in p for p in ec.validate_crosswalk(doc))


def test_crosswalk_rejects_duplicate_source_ids(tmp_path):
    source = tmp_path / "HMAGLOFDB.csv"
    _write_catalog(source)
    text = source.read_text(encoding="cp1252").replace("\n2,", "\n1,")
    source.write_text(text, encoding="cp1252")
    with pytest.raises(ValueError, match="duplicate GF_ID"):
        ec.build_crosswalk(source, "test-1.0")


def test_adjudication_intake_is_append_only_and_digest_bound(tmp_path):
    source = tmp_path / "HMAGLOFDB.csv"
    _write_catalog(source)
    crosswalk = ec.build_crosswalk(source, "test-1.0")
    crosswalk_path = tmp_path / "crosswalk.json"
    crosswalk_path.write_text(json.dumps(crosswalk), encoding="utf-8")
    intake = ea.build_intake(crosswalk_path)
    assert intake["status"] == "AWAITING_REVIEWER_ADJUDICATION"
    assert ea.validate_adjudication(
        intake, crosswalk, ea.sha256_file(crosswalk_path)) == []
    tampered = dict(intake, crosswalk_sha256="0" * 64)
    assert any("digest" in p for p in ea.validate_adjudication(
        tampered, crosswalk, ea.sha256_file(crosswalk_path)))


def test_adjudication_rejects_duplicate_independent_episode_ids(tmp_path):
    source = tmp_path / "HMAGLOFDB.csv"
    _write_catalog(source)
    crosswalk = ec.build_crosswalk(source, "test-1.0")
    cw_path = tmp_path / "cw.json"
    cw_path.write_text(json.dumps(crosswalk), encoding="utf-8")
    decision = _reviewed_decision(cw_path, crosswalk)
    # Two rows claiming the same episode id must agree on independence.
    decision["records"][1]["episode"]["candidate_episode_id"] = "IND:EP:0"
    decision["records"][1]["episode"]["independence_status"] = "NOT_INDEPENDENT"
    assert any("inconsistent" in p for p in ea.validate_adjudication(
        decision, crosswalk, ea.sha256_file(cw_path)))


def test_adjudication_requires_territory_and_geography(tmp_path):
    source = tmp_path / "HMAGLOFDB.csv"
    _write_catalog(source)
    crosswalk = ec.build_crosswalk(source, "test-1.0")
    cw_path = tmp_path / "cw.json"
    cw_path.write_text(json.dumps(crosswalk), encoding="utf-8")
    decision = _reviewed_decision(cw_path, crosswalk)
    del decision["geography"]
    assert any("geography" in p for p in ea.validate_adjudication(
        decision, crosswalk, ea.sha256_file(cw_path)))
    decision = _reviewed_decision(cw_path, crosswalk)
    decision["records"][0]["adjudication"]["territory_status"] = "OUTSIDE"
    assert any("IN_COUNTRY" in p for p in ea.validate_adjudication(
        decision, crosswalk, ea.sha256_file(cw_path)))


def test_report_accepts_only_sha_bound_reviewed_successor(tmp_path):
    crosswalk, cw_path, lf_path, reg_path = _paths(tmp_path)
    decision = _reviewed_decision(cw_path, crosswalk)
    decision_path = _write_bound(tmp_path / "decision.json", decision)
    report = fr.build_report(cw_path, lf_path, reg_path, decision_path)
    assert report["denominators"]["independent_exact_day_episodes"] == 3
    assert report["independent_episode_ids"] == [
        "IND:EP:0", "IND:EP:1", "IND:EP:2"]
    assert "adjudication_sha256" in report["inputs"]
    assert report["inputs"]["sidecars_verified"] is True
    assert fr.validate_report(report) == []


def test_report_rejects_missing_or_tampered_sidecars(tmp_path):
    crosswalk, cw_path, lf_path, reg_path = _paths(tmp_path)
    report = fr.build_report(cw_path, lf_path, reg_path)
    assert fr.validate_report(report) == []
    # Missing sidecar fails closed.
    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps(crosswalk), encoding="utf-8")
    with pytest.raises(ValueError, match="missing evidence sidecar"):
        fr.build_report(bare, lf_path, reg_path)
    # Tampered bytes with a stale sidecar fail closed.
    tampered = json.loads(cw_path.read_text())
    tampered["records"][0]["catalog_fields"]["lake_name"] = "tampered"
    tampered_path = tmp_path / "tampered.json"
    tampered_path.write_text(json.dumps(tampered), encoding="utf-8")
    tampered_path.with_suffix(".json.sha256").write_text(
        cw_path.with_suffix(".json.sha256").read_text(), encoding="utf-8")
    with pytest.raises(ValueError, match="sidecar digest mismatch"):
        fr.build_report(tampered_path, lf_path, reg_path)


def test_report_rejects_unvalidated_input_shape(tmp_path):
    crosswalk, cw_path, lf_path, reg_path = _paths(tmp_path)
    bad = json.loads(cw_path.read_text())
    bad["authority"]["forecast_authorized"] = True
    bad_path = _write_bound(tmp_path / "bad_cw.json", bad)
    with pytest.raises(ValueError, match="crosswalk validation failed"):
        fr.build_report(bad_path, lf_path, reg_path)


def _inventory(tmp_path: Path, rows: list[dict]) -> Path:
    p = tmp_path / "inventory.json"
    p.write_text(json.dumps(rows), encoding="utf-8")
    return p


def test_lake_frame_unknown_is_not_control(tmp_path):
    p = _inventory(tmp_path, [{"source_record_id": "NRSC:1", "lake_id": "1",
                               "latitude": 30, "longitude": 80}])
    doc = lf.build_frame(p, "NRSC", "test-1")
    assert doc["summary"]["n_verified_non_event_controls"] == 0
    assert doc["records"][0]["observation"]["status"] == "UNKNOWN"
    assert lf.validate_frame(doc) == []


def test_lake_frame_requires_evidence_for_non_event(tmp_path):
    p = _inventory(tmp_path, [{"source_record_id": "NRSC:1", "lake_id": "1",
                               "latitude": 30, "longitude": 80,
                               "observation_status": "VERIFIED_NON_EVENT",
                               "observation_completeness": "FULL",
                               "observed_years": [2001]}])
    with pytest.raises(ValueError, match="lacks full interval evidence"):
        lf.build_frame(p, "NRSC", "test-1")


def test_lake_frame_full_requires_year_coverage(tmp_path):
    # FULL without any observed_years is a bare label — rejected.
    p = _inventory(tmp_path, [{"source_record_id": "NRSC:1", "lake_id": "1",
                               "latitude": 30, "longitude": 80,
                               "observation_completeness": "FULL"}])
    with pytest.raises(ValueError, match="lacks observed_years"):
        lf.build_frame(p, "NRSC", "test-1")
    # FULL with years that do not cover the declared at-risk interval.
    p = _inventory(tmp_path, [{"source_record_id": "NRSC:1", "lake_id": "1",
                               "latitude": 30, "longitude": 80,
                               "observation_completeness": "FULL",
                               "at_risk_start": "2001-01-01",
                               "at_risk_end": "2005-12-31",
                               "observed_years": [2001, 2003, 2005]}])
    with pytest.raises(ValueError, match="do not cover"):
        lf.build_frame(p, "NRSC", "test-1")


def test_lake_frame_rejects_invalid_territory_status(tmp_path):
    p = _inventory(tmp_path, [{"source_record_id": "NRSC:1", "lake_id": "1",
                               "latitude": 30, "longitude": 80,
                               "territory_status": "DEFINITELY_INDIA"}])
    with pytest.raises(ValueError, match="invalid territory_status"):
        lf.build_frame(p, "NRSC", "test-1")


def test_lake_frame_accepts_explicit_verified_control(tmp_path):
    p = _inventory(tmp_path, [{"source_record_id": "NRSC:1", "lake_id": "1",
                               "latitude": 30, "longitude": 80,
                               "observation_status": "VERIFIED_NON_EVENT",
                               "observation_completeness": "FULL",
                               "at_risk_start": "2001-01-01",
                               "at_risk_end": "2003-12-31",
                               "observed_years": [2001, 2002, 2003],
                               "evidence_refs": ["evidence:EV:TEST"]}])
    doc = lf.build_frame(p, "NRSC", "test-1")
    assert doc["summary"]["n_verified_non_event_controls"] == 1
    assert lf.validate_frame(doc) == []


def test_lake_frame_counts_only_full_observed_years(tmp_path):
    p = _inventory(tmp_path, [{"source_record_id": "NRSC:1", "lake_id": "1",
                               "lat": 30, "lon": 80,
                               "observation_completeness": "FULL",
                               "observed_years": [2001, 2002, 2003]}])
    doc = lf.build_frame(p, "NRSC", "test-1")
    assert doc["summary"]["n_observable_lake_years"] == 3
    assert lf.validate_frame(doc) == []


def test_lake_frame_rejects_noncanonical_or_reversed_at_risk_interval(tmp_path):
    base = {"source_record_id": "NRSC:1", "lake_id": "1",
            "latitude": 30, "longitude": 80,
            "observation_status": "VERIFIED_NON_EVENT",
            "observation_completeness": "FULL",
            "observed_years": [2001, 2002, 2003],
            "evidence_refs": ["evidence:EV:TEST"]}
    bad_format = dict(base, at_risk_start="2001-1-01", at_risk_end="2003-12-31")
    with pytest.raises(ValueError, match="canonical YYYY-MM-DD"):
        lf.build_frame(_inventory(tmp_path, [bad_format]), "NRSC", "test-1")
    reversed_interval = dict(base, at_risk_start="2003-12-31", at_risk_end="2001-01-01")
    with pytest.raises(ValueError, match="starts after"):
        lf.build_frame(_inventory(tmp_path, [reversed_interval]), "NRSC", "test-1")


def test_frame_validation_rejects_unverified_canonical_claim(tmp_path):
    p = _inventory(tmp_path, [{"source_record_id": "NRSC:1", "lake_id": "1",
                               "latitude": 30, "longitude": 80}])
    doc = lf.build_frame(p, "NRSC", "test-1")
    doc["records"][0]["lake"]["canonical_lake_id"] = "CL:1"
    assert any("RECONCILED" in problem for problem in lf.validate_frame(doc))
    doc["records"][0]["lake"]["identity_status"] = "RECONCILED"
    assert lf.validate_frame(doc) == []


def test_feasibility_keeps_gate_pending_while_adjudication_is_incomplete(tmp_path):
    _, cw_path, lf_path, reg_path = _paths(tmp_path)
    report = fr.build_report(cw_path, lf_path, reg_path)
    assert report["gates"]["event_weather_screen"] == "ADJUDICATION_INCOMPLETE"
    # An empty frame has no identities to reconcile — the gate is the
    # missing inventory, not unresolved identity work.
    assert report["gates"]["lake_year_screen"] == "INSUFFICIENT_LAKE_FRAME"
    assert report["gates"]["bulk_acquisition_authorized"] is False
    assert report["authority"] == fr.AUTHORITY_FLAGS
    assert fr.validate_report(report) == []


def test_feasibility_gate_requires_identity_reconciliation(tmp_path):
    crosswalk, cw_path, _, reg_path = _paths(tmp_path, 3)
    inventory = _inventory(tmp_path, [
        {"source_record_id": "NRSC:1", "lake_id": "1",
         "latitude": 30, "longitude": 80},
        {"source_record_id": "CWC:1", "lake_id": "9",
         "latitude": 31, "longitude": 79},
    ])
    frame = lf.build_frame(inventory, "NRSC", "test-1")
    lf_path = _write_bound(tmp_path / "lf2.json", frame)
    decision = _reviewed_decision(cw_path, crosswalk)
    decision_path = _write_bound(tmp_path / "decision.json", decision)
    report = fr.build_report(cw_path, lf_path, reg_path, decision_path)
    # Two unreconciled source rows block the lake screen even though rows
    # exist — source rows are not canonical lakes.
    assert report["denominators"]["unresolved_lake_identities"] == 2
    assert report["denominators"]["canonical_lakes"] == 0
    assert report["gates"]["lake_year_screen"] == "IDENTITY_RECONCILE_REQUIRED"
    assert report["next_gate"] == "RECONCILE_LAKE_IDENTITIES"


def _report_with_n_episodes(tmp_path: Path, n: int,
                            evidence: str = "evidence:EV:TEST"):
    crosswalk, cw_path, lf_path, reg_path = _paths(tmp_path, n_india=n)
    decision = _reviewed_decision(cw_path, crosswalk, evidence=evidence)
    decision_path = _write_bound(tmp_path / "decision.json", decision)
    return fr.build_report(cw_path, lf_path, reg_path, decision_path)


def test_feasibility_closes_event_route_after_completed_small_cohort(tmp_path):
    report = _report_with_n_episodes(tmp_path, 3)
    assert report["gates"]["event_weather_screen"] == "CLOSE_EVENT_WEATHER_ROUTE"
    assert report["next_gate"] == "RECONCILE_LAKE_INVENTORY"


def test_feasibility_requires_simulation_at_twenty_episodes(tmp_path):
    report = _report_with_n_episodes(tmp_path, 20)
    assert report["gates"]["event_weather_screen"] == "SIMULATION_REQUIRED"
    assert report["next_gate"] == "RUN_PREDECLARED_PRECISION_SIMULATION"


def test_feasibility_counts_distinct_episode_ids_not_rows(tmp_path):
    crosswalk, cw_path, lf_path, reg_path = _paths(tmp_path, 3)
    decision = _reviewed_decision(cw_path, crosswalk)
    # All three rows legitimately belong to one shared episode.
    for record in decision["records"]:
        record["episode"]["candidate_episode_id"] = "IND:EP:SHARED"
    decision_path = _write_bound(tmp_path / "decision.json", decision)
    report = fr.build_report(cw_path, lf_path, reg_path, decision_path)
    assert report["denominators"]["adjudicated_eligible_rows"] == 3
    assert report["denominators"]["independent_exact_day_episodes"] == 1
    assert report["independent_episode_ids"] == ["IND:EP:SHARED"]


def test_feasibility_rejects_unresolvable_evidence(tmp_path):
    # A nonempty citation string that does not resolve to BYTES_VERIFIED
    # register evidence cannot produce an eligible independent episode.
    report = _report_with_n_episodes(tmp_path, 3, evidence="paper:unverified")
    assert report["denominators"]["adjudicated_eligible_rows"] == 3
    assert report["denominators"]["independent_exact_day_episodes"] == 0
    assert report["denominators"]["eligible_unverified_evidence"] == 3
    assert report["gates"]["evidence_screen"] == "UNVERIFIED_EVIDENCE_PRESENT"
    assert report["next_gate"] == "VERIFY_EVENT_EVIDENCE"


def test_feasibility_rejects_out_of_coverage_evidence(tmp_path):
    crosswalk, cw_path, lf_path, _ = _paths(tmp_path, 3)
    narrow = _register_doc(coverage=("2020-01-01", "2020-12-31"))
    reg_path = _write_bound(tmp_path / "narrow_reg.json", narrow)
    decision = _reviewed_decision(cw_path, crosswalk)
    decision_path = _write_bound(tmp_path / "decision.json", decision)
    report = fr.build_report(cw_path, lf_path, reg_path, decision_path)
    assert report["denominators"]["independent_exact_day_episodes"] == 0
    assert report["gates"]["evidence_screen"] == "UNVERIFIED_EVIDENCE_PRESENT"


def test_feasibility_rejects_metadata_only_evidence(tmp_path):
    crosswalk, cw_path, lf_path, _ = _paths(tmp_path, 3)
    meta = _register_doc(state="METADATA_VERIFIED")
    reg_path = _write_bound(tmp_path / "meta_reg.json", meta)
    decision = _reviewed_decision(cw_path, crosswalk)
    decision_path = _write_bound(tmp_path / "decision.json", decision)
    report = fr.build_report(cw_path, lf_path, reg_path, decision_path)
    assert report["denominators"]["independent_exact_day_episodes"] == 0


def test_feasibility_validator_rejects_authority_flip(tmp_path):
    _, cw_path, lf_path, reg_path = _paths(tmp_path)
    report = fr.build_report(cw_path, lf_path, reg_path)
    report["gates"]["bulk_acquisition_authorized"] = True
    assert any("authority flag" in p for p in fr.validate_report(report))
    report = fr.build_report(cw_path, lf_path, reg_path)
    report["authority"]["forecast_authorized"] = True
    assert any("authority flags" in p for p in fr.validate_report(report))


def test_report_validator_catches_denominator_lie(tmp_path):
    _, cw_path, lf_path, reg_path = _paths(tmp_path)
    report = fr.build_report(cw_path, lf_path, reg_path)
    report["denominators"]["independent_exact_day_episodes"] = 25
    assert fr.validate_report(report)


def test_pinned_evidence_register_validates():
    register = Path(__file__).resolve().parents[1] / (
        "docs/science/INDIA_EVIDENCE_REGISTER_V0.json")
    doc = json.loads(register.read_text(encoding="utf-8"))
    assert er.validate_register(doc) == []
    # The seeded register is honest: metadata-verified entries only, so no
    # citation can produce a verified eligible event or control today.
    assert er.verified_records(doc) == {}
