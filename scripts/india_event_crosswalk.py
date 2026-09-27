"""Build a fail-closed India GLOF event crosswalk from a pinned catalog.

This is Phase 0 machinery only.  It performs no network activity and does
not adjudicate events, merge lake aliases, infer independence, or promote a
catalog row to an analysis case.  Every India row is retained with an
``UNREVIEWED`` eligibility state until a separate human/source-evidence
adjudication step changes it.

Example::

    python3 scripts/india_event_crosswalk.py \
      --hmaglofdb-csv /path/HMAGLOFDB.csv \
      --source-version 1.3.0 \
      --out /tmp/india-event-crosswalk.json
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


SCHEMA = "INDIA_EVENT_CROSSWALK_V0"
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
PLACEHOLDER_LAKE_IDS = frozenset({"", "NA", "N/A", "NONE", "NO LAKE",
                                  "NOT MAPPED", "UNKNOWN", "EPHEMERAL"})
_INT_RE = re.compile(r"^[+-]?\d+(?:\.0+)?$")
# Territory is an adjudicated classification, never a coordinate guess:
# catalog country strings select rows; they do not classify territory.
TERRITORY_STATUSES = {"IN_COUNTRY", "OUTSIDE", "UNCERTAIN", "UNASSESSED"}
# Every catalog row is retained; candidate_class is a catalog-string
# triage for review ordering only — never a territory classification.
CANDIDATE_CLASSES = {"TARGET_COUNTRY", "TRANSBOUNDARY", "UNKNOWN_COUNTRY",
                     "OUTSIDE"}


def _candidate_class(country_text: Any, target: str) -> str:
    value = _text(country_text)
    if not value:
        return "UNKNOWN_COUNTRY"
    folded = value.casefold()
    if folded == target:
        return "TARGET_COUNTRY"
    # Mentions the target alongside other labels — kept for adjudication
    # rather than silently dropped (transboundary/borderline labels).
    if target and target in folded:
        return "TRANSBOUNDARY"
    return "OUTSIDE"


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _integer(value: Any) -> int | None:
    value = _text(value)
    if not value or not _INT_RE.fullmatch(value):
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _valid_date(year: int | None, month: int | None,
                day: int | None) -> bool:
    if year is None or not 1 <= year <= 9999 or month is None or day is None:
        return False
    try:
        dt.date(year, month, day)
    except ValueError:
        return False
    return True


def _date_info(row: dict[str, Any]) -> dict[str, Any]:
    year_exact = _integer(row.get("Year_exact"))
    month = _integer(row.get("Month"))
    day = _integer(row.get("Day"))
    year_approx = _integer(row.get("Year_approx"))

    if _valid_date(year_exact, month, day):
        value = dt.date(year_exact, month, day).isoformat()
        return {"precision": "day", "basis": "exact", "start": value,
                "end": value, "year": year_exact, "month": month,
                "day": day, "post_1979_candidate": year_exact >= 1980}

    if (year_exact is not None and 1 <= year_exact <= 9999
            and month is not None and 1 <= month <= 12):
        next_year = year_exact + 1 if month == 12 else year_exact
        next_month = 1 if month == 12 else month + 1
        last = dt.date(next_year, next_month, 1) - dt.timedelta(days=1)
        start = dt.date(year_exact, month, 1).isoformat()
        return {"precision": "month", "basis": "exact", "start": start,
                "end": last.isoformat(), "year": year_exact, "month": month,
                "day": None, "post_1979_candidate": year_exact >= 1980}

    if year_exact is not None and 1 <= year_exact <= 9999:
        return {"precision": "year", "basis": "exact", "start": f"{year_exact:04d}-01-01",
                "end": f"{year_exact:04d}-12-31", "year": year_exact,
                "month": None, "day": None, "post_1979_candidate": year_exact >= 1980}

    if year_approx is not None and 1 <= year_approx <= 9999:
        return {"precision": "year", "basis": "approximate", "start": None,
                "end": None, "year": year_approx, "month": None, "day": None,
                "post_1979_candidate": year_approx >= 1980}

    return {"precision": "unknown", "basis": "unknown", "start": None,
            "end": None, "year": None, "month": None, "day": None,
            "post_1979_candidate": False}


def _lake_identity(raw: Any) -> dict[str, Any]:
    value = _text(raw)
    status = "PLACEHOLDER" if value.upper() in PLACEHOLDER_LAKE_IDS else "SOURCE_ID_PRESENT"
    return {"raw_id": value, "status": status,
            "canonical_lake_id": None}


def _float_or_none(value: Any) -> float | None:
    text = _text(value)
    if not text:
        return None
    try:
        result = float(text)
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def _location(row: dict[str, Any]) -> dict[str, Any]:
    lat = _float_or_none(row.get("Lat_lake"))
    lon = _float_or_none(row.get("Lon_lake"))
    return {"latitude": lat, "longitude": lon,
            "territory_status": "UNASSESSED"}


def _evidence_refs(row: dict[str, Any]) -> list[str]:
    refs = []
    for key in ("Ref_scientific", "Ref_scientific_full", "Ref_other", "Sat_evidence"):
        value = _text(row.get(key))
        if value:
            refs.append(f"{key}:{value}")
    return refs


def _record(row: dict[str, Any], source_version: str, target: str) -> dict[str, Any]:
    gf_id = _text(row.get("GF_ID"))
    if not gf_id:
        raise ValueError("India catalog row is missing GF_ID")
    date = _date_info(row)
    return {
        "source_record_id": f"HMAGLOFDB:{gf_id}",
        "source": {"name": "HMAGLOFDB", "version": source_version,
                   "record_id": gf_id},
        "catalog_fields": {
            "lake_name": _text(row.get("Lake_name")),
            "glacier_name": _text(row.get("Glacier_name")),
            "country": _text(row.get("Country")),
            "province": _text(row.get("Province")),
            "river_basin": _text(row.get("River_Basin")),
            "lat_lake": _text(row.get("Lat_lake")),
            "lon_lake": _text(row.get("Lon_lake")),
            "driver_lake": _text(row.get("Driver_lake")),
            "driver_glof": _text(row.get("Driver_GLOF")),
            "mechanism": _text(row.get("Mechanism")),
            "repeat_raw": _text(row.get("Repeat")),
            "sat_evidence_raw": _text(row.get("Sat_evidence")),
        },
        "date": date,
        "location": _location(row),
        "candidate_class": _candidate_class(row.get("Country"), target),
        "lake_identity": _lake_identity(row.get("GL_ID")),
        "evidence": {"references": _evidence_refs(row)},
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
            "disposition_reason": None,
        },
    }


def build_crosswalk(csv_path: str | Path, source_version: str,
                    country: str = "India") -> dict[str, Any]:
    path = Path(csv_path)
    with path.open(encoding="cp1252", newline="") as handle:
        rows = list(csv.DictReader(handle))
    target = _text(country).casefold()
    # Every catalog row is retained — borderline country labels can never
    # silently remove a candidate.  OUTSIDE rows are reference-only.
    records = [_record(row, source_version, target) for row in rows]
    ids = [r["source_record_id"] for r in records]
    if len(ids) != len(set(ids)):
        raise ValueError("source catalog contains duplicate GF_ID values")

    candidates = [r for r in records
                  if r["candidate_class"] != "OUTSIDE"]
    exact = [r for r in candidates if r["date"]["precision"] == "day"]
    post = [r for r in exact if r["date"]["post_1979_candidate"]]
    post_with_id = [r for r in post if r["lake_identity"]["status"] == "SOURCE_ID_PRESENT"]
    unique_ids = sorted({r["lake_identity"]["raw_id"] for r in post_with_id})
    by_class = {cls: sum(r["candidate_class"] == cls for r in records)
                for cls in CANDIDATE_CLASSES}
    return {
        "schema": SCHEMA,
        "version": 0,
        "claim_scope": "research_only_no_operational_authorization",
        "authority": dict(AUTHORITY_FLAGS),
        "adjudication_state": "AWAITING_ADJUDICATION",
        "geography": {
            "classification_basis": "CATALOG_COUNTRY_FIELD_ONLY",
            "boundary_source": None,
            "boundary_version": None,
            "crs": None,
            "territory_statuses": sorted(TERRITORY_STATUSES),
        },
        "source": {"name": "HMAGLOFDB", "version": source_version,
                   "path_label": path.name, "sha256": sha256_file(path)},
        "rules": [
            "Every catalog row is retained; no row disappears on a raw country-string match.",
            "OUTSIDE records are reference-only and never enter India denominators.",
            "Catalog rows are not independent episodes.",
            "No lake alias, recurrence, cascade, mechanism, or eligibility is inferred.",
            "UNREVIEWED rows cannot enter weather analysis or serve as controls.",
            "Placeholder GL_ID values are not canonical lake identities.",
            "Coordinates are never a territory classification; adjudication assigns IN_COUNTRY/OUTSIDE/UNCERTAIN against a versioned boundary.",
            "A future adjudication must be append-only and cite primary evidence.",
        ],
        "summary": {
            "n_total_rows": len(records),
            "n_target_country_rows": by_class["TARGET_COUNTRY"],
            "n_transboundary_rows": by_class["TRANSBOUNDARY"],
            "n_unknown_country_rows": by_class["UNKNOWN_COUNTRY"],
            "n_reference_only_rows": by_class["OUTSIDE"],
            "n_candidate_rows": len(candidates),
            "n_exact_day_rows": len(exact),
            "n_exact_day_post_1979_rows": len(post),
            "n_post_1979_rows_with_source_lake_id": len(post_with_id),
            "n_unique_post_1979_source_lake_ids": len(unique_ids),
            "post_1979_source_lake_ids": unique_ids,
            "n_unreviewed": len(records),
            "n_analysis_eligible": 0,
            "n_independent_episodes": 0,
        },
        "records": records,
    }


def _summary_from_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    records = [record for record in records if isinstance(record, dict)]
    candidates = [r for r in records
                  if r.get("candidate_class") != "OUTSIDE"]
    exact = [r for r in candidates
             if isinstance(r.get("date"), dict)
             and r["date"].get("precision") == "day"]
    post = [r for r in exact
            if isinstance(r.get("date"), dict)
            and r["date"].get("post_1979_candidate") is True]
    post_with_id = [r for r in post
                    if isinstance(r.get("lake_identity"), dict)
                    and r["lake_identity"].get("status") == "SOURCE_ID_PRESENT"]
    unique_ids = sorted({r.get("lake_identity", {}).get("raw_id")
                         for r in post_with_id
                         if isinstance(r.get("lake_identity", {}).get("raw_id"), str)})
    by_class = {cls: sum(r.get("candidate_class") == cls for r in records)
                for cls in CANDIDATE_CLASSES}
    return {
        "n_total_rows": len(records),
        "n_target_country_rows": by_class["TARGET_COUNTRY"],
        "n_transboundary_rows": by_class["TRANSBOUNDARY"],
        "n_unknown_country_rows": by_class["UNKNOWN_COUNTRY"],
        "n_reference_only_rows": by_class["OUTSIDE"],
        "n_candidate_rows": len(candidates),
        "n_exact_day_rows": len(exact),
        "n_exact_day_post_1979_rows": len(post),
        "n_post_1979_rows_with_source_lake_id": len(post_with_id),
        "n_unique_post_1979_source_lake_ids": len(unique_ids),
        "post_1979_source_lake_ids": unique_ids,
        "n_unreviewed": len(records),
        "n_analysis_eligible": 0,
        "n_independent_episodes": 0,
    }


def validate_crosswalk(doc: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if doc.get("schema") != SCHEMA:
        problems.append("unexpected schema")
    if doc.get("claim_scope") != "research_only_no_operational_authorization":
        problems.append("unexpected claim scope")
    if doc.get("adjudication_state") != "AWAITING_ADJUDICATION":
        problems.append("crosswalk must remain awaiting adjudication")
    if doc.get("authority") != AUTHORITY_FLAGS:
        problems.append("authority flags must all be present and false")
    geography = doc.get("geography")
    if not isinstance(geography, dict) or not geography.get("classification_basis"):
        problems.append("geography must declare a classification_basis")
    elif not isinstance(geography.get("territory_statuses"), list) or not set(
            geography["territory_statuses"]) <= TERRITORY_STATUSES:
        problems.append("geography.territory_statuses must list known statuses")
    records = doc.get("records")
    if not isinstance(records, list):
        return ["records must be a list"]
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            problems.append("record must be an object")
            continue
        sid = record.get("source_record_id")
        if not isinstance(sid, str) or not sid:
            problems.append("record missing source_record_id")
        elif sid in seen:
            problems.append(f"duplicate source_record_id: {sid}")
        seen.add(sid)
        adj = record.get("adjudication", {})
        if not isinstance(adj, dict):
            problems.append(f"{sid}: adjudication must be an object")
            adj = {}
        if adj.get("eligibility") != "UNREVIEWED":
            problems.append(f"{sid}: crosswalk cannot silently adjudicate eligibility")
        if adj.get("review_state") != "AWAITING_ADJUDICATION":
            problems.append(f"{sid}: unexpected review_state")
        episode = record.get("episode", {})
        if not isinstance(episode, dict):
            problems.append(f"{sid}: episode must be an object")
            episode = {}
        if episode.get("candidate_episode_id") is not None:
            problems.append(f"{sid}: episode id requires adjudication")
        if record.get("candidate_class") not in CANDIDATE_CLASSES:
            problems.append(f"{sid}: invalid candidate_class")
        location = record.get("location", {})
        if not isinstance(location, dict):
            problems.append(f"{sid}: location must be an object")
        elif location.get("territory_status") not in TERRITORY_STATUSES:
            problems.append(f"{sid}: invalid territory_status")
        else:
            for field in ("latitude", "longitude"):
                value = location.get(field)
                if value is not None and not isinstance(value, (int, float)):
                    problems.append(f"{sid}: {field} must be numeric or null")
            lat, lon = location.get("latitude"), location.get("longitude")
            if isinstance(lat, (int, float)) and not -90 <= lat <= 90:
                problems.append(f"{sid}: latitude out of range")
            if isinstance(lon, (int, float)) and not -180 <= lon <= 180:
                problems.append(f"{sid}: longitude out of range")
        date = record.get("date", {})
        if not isinstance(date, dict):
            problems.append(f"{sid}: date must be an object")
            continue
        if date.get("precision") == "day":
            if not date.get("start") or date.get("start") != date.get("end"):
                problems.append(f"{sid}: day precision has invalid bounds")
        elif date.get("precision") == "unknown" and date.get("start") is not None:
            problems.append(f"{sid}: unknown precision has a date")
    summary = doc.get("summary", {})
    expected = _summary_from_records(records)
    for key, value in expected.items():
        if summary.get(key) != value:
            problems.append(f"summary {key} mismatch")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hmaglofdb-csv", required=True)
    parser.add_argument("--source-version", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--country", default="India")
    args = parser.parse_args()
    doc = build_crosswalk(args.hmaglofdb_csv, args.source_version, args.country)
    problems = validate_crosswalk(doc)
    if problems:
        raise SystemExit("crosswalk validation failed: " + "; ".join(problems))
    write_once_json(args.out, doc, indent=2)
    write_once_sidecar(args.out)
    print(json.dumps({"status": "PHASE0_CROSSWALK_READY",
                      "out": str(args.out), "summary": doc["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
