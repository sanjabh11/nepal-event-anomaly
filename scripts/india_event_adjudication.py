"""Create and validate the append-only India event-adjudication intake.

The v0 crosswalk is an immutable catalog snapshot.  Reviewers must not edit
it in place to add eligibility or episode identity.  This module creates a
SHA-bound intake successor and validates a later reviewed successor before it
can be merged in-memory for a feasibility report.  It performs no network
activity and authorizes no acquisition.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from p5_safe_io import write_once_json, write_once_sidecar  # noqa: E402
import india_event_crosswalk as crosswalk  # noqa: E402


SCHEMA = "INDIA_EVENT_ADJUDICATION_V0"
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
MECHANISM_CERTAINTY = {"CONFIRMED", "PROBABLE", "POSSIBLE", "UNKNOWN"}
INDEPENDENCE = {"INDEPENDENT", "NOT_INDEPENDENT", "UNASSESSED"}


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _blank_record(source_record_id: str) -> dict[str, Any]:
    return {
        "source_record_id": source_record_id,
        "episode": {
            "candidate_episode_id": None,
            "recurrence_group_id": None,
            "cascade_group_id": None,
            "independence_status": "UNASSESSED",
        },
        "adjudication": {
            "eligibility": "UNREVIEWED",
            "review_state": "AWAITING_ADJUDICATION",
            "reviewer_ids": [],
            "reviewed_utc": None,
            "location_confirmed": None,
            "mechanism": None,
            "mechanism_certainty": None,
            "evidence_citations": [],
            "disposition_reason": None,
        },
    }


def build_intake(crosswalk_path: str | Path) -> dict[str, Any]:
    path = Path(crosswalk_path)
    source = json.loads(path.read_text())
    problems = crosswalk.validate_crosswalk(source)
    if problems:
        raise ValueError("crosswalk is not a clean v0 snapshot: " + "; ".join(problems))
    records = [_blank_record(record["source_record_id"])
               for record in source["records"]]
    return {
        "schema": SCHEMA,
        "version": 0,
        "claim_scope": "research_only_no_operational_authorization",
        "authority": dict(AUTHORITY_FLAGS),
        "status": "AWAITING_REVIEWER_ADJUDICATION",
        "crosswalk_sha256": sha256_file(path),
        "n_records": len(records),
        "rules": [
            "The crosswalk v0 bytes are never modified.",
            "Every source_record_id requires a disposition; no silent drops.",
            "Eligibility, mechanism, recurrence, cascade, and independence are reviewer fields.",
            "A catalog mechanism string alone is not mechanism adjudication.",
            "This record authorizes no weather, satellite, or seismic acquisition.",
        ],
        "records": records,
    }


def _parse_utc(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def validate_adjudication(doc: dict[str, Any], crosswalk_doc: dict[str, Any],
                          crosswalk_sha256: str) -> list[str]:
    problems: list[str] = []
    if doc.get("schema") != SCHEMA:
        problems.append("unexpected adjudication schema")
    if doc.get("claim_scope") != "research_only_no_operational_authorization":
        problems.append("unexpected claim scope")
    if doc.get("authority") != AUTHORITY_FLAGS:
        problems.append("authority flags must all be present and false")
    if doc.get("crosswalk_sha256") != crosswalk_sha256:
        problems.append("crosswalk digest does not match adjudication target")
    source_ids = [r.get("source_record_id") for r in crosswalk_doc.get("records", [])
                  if isinstance(r, dict)]
    records = doc.get("records")
    if not isinstance(records, list):
        return problems + ["records must be a list"]
    ids = [r.get("source_record_id") if isinstance(r, dict) else None for r in records]
    valid_ids = [value for value in ids if isinstance(value, str)]
    if len(valid_ids) != len(set(valid_ids)):
        problems.append("duplicate adjudication source_record_id")
    if any(not isinstance(value, str) or not value for value in ids):
        problems.append("adjudication source_record_id must be a non-empty string")
    if set(valid_ids) != set(source_ids):
        problems.append("adjudication roster does not exactly match crosswalk")
    status = doc.get("status")
    if status not in {"AWAITING_REVIEWER_ADJUDICATION", "REVIEWED"}:
        problems.append("invalid adjudication status")
    for record in records:
        if not isinstance(record, dict):
            problems.append("adjudication record must be an object")
            continue
        sid = record.get("source_record_id", "?")
        episode = record.get("episode")
        adj = record.get("adjudication")
        if not isinstance(episode, dict) or not isinstance(adj, dict):
            problems.append(f"{sid}: episode and adjudication must be objects")
            continue
        if status == "AWAITING_REVIEWER_ADJUDICATION":
            blank_defaults = (
                adj.get("reviewer_ids") == []
                and adj.get("reviewed_utc") is None
                and adj.get("location_confirmed") is None
                and adj.get("mechanism") is None
                and adj.get("mechanism_certainty") is None
                and adj.get("evidence_citations") == []
            )
            if (adj.get("eligibility") != "UNREVIEWED"
                    or adj.get("review_state") != "AWAITING_ADJUDICATION"
                    or not blank_defaults):
                problems.append(f"{sid}: pending intake contains a decision")
            continue
        if adj.get("eligibility") not in TERMINAL_ELIGIBILITY:
            problems.append(f"{sid}: invalid eligibility")
        if adj.get("review_state") != "COMPLETED":
            problems.append(f"{sid}: reviewed record is not completed")
        reviewer_ids = adj.get("reviewer_ids")
        if not isinstance(reviewer_ids, list) or not reviewer_ids or any(
                not isinstance(v, str) or not v.strip() for v in reviewer_ids):
            problems.append(f"{sid}: reviewer_ids required")
        elif len(reviewer_ids) != len(set(reviewer_ids)):
            problems.append(f"{sid}: duplicate reviewer_ids")
        if not _parse_utc(adj.get("reviewed_utc")):
            problems.append(f"{sid}: reviewed_utc must be timezone-aware ISO")
        if not isinstance(adj.get("location_confirmed"), bool):
            problems.append(f"{sid}: location_confirmed must be boolean")
        if adj.get("mechanism_certainty") not in MECHANISM_CERTAINTY:
            problems.append(f"{sid}: invalid mechanism_certainty")
        if (not isinstance(adj.get("evidence_citations"), list)
                or not adj.get("evidence_citations")
                or any(not isinstance(v, str) or not v.strip()
                       for v in adj.get("evidence_citations", []))):
            problems.append(f"{sid}: evidence_citations required")
        if episode.get("independence_status") not in {"INDEPENDENT", "NOT_INDEPENDENT"}:
            problems.append(f"{sid}: reviewed independence_status required")
        if adj.get("eligibility") == "ELIGIBLE":
            if not adj.get("location_confirmed"):
                problems.append(f"{sid}: eligible record lacks location confirmation")
            if not episode.get("candidate_episode_id"):
                problems.append(f"{sid}: eligible record lacks candidate episode id")
            if not adj.get("mechanism") or adj.get("mechanism_certainty") not in {"CONFIRMED", "PROBABLE"}:
                problems.append(f"{sid}: eligible record lacks mechanism adjudication")
    return problems


def apply_adjudication(crosswalk_doc: dict[str, Any], adjudication_doc: dict[str, Any],
                       crosswalk_sha256: str) -> list[dict[str, Any]]:
    problems = validate_adjudication(adjudication_doc, crosswalk_doc, crosswalk_sha256)
    if problems:
        raise ValueError("adjudication validation failed: " + "; ".join(problems))
    if adjudication_doc["status"] == "AWAITING_REVIEWER_ADJUDICATION":
        return list(crosswalk_doc["records"])
    decisions = {r["source_record_id"]: r for r in adjudication_doc["records"]}
    merged: list[dict[str, Any]] = []
    for source in crosswalk_doc["records"]:
        record = dict(source)
        decision = decisions[source["source_record_id"]]
        record["episode"] = dict(decision["episode"])
        record["adjudication"] = dict(decision["adjudication"])
        merged.append(record)
    return merged


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crosswalk", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    doc = build_intake(args.crosswalk)
    write_once_json(args.out, doc, indent=2)
    write_once_sidecar(args.out)
    print(json.dumps({"status": doc["status"], "records": doc["n_records"],
                      "out": args.out}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
