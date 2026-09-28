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
REVIEWED_TERRITORY = {"IN_COUNTRY", "OUTSIDE", "UNCERTAIN"}


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
            "territory_status": "UNASSESSED",
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
        "geography": {"boundary_source": None, "boundary_version": None,
                      "crs": None},
        "rules": [
            "The crosswalk v0 bytes are never modified.",
            "Every source_record_id requires a disposition; no silent drops.",
            "Eligibility, mechanism, recurrence, cascade, and independence are reviewer fields.",
            "A catalog mechanism string alone is not mechanism adjudication.",
            "Territory is adjudicated as IN_COUNTRY/OUTSIDE/UNCERTAIN against a declared boundary source, version, and CRS — never from coordinates alone.",
            "reviewer_ids are attribution strings, not authenticated identities or cryptographic signoff.",
            "ELIGIBLE counting additionally requires at least one evidence:<id> citation resolving to a BYTES_VERIFIED register record covering the event date.",
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
    geography = doc.get("geography")
    if status == "REVIEWED":
        if not isinstance(geography, dict) or any(
                not isinstance(geography.get(field), str)
                or not geography[field].strip()
                for field in ("boundary_source", "boundary_version", "crs")):
            problems.append(
                "reviewed adjudication requires declared geography "
                "(boundary_source, boundary_version, crs)")
    elif isinstance(geography, dict) and any(
            geography.get(field) is not None
            for field in ("boundary_source", "boundary_version", "crs")):
        problems.append("pending adjudication cannot declare a boundary")
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
                    or adj.get("territory_status") != "UNASSESSED"
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
        if adj.get("territory_status") not in REVIEWED_TERRITORY:
            problems.append(f"{sid}: reviewed territory_status must be "
                            "IN_COUNTRY, OUTSIDE, or UNCERTAIN")
        if adj.get("territory_status") == "IN_COUNTRY":
            ev = adj.get("territory_evidence")
            ok = (isinstance(ev, dict)
                  and isinstance(ev.get("artifact"), str)
                  and isinstance(ev.get("artifact_sha256"), str)
                  and len(ev["artifact_sha256"]) == 64
                  and ev.get("decision_state") == "QUALIFIED"
                  and ev.get("binding") == "sha256")
            if not ok:
                problems.append(
                    f"{sid}: IN_COUNTRY territory requires a digest-bound "
                    "territory_evidence decision (artifact, sha256, "
                    "decision_state=QUALIFIED); a reviewer vote alone or a "
                    "source-relative spatial relation cannot establish "
                    "India-administered status")
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
            if adj.get("territory_status") != "IN_COUNTRY":
                problems.append(f"{sid}: eligible record requires territory IN_COUNTRY")
            if not episode.get("candidate_episode_id"):
                problems.append(f"{sid}: eligible record lacks candidate episode id")
            if not adj.get("mechanism") or adj.get("mechanism_certainty") not in {"CONFIRMED", "PROBABLE"}:
                problems.append(f"{sid}: eligible record lacks mechanism adjudication")
    # Rows sharing a candidate episode id describe the same episode; every
    # episode-level fact must agree — date interval, lake identity,
    # territory, mechanism, recurrence/cascade grouping, and location —
    # else the shared id is an EPISODE_CONFLICT and fails closed.
    by_episode: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        episode = record.get("episode")
        if not isinstance(episode, dict):
            continue
        episode_id = episode.get("candidate_episode_id")
        if isinstance(episode_id, str) and episode_id.strip():
            by_episode.setdefault(episode_id, []).append(record)

    # Episode facts like date, lake identity, and location live on the
    # crosswalk record; the adjudication row only carries decisions.
    crosswalk_by_id = {r.get("source_record_id"): r
                       for r in crosswalk_doc.get("records", [])
                       if isinstance(r, dict)}

    def _episode_facts(record: dict[str, Any]) -> dict[str, Any]:
        adj = record.get("adjudication") if isinstance(
            record.get("adjudication"), dict) else {}
        source = crosswalk_by_id.get(record.get("source_record_id"), {})
        date = source.get("date") if isinstance(
            source.get("date"), dict) else {}
        lake = source.get("lake_identity") if isinstance(
            source.get("lake_identity"), dict) else {}
        episode = record.get("episode") if isinstance(
            record.get("episode"), dict) else {}
        location = source.get("location") if isinstance(
            source.get("location"), dict) else {}
        return {
            "date_start": date.get("start"),
            "date_end": date.get("end"),
            "lake_raw_id": lake.get("raw_id") or None,
            "canonical_lake_id": lake.get("canonical_lake_id") or None,
            "territory_status": adj.get("territory_status"),
            "mechanism": adj.get("mechanism"),
            "recurrence_group_id": episode.get("recurrence_group_id") or None,
            "cascade_group_id": episode.get("cascade_group_id") or None,
            "independence_status": episode.get("independence_status"),
            "latitude": location.get("latitude"),
            "longitude": location.get("longitude"),
        }

    for episode_id, members in by_episode.items():
        facts = [_episode_facts(record) for record in members]
        for field in facts[0]:
            values = {f[field] for f in facts}
            if len(values) > 1:
                problems.append(
                    f"candidate_episode_id {episode_id} has conflicting "
                    f"{field} values {sorted(str(v) for v in values)} "
                    f"(EPISODE_CONFLICT)")
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
