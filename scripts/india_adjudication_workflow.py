"""Human-in-the-loop adjudication workflow for the India Phase-0 event
intake.

Three verbs, all fail-closed:

  worksheet  Emit a fill-in CSV joining each intake record to its
             crosswalk context (catalog country/lake/basin/date/mechanism
             and evidence references).  Review columns start blank;
             mechanical suggestions for OUTSIDE-classified rows are
             clearly marked `suggested_*` and carry no decision weight.

  ingest     Read a completed worksheet, build a REVIEWED adjudication
             successor document, validate it against the bound crosswalk
             with india_event_adjudication.validate_adjudication, and
             write-once seal it ONLY when validation is clean.

  verify     Revalidate an existing reviewed document.

Every row of the intake roster must carry a disposition: no silent
drops.  Territory stays UNASSESSED unless a separate administrative
evidence contract and owner review resolve to verified bytes.  A
source-relative spatial decision cannot establish IN_COUNTRY or OUTSIDE.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p5_safe_io import write_once_json, write_once_sidecar  # noqa: E402
import india_event_adjudication as ea  # noqa: E402

CONTEXT_COLS = [
    "ctx_country", "ctx_province", "ctx_lake_name", "ctx_glacier_name",
    "ctx_basin", "ctx_latitude", "ctx_longitude",
    "ctx_year", "ctx_date_start", "ctx_date_end", "ctx_date_precision",
    "ctx_date_basis", "ctx_mechanism", "ctx_driver_lake",
    "ctx_driver_glof", "ctx_repeat_raw", "ctx_candidate_class",
    "ctx_post_1979_candidate", "ctx_evidence_refs",
]
REVIEW_COLS = [
    "review_eligibility", "review_territory_status",
    "review_location_confirmed", "review_mechanism",
    "review_mechanism_certainty", "review_episode_id",
    "review_independence_status", "review_recurrence_group_id",
    "review_cascade_group_id", "review_evidence_citations",
    "review_disposition_reason", "review_reviewer_ids",
    "review_reviewed_utc", "territory_evidence_artifact",
    "territory_evidence_sha256",
]
SUGGEST_COLS = ["suggested_eligibility", "suggested_disposition_reason"]
ELIGIBILITY = {"ELIGIBLE", "INELIGIBLE", "UNCERTAIN"}
TERRITORY = {"IN_COUNTRY", "OUTSIDE", "UNCERTAIN", "UNASSESSED"}
CERTAINTY = {"CONFIRMED", "PROBABLE", "POSSIBLE", "UNKNOWN"}
INDEPENDENCE = {"INDEPENDENT", "NOT_INDEPENDENT"}


def _context(cw: dict) -> str:
    f = cw.get("catalog_fields", {})
    d = cw.get("date", {})
    ev = cw.get("evidence", {})
    return {
        "ctx_country": f.get("country"), "ctx_province": f.get("province"),
        "ctx_lake_name": f.get("lake_name"),
        "ctx_glacier_name": f.get("glacier_name"),
        "ctx_basin": f.get("river_basin"),
        "ctx_latitude": f.get("lat_lake"), "ctx_longitude": f.get("lon_lake"),
        "ctx_year": d.get("year"), "ctx_date_start": d.get("start"),
        "ctx_date_end": d.get("end"),
        "ctx_date_precision": d.get("precision"),
        "ctx_date_basis": d.get("basis"),
        "ctx_mechanism": f.get("mechanism"),
        "ctx_driver_lake": f.get("driver_lake"),
        "ctx_driver_glof": f.get("driver_glof"),
        "ctx_repeat_raw": f.get("repeat_raw"),
        "ctx_candidate_class": cw.get("candidate_class"),
        "ctx_post_1979_candidate": d.get("post_1979_candidate"),
        "ctx_evidence_refs": " | ".join(ev.get("references") or []),
    }


def worksheet(intake_path, crosswalk_path, out_csv) -> dict:
    intake = json.loads(Path(intake_path).read_text())
    cw = json.loads(Path(crosswalk_path).read_text())
    cw_map = {r["source_record_id"]: r for r in cw["records"]}
    rows = []
    for rec in intake["records"]:
        sid = rec["source_record_id"]
        src = cw_map.get(sid, {})
        row = {"source_record_id": sid}
        row.update(_context(src))
        cls = src.get("candidate_class")
        row["suggested_eligibility"] = (
            "INELIGIBLE" if cls == "OUTSIDE" else "")
        row["suggested_disposition_reason"] = (
            "outside target territory (catalog triage; reviewer must "
            "confirm)" if cls == "OUTSIDE" else "")
        for c in REVIEW_COLS:
            row[c] = ""
        rows.append(row)
    cols = (["source_record_id"] + CONTEXT_COLS + SUGGEST_COLS
            + REVIEW_COLS)
    with open(out_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    return {"rows": len(rows),
            "reviewable": sum(1 for r in rows
                              if r["ctx_candidate_class"] != "OUTSIDE")}


def _truthy(v: str):
    t = (v or "").strip().lower()
    if t in {"", "none", "null"}:
        return None
    if t in {"true", "yes", "1"}:
        return True
    if t in {"false", "no", "0"}:
        return False
    raise ValueError(f"boolean expected, got {v!r}")


def ingest(intake_path, crosswalk_path, worksheet_path, out_path,
           boundary_source, boundary_version, boundary_crs) -> list[str]:
    intake = json.loads(Path(intake_path).read_text())
    cw = json.loads(Path(crosswalk_path).read_text())
    cw_sha = ea.sha256_file(crosswalk_path)
    if intake.get("status") != "AWAITING_REVIEWER_ADJUDICATION":
        return ["intake is not AWAITING_REVIEWER_ADJUDICATION"]
    answers = {}
    with open(worksheet_path, newline="") as fh:
        for row in csv.DictReader(fh):
            answers[row["source_record_id"]] = row
    problems = []
    doc = json.loads(json.dumps(intake))
    doc["status"] = "REVIEWED"
    doc["geography"] = {"boundary_source": boundary_source,
                        "boundary_version": boundary_version,
                        "crs": boundary_crs}
    for rec in doc["records"]:
        sid = rec["source_record_id"]
        ans = answers.get(sid)
        if ans is None:
            problems.append(f"{sid}: no worksheet row")
            continue
        adj, ep = rec["adjudication"], rec["episode"]
        eligibility = (ans.get("review_eligibility") or "").strip().upper()
        territory = (ans.get("review_territory_status") or "").strip().upper()
        certainty = (ans.get("review_mechanism_certainty") or ""
                     ).strip().upper()
        independence = (ans.get("review_independence_status") or ""
                        ).strip().upper()
        if eligibility not in ELIGIBILITY:
            problems.append(f"{sid}: eligibility must be one of "
                            f"{sorted(ELIGIBILITY)}")
        if territory not in TERRITORY:
            problems.append(f"{sid}: territory must be one of "
                            f"{sorted(TERRITORY)}")
        if certainty not in CERTAINTY:
            problems.append(f"{sid}: mechanism_certainty must be one of "
                            f"{sorted(CERTAINTY)}")
        if independence not in INDEPENDENCE:
            problems.append(f"{sid}: independence must be one of "
                            f"{sorted(INDEPENDENCE)}")
        try:
            loc_confirmed = _truthy(ans.get("review_location_confirmed"))
        except ValueError as exc:
            problems.append(f"{sid}: {exc}")
            loc_confirmed = None
        reviewers = [v.strip() for v in
                     (ans.get("review_reviewer_ids") or "").split(";")
                     if v.strip()]
        reviewed_utc = (ans.get("review_reviewed_utc") or "").strip() or None
        citations = [v.strip() for v in
                     (ans.get("review_evidence_citations") or "").split(";")
                     if v.strip()]
        adj.update({
            "eligibility": eligibility or "UNREVIEWED",
            "review_state": "COMPLETED" if reviewers else
                            "AWAITING_ADJUDICATION",
            "reviewer_ids": reviewers,
            "reviewed_utc": reviewed_utc,
            "location_confirmed": loc_confirmed,
            "territory_status": territory or "UNASSESSED",
            "mechanism": (ans.get("review_mechanism") or "").strip() or None,
            "mechanism_certainty": certainty or None,
            "evidence_citations": citations,
            "disposition_reason": (ans.get("review_disposition_reason")
                                   or "").strip() or None,
        })
        ep.update({
            "candidate_episode_id":
                (ans.get("review_episode_id") or "").strip() or None,
            "independence_status": independence or "UNASSESSED",
            "recurrence_group_id":
                (ans.get("review_recurrence_group_id") or "").strip()
                or None,
            "cascade_group_id":
                (ans.get("review_cascade_group_id") or "").strip() or None,
        })
        te_art = (ans.get("territory_evidence_artifact") or "").strip()
        te_sha = (ans.get("territory_evidence_sha256") or "").strip()
        if te_art or te_sha:
            adj["territory_evidence"] = {
                "artifact": te_art or None,
                "artifact_sha256": te_sha or None,
                "decision_state": "QUALIFIED",
                "binding": "sha256"}
    if problems:
        return problems
    problems = ea.validate_adjudication(
        doc, cw, cw_sha, territory_evidence_dir=Path(crosswalk_path).parent)
    if problems:
        return problems
    write_once_json(out_path, doc)
    write_once_sidecar(out_path)
    return []


def main() -> int:
    ap = argparse.ArgumentParser(prog="india_adjudication_workflow")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("worksheet")
    w.add_argument("--intake", required=True)
    w.add_argument("--crosswalk", required=True)
    w.add_argument("--out", required=True)
    i = sub.add_parser("ingest")
    i.add_argument("--intake", required=True)
    i.add_argument("--crosswalk", required=True)
    i.add_argument("--worksheet", required=True)
    i.add_argument("--out", required=True)
    i.add_argument("--boundary-source", required=True)
    i.add_argument("--boundary-version", required=True)
    i.add_argument("--boundary-crs", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--doc", required=True)
    v.add_argument("--crosswalk", required=True)
    args = ap.parse_args()
    if args.cmd == "worksheet":
        info = worksheet(args.intake, args.crosswalk, args.out)
        print(json.dumps({"status": "WORKSHEET_WRITTEN", **info}, indent=2))
        return 0
    if args.cmd == "ingest":
        problems = ingest(args.intake, args.crosswalk, args.worksheet,
                          args.out, args.boundary_source,
                          args.boundary_version, args.boundary_crs)
        if problems:
            for p in problems:
                print("ADJUDICATION_FAIL:", p)
            return 1
        print("ADJUDICATION_SEALED:", args.out)
        return 0
    doc = json.loads(Path(args.doc).read_text())
    cw = json.loads(Path(args.crosswalk).read_text())
    problems = ea.validate_adjudication(
        doc, cw, ea.sha256_file(args.crosswalk),
        territory_evidence_dir=Path(args.crosswalk).parent)
    if problems:
        for p in problems:
            print("ADJUDICATION_FAIL:", p)
        return 1
    print("ADJUDICATION_VERIFY_OK:", args.doc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
