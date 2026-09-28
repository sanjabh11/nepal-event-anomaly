"""Adjudication worksheet/ingest workflow contract tests."""
import csv
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import india_adjudication_workflow as wf  # noqa: E402
import india_event_adjudication as ea  # noqa: E402
import india_event_crosswalk as ec  # noqa: E402

E = Path("/Users/sanjayb/nepal-event-anomaly-evidence")
_INTAKE = E / "india-phase0-source-intake" / "INDIA_EVENT_ADJUDICATION_INTAKE_V0.json"
_CROSSWALK = E / "india-phase0-source-intake" / "INDIA_HMAGLOFDB_CROSSWALK_V2.json"
_PRESENT = _INTAKE.is_file() and _CROSSWALK.is_file()


def _mini_crosswalk(tmp_path, rows):
    """Build a tiny real crosswalk via the crosswalk builder."""
    header = ("GF_ID,Lake_Name,Glacier_Name,Country,Province,River_Basin,"
              "Year,Month,Day,Mechanism,Driver_Lake,Driver_GLOF,"
              "Lat_Lake,Lon_Lake,Repeat,Ref_scientific")
    lines = [header]
    for i, (country, cls) in enumerate(rows, start=1):
        lines.append(f"GL:{i},Lake{i},Glac{i},{country},Prov{i},Basin{i},"
                     f"2001,6,17,ice,Ldriver,Gdriver,30.{i:03d},80.{i:03d},"
                     f"N,ref{i}")
    src = tmp_path / "catalog.csv"
    src.write_text("\n".join(lines), encoding="cp1252")
    return ec.build_crosswalk(src, "test-0")


def _filled_row(sid, **kw):
    row = {"source_record_id": sid}
    base = {
        "review_eligibility": "INELIGIBLE",
        "review_territory_status": "UNCERTAIN",
        "review_location_confirmed": "yes",
        "review_mechanism": "",
        "review_mechanism_certainty": "UNKNOWN",
        "review_episode_id": "EP:1",
        "review_independence_status": "NOT_INDEPENDENT",
        "review_recurrence_group_id": "",
        "review_cascade_group_id": "",
        "review_evidence_citations": "evidence:EV:TEST",
        "review_disposition_reason": "test",
        "review_reviewer_ids": "reviewer-1",
        "review_reviewed_utc": "2026-09-28T00:00:00+00:00",
        "territory_evidence_artifact": "",
        "territory_evidence_sha256": "",
    }
    base.update(kw)
    row.update(base)
    return row


def _write_csv(path, rows):
    cols = ["source_record_id"] + wf.REVIEW_COLS
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def test_ingest_seals_clean_reviewed_doc(tmp_path):
    cw = _mini_crosswalk(tmp_path, [("India", "T"), ("Nepal", "O")])
    cw_path = tmp_path / "cw.json"
    cw_path.write_text(json.dumps(cw))
    intake = ea.build_intake(cw_path)
    ip = tmp_path / "intake.json"
    ip.write_text(json.dumps(intake))
    rows = [_filled_row(r["source_record_id"]) for r in intake["records"]]
    ws = tmp_path / "ws.csv"
    _write_csv(ws, rows)
    out = tmp_path / "decision.json"
    problems = wf.ingest(ip, cw_path, ws, out, "SoI-mirror", "v1", "EPSG:4326")
    assert problems == []
    doc = json.loads(out.read_text())
    assert doc["status"] == "REVIEWED"
    assert all(r["adjudication"]["review_state"] == "COMPLETED"
               for r in doc["records"])


def test_ingest_fails_on_missing_disposition(tmp_path):
    cw = _mini_crosswalk(tmp_path, [("India", "T"), ("Nepal", "O")])
    cw_path = tmp_path / "cw.json"
    cw_path.write_text(json.dumps(cw))
    intake = ea.build_intake(cw_path)
    ip = tmp_path / "intake.json"
    ip.write_text(json.dumps(intake))
    ws = tmp_path / "ws.csv"
    _write_csv(ws, [_filled_row(intake["records"][0]["source_record_id"])])
    out = tmp_path / "decision.json"
    problems = wf.ingest(ip, cw_path, ws, out, "x", "y", "z")
    assert any("no worksheet row" in p for p in problems)
    assert not out.exists()


def test_ingest_rejects_bad_enum(tmp_path):
    cw = _mini_crosswalk(tmp_path, [("India", "T")])
    cw_path = tmp_path / "cw.json"
    cw_path.write_text(json.dumps(cw))
    intake = ea.build_intake(cw_path)
    ip = tmp_path / "intake.json"
    ip.write_text(json.dumps(intake))
    ws = tmp_path / "ws.csv"
    _write_csv(ws, [_filled_row(intake["records"][0]["source_record_id"],
                              review_eligibility="MAYBE")])
    problems = wf.ingest(ip, cw_path, ws, tmp_path / "d.json",
                         "x", "y", "z")
    assert any("eligibility" in p for p in problems)


def test_ingest_enforces_typed_territory_evidence(tmp_path):
    cw = _mini_crosswalk(tmp_path, [("India", "T")])
    cw_path = tmp_path / "cw.json"
    cw_path.write_text(json.dumps(cw))
    intake = ea.build_intake(cw_path)
    ip = tmp_path / "intake.json"
    ip.write_text(json.dumps(intake))
    ws = tmp_path / "ws.csv"
    sid = intake["records"][0]["source_record_id"]
    _write_csv(ws, [_filled_row(sid, review_eligibility="ELIGIBLE",
                                review_territory_status="IN_COUNTRY",
                                review_mechanism="moraine",
                                review_mechanism_certainty="CONFIRMED",
                                review_independence_status="INDEPENDENT")])
    problems = wf.ingest(ip, cw_path, ws, tmp_path / "d.json",
                         "x", "y", "z")
    assert any("territory_evidence" in p for p in problems)


def test_ingest_with_qualified_territory_evidence(tmp_path):
    cw = _mini_crosswalk(tmp_path, [("India", "T")])
    cw_path = tmp_path / "cw.json"
    cw_path.write_text(json.dumps(cw))
    intake = ea.build_intake(cw_path)
    ip = tmp_path / "intake.json"
    ip.write_text(json.dumps(intake))
    ws = tmp_path / "ws.csv"
    sid = intake["records"][0]["source_record_id"]
    _write_csv(ws, [_filled_row(
        sid, review_eligibility="ELIGIBLE",
        review_territory_status="IN_COUNTRY",
        review_mechanism="moraine",
        review_mechanism_certainty="CONFIRMED",
        review_independence_status="INDEPENDENT",
        territory_evidence_artifact="INDIA_TERRITORY_DECISION_V0.json",
        territory_evidence_sha256="b" * 64)])
    problems = wf.ingest(ip, cw_path, ws, tmp_path / "d.json",
                         "x", "y", "z")
    # evidence citation must resolve to BYTES_VERIFIED -> fails upstream
    # on evidence:EV:TEST, but never on the territory gate
    assert not any("territory_evidence" in p for p in problems)


@pytest.mark.skipif(not _PRESENT, reason="external evidence root unavailable")
def test_live_worksheet_covers_intake(tmp_path):
    out = tmp_path / "ws.csv"
    info = wf.worksheet(_INTAKE, _CROSSWALK, out)
    assert info["rows"] == 768
    assert info["reviewable"] == 61
    with open(out) as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 768
    outside = [r for r in rows if r["ctx_candidate_class"] == "OUTSIDE"]
    assert all(r["suggested_eligibility"] == "INELIGIBLE" for r in outside)
