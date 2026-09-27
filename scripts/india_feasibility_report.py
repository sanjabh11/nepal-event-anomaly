"""Derive the India Phase-0 feasibility gate from local ledgers only.

The report intentionally cannot authorize acquisition.  It separates
catalog rows, adjudicated independent episodes, canonical lakes, and
verified non-event controls, and keeps the gate pending while
adjudication, evidence verification, or identity reconciliation is
incomplete.  Every input must pass its own validator and carry a
matching ``.sha256`` sidecar; a citation is not evidence until it
resolves to a BYTES_VERIFIED record in the evidence register.  A future
precision simulation must be supplied as a separate, versioned input
before an event-weather canary is considered.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "scripts"))
from p5_safe_io import write_once_json, write_once_sidecar  # noqa: E402
import india_event_adjudication as event_adjudication  # noqa: E402
import india_event_crosswalk as event_crosswalk  # noqa: E402
import india_evidence_register as evidence_register  # noqa: E402
import india_lake_frame as lake_frame  # noqa: E402


SCHEMA = "INDIA_FEASIBILITY_REPORT_V0"
AUTHORITY_FLAGS = {
    "bulk_acquisition_authorized": False,
    "weather_download_authorized": False,
    "satellite_bulk_authorized": False,
    "seismic_waveform_authorized": False,
    "forecast_authorized": False,
    "warning_authorized": False,
    "detector_authorized": False,
    "odds_authorized": False,
    "causal_authorized": False,
    "operational_authorized": False,
}
TERMINAL_ELIGIBILITY = {"ELIGIBLE", "INELIGIBLE", "UNCERTAIN"}
MECHANISM_CERTAINTY = {"CONFIRMED", "PROBABLE"}


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _verify_sidecar(path: Path) -> None:
    """Fail closed unless ``<path>.sha256`` matches the live bytes."""
    sidecar = Path(str(path) + ".sha256")
    if not sidecar.is_file():
        raise ValueError(f"missing evidence sidecar: {sidecar}")
    token = sidecar.read_text(encoding="utf-8").split()
    if not token or token[0] != sha256_file(path):
        raise ValueError(f"sidecar digest mismatch: {sidecar}")


def _load_verified_json(path: str | Path, validator, label: str) -> dict:
    """Load a governed JSON input, verify its sidecar, run its validator."""
    path = Path(path)
    _verify_sidecar(path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    problems = validator(doc)
    if problems:
        raise ValueError(f"{label} validation failed: " + "; ".join(problems))
    return doc


def _independent_exact(
        records: list[dict[str, Any]],
        verified: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    eligible = []
    for record in records:
        if not isinstance(record, dict):
            continue
        adj = record.get("adjudication", {})
        episode = record.get("episode", {})
        identity = record.get("lake_identity", {})
        date = record.get("date", {})
        if not isinstance(adj, dict):
            adj = {}
        if not isinstance(episode, dict):
            episode = {}
        if not isinstance(identity, dict):
            identity = {}
        if not isinstance(date, dict):
            date = {}
        has_identity = bool(identity.get("canonical_lake_id") or
                            identity.get("status") == "SOURCE_ID_PRESENT")
        resolved = evidence_register.resolved_evidence_ids(
            adj.get("evidence_citations"), verified,
            date.get("start"), date.get("end"))
        mechanism_ok = (adj.get("mechanism_certainty") in MECHANISM_CERTAINTY
                        and bool(adj.get("mechanism")))
        if (adj.get("eligibility") == "ELIGIBLE"
                and adj.get("review_state") == "COMPLETED"
                and adj.get("reviewer_ids")
                and adj.get("location_confirmed") is True
                and adj.get("territory_status") == "IN_COUNTRY"
                and mechanism_ok
                and resolved
                and has_identity
                and episode.get("candidate_episode_id")
                and episode.get("independence_status") == "INDEPENDENT"
                and date.get("precision") == "day"):
            eligible.append(record)
    return eligible


def _evidence_verified_event(
        record: dict[str, Any],
        verified: dict[str, dict[str, Any]]) -> bool:
    adj = record.get("adjudication") if isinstance(record, dict) else None
    date = record.get("date") if isinstance(record, dict) else None
    if not isinstance(adj, dict) or not isinstance(date, dict):
        return False
    return bool(evidence_register.resolved_evidence_ids(
        adj.get("evidence_citations"), verified,
        date.get("start"), date.get("end")))


def _review_complete(record: dict[str, Any]) -> bool:
    if not isinstance(record, dict):
        return False
    adj = record.get("adjudication", {})
    if not isinstance(adj, dict):
        return False
    evidence = record.get("evidence", {})
    if not isinstance(evidence, dict):
        evidence = {}
    return bool(
        adj.get("review_state") == "COMPLETED"
        and adj.get("eligibility") in TERMINAL_ELIGIBILITY
        and adj.get("reviewer_ids")
        and (adj.get("evidence_citations") or evidence.get("references"))
    )


def _lake_denominators(
        lake_records: list[dict[str, Any]],
        verified: dict[str, dict[str, Any]]) -> dict[str, int]:
    """Canonical-lake denominators: rows are source rows, not lakes."""
    canonical_ids: set[str] = set()
    unresolved = 0
    control_ids: set[str] = set()
    control_candidates = 0
    unverified_controls = 0
    for record in lake_records:
        if not isinstance(record, dict):
            continue
        lake = record.get("lake", {})
        if not isinstance(lake, dict):
            lake = {}
        canonical = lake.get("canonical_lake_id")
        if lake.get("identity_status") == "RECONCILED" and isinstance(
                canonical, str) and canonical.strip():
            canonical_ids.add(canonical)
        else:
            unresolved += 1
        obs = record.get("observation", {})
        if not isinstance(obs, dict) or obs.get("control_eligible") is not True:
            continue
        control_candidates += 1
        resolved = evidence_register.resolved_evidence_ids(
            obs.get("evidence_refs"), verified,
            obs.get("at_risk_start"), obs.get("at_risk_end"))
        if not resolved:
            unverified_controls += 1
            continue
        control_ids.add(canonical if isinstance(canonical, str)
                        and canonical.strip()
                        else f"UNRESOLVED:{record.get('source_record_id')}")
    return {
        "mapped_lake_rows": len(lake_records),
        "canonical_lakes": len(canonical_ids),
        "unresolved_lake_identities": unresolved,
        "control_candidates": control_candidates,
        "verified_non_event_controls": len(control_ids),
        "unverified_control_candidates": unverified_controls,
    }


def _event_screen(unreviewed: int, independent_exact: int) -> str:
    if unreviewed:
        return "ADJUDICATION_INCOMPLETE"
    if independent_exact < 10:
        return "CLOSE_EVENT_WEATHER_ROUTE"
    if independent_exact < 20:
        return "DESCRIPTIVE_ONLY_CANDIDATE"
    return "SIMULATION_REQUIRED"


def _lake_screen(unresolved: int, canonical_lakes: int,
                 verified_controls: int) -> str:
    if unresolved:
        return "IDENTITY_RECONCILE_REQUIRED"
    if canonical_lakes < 150:
        return "INSUFFICIENT_LAKE_FRAME"
    if not verified_controls:
        return "CONTROL_FRAME_NOT_ESTABLISHED"
    return "OBSERVATION_FRAME_REQUIRES_REVIEW"


def _evidence_screen(unverified_eligible: int,
                     unverified_controls: int) -> str:
    if unverified_eligible or unverified_controls:
        return "UNVERIFIED_EVIDENCE_PRESENT"
    return "EVIDENCE_BOUND"


def build_report(crosswalk_path: str | Path, lake_frame_path: str | Path,
                 register_path: str | Path,
                 adjudication_path: str | Path | None = None) -> dict[str, Any]:
    crosswalk_path = Path(crosswalk_path)
    lake_frame_path = Path(lake_frame_path)
    register_path = Path(register_path)
    register = _load_verified_json(
        register_path, evidence_register.validate_register, "register")
    verified = evidence_register.verified_records(register)
    crosswalk = _load_verified_json(
        crosswalk_path, event_crosswalk.validate_crosswalk, "crosswalk")
    frame = _load_verified_json(
        lake_frame_path, lake_frame.validate_frame, "lake frame")
    if adjudication_path is not None:
        adjudication_path = Path(adjudication_path)
        adjudication = _load_verified_json(
            adjudication_path,
            lambda doc: event_adjudication.validate_adjudication(
                doc, crosswalk, sha256_file(crosswalk_path)),
            "adjudication")
        records = event_adjudication.apply_adjudication(
            crosswalk, adjudication, sha256_file(crosswalk_path))
    else:
        records = crosswalk.get("records", [])
    lake_records = frame.get("records", [])

    unreviewed = sum(not _review_complete(r) for r in records)
    eligible = [r for r in records
                if isinstance(r, dict)
                and isinstance(r.get("adjudication"), dict)
                and r["adjudication"].get("eligibility") == "ELIGIBLE"
                and r["adjudication"].get("review_state") == "COMPLETED"]
    unverified_eligible = sum(
        not _evidence_verified_event(r, verified) for r in eligible)
    independent_records = _independent_exact(records, verified)
    episode_ids = sorted({
        r["episode"]["candidate_episode_id"] for r in independent_records})
    lake_denominators = _lake_denominators(lake_records, verified)

    event_screen = _event_screen(unreviewed, len(episode_ids))
    lake_screen = _lake_screen(
        lake_denominators["unresolved_lake_identities"],
        lake_denominators["canonical_lakes"],
        lake_denominators["verified_non_event_controls"])
    evidence_screen = _evidence_screen(
        unverified_eligible,
        lake_denominators["unverified_control_candidates"])
    report = {
        "schema": SCHEMA,
        "version": 0,
        "claim_scope": "research_only_no_operational_authorization",
        "authority": dict(AUTHORITY_FLAGS),
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "inputs": {
            "crosswalk_sha256": sha256_file(crosswalk_path),
            "lake_frame_sha256": sha256_file(lake_frame_path),
            "evidence_register_sha256": sha256_file(register_path),
            "sidecars_verified": True,
        },
        "denominators": {
            "catalog_rows": len(records),
            "adjudicated_eligible_rows": len(eligible),
            "eligible_unverified_evidence": unverified_eligible,
            "independent_exact_day_episodes": len(episode_ids),
            "mapped_lake_rows": lake_denominators["mapped_lake_rows"],
            "canonical_lakes": lake_denominators["canonical_lakes"],
            "unresolved_lake_identities":
                lake_denominators["unresolved_lake_identities"],
            "control_candidates": lake_denominators["control_candidates"],
            "verified_non_event_controls":
                lake_denominators["verified_non_event_controls"],
            "unverified_control_candidates":
                lake_denominators["unverified_control_candidates"],
            "observable_lake_years":
                frame.get("summary", {}).get("n_observable_lake_years", 0),
            "unreviewed_event_rows": unreviewed,
        },
        "independent_episode_ids": episode_ids,
        "gates": {
            "event_weather_screen": event_screen,
            "lake_year_screen": lake_screen,
            "evidence_screen": evidence_screen,
            "precision_simulation": "NOT_RUN",
            "bulk_acquisition_authorized": False,
            "weather_download_authorized": False,
            "satellite_bulk_authorized": False,
            "seismic_waveform_authorized": False,
        },
        "next_gate": ("COMPLETE_EVENT_ADJUDICATION" if unreviewed else
                      "VERIFY_EVENT_EVIDENCE" if unverified_eligible else
                      "RUN_PREDECLARED_PRECISION_SIMULATION"
                      if len(episode_ids) >= 20 else
                      "RECONCILE_LAKE_IDENTITIES"
                      if lake_denominators["unresolved_lake_identities"] else
                      "ESTABLISH_OBSERVATION_FRAME"
                      if lake_denominators["canonical_lakes"] >= 150 else
                      "RECONCILE_LAKE_INVENTORY"),
        "decision": "PHASE0_ONLY_NO_ACQUISITION",
        "notes": [
            "A catalog row is not an independent episode.",
            "A mapped or monitored lake is not a verified non-event control.",
            "Fixed count bands are workflow screens, not power guarantees.",
            "The 150-lake screen is a heuristic bound on frame size, never 150 positive events.",
            "A citation string is attribution until it resolves to a BYTES_VERIFIED register record with covering temporal coverage.",
            "Reviewer ids are attribution, not authenticated signoff.",
            "Rows sharing a candidate episode id count once in episode denominators.",
            "No weather, satellite, or seismic payload may be retrieved from this report.",
        ],
    }
    if adjudication_path is not None:
        report["inputs"]["adjudication_sha256"] = sha256_file(adjudication_path)
    return report


def validate_report(doc: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if doc.get("schema") != SCHEMA:
        problems.append("unexpected schema")
    if doc.get("claim_scope") != "research_only_no_operational_authorization":
        problems.append("unexpected claim scope")
    if doc.get("authority") != AUTHORITY_FLAGS:
        problems.append("authority flags must all be present and false")
    d = doc.get("denominators", {})
    for key in ("catalog_rows", "adjudicated_eligible_rows",
                "eligible_unverified_evidence",
                "independent_exact_day_episodes", "mapped_lake_rows",
                "canonical_lakes", "unresolved_lake_identities",
                "control_candidates", "verified_non_event_controls",
                "unverified_control_candidates", "observable_lake_years",
                "unreviewed_event_rows"):
        if not isinstance(d.get(key), int) or d[key] < 0:
            problems.append(f"invalid denominator: {key}")
    if d.get("adjudicated_eligible_rows", 0) > d.get("catalog_rows", 0):
        problems.append("eligible rows exceed catalog rows")
    if d.get("independent_exact_day_episodes", 0) > d.get(
            "adjudicated_eligible_rows", 0):
        problems.append("independent episodes exceed eligible rows")
    if d.get("eligible_unverified_evidence", 0) > d.get(
            "adjudicated_eligible_rows", 0):
        problems.append("unverified-eligible rows exceed eligible rows")
    if d.get("verified_non_event_controls", 0) > d.get(
            "control_candidates", 0):
        problems.append("verified controls exceed control candidates")
    if d.get("canonical_lakes", 0) > d.get("mapped_lake_rows", 0):
        problems.append("canonical lakes exceed mapped lake rows")
    ids = doc.get("independent_episode_ids")
    if not isinstance(ids, list) or len(ids) != len(set(ids)) or any(
            not isinstance(v, str) or not v for v in ids):
        problems.append("independent_episode_ids must be unique strings")
    elif len(ids) != d.get("independent_exact_day_episodes"):
        problems.append("episode id list does not match episode denominator")
    expected_event = _event_screen(
        d.get("unreviewed_event_rows", 0),
        d.get("independent_exact_day_episodes", 0),
    )
    expected_lake = _lake_screen(
        d.get("unresolved_lake_identities", 0),
        d.get("canonical_lakes", 0),
        d.get("verified_non_event_controls", 0),
    )
    expected_evidence = _evidence_screen(
        d.get("eligible_unverified_evidence", 0),
        d.get("unverified_control_candidates", 0),
    )
    gates = doc.get("gates", {})
    if gates.get("event_weather_screen") != expected_event:
        problems.append("event screen does not match denominators")
    if gates.get("lake_year_screen") != expected_lake:
        problems.append("lake screen does not match denominators")
    if gates.get("evidence_screen") != expected_evidence:
        problems.append("evidence screen does not match denominators")
    for key in ("bulk_acquisition_authorized", "weather_download_authorized",
                "satellite_bulk_authorized", "seismic_waveform_authorized"):
        if gates.get(key) is not False:
            problems.append(f"authority flag must remain false: {key}")
    if doc.get("decision") != "PHASE0_ONLY_NO_ACQUISITION":
        problems.append("unexpected decision state")
    expected_next = ("COMPLETE_EVENT_ADJUDICATION"
                     if d.get("unreviewed_event_rows", 0) else
                     "VERIFY_EVENT_EVIDENCE"
                     if d.get("eligible_unverified_evidence", 0) else
                     "RUN_PREDECLARED_PRECISION_SIMULATION"
                     if d.get("independent_exact_day_episodes", 0) >= 20 else
                     "RECONCILE_LAKE_IDENTITIES"
                     if d.get("unresolved_lake_identities", 0) else
                     "ESTABLISH_OBSERVATION_FRAME"
                     if d.get("canonical_lakes", 0) >= 150 else
                     "RECONCILE_LAKE_INVENTORY")
    if doc.get("next_gate") != expected_next:
        problems.append("next_gate does not match denominators")
    inputs = doc.get("inputs", {})
    if inputs.get("sidecars_verified") is not True:
        problems.append("inputs.sidecars_verified must be true")
    if not isinstance(inputs.get("evidence_register_sha256"), str):
        problems.append("inputs must bind evidence_register_sha256")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crosswalk", required=True)
    parser.add_argument("--lake-frame", required=True)
    parser.add_argument("--evidence-register", required=True)
    parser.add_argument("--adjudication")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    doc = build_report(args.crosswalk, args.lake_frame,
                       args.evidence_register, args.adjudication)
    problems = validate_report(doc)
    if problems:
        raise SystemExit("feasibility report validation failed: " + "; ".join(problems))
    write_once_json(args.out, doc, indent=2)
    write_once_sidecar(args.out)
    print(json.dumps({"status": doc["decision"], "out": str(args.out),
                      "gates": doc["gates"], "next_gate": doc["next_gate"]},
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
