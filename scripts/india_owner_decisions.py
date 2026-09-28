"""Owner sign-off lane for the remaining India Phase-0 decisions.

Each verb writes a typed, digest-bound, write-once decision artifact to
the evidence root.  No acquisition is performed; a decision only records
what the owner authorized or deferred.

  observation-intake   INTAKE_PAYLOAD / METADATA_ONLY / DEFER / REJECT
                       per candidate inventory.
  admin-contract       RECORD or DEFER the administrative-evidence
                       contract that would be required before any row
                       may carry IN_COUNTRY/OUTSIDE territory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p5_safe_io import write_once_json, write_once_sidecar  # noqa: E402
import validate_india_source_intake as intake  # noqa: E402

E = Path("/Users/sanjayb/nepal-event-anomaly-evidence"
         "/india-phase0-source-intake")
CANDIDATES = E / "INDIA_OBSERVATION_SOURCE_CANDIDATES_V0.json"
INTAKE_STATES = ("INTAKE_PAYLOAD", "METADATA_ONLY", "DEFER", "REJECT")
CONTRACT_STATES = ("RECORD_RULE", "DEFER", "DECLINE")


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
    args = ap.parse_args()
    if args.cmd == "observation-intake":
        problems = observation_intake(args.candidate, args.choice,
                                      args.by, args.rationale, args.out)
    else:
        problems = []
        if args.state == "RECORD_RULE" and not args.rule:
            problems.append("RECORD_RULE requires --rule")
        if not problems:
            problems = admin_contract(args.state, args.by,
                                      args.rationale, args.out,
                                      args.rule)
    if problems:
        for p in problems:
            print("DECISION_FAIL:", p)
        return 1
    print("DECISION_SEALED:", args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
