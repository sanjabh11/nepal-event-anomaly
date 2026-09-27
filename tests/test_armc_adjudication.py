"""Adversarial tests for the hardened adjudication validator + audit gen."""
import copy, json
from pathlib import Path
import pytest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import armc_adjudication_intake as ai

EV = Path("/Users/sanjayb/nepal-event-anomaly-evidence/p5-glof-2026-09-19")
if (EV / "glof-events/p3_runner_package_v0.json").is_file():
    ROSTER = {e["event_id"] for e in
              json.loads((EV / "glof-events/p3_runner_package_v0.json")
                         .read_text())["event_labels"]}
else:
    ROSTER = None
pytestmark = pytest.mark.skipif(
    ROSTER is None,
    reason="operator-local GLOF evidence lane absent (CI checkout)")

def _valid_event(eid="e1"):
    return {"event_id": eid,
            "local": {"time_start": "2016-06-10T00:00:00Z",
                      "time_end": "2016-06-15T00:00:00Z"},
            "adjudication": {
                "disposition": "ELIGIBLE", "reviewer_ids": ["r1", "r2"],
                "reviewed_utc": "2026-09-24T03:00:00Z",
                "event_time_interval": {"start": "2016-06-10T00:00:00Z",
                                        "end": "2016-06-15T00:00:00Z",
                                        "precision": "interval"},
                "location_confirmed": True, "dam_lake_type": "moraine",
                "mechanism": "overtopping", "mechanism_certainty": "probable",
                "cascade_membership": "", "recurrence": False,
                "evidence_citations": ["HMAGLOFDB:400"], "disagreement_notes": ""}}

def _doc(ev): return {"events": ev}

def test_valid_record_passes():
    ev = _valid_event()
    assert ai.validate_record(_doc([ev]), {"e1"}) == []

def test_missing_roster_id_fails():
    p = ai.validate_record(_doc([]), {"e1"})
    assert any("missing roster" in x for x in p)

def test_extra_id_fails():
    p = ai.validate_record(_doc([_valid_event("eX")]), {"e1"})
    assert any("extra ids" in x for x in p)

def test_duplicate_id_fails():
    p = ai.validate_record(_doc([_valid_event(), _valid_event()]), None)
    assert any("duplicate event_ids" in x for x in p)

def test_bad_disposition_fails():
    ev = _valid_event(); ev["adjudication"]["disposition"] = "MAYBE"
    assert any("disposition" in x for x in ai.validate_record(_doc([ev]), {"e1"}))

def test_no_reviewers_fails():
    ev = _valid_event(); ev["adjudication"]["reviewer_ids"] = []
    assert any("reviewer_ids" in x for x in ai.validate_record(_doc([ev]), {"e1"}))

def test_duplicate_reviewers_fail():
    ev = _valid_event(); ev["adjudication"]["reviewer_ids"] = ["r1", "r1"]
    assert any("duplicate reviewer" in x for x in ai.validate_record(_doc([ev]), {"e1"}))

def test_malformed_utc_fails():
    ev = _valid_event(); ev["adjudication"]["reviewed_utc"] = "not-a-date"
    assert any("reviewed_utc" in x for x in ai.validate_record(_doc([ev]), {"e1"}))

def test_bad_precision_fails():
    ev = _valid_event(); ev["adjudication"]["event_time_interval"]["precision"] = "fuzzy"
    assert any("precision" in x for x in ai.validate_record(_doc([ev]), {"e1"}))

def test_interval_start_after_end_fails():
    ev = _valid_event()
    ev["adjudication"]["event_time_interval"]["start"] = "2016-06-20T00:00:00Z"
    assert any("start after end" in x for x in ai.validate_record(_doc([ev]), {"e1"}))

def test_interval_not_covering_v0_fails():
    """Reviewer narrows the interval inside the v0 label bounds -> fail."""
    ev = _valid_event()
    ev["adjudication"]["event_time_interval"] = {
        "start": "2016-06-11T00:00:00Z", "end": "2016-06-12T00:00:00Z",
        "precision": "interval"}
    p = ai.validate_record(_doc([ev]), {"e1"})
    assert any("does not cover v0" in x for x in p)

def test_mechanism_without_certainty_fails():
    ev = _valid_event(); ev["adjudication"]["mechanism_certainty"] = None
    assert any("certainty" in x for x in ai.validate_record(_doc([ev]), {"e1"}))

def test_no_evidence_citations_fails():
    ev = _valid_event(); ev["adjudication"]["evidence_citations"] = []
    assert any("evidence_citations" in x for x in ai.validate_record(_doc([ev]), {"e1"}))

def test_eligible_requires_location_confirmed():
    ev = _valid_event(); ev["adjudication"]["location_confirmed"] = False
    assert any("location_confirmed" in x for x in ai.validate_record(_doc([ev]), {"e1"}))

def test_eligible_requires_day_or_interval():
    ev = _valid_event()
    ev["adjudication"]["event_time_interval"]["precision"] = "year"
    assert any("day/bounded-interval" in x for x in ai.validate_record(_doc([ev]), {"e1"}))

def test_uncertain_disposition_ok_without_interval():
    ev = _valid_event()
    ev["adjudication"].update({"disposition": "UNCERTAIN", "location_confirmed": None,
                              "event_time_interval": {"start": None, "end": None,
                                                      "precision": "unknown"}})
    assert ai.validate_record(_doc([ev]), {"e1"}) == []

def test_full_roster_required():
    """Intake doc covering all 45 unadjudicated events must fail closed
    (nothing adjudicated yet) but never silently drop roster members."""
    intake = json.loads(Path(
        "/Users/sanjayb/nepal-event-anomaly-evidence/p5-armc-pressure-levels-2026-09-22/"
        "retrieval/armc_event_adjudication_intake_v0.json").read_text())
    ids = {e["event_id"] for e in intake["events"]}
    assert ids == ROSTER
    p = ai.validate_record(intake, ROSTER)
    assert p  # empty adjudications must fail
    assert not any("missing roster" in x or "extra ids" in x for x in p)


import armc_readiness_audit_gen as ag

PKG_P = EV / "glof-events/p3_runner_package_v0.json"
CSV_P = EV / "glof-events/HMAGLOFDB.csv"

@pytest.fixture(scope="module")
def pkg_csv():
    import pandas as pd
    return (json.loads(PKG_P.read_text()),
            pd.read_csv(CSV_P, low_memory=False, encoding="latin-1"))

def test_reconciliation_all_traceable(pkg_csv):
    pkg, csv = pkg_csv
    recon = ag.reconcile(pkg, csv)
    assert len(recon) == 45
    assert all(r["disposition"] == "TRACEABLE" for r in recon)

def test_reconciliation_catches_tampered_coordinate(pkg_csv):
    """Adversarial: shift a latitude by >1e-3 -> FIELD_MISMATCH."""
    pkg, csv = copy.deepcopy(pkg_csv[0]), pkg_csv[1]
    pkg["event_labels"][0]["latitude"] += 0.01
    recon = ag.reconcile(pkg, csv)
    bad = [r for r in recon if r["disposition"] == "FIELD_MISMATCH"]
    assert len(bad) == 1 and "lat_lake" in bad[0]["mismatches"]

def test_reconciliation_missing_gf_fails_closed(pkg_csv):
    pkg = copy.deepcopy(pkg_csv[0])
    pkg["event_labels"][0]["event_id"] = "icimod_hmaglofdb_v1_3_0:1.3.0:999999"
    recon = ag.reconcile(pkg, pkg_csv[1])
    assert recon[0]["disposition"] == "UNRESOLVED_NO_SOURCE_ROW"

def test_spatial_counts_and_gf730_source(pkg_csv):
    """Regression: 730 must be gandaki/cds; counts must be 7/6/5/2."""
    import pandas as pd
    els = pd.DataFrame(pkg_csv[0]["event_labels"])
    st = ag.spatial_table(els)
    assert int(st["in_anchor_box"].sum()) == 7
    inbox = st[st["in_anchor_box"] & st["in_2001_2025_window"] & (st["precision"] == "day")]
    assert len(inbox) == 5
    g730 = inbox[inbox["event_id"].str.endswith(":730")].iloc[0]
    assert g730["basin_group"] == "gandaki" and g730["dominant_source_500hpa"] == "cds"
    jja = inbox[inbox["in_jja"]]
    assert len(jja) == 2 and (jja["basin_group"] == "koshi").all()


def test_audit_gen_rerun_reproducible(tmp_path, monkeypatch):
    """Independent reruns must reproduce identical content except timestamp."""
    import subprocess, os
    ROOT_E = "/Users/sanjayb/nepal-event-anomaly-evidence"
    args = ["--package", f"{ROOT_E}/p5-glof-2026-09-19/glof-events/p3_runner_package_v0.json",
            "--hmaglofdb-csv", f"{ROOT_E}/p5-glof-2026-09-19/glof-events/HMAGLOFDB.csv",
            "--frame", f"{ROOT_E}/p5-armc-pressure-levels-2026-09-22/daily-frame-v1/armc_daily_frame_v0.csv",
            "--source-map", f"{ROOT_E}/p5-armc-pressure-levels-2026-09-22/retrieval/armc_source_map_v15.json"]
    outs = []
    for i in (1, 2):
        o = tmp_path / f"audit_{i}.json"
        r = subprocess.run([sys.executable, "scripts/armc_readiness_audit_gen.py",
                            *args, "--out", str(o)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        outs.append(json.loads(o.read_text()))
    a, b = outs
    assert a.pop("generated_utc") or True
    b.pop("generated_utc")
    assert a == b  # byte-equal except timestamp
    assert a["spatial_identifiability"]["counts"]["n_in_anchor_box_AND_window_AND_day_precision_AND_jja"] == 2
