"""Derive the India Phase-0 feasibility gate from local ledgers only.

The report intentionally cannot authorize acquisition.  It separates
catalog rows, adjudicated independent episodes, mapped lakes, and verified
non-event controls, and keeps the gate pending while adjudication is
incomplete.  A future precision simulation must be supplied as a separate,
versioned input before an event-weather canary is considered.
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


def _independent_exact(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    eligible = []
    for record in records:
        if not isinstance(record, dict):
            continue
        adj = record.get("adjudication", {})
        episode = record.get("episode", {})
        identity = record.get("lake_identity", {})
        evidence = record.get("evidence", {})
        if not isinstance(adj, dict):
            adj = {}
        if not isinstance(episode, dict):
            episode = {}
        if not isinstance(identity, dict):
            identity = {}
        if not isinstance(evidence, dict):
            evidence = {}
        date = record.get("date", {})
        if not isinstance(date, dict):
            date = {}
        adjudication_refs = adj.get("evidence_citations", [])
        source_refs = evidence.get("references", [])
        has_identity = bool(identity.get("canonical_lake_id") or
                            identity.get("status") == "SOURCE_ID_PRESENT")
        has_evidence = bool(source_refs or adjudication_refs)
        mechanism_ok = (adj.get("mechanism_certainty") in MECHANISM_CERTAINTY
                        and bool(adj.get("mechanism")))
        if (adj.get("eligibility") == "ELIGIBLE"
                and adj.get("review_state") == "COMPLETED"
                and adj.get("reviewer_ids")
                and adj.get("location_confirmed") is True
                and mechanism_ok
                and has_evidence
                and has_identity
                and episode.get("candidate_episode_id")
                and episode.get("independence_status") == "INDEPENDENT"
                and date.get("precision") == "day"):
            eligible.append(record)
    return eligible


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


def _validate_input_shape(crosswalk: dict[str, Any], frame: dict[str, Any]) -> None:
    if crosswalk.get("schema") != "INDIA_EVENT_CROSSWALK_V0":
        raise ValueError("crosswalk has unexpected schema")
    if not isinstance(crosswalk.get("records"), list):
        raise ValueError("crosswalk records must be a list")
    if frame.get("schema") != "INDIA_LAKE_FRAME_V0":
        raise ValueError("lake frame has unexpected schema")
    if not isinstance(frame.get("records"), list):
        raise ValueError("lake frame records must be a list")


def _event_screen(unreviewed: int, independent_exact: int) -> str:
    if unreviewed:
        return "ADJUDICATION_INCOMPLETE"
    if independent_exact < 10:
        return "CLOSE_EVENT_WEATHER_ROUTE"
    if independent_exact < 20:
        return "DESCRIPTIVE_ONLY_CANDIDATE"
    return "SIMULATION_REQUIRED"


def _lake_screen(mapped_lakes: int, controls: int) -> str:
    if mapped_lakes < 150:
        return "INSUFFICIENT_LAKE_FRAME"
    if not controls:
        return "CONTROL_FRAME_NOT_ESTABLISHED"
    return "OBSERVATION_FRAME_REQUIRES_REVIEW"


def build_report(crosswalk_path: str | Path, lake_frame_path: str | Path,
                 adjudication_path: str | Path | None = None) -> dict[str, Any]:
    crosswalk_path, lake_frame_path = Path(crosswalk_path), Path(lake_frame_path)
    crosswalk = json.loads(crosswalk_path.read_text())
    frame = json.loads(lake_frame_path.read_text())
    _validate_input_shape(crosswalk, frame)
    if adjudication_path is not None:
        adjudication_path = Path(adjudication_path)
        adjudication = json.loads(adjudication_path.read_text())
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
    independent_exact = _independent_exact(records)
    controls = [r for r in lake_records
                if isinstance(r, dict)
                and isinstance(r.get("observation"), dict)
                and r["observation"].get("control_eligible") is True]
    event_screen = _event_screen(unreviewed, len(independent_exact))
    lake_screen = _lake_screen(len(lake_records), len(controls))
    report = {
        "schema": SCHEMA,
        "version": 0,
        "claim_scope": "research_only_no_operational_authorization",
        "authority": dict(AUTHORITY_FLAGS),
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "inputs": {"crosswalk_sha256": sha256_file(crosswalk_path),
                   "lake_frame_sha256": sha256_file(lake_frame_path)},
        "denominators": {
            "catalog_rows": len(records),
            "adjudicated_eligible_rows": len(eligible),
            "independent_exact_day_episodes": len(independent_exact),
            "mapped_lake_rows": len(lake_records),
            "verified_non_event_controls": len(controls),
            "observable_lake_years": frame.get("summary", {}).get("n_observable_lake_years", 0),
            "unreviewed_event_rows": unreviewed,
        },
        "gates": {
            "event_weather_screen": event_screen,
            "lake_year_screen": lake_screen,
            "precision_simulation": "NOT_RUN",
            "bulk_acquisition_authorized": False,
            "weather_download_authorized": False,
            "satellite_bulk_authorized": False,
            "seismic_waveform_authorized": False,
        },
        "next_gate": ("COMPLETE_EVENT_ADJUDICATION" if unreviewed else
                      "RUN_PREDECLARED_PRECISION_SIMULATION" if len(independent_exact) >= 20 else
                      "ESTABLISH_OBSERVATION_FRAME" if len(lake_records) >= 150 else
                      "RECONCILE_LAKE_INVENTORY"),
        "decision": "PHASE0_ONLY_NO_ACQUISITION",
        "notes": [
            "A catalog row is not an independent episode.",
            "A mapped or monitored lake is not a verified non-event control.",
            "Fixed count bands are workflow screens, not power guarantees.",
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
    for key in ("catalog_rows", "adjudicated_eligible_rows", "independent_exact_day_episodes",
                "mapped_lake_rows", "verified_non_event_controls", "unreviewed_event_rows"):
        if not isinstance(d.get(key), int) or d[key] < 0:
            problems.append(f"invalid denominator: {key}")
    if d.get("adjudicated_eligible_rows", 0) > d.get("catalog_rows", 0):
        problems.append("eligible rows exceed catalog rows")
    if d.get("independent_exact_day_episodes", 0) > d.get("adjudicated_eligible_rows", 0):
        problems.append("independent episodes exceed eligible rows")
    expected_event = _event_screen(
        d.get("unreviewed_event_rows", 0),
        d.get("independent_exact_day_episodes", 0),
    )
    expected_lake = _lake_screen(
        d.get("mapped_lake_rows", 0),
        d.get("verified_non_event_controls", 0),
    )
    if doc.get("gates", {}).get("event_weather_screen") != expected_event:
        problems.append("event screen does not match denominators")
    if doc.get("gates", {}).get("lake_year_screen") != expected_lake:
        problems.append("lake screen does not match denominators")
    gates = doc.get("gates", {})
    for key in ("bulk_acquisition_authorized", "weather_download_authorized",
                "satellite_bulk_authorized", "seismic_waveform_authorized"):
        if gates.get(key) is not False:
            problems.append(f"authority flag must remain false: {key}")
    if doc.get("decision") != "PHASE0_ONLY_NO_ACQUISITION":
        problems.append("unexpected decision state")
    expected_next = ("COMPLETE_EVENT_ADJUDICATION"
                     if d.get("unreviewed_event_rows", 0) else
                     "RUN_PREDECLARED_PRECISION_SIMULATION"
                     if d.get("independent_exact_day_episodes", 0) >= 20 else
                     "ESTABLISH_OBSERVATION_FRAME"
                     if d.get("mapped_lake_rows", 0) >= 150 else
                     "RECONCILE_LAKE_INVENTORY")
    if doc.get("next_gate") != expected_next:
        problems.append("next_gate does not match denominators")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crosswalk", required=True)
    parser.add_argument("--lake-frame", required=True)
    parser.add_argument("--adjudication")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    doc = build_report(args.crosswalk, args.lake_frame, args.adjudication)
    problems = validate_report(doc)
    if problems:
        raise SystemExit("feasibility report validation failed: " + "; ".join(problems))
    write_once_json(args.out, doc, indent=2)
    write_once_sidecar(args.out)
    print(json.dumps({"status": doc["decision"], "out": str(args.out),
                      "gates": doc["gates"], "next_gate": doc["next_gate"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
