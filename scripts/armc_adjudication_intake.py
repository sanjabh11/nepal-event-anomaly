"""Arm C event-adjudication record machinery (v16+ readiness phase).

Codex phase-3 deliverable: a VERSIONED adjudication schema, a validator,
and a pre-filled intake record.  Human reviewers fill `adjudication`
fields per event; the validator enforces the schema and the append-only
rule (v0 records are never modified — a v1 adjudication record
supersedes while preserving the v0 digest chain).

Dispositions: ELIGIBLE | INELIGIBLE | UNCERTAIN — every candidate gets
one; no silent drops.
"""
import json, sys, hashlib, argparse, datetime
from pathlib import Path
import pandas as pd
sys.path.insert(0, "scripts")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from p5_safe_io import write_once_json, write_once_sidecar

ADJUDICATION_SCHEMA = {
    "schema": "P5_EVENT_ADJUDICATION_V1",
    "version": 1,
    "required_fields_per_event": {
        "event_id": "str — must match an EventLabelV0 id",
        "disposition": "ELIGIBLE | INELIGIBLE | UNCERTAIN",
        "reviewer_ids": "non-empty list of reviewer identifiers",
        "reviewed_utc": "ISO timestamp",
        "event_time_interval": {"start": "ISO or null", "end": "ISO or null",
                                "precision": "day|month|year|interval|unknown"},
        "location_confirmed": "bool — lake identity/location verified against source",
        "dam_lake_type": "str or null — from source Lake_type/Driver_lake, NOT inferred",
        "mechanism": "str or null — only if source evidence supports it",
        "mechanism_certainty": "confirmed|probable|possible|unknown",
        "cascade_membership": "str — cascade_group_id or ''",
        "recurrence": "bool — true if part of a repeat-event series",
        "evidence_citations": "non-empty list — source refs backing the disposition",
        "disagreement_notes": "str — preserve reviewer disagreements",
    },
    "rules": [
        "v0 EventLabelV0 records are never modified; adjudication is a new record",
        "UNCERTAIN is a terminal disposition, not a failure",
        "Nepal vs Tibetan-headwater records are adjudicated in separate strata",
        "no record may be silently dropped; every candidate requires a disposition",
        "source evidence (Ref_scientific/Sat_evidence) must be cited per disposition",
        "ELIGIBLE requires location_confirmed=true and day or bounded-interval timing",
        "a reviewer-supplied interval must fully cover the v0 label's stated interval",
    ],
}

def _sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def build_intake(pkg_path, recon_path) -> dict:
    pkg = json.loads(Path(pkg_path).read_text())
    recon = {r["event_id"]: r for r in json.loads(Path(recon_path).read_text())["records"]}
    events = []
    for e in pkg["event_labels"]:
        r = recon.get(e["event_id"], {})
        src = r.get("source", {})
        events.append({
            "event_id": e["event_id"],
            "local": {"basin_group": e["basin_group"],
                      "precision": e["event_time_precision"],
                      "time_start": e["event_time_start"], "time_end": e["event_time_end"],
                      "lat": e["latitude"], "lon": e["longitude"],
                      "cascade_group_id": e.get("cascade_group_id", ""),
                      "adjudication_state_v0": e["adjudication_state"]},
            "source_fields": {"Lake_name": src.get("Lake_name"), "River_Basin": src.get("River_Basin"),
                              "Country": src.get("Country"), "Transboundary": src.get("Transboundary"),
                              "Year_exact": src.get("Year_exact"), "Month": src.get("Month"),
                              "Day": src.get("Day"), "Driver_lake": src.get("Driver_lake"),
                              "Driver_GLOF": src.get("Driver_GLOF"), "Mechanism": src.get("Mechanism"),
                              "Repeat": src.get("Repeat")},
            "adjudication": {  # reviewer fills these — empty by construction
                "disposition": None, "reviewer_ids": [], "reviewed_utc": None,
                "event_time_interval": {"start": None, "end": None, "precision": None},
                "location_confirmed": None, "dam_lake_type": None, "mechanism": None,
                "mechanism_certainty": None, "cascade_membership": None,
                "recurrence": None, "evidence_citations": [],
                "disagreement_notes": ""},
        })
    return {"schema": "P5_EVENT_ADJUDICATION_INTAKE_V0",
            "claim_scope": "research_only_no_operational_authorization",
            "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "adjudication_schema": ADJUDICATION_SCHEMA,
            "package_sha256": _sha(pkg_path),
            "reconciliation_sha256": _sha(recon_path),
            "n_events": len(events),
            "status": "AWAITING_REVIEWER_ADJUDICATION",
            "events": events}

def _parse_iso(s):
    """Return pandas Timestamp or None."""
    if not isinstance(s, str) or not s:
        return None
    try:
        return pd.Timestamp(s)
    except Exception:
        return None

_PREC = {"day", "month", "year", "interval", "unknown"}

def validate_record(doc: dict, expected_ids: set | None = None) -> list[str]:
    """Hardened validator: roster completeness + per-event schema + interval coverage.
    expected_ids: authoritative roster of event_ids that must ALL be present."""
    problems = []
    events = doc.get("events", [])
    seen = [ev.get("event_id") for ev in events]
    dup = {i for i in seen if seen.count(i) > 1}
    if dup:
        problems.append(f"duplicate event_ids: {sorted(dup)}")
    if expected_ids is not None:
        missing = expected_ids - set(seen)
        extra = set(seen) - expected_ids
        if missing:
            problems.append(f"missing roster ids: {sorted(missing)}")
        if extra:
            problems.append(f"extra ids not in roster: {sorted(extra)}")
    local_by_id = {ev.get("event_id"): ev.get("local", {}) for ev in events}
    for ev in events:
        adj = ev.get("adjudication", {})
        eid = ev.get("event_id", "?")
        disp = adj.get("disposition")
        if disp not in ("ELIGIBLE", "INELIGIBLE", "UNCERTAIN"):
            problems.append(f"{eid}: disposition missing/invalid")
        rev = adj.get("reviewer_ids") or []
        if not rev:
            problems.append(f"{eid}: no reviewer_ids")
        elif len(set(rev)) != len(rev):
            problems.append(f"{eid}: duplicate reviewer_ids")
        if _parse_iso(adj.get("reviewed_utc")) is None:
            problems.append(f"{eid}: reviewed_utc missing/malformed")
        iv = adj.get("event_time_interval") or {}
        ts, te = _parse_iso(iv.get("start")), _parse_iso(iv.get("end"))
        if iv.get("precision") not in _PREC:
            problems.append(f"{eid}: interval precision missing/invalid")
        if (iv.get("start") is None) != (iv.get("end") is None):
            problems.append(f"{eid}: interval start/end asymmetric")
        if ts is not None and te is not None and ts > te:
            problems.append(f"{eid}: interval start after end")
        # reviewer interval must cover the v0 label interval when given
        loc = local_by_id.get(eid, {})
        v0s, v0e = _parse_iso(loc.get("time_start")), _parse_iso(loc.get("time_end"))
        if ts is not None and te is not None and v0s is not None and v0e is not None:
            if ts > v0s or te < v0e:
                problems.append(f"{eid}: adjudicated interval does not cover v0 label interval")
        if adj.get("mechanism") and adj.get("mechanism_certainty") not in (
                "confirmed", "probable", "possible", "unknown"):
            problems.append(f"{eid}: mechanism without certainty class")
        if not adj.get("evidence_citations"):
            problems.append(f"{eid}: no evidence_citations")
        if disp == "ELIGIBLE":
            if adj.get("location_confirmed") is not True:
                problems.append(f"{eid}: ELIGIBLE without location_confirmed=true")
            if iv.get("precision") not in ("day", "interval"):
                problems.append(f"{eid}: ELIGIBLE without day/bounded-interval precision")
            if iv.get("precision") == "interval" and (ts is None or te is None):
                problems.append(f"{eid}: ELIGIBLE interval-precision lacks bounds")
    return problems

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--package", required=True)
    ap.add_argument("--reconciliation", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    doc = build_intake(a.package, a.reconciliation)
    write_once_json(a.out, doc, indent=2)
    write_once_sidecar(a.out)
    print(json.dumps({"status": doc["status"], "events": doc["n_events"],
                      "out": a.out}, indent=2))

if __name__ == "__main__":
    main()
