"""Automated triage pass over the 45-event adjudication intake.

IMPORTANT — this is NOT human adjudication.  Codex phase-4 requires two
independent qualified human reviewers.  This script produces a
provisional, evidence-bound DRAFT: every field is copied verbatim from
the HMAGLOFDB v1.3.0 source row (joined by GF_ID); nothing is inferred
from model outputs or weather data.  Disposition is UNCERTAIN by default
— an agent cannot grant ELIGIBLE, and no record is discarded.

A human reviewer confirms/overrides each record via
armc_adjudicate_cli.py; only then does a record become adjudicated.
"""
import json, sys, hashlib, argparse, datetime
from pathlib import Path
sys.path.insert(0, "scripts")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
from p5_safe_io import write_once_json, write_once_sidecar
from armc_adjudication_intake import validate_record

AGENT_ID = "devin-agent"

def _sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def _clean(v):
    s = str(v).strip()
    return None if s.lower() in ("nan", "none", "", "unknown") else s

def triage(ev, csv_by_gf):
    """One record: verbatim source fields + UNCERTAIN disposition."""
    gf = ev["event_id"].rsplit(":", 1)[1]
    row = csv_by_gf.get(gf)
    l = ev["local"]
    cites = [f"HMAGLOFDB v1.3.0 GF_ID:{gf}"]
    notes = ["automated triage — requires human reviewer confirmation"]
    if row is not None:
        ref = _clean(row.get("Ref_scientific"))
        sat = _clean(row.get("Sat_evidence"))
        if ref:
            cites.append(f"Ref_scientific:{ref}")
        if sat:
            cites.append(f"Sat_evidence:{sat}")
        mech = _clean(row.get("Mechanism"))
        lake_t = _clean(row.get("Lake_type"))
        repeat = str(row.get("Repeat", "")).strip().upper() == "Y"
        impact = _clean(row.get("Impact_type"))
    else:
        mech = lake_t = impact = None
        repeat = False
        notes.append("NO SOURCE ROW — unresolvable without manual lookup")
    if l["precision"] in ("year", "month"):
        notes.append(f"v0 precision={l['precision']} — needs exact-day evidence before any daily analysis")
    if l["precision"] == "day":
        notes.append("v0 precision=day — verify ±3d catalog uncertainty per HMAGLOFDB paper")
    if repeat:
        notes.append("Repeat=Y — recurrent series; episodes may not be independent")
    return {"disposition": "UNCERTAIN",
            "reviewer_ids": [AGENT_ID],
            "reviewed_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "event_time_interval": {"start": l["time_start"], "end": l["time_end"],
                                    "precision": l["precision"]},
            "location_confirmed": None,
            "dam_lake_type": lake_t,
            "mechanism": mech,
            "mechanism_certainty": "possible" if mech else "unknown",
            "cascade_membership": l["cascade_group_id"],
            "recurrence": repeat,
            "evidence_citations": cites,
            "disagreement_notes": "; ".join(notes)}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--intake", required=True)
    ap.add_argument("--hmaglofdb-csv", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    intake = json.loads(Path(a.intake).read_text())
    csv = pd.read_csv(a.hmaglofdb_csv, low_memory=False, encoding="latin-1")
    csv_by_gf = {str(r["GF_ID"]): r for _, r in csv.iterrows()}
    roster = {e["event_id"] for e in intake["events"]}
    doc = dict(intake)
    doc["schema"] = "P5_EVENT_ADJUDICATION_V1"
    doc["triage_note"] = ("AUTOMATED TRIAGE DRAFT — all fields verbatim from HMAGLOFDB "
                          "v1.3.0 source rows; UNCERTAIN dispositions pending human review; "
                          "reviewer_ids=['devin-agent'] is NOT independent human adjudication")
    doc["signatures"] = {"triage_agent": {"id": AGENT_ID, "role": "automated_evidence_triage",
                        "signed_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "independence": False,
                        "note": "not a substitute for qualified human review"},
                       "advisor_concurrence": {"id": "ecc-advisor", "role": "process_review",
                        "note": "advisor reviewed triage rules; LLM concurrence is not "
                                "independent adjudication"}}
    for ev in doc["events"]:
        ev["adjudication"] = triage(ev, csv_by_gf)
    problems = validate_record(doc, roster)
    doc["validation"] = {"problems": problems, "valid": not problems}
    write_once_json(a.out, doc, indent=2)
    write_once_sidecar(a.out)
    print(json.dumps({"out": a.out, "events": len(doc["events"]),
                      "valid": not problems, "problems": problems[:5],
                      "dispositions": {"UNCERTAIN": len(doc["events"])}}, indent=2))

if __name__ == "__main__":
    main()
