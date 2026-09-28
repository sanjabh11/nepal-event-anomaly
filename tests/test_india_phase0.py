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
    "Repeat", "Sat_evidence", "Transboundary", "Ref_scientific", "Ref_scientific_full", "Ref_other",
]

GEOGRAPHY = {"boundary_source": "test-boundary", "boundary_version": "v1",
             "crs": "EPSG:4326"}

METHOD = {"modality": "SATELLITE_RS", "cadence": "ANNUAL",
          "temporal_coverage": {"start": "2000-01-01", "end": "2020-12-31"},
          "spatial_resolution": "30m", "detection_threshold": ">=0.25ha",
          "gaps_censoring": []}


def _write_catalog(path: Path, n_india: int = 3,
                   extra_rows: list[dict] | None = None) -> None:
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
    rows.extend(extra_rows or [])
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
                  countries=("India",), basins=None,
                  state="BYTES_VERIFIED") -> dict:
    digest = "a" * 64 if state == "BYTES_VERIFIED" else None
    payload = None
    if state == "BYTES_VERIFIED":
        payload = {"kind": "EXTERNAL_RETRIEVAL_RECEIPT",
                   "size_bytes": 1234,
                   "receipt": {
                       "source_url": "https://example.org/ev.bin",
                       "final_url": "https://example.org/ev.bin",
                       "retrieved_utc": "2026-09-27T00:00:00Z",
                       "http_status": 200, "content_type": "application/octet-stream",
                       "response_sha256": digest, "request_params": None,
                       "terms_reviewed": "fixture terms"}}
    return {
        "schema": er.SCHEMA, "version": 0,
        "claim_scope": "research_only_no_operational_authorization",
        "authority": dict(er.AUTHORITY_FLAGS), "register_state": "OPEN",
        "records": [{
            "evidence_id": "EV:TEST", "source_name": "test-src",
            "source_version": "1", "locator_type": "DATASET_RECORD",
            "locator": "test-bytes", "sha256": digest,
            "payload": payload,
            "verification_state": state, "access_terms": "test",
            "coverage": {"temporal_start": coverage[0],
                         "temporal_end": coverage[1],
                         "spatial": {"countries": list(countries),
                                     "basins": basins, "bbox": None,
                                     "polygon_ref": None},
                         "spatial_label": "test"},
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
    crosswalk_records = {r["source_record_id"]: r
                         for r in crosswalk["records"]}
    for index, record in enumerate(decision["records"]):
        source = crosswalk_records[record["source_record_id"]]
        if source.get("candidate_class") == "OUTSIDE":
            # Reference-only rows get a disposition, not eligibility.
            record["episode"].update({
                "candidate_episode_id": f"IND:EP:{index}",
                "independence_status": "NOT_INDEPENDENT"})
            record["adjudication"].update({
                "eligibility": "INELIGIBLE",
                "review_state": "COMPLETED",
                "reviewer_ids": ["reviewer-1"],
                "reviewed_utc": "2026-09-27T00:00:00+00:00",
                "location_confirmed": True,
                "territory_status": "OUTSIDE",
                "mechanism": None, "mechanism_certainty": "UNKNOWN",
                "evidence_citations": [evidence],
                "disposition_reason": "outside target territory"})
            continue
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


def test_crosswalk_retains_all_rows_and_classifies_candidates(tmp_path):
    extra = [
        {"GF_ID": "9001", "Country": "India, Nepal", "Year_exact": "2010",
         "Month": "5", "Day": "2", "GL_ID": "T1"},
        {"GF_ID": "9002", "Country": "", "Year_exact": "2011",
         "Month": "6", "Day": "3", "GL_ID": "T2"},
    ]
    source = tmp_path / "HMAGLOFDB.csv"
    _write_catalog(source, 3, extra_rows=extra)
    doc = ec.build_crosswalk(source, "test-1.0")
    # No row disappears: 3 India + 1 transboundary + 1 unknown + 1 Nepal.
    assert doc["summary"]["n_total_rows"] == 6
    assert doc["summary"]["n_target_country_rows"] == 3
    assert doc["summary"]["n_transboundary_rows"] == 1
    assert doc["summary"]["n_unknown_country_rows"] == 1
    assert doc["summary"]["n_reference_only_rows"] == 1
    assert doc["summary"]["n_candidate_rows"] == 5
    by_id = {r["source_record_id"]: r for r in doc["records"]}
    assert by_id["HMAGLOFDB:9001"]["candidate_class"] == "TRANSBOUNDARY"
    assert by_id["HMAGLOFDB:9002"]["candidate_class"] == "UNKNOWN_COUNTRY"
    assert by_id["HMAGLOFDB:9000"]["candidate_class"] == "OUTSIDE"
    # Day-precision stats count candidates only (India/transboundary/unknown).
    assert doc["summary"]["n_exact_day_rows"] == 5
    assert ec.validate_crosswalk(doc) == []
    assert all(r["location"]["territory_status"] == "UNASSESSED"
               for r in doc["records"])


def test_crosswalk_rejects_implicit_episode_or_eligibility(tmp_path):
    source = tmp_path / "HMAGLOFDB.csv"
    _write_catalog(source)
    doc = ec.build_crosswalk(source, "test-1.0")
    doc["records"][0]["adjudication"]["eligibility"] = "ELIGIBLE"
    assert any("cannot silently" in p for p in ec.validate_crosswalk(doc))


def test_crosswalk_retains_and_flags_duplicate_gf_ids(tmp_path):
    # Real HMAGLOFDB v1.3.0 defect: GF_ID 738-741 label unrelated events;
    # rows must be retained with the collision flagged, not dropped.
    source = tmp_path / "HMAGLOFDB.csv"
    _write_catalog(source)
    text = source.read_text(encoding="cp1252").replace("\n2,", "\n1,")
    source.write_text(text, encoding="cp1252")
    doc = ec.build_crosswalk(source, "test-1.0")
    assert doc["summary"]["n_total_rows"] == 4
    assert doc["summary"]["n_gf_id_collisions"] == 1
    ids = [r["source_record_id"] for r in doc["records"]]
    assert len(ids) == len(set(ids))
    flagged = [r for r in doc["records"] if r.get("gf_id_collision")]
    assert len(flagged) == 2


def test_crosswalk_transboundary_flag_classification(tmp_path):
    # A catalog Transboundary=Y flag keeps an India-labelled row in the
    # TRANSBOUNDARY adjudication class instead of plain TARGET_COUNTRY.
    source = tmp_path / "HMAGLOFDB.csv"
    _write_catalog(source, n_india=2, extra_rows=[
        {"GF_ID": "7001", "Year_approx": "", "Year_exact": "2013",
         "Month": "6", "Day": "17", "Lake_name": "Chorabari",
         "Glacier_name": "", "GL_ID": "GL:tb", "Country": "India",
         "Province": "Uttarakhand", "River_Basin": "Ganga",
         "Lat_lake": "30.7", "Lon_lake": "79.1", "Driver_lake": "",
         "Driver_GLOF": "", "Mechanism": "", "Repeat": "",
         "Sat_evidence": "", "Transboundary": "Y",
         "Ref_scientific": "", "Ref_scientific_full": "",
         "Ref_other": ""}])
    doc = ec.build_crosswalk(source, "test-1.0")
    by_id = {r["source"]["record_id"]: r for r in doc["records"]}
    assert by_id["7001"]["candidate_class"] == "TRANSBOUNDARY"
    assert by_id["1"]["candidate_class"] == "TARGET_COUNTRY"
    assert doc["summary"]["n_transboundary_rows"] == 1
    assert doc["summary"]["n_candidate_rows"] == 3


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


def test_adjudication_rejects_conflicting_shared_episode(tmp_path):
    source = tmp_path / "HMAGLOFDB.csv"
    _write_catalog(source)
    crosswalk = ec.build_crosswalk(source, "test-1.0")
    cw_path = tmp_path / "cw.json"
    cw_path.write_text(json.dumps(crosswalk), encoding="utf-8")
    decision = _reviewed_decision(cw_path, crosswalk)
    # Two rows claiming the same episode id with different dates/lakes —
    # an EPISODE_CONFLICT, fail closed.
    decision["records"][1]["episode"]["candidate_episode_id"] = "IND:EP:0"
    decision["records"][1]["episode"]["independence_status"] = "INDEPENDENT"
    problems = ea.validate_adjudication(
        decision, crosswalk, ea.sha256_file(cw_path))
    assert any("EPISODE_CONFLICT" in p for p in problems)


def test_adjudication_accepts_consistent_shared_episode(tmp_path):
    source = tmp_path / "HMAGLOFDB.csv"
    _write_catalog(source, n_india=2)
    # Two rows that are truly the same lake/date cascade pair.
    text = source.read_text(encoding="cp1252")
    lines = text.splitlines()
    lines[2] = lines[2].replace(",2002,6,17,", ",2001,6,17,").replace(
        "GL:1", "GL:0").replace("Lake1", "Lake0")
    source.write_text("\n".join(lines), encoding="cp1252")
    crosswalk = ec.build_crosswalk(source, "test-1.0")
    cw_path = tmp_path / "cw.json"
    cw_path.write_text(json.dumps(crosswalk), encoding="utf-8")
    decision = _reviewed_decision(cw_path, crosswalk)
    for record in decision["records"][:2]:
        record["episode"]["candidate_episode_id"] = "IND:EP:SHARED"
        record["episode"]["independence_status"] = "INDEPENDENT"
    assert ea.validate_adjudication(
        decision, crosswalk, ea.sha256_file(cw_path)) == []


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
    assert report["denominators"]["catalog_rows"] == 4
    assert report["denominators"]["india_candidate_rows"] == 3
    assert report["denominators"]["reference_only_rows"] == 1
    assert report["independent_episode_ids"] == [
        "IND:EP:0", "IND:EP:1", "IND:EP:2"]
    assert "adjudication_sha256" in report["inputs"]
    assert report["inputs"]["sidecars_verified"] is True
    assert fr.validate_report(report) == []


def test_report_passes_complete_observation_partition_to_readiness(tmp_path):
    crosswalk, cw_path, lf_path, reg_path = _paths(tmp_path)
    inventory_path = tmp_path / "lake_inventory.json"
    inventory_path.write_text(json.dumps([{
        "source_record_id": "NRSC:1", "lake_id": "1",
        "latitude": 30.7, "longitude": 79.1,
    }]), encoding="utf-8")
    frame = lf.build_frame(inventory_path, "NRSC", "test-1")
    observed_lake_frame = _write_bound(tmp_path / "lf_observed.json", frame)

    report = fr.build_report(cw_path, observed_lake_frame, reg_path)
    readiness = report["phase0_decision_readiness"]
    assert report["denominators"]["observation_unknown_lake_rows"] == 1
    assert "OBSERVATION_STATUS_DENOMINATOR_MISMATCH" not in readiness[
        "blocking_reasons"]
    assert readiness["required_condition_status"][
        "VERIFIED_NON_EVENT_STATUS_NOT_INFERRED_FROM_ABSENCE"] == "SATISFIED"
    assert readiness["status"] == "BLOCKED"


@pytest.mark.parametrize("malformed", [None, [], "report", 7])
def test_report_validator_rejects_non_object_root(malformed):
    assert fr.validate_report(malformed) == ["report root must be an object"]


def test_report_validator_rejects_malformed_gate_and_unhashable_episode_ids():
    report = {
        "schema": fr.SCHEMA,
        "claim_scope": "research_only_no_operational_authorization",
        "authority": fr.AUTHORITY_FLAGS,
        "denominators": {},
        "independent_episode_ids": [["unhashable"]],
        "gates": [],
        "decision": "PHASE0_ONLY_NO_ACQUISITION",
        "inputs": {},
    }
    problems = fr.validate_report(report)
    assert "independent_episode_ids must be unique strings" in problems
    assert "gates must be an object" in problems


def test_report_rejects_missing_or_tampered_sidecars(tmp_path):
    crosswalk, cw_path, lf_path, reg_path = _paths(tmp_path)
    report = fr.build_report(cw_path, lf_path, reg_path)
    assert fr.validate_report(report) == []
    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps(crosswalk), encoding="utf-8")
    with pytest.raises(ValueError, match="missing evidence sidecar"):
        fr.build_report(bare, lf_path, reg_path)
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


def test_verify_report_catches_forged_report(tmp_path):
    crosswalk, cw_path, lf_path, reg_path = _paths(tmp_path)
    decision = _reviewed_decision(cw_path, crosswalk)
    decision_path = _write_bound(tmp_path / "decision.json", decision)
    report = fr.build_report(cw_path, lf_path, reg_path, decision_path)
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    # Recompute must confirm a genuine report.
    assert fr.verify_report(report_path, cw_path, lf_path, reg_path,
                            decision_path) == []
    # A forged-but-internally-consistent report fails: shrink the episode
    # count and fix every mirror-computed field so validate_report stays
    # clean — only recomputation can catch it.
    forged = json.loads(json.dumps(report))
    forged["denominators"]["independent_exact_day_episodes"] = 2
    forged["independent_episode_ids"] = ["FAKE:0", "FAKE:1"]
    forged_path = tmp_path / "forged.json"
    forged_path.write_text(json.dumps(forged), encoding="utf-8")
    assert fr.validate_report(forged) == []  # consistent forgery passes schema
    problems = fr.verify_report(forged_path, cw_path, lf_path, reg_path,
                                decision_path)
    assert any("recomputed" in p or "differ" in p for p in problems)


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


def test_lake_frame_rejects_unknown_plus_full(tmp_path):
    p = _inventory(tmp_path, [{"source_record_id": "NRSC:1", "lake_id": "1",
                               "latitude": 30, "longitude": 80,
                               "observation_status": "UNKNOWN",
                               "observation_completeness": "FULL",
                               "observed_years": [2001],
                               "observation_method": dict(METHOD)}])
    with pytest.raises(ValueError, match="UNKNOWN observation cannot claim"):
        lf.build_frame(p, "NRSC", "test-1")


def test_lake_frame_full_requires_method_declaration(tmp_path):
    base = {"source_record_id": "NRSC:1", "lake_id": "1",
            "latitude": 30, "longitude": 80,
            "observation_status": "PARTIAL",
            "observation_completeness": "FULL",
            "observed_years": [2001, 2002, 2003]}
    with pytest.raises(ValueError, match="observation_method"):
        lf.build_frame(_inventory(tmp_path, [base]), "NRSC", "test-1")
    missing = dict(base, observation_method={k: v for k, v in METHOD.items()
                                             if k != "detection_threshold"})
    with pytest.raises(ValueError, match="detection_threshold"):
        lf.build_frame(_inventory(tmp_path, [missing]), "NRSC", "test-1")
    bad_cadence = dict(base, observation_method=dict(
        METHOD, cadence="WHENEVER"))
    with pytest.raises(ValueError, match="cadence"):
        lf.build_frame(_inventory(tmp_path, [bad_cadence]), "NRSC", "test-1")


def test_lake_frame_full_rejects_gap_and_coverage_conflicts(tmp_path):
    base = {"source_record_id": "NRSC:1", "lake_id": "1",
            "latitude": 30, "longitude": 80,
            "observation_status": "PARTIAL",
            "observation_completeness": "FULL",
            "observed_years": [2001, 2002, 2003]}
    gappy = dict(base, observation_method=dict(METHOD, gaps_censoring=[2002]))
    with pytest.raises(ValueError, match="gaps"):
        lf.build_frame(_inventory(tmp_path, [gappy]), "NRSC", "test-1")
    narrow = dict(base, observation_method=dict(
        METHOD, temporal_coverage={"start": "2005-01-01",
                                   "end": "2010-12-31"}))
    with pytest.raises(ValueError, match="outside declared"):
        lf.build_frame(_inventory(tmp_path, [narrow]), "NRSC", "test-1")


def test_lake_frame_requires_evidence_for_non_event(tmp_path):
    p = _inventory(tmp_path, [{"source_record_id": "NRSC:1", "lake_id": "1",
                               "latitude": 30, "longitude": 80,
                               "observation_status": "VERIFIED_NON_EVENT",
                               "observation_completeness": "FULL",
                               "observed_years": [2001],
                               "observation_method": dict(METHOD)}])
    with pytest.raises(ValueError, match="lacks full interval evidence"):
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
                               "observation_method": dict(METHOD),
                               "evidence_refs": ["evidence:EV:TEST"]}])
    doc = lf.build_frame(p, "NRSC", "test-1")
    assert doc["summary"]["n_verified_non_event_controls"] == 1
    assert lf.validate_frame(doc) == []


def test_lake_frame_counts_only_full_observed_years(tmp_path):
    p = _inventory(tmp_path, [{"source_record_id": "NRSC:1", "lake_id": "1",
                               "lat": 30, "lon": 80,
                               "observation_status": "PARTIAL",
                               "observation_completeness": "FULL",
                               "observed_years": [2001, 2002, 2003],
                               "observation_method": dict(METHOD)}])
    doc = lf.build_frame(p, "NRSC", "test-1")
    assert doc["summary"]["n_observable_lake_years"] == 3
    assert lf.validate_frame(doc) == []


def test_feasibility_denominators_partition_all_rows_and_deduplicate_years():
    verified = er.verified_records(_register_doc())
    rows = [
        {"location": {"territory_status": "OUTSIDE"},
         "lake": {"identity_status": "UNRECONCILED"},
         "observation": {"status": "UNKNOWN", "completeness": "UNKNOWN"}},
        {"location": {"territory_status": "IN_COUNTRY", "basin": "Ganga"},
         "lake": {"identity_status": "RECONCILED", "canonical_lake_id": "IN:L1"},
         "observation": {
             "status": "VERIFIED_NON_EVENT", "completeness": "FULL",
             "control_eligible": True, "evidence_refs": ["evidence:EV:TEST"],
             "at_risk_start": "2001-07-01", "at_risk_end": "2002-12-31",
             "observed_years": [2001, 2002]}},
        {"location": {"territory_status": "IN_COUNTRY", "basin": "Ganga"},
         "lake": {"identity_status": "RECONCILED", "canonical_lake_id": "IN:L1"},
         "observation": {"status": "PARTIAL", "completeness": "UNKNOWN"}},
    ]
    counts = fr._lake_denominators(rows, verified)
    assert counts["mapped_lake_rows"] == 3
    assert counts["outside_lakes"] == 1
    assert counts["in_country_canonical_lakes"] == 1
    assert counts["observation_unknown_lake_rows"] == 1
    assert counts["observation_partial_lake_rows"] == 1
    assert counts["observation_verified_non_event_lake_rows"] == 1
    assert counts["observation_full_lake_rows"] == 1
    assert counts["observable_lake_years"] == 2  # one lake, two unique years
    assert counts["verified_non_event_controls"] == 1
    assert counts["verified_non_event_intervals"] == 1
    assert counts["verified_non_event_lake_years"] == 1  # 2001 interval is partial
    assert counts["unverified_control_candidates"] == 0


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
    assert report["gates"]["lake_year_screen"] == "INSUFFICIENT_LAKE_FRAME"
    assert report["gates"]["bulk_acquisition_authorized"] is False
    assert report["authority"] == fr.AUTHORITY_FLAGS
    assert fr.validate_report(report) == []


def test_feasibility_gate_requires_identity_reconciliation(tmp_path):
    crosswalk, cw_path, _, reg_path = _paths(tmp_path, 3)
    inventory = _inventory(tmp_path, [
        {"source_record_id": "NRSC:1", "lake_id": "1",
         "latitude": 30, "longitude": 80, "territory_status": "IN_COUNTRY"},
        {"source_record_id": "CWC:1", "lake_id": "9",
         "latitude": 31, "longitude": 79, "territory_status": "IN_COUNTRY"},
    ])
    frame = lf.build_frame(inventory, "NRSC", "test-1")
    lf_path = _write_bound(tmp_path / "lf2.json", frame)
    decision = _reviewed_decision(cw_path, crosswalk)
    decision_path = _write_bound(tmp_path / "decision.json", decision)
    report = fr.build_report(cw_path, lf_path, reg_path, decision_path)
    assert report["denominators"]["unresolved_lake_identities"] == 2
    assert report["denominators"]["in_country_canonical_lakes"] == 0
    assert report["gates"]["lake_year_screen"] == "IDENTITY_RECONCILE_REQUIRED"
    assert report["next_gate"] == "RECONCILE_LAKE_IDENTITIES"


def test_feasibility_gate_requires_territory_classification(tmp_path):
    crosswalk, cw_path, _, reg_path = _paths(tmp_path, 3)
    inventory = _inventory(tmp_path, [
        {"source_record_id": "NRSC:1", "lake_id": "1",
         "latitude": 30, "longitude": 80},
        {"source_record_id": "NRSC:2", "lake_id": "2",
         "latitude": 31, "longitude": 79,
         "territory_status": "UNCERTAIN"},
        {"source_record_id": "NRSC:3", "lake_id": "3",
         "latitude": 32, "longitude": 78, "territory_status": "OUTSIDE"},
    ])
    frame = lf.build_frame(inventory, "NRSC", "test-1")
    lf_path = _write_bound(tmp_path / "lf3.json", frame)
    decision = _reviewed_decision(cw_path, crosswalk)
    decision_path = _write_bound(tmp_path / "decision.json", decision)
    report = fr.build_report(cw_path, lf_path, reg_path, decision_path)
    assert report["denominators"]["uncertain_territory_lakes"] == 2
    assert report["denominators"]["outside_lakes"] == 1
    assert report["gates"]["lake_year_screen"] == "TERRITORY_REVIEW_REQUIRED"
    assert report["next_gate"] == "REVIEW_TERRITORY_CLASSIFICATION"


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
    crosswalk, cw_path, lf_path, reg_path = _paths(tmp_path, 2)
    # Build two rows that truly share a lake/date so the shared episode id
    # is consistent (same episode, two source rows).
    src = tmp_path / "HMAGLOFDB.csv"
    text = src.read_text(encoding="cp1252")
    lines = text.splitlines()
    lines[2] = lines[2].replace(",2002,6,17,", ",2001,6,17,").replace(
        "GL:1", "GL:0").replace("Lake1", "Lake0")
    src.write_text("\n".join(lines), encoding="cp1252")
    crosswalk = ec.build_crosswalk(src, "test-1.0")
    cw_path = _write_bound(tmp_path / "cw2.json", crosswalk)
    decision = _reviewed_decision(cw_path, crosswalk)
    for record in decision["records"][:2]:
        record["episode"]["candidate_episode_id"] = "IND:EP:SHARED"
    decision_path = _write_bound(tmp_path / "decision.json", decision)
    report = fr.build_report(cw_path, lf_path, reg_path, decision_path)
    assert report["denominators"]["adjudicated_eligible_rows"] == 2
    assert report["denominators"]["independent_exact_day_episodes"] == 1
    assert report["independent_episode_ids"] == ["IND:EP:SHARED"]


def test_feasibility_rejects_unresolvable_evidence(tmp_path):
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


def test_feasibility_rejects_wrong_country_evidence(tmp_path):
    # Evidence whose spatial coverage excludes India cannot support an
    # India eligibility claim, even with a valid digest.
    crosswalk, cw_path, lf_path, _ = _paths(tmp_path, 3)
    foreign = _register_doc(countries=("Nepal", "Bhutan"))
    reg_path = _write_bound(tmp_path / "foreign_reg.json", foreign)
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
    assert er.verified_records(doc) == {}
