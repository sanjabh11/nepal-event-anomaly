"""Build a conservative India lake observation frame from local inventory rows.

The input is deliberately source-neutral CSV or JSON.  This tool never
downloads inventory data and never treats a missing catalog event as a
verified non-event.  A lake becomes a control only when the input explicitly
supplies full observation completeness, an at-risk interval, and evidence
references.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "scripts"))
from p5_safe_io import write_once_json, write_once_sidecar  # noqa: E402


SCHEMA = "INDIA_LAKE_FRAME_V0"
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
OBSERVATION_STATUSES = {"UNKNOWN", "PARTIAL", "KNOWN_BREACH", "VERIFIED_NON_EVENT"}
COMPLETENESS = {"UNKNOWN", "PARTIAL", "FULL"}
TERRITORY_STATUSES = {"IN_COUNTRY", "OUTSIDE", "UNCERTAIN", "UNASSESSED"}
IDENTITY_STATUSES = {"UNRECONCILED", "RECONCILED", "UNRESOLVED_CONFLICT"}


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _float(value: Any, field: str) -> float:
    try:
        result = float(_text(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _refs(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    text = _text(value)
    if not text:
        return []
    return [part.strip() for part in text.split(";") if part.strip()]


def _first_text(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _text(row.get(key))
        if value:
            return value
    return ""


def _iso_date(value: Any, field: str, source_id: str) -> str | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = dt.date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{source_id}: {field} must be canonical YYYY-MM-DD") from exc
    if parsed.isoformat() != text:
        raise ValueError(f"{source_id}: {field} must be canonical YYYY-MM-DD")
    return text


def _observed_years(row: dict[str, Any], source_id: str) -> list[int]:
    raw = row.get("observed_years")
    if raw is None:
        raw = row.get("observable_years")
    if isinstance(raw, list):
        values = raw
    else:
        text = _text(raw)
        values = [] if not text else re.split(r"[;,]", text)
    years: list[int] = []
    for value in values:
        text = _text(value)
        if not text or not text.isdigit():
            raise ValueError(f"{source_id}: observed_years must contain integer years")
        year = int(text)
        if not 1 <= year <= 9999:
            raise ValueError(f"{source_id}: observed_years contains invalid year")
        years.append(year)
    if len(years) != len(set(years)):
        raise ValueError(f"{source_id}: observed_years contains duplicates")
    return sorted(years)


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".json":
        doc = json.loads(path.read_text())
        if isinstance(doc, list):
            return doc
        if isinstance(doc, dict) and isinstance(doc.get("records"), list):
            return doc["records"]
        raise ValueError("JSON inventory must be a list or contain records[]")
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _record(row: dict[str, Any], source_name: str, source_version: str) -> dict[str, Any]:
    source_id = _first_text(row, ("source_record_id", "lake_id", "id"))
    if not source_id:
        raise ValueError("lake inventory row is missing source_record_id/lake_id")
    lake_id = _first_text(row, ("lake_id", "id", "source_record_id"))
    lat = _float(_first_text(row, ("latitude", "lat")), "latitude")
    lon = _float(_first_text(row, ("longitude", "lon")), "longitude")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise ValueError(f"{source_id}: coordinates out of range")
    observation_status = _text(row.get("observation_status")).upper() or "UNKNOWN"
    completeness = _text(row.get("observation_completeness")).upper() or "UNKNOWN"
    if observation_status not in OBSERVATION_STATUSES:
        raise ValueError(f"{source_id}: invalid observation_status")
    if completeness not in COMPLETENESS:
        raise ValueError(f"{source_id}: invalid observation_completeness")
    territory_status = (_text(row.get("territory_status")).upper()
                        or "UNASSESSED")
    if territory_status not in TERRITORY_STATUSES:
        raise ValueError(f"{source_id}: invalid territory_status")
    refs = _refs(row.get("evidence_refs") or row.get("evidence"))
    at_risk_start = _iso_date(row.get("at_risk_start"), "at_risk_start", source_id)
    at_risk_end = _iso_date(row.get("at_risk_end"), "at_risk_end", source_id)
    if at_risk_start and at_risk_end and at_risk_start > at_risk_end:
        raise ValueError(f"{source_id}: at-risk interval starts after it ends")
    observed_years = _observed_years(row, source_id)
    # FULL completeness is a coverage claim, not a label: it requires an
    # explicit observed_years list, and when an at-risk interval is declared
    # that list must contain every year of the interval.  A monitored
    # portfolio presence is not a verified non-event history.
    if completeness == "FULL":
        if not observed_years:
            raise ValueError(
                f"{source_id}: FULL completeness lacks observed_years coverage")
        if at_risk_start and at_risk_end:
            required = set(range(int(at_risk_start[:4]),
                                 int(at_risk_end[:4]) + 1))
            if not required <= set(observed_years):
                raise ValueError(
                    f"{source_id}: observed_years do not cover the declared "
                    "at-risk interval")
    control_eligible = (observation_status == "VERIFIED_NON_EVENT"
                        and completeness == "FULL"
                        and bool(at_risk_start and at_risk_end and refs))
    if observation_status == "VERIFIED_NON_EVENT" and not control_eligible:
        raise ValueError(f"{source_id}: VERIFIED_NON_EVENT lacks full interval evidence")
    return {
        "source_record_id": source_id,
        "source": {"name": source_name, "version": source_version},
        "lake": {"source_lake_id": lake_id, "canonical_lake_id": None,
                 "identity_status": "UNRECONCILED",
                 "name": _text(row.get("lake_name"))},
        "location": {"latitude": lat, "longitude": lon,
                      "territory_status": territory_status,
                      "state": _text(row.get("state")),
                      "basin": _text(row.get("basin"))},
        "attributes": {"lake_type": _text(row.get("lake_type")),
                       "area_ha": _text(row.get("area_ha")),
                       "dam_type": _text(row.get("dam_type")),
                       "glacier_connected": _text(row.get("glacier_connected"))},
        "observation": {
            "status": observation_status,
            "completeness": completeness,
            "first_observed_year": _text(row.get("first_observed_year")) or None,
            "last_observed_year": _text(row.get("last_observed_year")) or None,
            "observed_years": observed_years,
            "at_risk_start": at_risk_start,
            "at_risk_end": at_risk_end,
            "evidence_refs": refs,
            "control_eligible": control_eligible,
        },
        "event_linkage": {"known_breach_ids": [], "linkage_status": "UNASSESSED"},
    }


def build_frame(inventory_path: str | Path, source_name: str,
                source_version: str) -> dict[str, Any]:
    path = Path(inventory_path)
    rows = _read_rows(path)
    records = [_record(row, source_name, source_version) for row in rows]
    source_ids = [r["source_record_id"] for r in records]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("duplicate source_record_id in lake inventory")
    controls = [r for r in records if r["observation"]["control_eligible"]]
    return {
        "schema": SCHEMA,
        "version": 0,
        "claim_scope": "research_only_no_operational_authorization",
        "authority": dict(AUTHORITY_FLAGS),
        "source": {"name": source_name, "version": source_version,
                   "path_label": path.name, "sha256": sha256_file(path)},
        "rules": [
            "Mapped lake is not an event and is not a control by default.",
            "UNRECONCILED lake identities cannot be merged across sources silently.",
            "UNKNOWN observation status is never promoted to VERIFIED_NON_EVENT.",
            "Control eligibility requires explicit full observation evidence and an at-risk interval.",
            "No forecast, warning, operational, or event-risk claim is authorized.",
        ],
        "summary": {
            "n_inventory_rows": len(records),
            "n_unique_source_lakes": len(set(source_ids)),
            "n_verified_non_event_controls": len(controls),
            "n_unknown_observation": sum(r["observation"]["status"] == "UNKNOWN" for r in records),
            "n_observation_full": sum(r["observation"]["completeness"] == "FULL" for r in records),
            "n_observable_lake_years": sum(
                len(r["observation"]["observed_years"])
                for r in records
                if r["observation"]["completeness"] == "FULL"
            ),
        },
        "records": records,
    }


def validate_frame(doc: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if doc.get("schema") != SCHEMA:
        problems.append("unexpected schema")
    if doc.get("claim_scope") != "research_only_no_operational_authorization":
        problems.append("unexpected claim scope")
    if doc.get("authority") != AUTHORITY_FLAGS:
        problems.append("authority flags must all be present and false")
    records = doc.get("records")
    if not isinstance(records, list):
        return ["records must be a list"]
    seen = set()
    controls = 0
    full_years = 0
    unknown = 0
    full = 0
    for record in records:
        if not isinstance(record, dict):
            problems.append("record must be an object")
            continue
        sid = record.get("source_record_id")
        if not isinstance(sid, str) or not sid:
            problems.append(f"duplicate/missing source_record_id: {sid}")
            continue
        if sid in seen:
            problems.append(f"duplicate/missing source_record_id: {sid}")
        seen.add(sid)
        obs = record.get("observation", {})
        if not isinstance(obs, dict):
            problems.append(f"{sid}: observation must be an object")
            continue
        if obs.get("status") not in OBSERVATION_STATUSES:
            problems.append(f"{sid}: invalid observation status")
        if obs.get("completeness") not in COMPLETENESS:
            problems.append(f"{sid}: invalid observation completeness")
        if obs.get("completeness") == "FULL":
            full += 1
            years = obs.get("observed_years")
            if (not isinstance(years, list) or not years or any(
                    not isinstance(year, int) or not 1 <= year <= 9999
                    for year in years)):
                problems.append(f"{sid}: FULL completeness lacks valid "
                                "observed_years coverage")
            elif len(years) != len(set(years)) or years != sorted(years):
                problems.append(f"{sid}: observed_years must be unique and sorted")
            else:
                start, end = obs.get("at_risk_start"), obs.get("at_risk_end")
                if start and end:
                    required = set(range(int(start[:4]), int(end[:4]) + 1))
                    if not required <= set(years):
                        problems.append(
                            f"{sid}: observed_years do not cover the declared "
                            "at-risk interval")
                full_years += len(years)
        if obs.get("status") == "UNKNOWN":
            unknown += 1
        location = record.get("location", {})
        if not isinstance(location, dict):
            problems.append(f"{sid}: location must be an object")
        elif location.get("territory_status") not in TERRITORY_STATUSES:
            problems.append(f"{sid}: invalid territory_status")
        lake = record.get("lake", {})
        if not isinstance(lake, dict):
            problems.append(f"{sid}: lake must be an object")
        else:
            identity_status = lake.get("identity_status")
            canonical = lake.get("canonical_lake_id")
            if identity_status not in IDENTITY_STATUSES:
                problems.append(f"{sid}: invalid lake identity_status")
            elif identity_status == "RECONCILED":
                if not isinstance(canonical, str) or not canonical.strip():
                    problems.append(
                        f"{sid}: RECONCILED identity requires a canonical "
                        "lake id")
            elif canonical is not None:
                problems.append(
                    f"{sid}: canonical lake identity requires RECONCILED "
                    "status")
        start, end = obs.get("at_risk_start"), obs.get("at_risk_end")
        for field, value in (("at_risk_start", start), ("at_risk_end", end)):
            if value is not None:
                try:
                    if _iso_date(value, field, sid) != value:
                        problems.append(f"{sid}: non-canonical {field}")
                except ValueError as exc:
                    problems.append(str(exc))
        if start and end and start > end:
            problems.append(f"{sid}: at-risk interval starts after it ends")
        eligible = obs.get("control_eligible") is True
        expected = (obs.get("status") == "VERIFIED_NON_EVENT"
                    and obs.get("completeness") == "FULL"
                    and bool(start and end and obs.get("evidence_refs")))
        if eligible != expected:
            problems.append(f"{sid}: control_eligible is inconsistent")
        if obs.get("status") == "VERIFIED_NON_EVENT" and not expected:
            problems.append(f"{sid}: VERIFIED_NON_EVENT lacks full interval evidence")
        if eligible:
            controls += 1
    summary = doc.get("summary", {})
    if summary.get("n_inventory_rows") != len(records):
        problems.append("summary n_inventory_rows mismatch")
    if summary.get("n_verified_non_event_controls") != controls:
        problems.append("summary control count mismatch")
    if summary.get("n_unique_source_lakes") != len(seen):
        problems.append("summary unique lake count mismatch")
    if summary.get("n_unknown_observation") != unknown:
        problems.append("summary unknown-observation count mismatch")
    if summary.get("n_observation_full") != full:
        problems.append("summary full-observation count mismatch")
    if summary.get("n_observable_lake_years") != full_years:
        problems.append("summary observable-lake-year count mismatch")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--source-name", required=True)
    parser.add_argument("--source-version", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    doc = build_frame(args.inventory, args.source_name, args.source_version)
    problems = validate_frame(doc)
    if problems:
        raise SystemExit("lake frame validation failed: " + "; ".join(problems))
    write_once_json(args.out, doc, indent=2)
    write_once_sidecar(args.out)
    print(json.dumps({"status": "PHASE0_LAKE_FRAME_READY",
                      "out": str(args.out), "summary": doc["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
