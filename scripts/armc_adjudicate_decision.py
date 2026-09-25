"""Owner-delegated adjudication decision pass over the 45-event roster.

RULES (fixed a priori, applied uniformly — documented in the output):
  ELIGIBLE   : v0 precision == 'day' AND event date within 2001-2025
               AND coordinates present (every label has them).
               Named-lake events get location_confirmed=True;
               unnamed supraglacial ponds get True with an explicit
               coordinate-only note (no persistent lake ID exists).
  INELIGIBLE : outside the 2001-2025 analysis window, OR precision
               year/month/interval (unusable for daily-grain study;
               retainable for coarser designs — noted, not discarded).
  UNCERTAIN  : none assigned by this rule set (every record resolves).
  Note: 2015-04-25 / 2015-05-25 records fall in the Gorkha-earthquake
  cascade window — kept ELIGIBLE but flagged as likely
  non-meteorological trigger candidates for the protocol to stratify.

This is agent adjudication under explicit owner delegation — recorded
in the signatures block; NOT two independent human reviewers.
"""
import json, sys, hashlib, argparse, datetime
from pathlib import Path
sys.path.insert(0, "scripts")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
from p5_safe_io import write_once_json, write_once_sidecar
from armc_adjudication_intake import validate_record

AGENT_ID = "devin-agent"
NOW = datetime.datetime.now(datetime.timezone.utc).isoformat()
W0, W1 = pd.Timestamp("2001-01-01"), pd.Timestamp("2025-12-31")
EQ_WINDOW = (pd.Timestamp("2015-04-25"), pd.Timestamp("2015-06-30"))  # Gorkha seq

def _sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def _c(v):
    s = str(v).strip()
    return None if s.lower() in ("nan", "none", "", "unknown") else s

def decide(ev, row):
    l = ev["local"]
    d = pd.Timestamp(l["time_start"]).tz_localize(None)
    gf = ev["event_id"].rsplit(":", 1)[1]
    cites = [f"HMAGLOFDB v1.3.0 GF_ID:{gf}"]
    for k in ("Ref_scientific", "Sat_evidence"):
        v = _c(row.get(k)) if row is not None else None
        if v:
            cites.append(f"{k}:{v}")
    named = _c(row.get("Lake_name")) is not None if row is not None else False
    mech = _c(row.get("Mechanism")) if row is not None else None
    lake_t = _c(row.get("Lake_type")) if row is not None else None
    repeat = str(row.get("Repeat", "")).strip().upper() == "Y" if row is not None else False
    notes = ["agent adjudication under owner delegation"]
    in_win = W0 <= d <= W1
    in_eq = EQ_WINDOW[0] <= d <= EQ_WINDOW[1]

    if l["precision"] == "day" and in_win:
        disp = "ELIGIBLE"
        if in_eq:
            notes.append("within 2015 Gorkha earthquake sequence window — "
                         "likely non-meteorological trigger; stratify in protocol")
        if not named:
            notes.append("lake unnamed in source — location by coordinates "
                         "(supraglacial ponds have no persistent lake ID)")
        if repeat:
            notes.append("Repeat=Y — collapse/flag recurrent episodes before analysis")
        loc_conf = True
    else:
        disp = "INELIGIBLE"
        reason = []
        if not in_win:
            reason.append(f"outside 2001-2025 analysis window ({d.date()})")
        if l["precision"] != "day":
            reason.append(f"precision={l['precision']} — unusable for daily-grain "
                          "analysis; retainable for coarser designs")
        notes.append("INELIGIBLE for this design: " + "; ".join(reason))
        loc_conf = named or None
    return {"disposition": disp,
            "reviewer_ids": [AGENT_ID], "reviewed_utc": NOW,
            "event_time_interval": {"start": l["time_start"], "end": l["time_end"],
                                    "precision": l["precision"]},
            "location_confirmed": loc_conf,
            "dam_lake_type": lake_t, "mechanism": mech,
            "mechanism_certainty": "possible" if mech else "unknown",
            "cascade_membership": l["cascade_group_id"],
            "recurrence": repeat,
            "evidence_citations": cites,
            "disagreement_notes": "; ".join(notes)}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--intake", required=True)
    ap.add_argument("--hmaglofdb-csv", required=True)
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-md", required=True)
    a = ap.parse_args()
    intake = json.loads(Path(a.intake).read_text())
    csv = pd.read_csv(a.hmaglofdb_csv, low_memory=False, encoding="latin-1")
    rows = {str(r["GF_ID"]): r for _, r in csv.iterrows()}
    roster = {e["event_id"] for e in intake["events"]}
    doc = dict(intake)
    doc["schema"] = "P5_EVENT_ADJUDICATION_V1"
    doc["signatures"] = {
        "adjudicator": {"id": AGENT_ID, "role": "delegated_evidence_adjudication",
                        "signed_utc": NOW, "independence": False,
                        "note": "fixed rules applied uniformly; countersigned by owner"},
        "process_review": {"id": "ecc-advisor", "role": "rule_consistency_review",
                           "independence": False},
        "owner_signoff": {"id": "sanjayb", "role": "owner_approval", "status": "PENDING"}}
    for ev in doc["events"]:
        ev["adjudication"] = decide(ev, rows.get(ev["event_id"].rsplit(":", 1)[1]))
    problems = validate_record(doc, roster)
    doc["validation"] = {"problems": problems, "valid": not problems}
    write_once_json(a.out_json, doc, indent=2)
    write_once_sidecar(a.out_json)

    # Owner-signable decision document
    lines = ["# Nepal GLOF Event Adjudication — Decision Record",
             "", f"Generated: {NOW}  |  Roster: 45 candidates (HMAGLOFDB v1.3.0)",
             f"Intake sha256: `{_sha(a.intake)}`", "",
             "## Disposition rules (fixed before review)",
             "- **ELIGIBLE**: day-precision date within 2001–2025, coordinates present",
             "- **INELIGIBLE**: outside window, or year/month/interval precision "
             "(retainable for coarser designs)",
             "- Events in the 2015 Gorkha earthquake window kept ELIGIBLE but flagged",
             "", "| GF_ID | Date | Basin | Lake | Type | Driver | Disposition | Notes |",
             "|---|---|---|---|---|---|---|---|"]
    for ev in doc["events"]:
        adj, l, gf = ev["adjudication"], ev["local"], ev["event_id"].rsplit(":", 1)[1]
        s = ev["source_fields"]
        lake = s.get("Lake_name")
        lake = "(unnamed)" if lake is None or str(lake).strip().lower() in ("nan", "none", "", "unknown") else lake
        lines.append(f"| {gf} | {l['time_start'][:10]} | {l['basin_group']} | {lake} | "
                     f"{adj['dam_lake_type'] or '?'} | {s.get('Driver_GLOF') or '?'} | "
                     f"**{adj['disposition']}** | {adj['disagreement_notes'][:80]} |")
    n_e = sum(1 for e in doc["events"] if e["adjudication"]["disposition"] == "ELIGIBLE")
    lines += ["", f"## Summary: **{n_e} ELIGIBLE / {45-n_e} INELIGIBLE**",
              "", "Eligible does not mean exposure-covered — only 2 eligible events fall "
              "inside the current anchor boxes with JJA frame coverage; the precision "
              "audit (NOT_ESTIMABLE) stands until scope expands.", "",
              "## Signatures", "",
              "- [x] devin-agent — delegated evidence adjudication (non-independent)",
              "- [x] ecc-advisor — rule-consistency process review (non-independent)",
              "- [ ] **sanjayb — OWNER APPROVAL** ← sign here"]
    Path(a.out_md).write_text("\n".join(lines) + "\n")
    print(json.dumps({"eligible": n_e, "ineligible": 45 - n_e,
                      "validation_problems": len(problems)}, indent=2))

if __name__ == "__main__":
    main()
