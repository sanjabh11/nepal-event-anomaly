"""Interactive CLI for human adjudication of the 45 Arm C event candidates.

Usage:
  python3 scripts/armc_adjudicate_cli.py \
      --intake <armc_event_adjudication_intake_v0.json> \
      --workfile <adjudication_wip.json>          # resumable working file
      --reviewer <your-reviewer-id>

Walks each candidate event showing the source evidence (lake name, dates,
mechanism, driver, repeat flag). Reviewer assigns a disposition; progress
is saved after every record — 'q' quits safely, rerun to resume.

On completion the record is validated against the hardened schema
(armc_adjudication_intake.validate_record) and can be sealed by the
coordinator. The CLI never modifies the v0 intake or event labels.
"""
import argparse, json, sys, datetime
from pathlib import Path
sys.path.insert(0, "scripts")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from armc_adjudication_intake import validate_record

DISP = {"e": "ELIGIBLE", "i": "INELIGIBLE", "u": "UNCERTAIN"}
PREC = {"d": "day", "m": "month", "y": "year", "n": "interval", "u": "unknown"}
MECH_CERT = {"c": "confirmed", "p": "probable", "o": "possible", "u": "unknown"}


def _ask(prompt, default=None):
    s = input(f"{prompt}" + (f" [{default}]" if default is not None else "") + ": ").strip()
    return s if s else (default if default is not None else "")


def _ask_choice(prompt, mapping, default=None):
    disp = "/".join(f"{k}={v}" for k, v in mapping.items())
    while True:
        s = _ask(f"{prompt} ({disp})", default).lower()
        if s in mapping:
            return mapping[s]
        if s == "q":
            return "q"
        print("  invalid choice")


def show_event(i, n, ev):
    l, s = ev["local"], ev["source_fields"]
    print(f"\n{'='*72}\nEVENT {i+1}/{n}  {ev['event_id']}")
    print(f"  basin={l['basin_group']}  coords=({l['lat']},{l['lon']})  "
          f"precision={l['precision']}  window={l['time_start'][:10]}..{l['time_end'][:10]}")
    print(f"  cascade_group={l['cascade_group_id'] or '-'}")
    for k, v in s.items():
        if v is not None and str(v).strip().lower() not in ("nan", "none", "", "unknown"):
            print(f"  {k}: {v}")
    if not any(v is not None and str(v).strip().lower() not in ("nan","none","","unknown")
               for v in s.values()):
        print("  (all source fields Unknown)")


def load_source_row(csv_path, gf_id):
    """Full HMAGLOFDB row for a GF_ID — every populated column."""
    import pandas as pd
    csv = pd.read_csv(csv_path, low_memory=False, encoding="latin-1")
    csv["GF_ID"] = csv["GF_ID"].astype(str)
    m = csv[csv["GF_ID"] == str(gf_id)]
    if m.empty:
        return {}
    return {c: m.iloc[0][c] for c in csv.columns
            if str(m.iloc[0][c]).strip() not in ("nan", "None", "")}


def _rev_key(v, mapping):
    """Inverse lookup: find the short key for an existing stored value."""
    for k, val in mapping.items():
        if val == v:
            return k
    return None


def adjudicate_event(ev, reviewer, prior=None):
    prior = prior or {}
    adj = {}
    d = _ask_choice("Disposition", DISP, _rev_key(prior.get("disposition"), DISP) or "u")
    if d == "q":
        return None
    adj["disposition"] = d
    adj["reviewer_ids"] = [reviewer]
    adj["reviewed_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    l = ev["local"]
    piv = prior.get("event_time_interval") or {}
    pdef = _rev_key(piv.get("precision"), PREC) or \
        {"day": "d", "month": "m", "year": "y"}.get(l["precision"], "u")
    p = _ask_choice("Timing precision", PREC, pdef)
    if p == "q":
        return None
    ts = _ask("  interval start ISO", (piv.get("start") or l["time_start"])[:10])
    te = _ask("  interval end ISO", (piv.get("end") or l["time_end"])[:10])
    adj["event_time_interval"] = {"start": ts or None, "end": te or None, "precision": p}
    lc = _ask_choice("Lake/location confirmed?", {"y": True, "n": False, "u": None},
                     _rev_key(prior.get("location_confirmed"), {"y": True, "n": False, "u": None}) or "u")
    if lc == "q":
        return None
    adj["location_confirmed"] = lc
    def _blank(v):
        return "" if v is None or str(v).strip().lower() in ("unknown", "nan", "none") else str(v)
    adj["dam_lake_type"] = _ask("dam/lake type (blank=unknown)",
                              _blank(prior.get("dam_lake_type")) or _blank(ev["source_fields"].get("Driver_lake"))) or None
    adj["mechanism"] = _ask("mechanism (blank=unknown)",
                            _blank(prior.get("mechanism")) or _blank(ev["source_fields"].get("Mechanism"))) or None
    if adj["mechanism"]:
        mc = _ask_choice("mechanism certainty", MECH_CERT,
                         _rev_key(prior.get("mechanism_certainty"), MECH_CERT) or "u")
        if mc == "q":
            return None
        adj["mechanism_certainty"] = mc
    else:
        adj["mechanism_certainty"] = "unknown"
    adj["cascade_membership"] = _ask("cascade group id",
                                   prior.get("cascade_membership") or l["cascade_group_id"])
    rec = _ask_choice("part of a repeat/recurrent series?", {"y": True, "n": False, "u": None},
                      _rev_key(prior.get("recurrence"), {"y": True, "n": False, "u": None}) or "u")
    if rec == "q":
        return None
    adj["recurrence"] = rec
    prior_cites = ", ".join(prior.get("evidence_citations") or [])
    cites = _ask("evidence citations (comma-separated, REQUIRED)",
                 prior_cites or "HMAGLOFDB:" + ev["event_id"].rsplit(":", 1)[1])
    adj["evidence_citations"] = [c.strip() for c in cites.split(",") if c.strip()]
    adj["disagreement_notes"] = _ask("disagreement/uncertainty notes",
                                   prior.get("disagreement_notes") or "")
    return adj


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--intake", required=True)
    ap.add_argument("--workfile", required=True)
    ap.add_argument("--reviewer", required=True)
    ap.add_argument("--hmaglofdb-csv", default=None,
                    help="optional — show the full source row (all populated columns)")
    ap.add_argument("--review", action="store_true",
                    help="re-prompt records that already have a disposition "
                         "(for reviewing an automated-triage draft)")
    a = ap.parse_args()

    intake = json.loads(Path(a.intake).read_text())
    roster = {e["event_id"] for e in intake["events"]}
    wf = Path(a.workfile)
    if wf.exists():
        doc = json.loads(wf.read_text())
        print(f"resuming: {sum(1 for e in doc['events'] if e['adjudication'].get('disposition'))}/{len(doc['events'])} done")
    else:
        doc = intake
        doc["schema"] = "P5_EVENT_ADJUDICATION_V1"
        doc["adjudicated_by_cli"] = True

    for i, ev in enumerate(doc["events"]):
        if ev["adjudication"].get("disposition") and not a.review:
            continue
        if a.hmaglofdb_csv:
            ev["_full_row"] = load_source_row(a.hmaglofdb_csv, ev["event_id"].rsplit(":", 1)[1])
            show_event(i, len(doc["events"]),
                       {**ev, "source_fields": ev["_full_row"]})
        else:
            show_event(i, len(doc["events"]), ev)
        prior = ev["adjudication"]
        if prior.get("disposition"):
            print(f"  prior triage: {prior['disposition']} by {prior.get('reviewer_ids')}")
        adj = adjudicate_event(ev, a.reviewer, prior=prior)
        if adj is None:
            wf.write_text(json.dumps(doc, indent=2) + "\n")
            print(f"\nsaved progress -> {wf} (resume by rerunning)")
            return
        ev["adjudication"] = adj
        wf.write_text(json.dumps(doc, indent=2) + "\n")
        print(f"  -> {adj['disposition']} saved")

    problems = validate_record(doc, roster)
    wf.write_text(json.dumps(doc, indent=2) + "\n")
    if problems:
        print(f"\nVALIDATION FAILED ({len(problems)} problems):")
        for p in problems[:20]:
            print("  -", p)
        print("fix and rerun to re-validate")
    else:
        print(f"\nALL 45 ADJUDICATED + VALIDATED -> {wf}\nready for coordinator sealing")


if __name__ == "__main__":
    main()
