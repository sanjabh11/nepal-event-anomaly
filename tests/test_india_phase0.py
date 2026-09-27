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
import india_feasibility_report as fr
import india_lake_frame as lf


FIELDS = [
    "GF_ID", "Year_approx", "Year_exact", "Month", "Day", "Lake_name",
    "Glacier_name", "GL_ID", "Country", "Province", "River_Basin",
    "Lat_lake", "Lon_lake", "Driver_lake", "Driver_GLOF", "Mechanism",
    "Repeat", "Sat_evidence", "Ref_scientific", "Ref_scientific_full", "Ref_other",
]


def _write_catalog(path: Path) -> None:
    rows = [
        {"GF_ID": "1", "Year_approx": "", "Year_exact": "2013", "Month": "6", "Day": "17",
         "Lake_name": "Chorabari", "Glacier_name": "", "GL_ID": "No lake", "Country": "India",
         "Province": "Uttarakhand", "River_Basin": "Alaknanda", "Lat_lake": "30.7", "Lon_lake": "79.1",
         "Driver_lake": "", "Driver_GLOF": "rain", "Mechanism": "moraine", "Repeat": "",
         "Sat_evidence": "yes", "Ref_scientific": "paper", "Ref_scientific_full": "", "Ref_other": ""},
        {"GF_ID": "2", "Year_approx": "", "Year_exact": "2014", "Month": "8", "Day": "6",
         "Lake_name": "Gya", "Glacier_name": "", "GL_ID": "GL077605E35421N", "Country": "India",
         "Province": "Ladakh", "River_Basin": "Indus", "Lat_lake": "34.1", "Lon_lake": "77.6",
         "Driver_lake": "", "Driver_GLOF": "", "Mechanism": "piping", "Repeat": "",
         "Sat_evidence": "yes", "Ref_scientific": "paper", "Ref_scientific_full": "", "Ref_other": ""},
        {"GF_ID": "3", "Year_approx": "", "Year_exact": "2000", "Month": "4", "Day": "",
         "Lake_name": "Unknown", "Glacier_name": "", "GL_ID": "NA", "Country": "India",
         "Province": "Kashmir", "River_Basin": "Jhelum", "Lat_lake": "34.0", "Lon_lake": "75.0",
         "Driver_lake": "", "Driver_GLOF": "", "Mechanism": "", "Repeat": "",
         "Sat_evidence": "", "Ref_scientific": "", "Ref_scientific_full": "", "Ref_other": ""},
        {"GF_ID": "4", "Year_approx": "1998", "Year_exact": "", "Month": "", "Day": "",
         "Lake_name": "Old", "Glacier_name": "", "GL_ID": "L4", "Country": "Nepal",
         "Province": "", "River_Basin": "", "Lat_lake": "", "Lon_lake": "",
         "Driver_lake": "", "Driver_GLOF": "", "Mechanism": "", "Repeat": "",
         "Sat_evidence": "", "Ref_scientific": "", "Ref_scientific_full": "", "Ref_other": ""},
    ]
    with path.open("w", newline="", encoding="cp1252") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def test_crosswalk_retains_rows_without_adjudicating(tmp_path):
    source = tmp_path / "HMAGLOFDB.csv"
    _write_catalog(source)
    doc = ec.build_crosswalk(source, "test-1.0")
    assert doc["summary"] == {
        "n_target_country_rows": 3,
        "n_exact_day_rows": 2,
        "n_exact_day_post_1979_rows": 2,
        "n_post_1979_rows_with_source_lake_id": 1,
        "n_unique_post_1979_source_lake_ids": 1,
        "post_1979_source_lake_ids": ["GL077605E35421N"],
        "n_unreviewed": 3,
        "n_analysis_eligible": 0,
        "n_independent_episodes": 0,
    }
    assert ec.validate_crosswalk(doc) == []
    assert doc["authority"] == ec.AUTHORITY_FLAGS
    assert all(r["adjudication"]["eligibility"] == "UNREVIEWED" for r in doc["records"])


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


def test_report_accepts_only_sha_bound_reviewed_successor(tmp_path):
    source = tmp_path / "HMAGLOFDB.csv"
    _write_catalog(source)
    cw = ec.build_crosswalk(source, "test-1.0")
    cw_path = tmp_path / "cw.json"
    lf_path = tmp_path / "lf.json"
    cw_path.write_text(json.dumps(cw), encoding="utf-8")
    lf_path.write_text(json.dumps(_frame_doc()), encoding="utf-8")
    intake = ea.build_intake(cw_path)
    decision = json.loads(json.dumps(intake))
    decision["status"] = "REVIEWED"
    for record in decision["records"]:
        record["episode"]["independence_status"] = "NOT_INDEPENDENT"
        record["adjudication"].update({
            "eligibility": "INELIGIBLE", "review_state": "COMPLETED",
            "reviewer_ids": ["reviewer-1"],
            "reviewed_utc": "2026-09-27T00:00:00+00:00",
            "location_confirmed": False, "mechanism": None,
            "mechanism_certainty": "UNKNOWN",
            "evidence_citations": ["paper:1"],
        })
    record = decision["records"][1]
    record["episode"].update({"candidate_episode_id": "IND:EP:1",
                               "independence_status": "INDEPENDENT"})
    record["adjudication"].update({
        "eligibility": "ELIGIBLE", "review_state": "COMPLETED",
        "reviewer_ids": ["reviewer-1"],
        "reviewed_utc": "2026-09-27T00:00:00+00:00",
        "location_confirmed": True, "mechanism": "moraine",
        "mechanism_certainty": "CONFIRMED",
        "evidence_citations": ["paper:1"],
    })
    decision_path = tmp_path / "decision.json"
    decision_path.write_text(json.dumps(decision), encoding="utf-8")
    report = fr.build_report(cw_path, lf_path, decision_path)
    assert report["denominators"]["independent_exact_day_episodes"] == 1
    assert "adjudication_sha256" in report["inputs"]
    assert fr.validate_report(report) == []


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
                               "observation_completeness": "FULL"}])
    with pytest.raises(ValueError, match="lacks full interval evidence"):
        lf.build_frame(p, "NRSC", "test-1")


def test_lake_frame_accepts_explicit_verified_control(tmp_path):
    p = _inventory(tmp_path, [{"source_record_id": "NRSC:1", "lake_id": "1",
                               "latitude": 30, "longitude": 80,
                               "observation_status": "VERIFIED_NON_EVENT",
                               "observation_completeness": "FULL",
                               "at_risk_start": "2001-01-01",
                               "at_risk_end": "2020-12-31",
                               "evidence_refs": ["satellite:frame-1"]}])
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
            "evidence_refs": ["satellite:frame-1"]}
    bad_format = dict(base, at_risk_start="2001-1-01", at_risk_end="2020-12-31")
    with pytest.raises(ValueError, match="canonical YYYY-MM-DD"):
        lf.build_frame(_inventory(tmp_path, [bad_format]), "NRSC", "test-1")
    reversed_interval = dict(base, at_risk_start="2020-12-31", at_risk_end="2001-01-01")
    with pytest.raises(ValueError, match="starts after"):
        lf.build_frame(_inventory(tmp_path, [reversed_interval]), "NRSC", "test-1")


def _crosswalk_doc(n=1, *, reviewed=False, eligible=False, independent=False):
    records = []
    for i in range(n):
        records.append({"source_record_id": f"HMAGLOFDB:{i}",
                        "date": {"precision": "day"},
                        "lake_identity": {"raw_id": f"GL:{i}",
                                           "status": "SOURCE_ID_PRESENT",
                                           "canonical_lake_id": None},
                        "evidence": {"references": ["paper:1"]},
                        "episode": {"candidate_episode_id": f"IND:EP:{i}",
                                    "independence_status": "INDEPENDENT" if independent else "UNASSESSED"},
                        "adjudication": {
                            "eligibility": "ELIGIBLE" if eligible else "UNREVIEWED",
                            "review_state": "COMPLETED" if reviewed else "AWAITING_ADJUDICATION",
                            "reviewer_ids": ["reviewer-1"] if reviewed else [],
                            "location_confirmed": True if reviewed else None,
                            "mechanism": "moraine" if reviewed else None,
                            "mechanism_certainty": "CONFIRMED" if reviewed else None,
                            "evidence_citations": ["paper:1"] if reviewed else []}})
    return {"schema": ec.SCHEMA, "records": records}


def _frame_doc(n=0):
    return {"schema": lf.SCHEMA, "records": [],
            "summary": {"n_observable_lake_years": 0}}


def test_feasibility_keeps_gate_pending_while_adjudication_is_incomplete(tmp_path):
    cw = tmp_path / "cw.json"; lfpath = tmp_path / "lf.json"
    cw.write_text(json.dumps(_crosswalk_doc()), encoding="utf-8")
    lfpath.write_text(json.dumps(_frame_doc()), encoding="utf-8")
    report = fr.build_report(cw, lfpath)
    assert report["gates"]["event_weather_screen"] == "ADJUDICATION_INCOMPLETE"
    assert report["gates"]["bulk_acquisition_authorized"] is False
    assert report["authority"] == fr.AUTHORITY_FLAGS
    assert fr.validate_report(report) == []


def test_feasibility_closes_event_route_after_completed_small_cohort(tmp_path):
    cw = tmp_path / "cw.json"; lfpath = tmp_path / "lf.json"
    cw.write_text(json.dumps(_crosswalk_doc(3, reviewed=True, eligible=True, independent=True)), encoding="utf-8")
    lfpath.write_text(json.dumps(_frame_doc()), encoding="utf-8")
    report = fr.build_report(cw, lfpath)
    assert report["gates"]["event_weather_screen"] == "CLOSE_EVENT_WEATHER_ROUTE"
    assert report["next_gate"] == "RECONCILE_LAKE_INVENTORY"


def test_feasibility_requires_simulation_at_twenty_episodes(tmp_path):
    cw = tmp_path / "cw.json"; lfpath = tmp_path / "lf.json"
    cw.write_text(json.dumps(_crosswalk_doc(20, reviewed=True, eligible=True, independent=True)), encoding="utf-8")
    lfpath.write_text(json.dumps(_frame_doc()), encoding="utf-8")
    report = fr.build_report(cw, lfpath)
    assert report["gates"]["event_weather_screen"] == "SIMULATION_REQUIRED"
    assert report["next_gate"] == "RUN_PREDECLARED_PRECISION_SIMULATION"


def test_feasibility_does_not_count_eligible_without_mechanism_adjudication(tmp_path):
    cw = tmp_path / "cw.json"; lfpath = tmp_path / "lf.json"
    doc = _crosswalk_doc(20, reviewed=True, eligible=True, independent=True)
    for record in doc["records"]:
        record["adjudication"].pop("mechanism_certainty")
    cw.write_text(json.dumps(doc), encoding="utf-8")
    lfpath.write_text(json.dumps(_frame_doc()), encoding="utf-8")
    report = fr.build_report(cw, lfpath)
    assert report["denominators"]["independent_exact_day_episodes"] == 0
    assert report["gates"]["event_weather_screen"] == "CLOSE_EVENT_WEATHER_ROUTE"


def test_feasibility_validator_rejects_authority_flip(tmp_path):
    cw = tmp_path / "cw.json"; lfpath = tmp_path / "lf.json"
    cw.write_text(json.dumps(_crosswalk_doc()), encoding="utf-8")
    lfpath.write_text(json.dumps(_frame_doc()), encoding="utf-8")
    report = fr.build_report(cw, lfpath)
    report["gates"]["bulk_acquisition_authorized"] = True
    assert any("authority flag" in p for p in fr.validate_report(report))
    report = fr.build_report(cw, lfpath)
    report["authority"]["forecast_authorized"] = True
    assert any("authority flags" in p for p in fr.validate_report(report))
