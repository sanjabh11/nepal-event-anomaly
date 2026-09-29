"""Owner sign-off lane for the remaining India Phase-0 decisions.

Each verb writes a typed, digest-bound, write-once decision artifact to
the evidence root.  No acquisition is performed; a decision only records
what the owner authorized or deferred.

  observation-intake   INTAKE_PAYLOAD / METADATA_ONLY / DEFER / REJECT
                       per candidate inventory.
  admin-contract       RECORD or DEFER the administrative-evidence
                       contract that would be required before any row
                       may carry IN_COUNTRY/OUTSIDE territory.
  scope-decision       Record the PoC claim scope: REGION_AGNOSTIC_POC
                       (territory is a stratifier, not a denominator
                       gate) or INDIA_SCOPED (administrative contract
                       required before India-administered claims).
  linkage-worksheet    Export the ambiguous cross-epoch candidate pairs
                       as a CSV worksheet for human review.
  linkage-review       Seal the owner's disposition of the candidate
                       pairs: THRESHOLD policy, WORKSHEET ingest, or
                       CONTAINMENT (one-to-one edges approve via the
                       strict channel or a bounded nested-containment
                       channel admitting grown/shrunk candidates).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p5_safe_io import write_once_json, write_once_bytes, write_once_sidecar  # noqa: E402
import validate_india_source_intake as intake  # noqa: E402

E = Path("/Users/sanjayb/nepal-event-anomaly-evidence"
         "/india-phase0-source-intake")
CANDIDATES = E / "INDIA_OBSERVATION_SOURCE_CANDIDATES_V0.json"
LINKAGE = E / "INDIA_LAKE_EPOCH_CANDIDATE_LINKAGE_V0.json"
INTAKE_STATES = ("INTAKE_PAYLOAD", "METADATA_ONLY", "DEFER", "REJECT")
CONTRACT_STATES = ("RECORD_RULE", "DEFER", "DECLINE")
SCOPE_STATES = ("REGION_AGNOSTIC_POC", "INDIA_SCOPED")
LINKAGE_REVIEW_MODES = ("THRESHOLD", "WORKSHEET", "CONTAINMENT")
WORKSHEET_DECISIONS = ("CONFIRM_SAME_LAKE", "REJECT", "UNSURE")
WORKSHEET_FIELDS = ("from_epoch", "to_epoch", "from_feature_id",
                    "to_feature_id", "intersection_over_union",
                    "fraction_of_from_area", "fraction_of_to_area",
                    "overlap_pattern", "review_decision", "review_note")


def sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def observation_intake(candidate_id, choice, reviewer, rationale,
                       out_path) -> list[str]:
    doc = json.loads(CANDIDATES.read_text())
    ids = {c["id"] for c in doc.get("candidates", [])}
    if candidate_id not in ids:
        return [f"unknown candidate {candidate_id!r}; known: {sorted(ids)}"]
    record = {
        "schema": "INDIA_OBSERVATION_INTAKE_DECISION_V0",
        "version": 0,
        "claim_scope": "research_only_phase0_source_decision",
        "candidate_id": candidate_id,
        "decision": choice,
        "decided_by": reviewer,
        "decided_utc": _now(),
        "decision_rationale": rationale,
        "candidates_artifact": CANDIDATES.name,
        "candidates_sha256": sha(CANDIDATES),
        "authority": dict(intake.AUTHORITY_FLAGS),
    }
    write_once_json(out_path, record)
    write_once_sidecar(out_path)
    return []


def admin_contract(state, reviewer, rationale, out_path, rule=None):
    doc = {
        "schema": "INDIA_ADMIN_TERRITORY_CONTRACT_V0",
        "version": 0,
        "claim_scope": "research_only_phase0_territory_gate",
        "decision_state": state,
        "decided_by": reviewer,
        "decided_utc": _now(),
        "decision_rationale": rationale,
        "rule": rule,
        "effect": ("Until a RECORD_RULE contract exists that the "
                   "territory validator can verify byte-for-byte, no "
                   "lake or event row may carry IN_COUNTRY or OUTSIDE; "
                   "territory remains UNASSESSED/UNCERTAIN. This "
                   "document only records the owner's stance; it does "
                   "not itself satisfy the validator."),
        "authority": dict(intake.AUTHORITY_FLAGS),
    }
    write_once_json(out_path, doc)
    write_once_sidecar(out_path)
    return []


def _load_linkage(linkage_path) -> tuple[dict, list[tuple[str, str, dict]]]:
    doc = json.loads(Path(linkage_path).read_text())
    pairs: list[tuple[str, str, dict]] = []
    for rel in doc.get("adjacent_epoch_relations", []):
        for p in rel.get("overlap_candidates", []):
            pairs.append((str(rel["from_epoch"]), str(rel["to_epoch"]), p))
    return doc, pairs


def scope_decision(state, reviewer, rationale, out_path) -> list[str]:
    doc = {
        "schema": "INDIA_POC_SCOPE_DECISION_V0",
        "version": 0,
        "claim_scope": "research_only_phase0_scope_decision",
        "decision_state": state,
        "decided_by": reviewer,
        "decided_utc": _now(),
        "decision_rationale": rationale,
        "effect": (
            "REGION_AGNOSTIC_POC: the clustering proof-of-concept proceeds "
            "on open-source lake outlines region-wide; administrative "
            "territory becomes a descriptive stratifier only, never a "
            "denominator gate. IN_COUNTRY/OUTSIDE labels remain forbidden "
            "in artifacts until a RECORD_RULE admin contract exists; rows "
            "stay UNASSESSED/UNCERTAIN. No artifact may claim an "
            "India-administered denominator under this scope. "
            "INDIA_SCOPED: the admin-evidence contract gate stays "
            "binding for any India-administered claim."),
        "authority": dict(intake.AUTHORITY_FLAGS),
    }
    write_once_json(out_path, doc)
    write_once_sidecar(out_path)
    return []


def linkage_worksheet(linkage_path, out_path, iou_below) -> list[str]:
    _, pairs = _load_linkage(linkage_path)
    rows = []
    for fr, to, p in pairs:
        if (p.get("overlap_pattern") != "ONE_TO_ONE_OVERLAP_CANDIDATE"
                or p.get("intersection_over_union", 0.0) < iou_below):
            rows.append({
                "from_epoch": fr, "to_epoch": to,
                "from_feature_id": p["from_feature_id"],
                "to_feature_id": p["to_feature_id"],
                "intersection_over_union": p.get("intersection_over_union"),
                "fraction_of_from_area": p.get("fraction_of_from_area"),
                "fraction_of_to_area": p.get("fraction_of_to_area"),
                "overlap_pattern": p.get("overlap_pattern"),
                "review_decision": "", "review_note": "",
            })
    rows.sort(key=lambda r: (r["from_epoch"], r["from_feature_id"]))
    text = io.StringIO()
    w = csv.DictWriter(text, fieldnames=list(WORKSHEET_FIELDS))
    w.writeheader()
    w.writerows(rows)
    write_once_bytes(Path(out_path), text.getvalue().encode("utf-8"))
    write_once_sidecar(out_path)
    return []


def _worksheet_rows(ws_path) -> tuple[list[dict], list[str]]:
    """Return (decided_rows, problems).  Blank review_decision rows are
    simply unreviewed — they count toward the remainder, not errors."""
    problems: list[str] = []
    rows: list[dict] = []
    skipped_blank = 0
    with open(ws_path, newline="", encoding="utf-8") as fh:
        for i, r in enumerate(csv.DictReader(fh), start=2):
            dec = (r.get("review_decision") or "").strip().upper()
            if not dec:
                skipped_blank += 1
                continue
            if dec not in WORKSHEET_DECISIONS:
                problems.append(
                    f"row {i}: review_decision must be one of "
                    f"{WORKSHEET_DECISIONS}, got {dec!r}")
                if len(problems) >= 20:
                    problems.append("... (further invalid rows omitted)")
                    return rows, problems
                continue
            rows.append(r)
    return rows, problems


def linkage_review(mode, reviewer, rationale, out_path, linkage_path,
                   iou_min=None, from_frac_min=None,
                   containment_min=None, area_ratio_max=None,
                   worksheet=None) -> list[str]:
    linkage = Path(linkage_path)
    if not linkage.is_file():
        return [f"linkage artifact missing: {linkage}"]
    doc, pairs = _load_linkage(linkage)
    verdicts: dict[str, int] = {}
    detail: dict[str, object] = {"mode": mode}
    problems: list[str] = []
    schema = "INDIA_LAKE_LINKAGE_REVIEW_V0"
    version = 0
    approved_refs: set[str] = set()
    if mode == "THRESHOLD":
        if iou_min is None or from_frac_min is None:
            return ["THRESHOLD requires --iou-min and --from-frac-min"]
        detail["iou_min"] = iou_min
        detail["from_frac_min"] = from_frac_min
        for _, _, p in pairs:
            ok = (p.get("overlap_pattern") == "ONE_TO_ONE_OVERLAP_CANDIDATE"
                  and p.get("intersection_over_union", 0.0) >= iou_min
                  and p.get("fraction_of_from_area", 0.0) >= from_frac_min)
            key = ("APPROVED_IDENTITY_CANDIDATE" if ok
                   else "UNCONFIRMED_CANDIDATE")
            verdicts[key] = verdicts.get(key, 0) + 1
            if ok:
                approved_refs.add(
                    f"{p['from_feature_id']}->{p['to_feature_id']}")
    elif mode == "CONTAINMENT":
        schema = "INDIA_LAKE_LINKAGE_REVIEW_V1"
        version = 1
        if (iou_min is None or from_frac_min is None
                or containment_min is None or area_ratio_max is None):
            return ["CONTAINMENT requires --iou-min, --from-frac-min, "
                    "--containment-min and --area-ratio-max"]
        detail.update({
            "iou_min": iou_min,
            "from_frac_min": from_frac_min,
            "containment_min": containment_min,
            "area_ratio_max": area_ratio_max,
            "rule_text": (
                "ONE_TO_ONE_OVERLAP_CANDIDATE edges approve via either "
                "channel: (a) strict threshold iou>=iou_min AND "
                "from_frac>=from_frac_min, or (b) containment "
                "max(from_frac,to_frac)>=containment_min AND implied "
                "area_ratio max(from/to, to/from)<=area_ratio_max where "
                "area_ratio derives from the shared intersection area "
                "(from_frac/to_frac). Containment admits grown or shrunk "
                "same-lake candidates the strict channel excludes; it "
                "does not confirm physical identity. Merge/split/"
                "many-to-many patterns stay unconfirmed regardless."),
        })
        via_strict = via_containment = 0
        for _, _, p in pairs:
            strict_ok = False
            contain_ok = False
            if p.get("overlap_pattern") == "ONE_TO_ONE_OVERLAP_CANDIDATE":
                iou = p.get("intersection_over_union", 0.0) or 0.0
                ff = p.get("fraction_of_from_area", 0.0) or 0.0
                tf = p.get("fraction_of_to_area", 0.0) or 0.0
                strict_ok = (iou >= iou_min and ff >= from_frac_min)
                if ff > 0.0 and tf > 0.0:
                    ratio = max(ff / tf, tf / ff)
                    contain_ok = (max(ff, tf) >= containment_min
                                  and ratio <= area_ratio_max)
            if strict_ok:
                via_strict += 1
            elif contain_ok:
                via_containment += 1
            key = ("APPROVED_IDENTITY_CANDIDATE" if strict_ok or contain_ok
                   else "UNCONFIRMED_CANDIDATE")
            verdicts[key] = verdicts.get(key, 0) + 1
            if strict_ok or contain_ok:
                approved_refs.add(
                    f"{p['from_feature_id']}->{p['to_feature_id']}")
        detail["approved_via_strict_threshold"] = via_strict
        detail["approved_via_containment"] = via_containment
        # Degree-1 safety: approved edges must never branch.  The linkage
        # generator enforces this for ONE_TO_ONE labels, but the review
        # re-verifies independently rather than trusting the label.
        seen_from: set[str] = set()
        seen_to: set[str] = set()
        for ref in approved_refs:
            left, right = ref.split("->", 1)
            if left in seen_from or right in seen_to:
                problems.append(
                    "approved candidate edge branches; degree-1 invariant "
                    f"violated at {ref}")
                break
            seen_from.add(left)
            seen_to.add(right)
        if problems:
            return problems
    else:
        if worksheet is None:
            return ["WORKSHEET requires --worksheet"]
        rows, problems = _worksheet_rows(Path(worksheet))
        if problems:
            return problems
        known = {(fr, to, p["from_feature_id"], p["to_feature_id"])
                 for fr, to, p in pairs}
        seen: set[tuple[str, str, str, str]] = set()
        for r in rows:
            key = (r["from_epoch"], r["to_epoch"],
                   r["from_feature_id"], r["to_feature_id"])
            if key not in known:
                problems.append(f"worksheet pair not in linkage: {key}")
            elif key in seen:
                problems.append(f"duplicate worksheet row: {key}")
            seen.add(key)
            verdicts[r["review_decision"].strip().upper()] = \
                verdicts.get(r["review_decision"].strip().upper(), 0) + 1
        if problems:
            return problems
        verdicts["UNREVIEWED_REMAINDER"] = len(pairs) - len(seen)
        detail["worksheet"] = str(worksheet)
        detail["worksheet_sha256"] = sha(worksheet)
    record = {
        "schema": schema,
        "version": version,
        "claim_scope": "research_only_phase0_candidate_linkage_review",
        "decided_by": reviewer,
        "decided_utc": _now(),
        "decision_rationale": rationale,
        "linkage_artifact": linkage.name,
        "linkage_sha256": sha(linkage),
        "total_pairs": len(pairs),
        "verdict_counts": verdicts,
        "detail": detail,
        "effect": ("Disposition labels candidate identity only; it does "
                   "not assert lake existence, territory, event validity, "
                   "or any operational claim. UNCONFIRMED_CANDIDATE pairs "
                   "remain usable only as candidates."),
        "authority": dict(intake.AUTHORITY_FLAGS),
    }
    write_once_json(out_path, record)
    write_once_sidecar(out_path)
    return []


def main() -> int:
    ap = argparse.ArgumentParser(prog="india_owner_decisions")
    sub = ap.add_subparsers(dest="cmd", required=True)
    o = sub.add_parser("observation-intake")
    o.add_argument("--candidate", required=True)
    o.add_argument("--choice", required=True, choices=INTAKE_STATES)
    o.add_argument("--by", required=True)
    o.add_argument("--rationale", required=True)
    o.add_argument("--out", required=True)
    c = sub.add_parser("admin-contract")
    c.add_argument("--state", required=True, choices=CONTRACT_STATES)
    c.add_argument("--by", required=True)
    c.add_argument("--rationale", required=True)
    c.add_argument("--rule", help="required for RECORD_RULE: the typed "
                   "contract text the validator must verify")
    c.add_argument("--out", required=True)
    s = sub.add_parser("scope-decision")
    s.add_argument("--state", required=True, choices=SCOPE_STATES)
    s.add_argument("--by", required=True)
    s.add_argument("--rationale", required=True)
    s.add_argument("--out", required=True)
    lw = sub.add_parser("linkage-worksheet")
    lw.add_argument("--linkage", default=str(LINKAGE))
    lw.add_argument("--iou-below", type=float, default=0.5,
                   help="export one-to-one pairs below this IoU plus all "
                        "merge/split/many-to-many patterns")
    lw.add_argument("--out", required=True)
    lr = sub.add_parser("linkage-review")
    lr.add_argument("--mode", required=True, choices=LINKAGE_REVIEW_MODES)
    lr.add_argument("--iou-min", type=float)
    lr.add_argument("--from-frac-min", type=float)
    lr.add_argument("--containment-min", type=float,
                    help="CONTAINMENT: approve one-to-one edges with "
                         "max(from_frac,to_frac) at or above this value")
    lr.add_argument("--area-ratio-max", type=float,
                    help="CONTAINMENT: cap on implied to/from area ratio "
                         "for the containment channel")
    lr.add_argument("--worksheet")
    lr.add_argument("--linkage", default=str(LINKAGE))
    lr.add_argument("--by", required=True)
    lr.add_argument("--rationale", required=True)
    lr.add_argument("--out", required=True)
    args = ap.parse_args()
    if args.cmd == "observation-intake":
        problems = observation_intake(args.candidate, args.choice,
                                      args.by, args.rationale, args.out)
    elif args.cmd == "admin-contract":
        problems = []
        if args.state == "RECORD_RULE" and not args.rule:
            problems.append("RECORD_RULE requires --rule")
        if not problems:
            problems = admin_contract(args.state, args.by,
                                      args.rationale, args.out,
                                      args.rule)
    elif args.cmd == "scope-decision":
        problems = scope_decision(args.state, args.by,
                                  args.rationale, args.out)
    elif args.cmd == "linkage-worksheet":
        problems = linkage_worksheet(args.linkage, args.out,
                                     args.iou_below)
    else:
        problems = linkage_review(args.mode, args.by, args.rationale,
                                  args.out, args.linkage,
                                  iou_min=args.iou_min,
                                  from_frac_min=args.from_frac_min,
                                  containment_min=args.containment_min,
                                  area_ratio_max=args.area_ratio_max,
                                  worksheet=args.worksheet)
    if problems:
        for p in problems:
            print("DECISION_FAIL:", p)
        return 1
    print("DECISION_SEALED:", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
