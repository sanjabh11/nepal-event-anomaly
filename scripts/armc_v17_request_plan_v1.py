"""Build an offline, non-authorizing Route-B selector inventory.

This draft does not mutate the sealed v17r plan and makes no provider calls.
It derives only the seven raw ERA5 inputs named by the frozen Route-B
exposures, carries every eligible event member separately, and describes the
distinct climatology and antecedent-window selections without pretending they
are executable provider request counts.
"""

from __future__ import annotations

import argparse
import calendar
import datetime as dt
import hashlib
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

SINGLE_VARS = ("tp", "tcwv", "cape", "t2m", "sp", "sf")
PRESSURE_VARS = ("t",)
PRESSURE_LEVEL_HPA = 500
YEARS = tuple(range(2001, 2026))
SNAPSHOT = "ZFKDHBCTBVHVXM3BQFV0"
GRID_DEG = 0.25
BOX_HALF_DEG = 0.25
EXPOSURE_START_LAG_DAYS = 10
EXPOSURE_END_LAG_DAYS = 4
# Float64 is used for a conservative decoded-array planning envelope. The
# source encoding and actual on-disk bytes still require a measured canary.
BYTES_PER_VALUE = 8
PLANNING_OVERHEAD_FACTOR = 1.15
BYTE_CAP = 500_000_000
GRID_TOLERANCE = 1e-10
COORDINATE_TOLERANCE = 1e-6
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

EXPECTED_PRIMARY = "tp_antecedent_7d — total precipitation sum over days -10..-4 before the reported event date, z-scored against the same box's same-month climatology (2001-2025, all days)"
EXPECTED_SECONDARIES = (
    "tcwv_mean anomaly days -10..-4",
    "cape_mean anomaly days -10..-4",
    "theta500_minus_thetasfc anomaly days -10..-4",
    "sf_daily anomaly days -10..-4",
)
def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _require_sha256(value: str, label: str) -> None:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")


def _stable_id(prefix: str, value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:12]}"


def _grid_axis(low: float, high: float, *, descending: bool = False) -> list[float]:
    """List nominal ERA5 0.25-degree centers in a closed event box.

    This is a planned coordinate list, not proof that the pinned Icechunk
    snapshot has those coordinates. An executor must exact-match its source
    coordinate arrays to this list and fail closed on any discrepancy.
    """
    first = math.ceil((low - GRID_TOLERANCE) / GRID_DEG)
    last = math.floor((high + GRID_TOLERANCE) / GRID_DEG)
    values = [round(index * GRID_DEG, 8) for index in range(first, last + 1)]
    return sorted(values, reverse=descending)


def _parse_utc_midnight(value: str, label: str) -> dt.datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO UTC timestamp")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != dt.timedelta(0):
        raise ValueError(f"{label} must use UTC")
    if parsed.time() != dt.time(0, 0):
        raise ValueError(f"{label} must be midnight UTC for day-precision events")
    return parsed


def _eligible_event_index(decision: dict) -> dict[str, dict]:
    if decision.get("schema") != "P5_EVENT_ADJUDICATION_V1":
        raise ValueError("unexpected adjudication decision schema")
    events = decision.get("events")
    if not isinstance(events, list):
        raise ValueError("adjudication decision must contain an events list")
    result: dict[str, dict] = {}
    for event in events:
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or ":" not in event_id:
            raise ValueError("adjudication event has a malformed event_id")
        member_id = event_id.rsplit(":", 1)[-1]
        if event.get("adjudication", {}).get("disposition") != "ELIGIBLE":
            continue
        if member_id in result:
            raise ValueError(f"duplicate eligible member id {member_id}")
        result[member_id] = event
    return result


def _validate_protocol(protocol: dict) -> None:
    if protocol.get("schema") != "P5_V17_PROTOCOL_V0":
        raise ValueError("unexpected Route-B protocol schema")
    if not str(protocol.get("route", "")).startswith("B — descriptive anomaly"):
        raise ValueError("only the frozen Route-B descriptive-anomaly protocol is supported")
    estimand = protocol.get("estimand", {})
    if estimand.get("type") != "descriptive mean anomaly":
        raise ValueError("unexpected Route-B estimand type")
    if estimand.get("primary_exposure") != EXPECTED_PRIMARY:
        raise ValueError("primary exposure differs from the reviewed Route-B contract")
    if tuple(estimand.get("secondary_exposures", ())) != EXPECTED_SECONDARIES:
        raise ValueError("secondary exposures differ from the reviewed Route-B contract")
    if estimand.get("missingness") != "listwise per unit; unit dropped if <90% antecedent-day coverage":
        raise ValueError("unexpected Route-B missingness rule")
    if "NO event-risk odds ratios" not in protocol.get("claim_ceiling", []):
        raise ValueError("Route-B event-risk-odds prohibition is missing")


def _unit_geometry(unit: dict) -> dict:
    required = ("unit_id", "member_ids", "lat", "lon", "lake")
    if any(key not in unit for key in required):
        raise ValueError("episode mapping unit is missing required geometry/member fields")
    if not isinstance(unit["unit_id"], str) or not unit["unit_id"].strip():
        raise ValueError("unit_id must be a non-empty string")
    members = unit["member_ids"]
    if not isinstance(members, list) or not members or any(not isinstance(x, str) or not x for x in members):
        raise ValueError(f"member_ids must be a non-empty string list for {unit['unit_id']}")
    if len(set(members)) != len(members):
        raise ValueError(f"duplicate member_id for {unit['unit_id']}")
    try:
        lat, lon = float(unit["lat"]), float(unit["lon"])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid coordinates for {unit['unit_id']}") from exc
    if not math.isfinite(lat) or not math.isfinite(lon) or not -90 <= lat <= 90 or not 0 <= lon < 360:
        raise ValueError(f"coordinates outside the declared domain for {unit['unit_id']}")
    box = {
        "lat_min": round(lat - BOX_HALF_DEG, 6),
        "lat_max": round(lat + BOX_HALF_DEG, 6),
        "lon_min": round(lon - BOX_HALF_DEG, 6),
        "lon_max": round(lon + BOX_HALF_DEG, 6),
    }
    if box["lat_min"] < -90 or box["lat_max"] > 90:
        raise ValueError(f"event box crosses a latitude boundary for {unit['unit_id']}")
    latitudes = _grid_axis(box["lat_min"], box["lat_max"], descending=True)
    longitudes = _grid_axis(box["lon_min"], box["lon_max"])
    if not latitudes or not longitudes or len(latitudes) > 3 or len(longitudes) > 3:
        raise ValueError(f"invalid nominal grid-center selection for {unit['unit_id']}")
    return {
        "unit_id": unit["unit_id"],
        "lake_label": unit["lake"],
        "member_ids": list(members),
        "event_box": box,
        "grid_selection": {
            "rule": "0.25-degree nominal ERA5 coordinate centers inside closed event box",
            "latitudes_descending": latitudes,
            "longitudes_ascending": longitudes,
            "cell_count": len(latitudes) * len(longitudes),
            "source_coordinate_verification": "REQUIRED_BEFORE_EXTRACTION_NOT_YET_VERIFIED",
        },
    }


def _event_plan(unit: dict, geometry: dict, record: dict) -> dict:
    member_id = record["event_id"].rsplit(":", 1)[-1]
    interval = record.get("adjudication", {}).get("event_time_interval", {})
    if interval.get("precision") != "day":
        raise ValueError(f"member {member_id} is not day-precision")
    start = _parse_utc_midnight(interval.get("start"), f"member {member_id} interval start")
    end = _parse_utc_midnight(interval.get("end"), f"member {member_id} interval end")
    if end != start + dt.timedelta(days=1):
        raise ValueError(f"member {member_id} day interval must be exactly 24 hours")
    if start.year not in YEARS:
        raise ValueError(f"member {member_id} date is outside the authorized year range")
    local = record.get("local", {})
    try:
        event_lat, event_lon = float(local["lat"]), float(local["lon"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"member {member_id} lacks valid adjudicated coordinates") from exc
    if abs(event_lat - float(unit["lat"])) > COORDINATE_TOLERANCE or abs(event_lon - float(unit["lon"])) > COORDINATE_TOLERANCE:
        raise ValueError(f"member {member_id} coordinates disagree with its episode-map unit")

    event_date = start.date()
    window_start = event_date - dt.timedelta(days=EXPOSURE_START_LAG_DAYS)
    window_end = event_date - dt.timedelta(days=EXPOSURE_END_LAG_DAYS)
    if window_start.year not in YEARS or window_end.year not in YEARS:
        raise ValueError(f"member {member_id} antecedent window is outside the authorized year range")
    days = [window_start + dt.timedelta(days=i) for i in range((window_end - window_start).days + 1)]
    geometry_key = {
        "event_box": geometry["event_box"],
        "grid_selection": {
            "latitudes_descending": geometry["grid_selection"]["latitudes_descending"],
            "longitudes_ascending": geometry["grid_selection"]["longitudes_ascending"],
        },
    }
    climatology_key = {**geometry_key, "calendar_month": event_date.month}
    return {
        "analysis_unit_id": geometry["unit_id"],
        "member_id": member_id,
        "event_id": record["event_id"],
        "event_date_utc": event_date.isoformat(),
        "event_interval_utc": {"start": interval["start"], "end": interval["end"]},
        "date_uncertainty_note": "Protocol's approximately +/-3-day uncertainty is not established by this day-precision source interval; treat it as a declared analysis assumption pending evidence.",
        "event_box": geometry["event_box"],
        "grid_selection": geometry["grid_selection"],
        "exposure_window": {
            "rule": "calendar dates -10 through -4 inclusive relative to event interval start date",
            "start_date": window_start.isoformat(),
            "end_date": window_end.isoformat(),
            "dates": [value.isoformat() for value in days],
            "days_inclusive": len(days),
        },
        "climatology_group_id": _stable_id("clim", climatology_key),
        "climatology_month": event_date.month,
        "climatology_years": list(YEARS),
        "washout_event_interval": {"start": interval["start"], "end": interval["end"]},
    }


def _build_selection_groups(event_rows: list[dict]) -> tuple[list[dict], list[dict]]:
    climatology: dict[tuple[str, int], dict] = {}
    spillover: dict[tuple[str, int, int], dict] = {}
    for event in event_rows:
        geometry_key = _stable_id("site", {
            "event_box": event["event_box"],
            "latitudes": event["grid_selection"]["latitudes_descending"],
            "longitudes": event["grid_selection"]["longitudes_ascending"],
        })
        month = event["climatology_month"]
        key = (geometry_key, month)
        group = climatology.setdefault(key, {
            "selection_id": event["climatology_group_id"],
            "event_box": event["event_box"],
            "grid_selection": event["grid_selection"],
            "calendar_month": month,
            "years": list(YEARS),
            "analysis_unit_ids": set(),
            "member_ids": set(),
            "washout_event_intervals": {},
        })
        group["analysis_unit_ids"].add(event["analysis_unit_id"])
        group["member_ids"].add(event["member_id"])
        group["washout_event_intervals"][event["member_id"]] = event["washout_event_interval"]

        for date_text in event["exposure_window"]["dates"]:
            date = dt.date.fromisoformat(date_text)
            if date.month == month:
                continue  # already covered by the same-month climatology selector
            spill_key = (geometry_key, date.year, date.month)
            spill = spillover.setdefault(spill_key, {
                "selection_id": None,
                "event_box": event["event_box"],
                "grid_selection": event["grid_selection"],
                "year": date.year,
                "month": date.month,
                "dates": set(),
                "analysis_unit_ids": set(),
                "member_ids": set(),
            })
            spill["dates"].add(date.isoformat())
            spill["analysis_unit_ids"].add(event["analysis_unit_id"])
            spill["member_ids"].add(event["member_id"])

    climate_rows = []
    for group in climatology.values():
        group["analysis_unit_ids"] = sorted(group["analysis_unit_ids"])
        group["member_ids"] = sorted(group["member_ids"], key=lambda value: int(value))
        group["washout_event_intervals"] = [
            {"member_id": member_id, **group["washout_event_intervals"][member_id]}
            for member_id in group["member_ids"]
        ]
        climate_rows.append(group)
    spill_rows = []
    for group in spillover.values():
        group["selection_id"] = _stable_id("spill", {
            "event_box": group["event_box"],
            "year": group["year"],
            "month": group["month"],
            "dates": sorted(group["dates"]),
        })
        group["dates"] = sorted(group["dates"])
        group["analysis_unit_ids"] = sorted(group["analysis_unit_ids"])
        group["member_ids"] = sorted(group["member_ids"], key=lambda value: int(value))
        spill_rows.append(group)
    climate_rows.sort(key=lambda row: row["selection_id"])
    spill_rows.sort(key=lambda row: row["selection_id"])
    return climate_rows, spill_rows


def build_plan(
    mapping: dict,
    protocol: dict,
    decision: dict,
    *,
    episode_mapping_sha256: str,
    protocol_sha256: str,
    decision_sha256: str,
    basis_amendment_sha256: str,
) -> dict:
    """Return a candidate selector inventory; performs no filesystem/network I/O."""
    for value, label in (
        (episode_mapping_sha256, "episode_mapping_sha256"),
        (protocol_sha256, "protocol_sha256"),
        (decision_sha256, "decision_sha256"),
        (basis_amendment_sha256, "basis_amendment_sha256"),
    ):
        _require_sha256(value, label)
    if mapping.get("schema") != "P5_EVENT_EPISODE_MAPPING_V0":
        raise ValueError("unexpected episode mapping schema")
    _validate_protocol(protocol)
    units = mapping.get("units")
    if not isinstance(units, list) or not units:
        raise ValueError("episode mapping must contain a non-empty units list")
    unit_ids = [unit.get("unit_id") for unit in units]
    if len(set(unit_ids)) != len(unit_ids):
        raise ValueError("duplicate unit_id in episode mapping")

    eligible = _eligible_event_index(decision)
    mapping_member_ids = [member_id for unit in units for member_id in unit.get("member_ids", [])]
    if len(set(mapping_member_ids)) != len(mapping_member_ids):
        raise ValueError("a member_id appears in more than one analysis unit")
    if set(mapping_member_ids) != set(eligible):
        missing = sorted(set(mapping_member_ids) - set(eligible))
        extra = sorted(set(eligible) - set(mapping_member_ids))
        raise ValueError(f"episode-map/adjudication eligible-member mismatch; missing={missing}, extra={extra}")

    geometries = [_unit_geometry(unit) for unit in units]
    events = []
    for unit, geometry in zip(units, geometries):
        for member_id in geometry["member_ids"]:
            events.append(_event_plan(unit, geometry, eligible[member_id]))
    events.sort(key=lambda row: (row["analysis_unit_id"], row["event_date_utc"], int(row["member_id"])))
    climatology_groups, spillover_groups = _build_selection_groups(events)

    raw_variables = {
        "single_level": list(SINGLE_VARS),
        "pressure_level_hpa_500": list(PRESSURE_VARS),
    }
    raw_variable_count = len(SINGLE_VARS) + len(PRESSURE_VARS)
    if raw_variable_count != len({var for variables in raw_variables.values() for var in variables}):
        raise ValueError("duplicate raw variable across source groups")

    cells_hours = 0
    for group in climatology_groups:
        cell_count = group["grid_selection"]["cell_count"]
        month = group["calendar_month"]
        cells_hours += sum(calendar.monthrange(year, month)[1] for year in YEARS) * 24 * cell_count
    for group in spillover_groups:
        cells_hours += len(group["dates"]) * 24 * group["grid_selection"]["cell_count"]
    raw_array_bytes = cells_hours * raw_variable_count * BYTES_PER_VALUE
    estimate_bytes = math.ceil(raw_array_bytes * PLANNING_OVERHEAD_FACTOR)

    feature_raw_map = {
        "tp_antecedent_7d": ["tp"],
        "tcwv_mean": ["tcwv"],
        "cape_mean": ["cape"],
        "theta500_minus_thetasfc": ["t@500hPa", "t2m", "sp"],
        "sf_daily": ["sf"],
    }
    return {
        "schema": "P5_V17_SELECTOR_INVENTORY_V1",
        "status": "CANDIDATE_ONLY_NOT_AUTHORIZED_FOR_ACQUISITION",
        "claim_scope": "research_only_no_operational_authorization",
        "basis_amendment_sha256": basis_amendment_sha256,
        "episode_mapping_sha256": episode_mapping_sha256,
        "protocol_sha256": protocol_sha256,
        "decision_sha256": decision_sha256,
        "snapshot": SNAPSHOT,
        "source": "earthmover_icechunk",
        "grid_deg": GRID_DEG,
        "grid_selection_rule": "nominal 0.25-degree coordinate centers inside each closed event box; source coordinate equality must be verified before extraction",
        "box_rule": "event coordinate +/-0.25 degrees; not a verified hydrologic catchment or lake footprint",
        "protocol_exposure_raw_variables": feature_raw_map,
        "raw_variable_count": raw_variable_count,
        "raw_variables": raw_variables,
        "feature_contract_status": "ROUTE_B_SPECIFIC_TRANSFORM_CONTRACT_MISSING; protocol binds the existing 17-column daily regime contract, which supplies the theta formula but not Route-B climatology/anomaly, length-matched z-score, spatial-mask, or event-unit rules",
        "data_semantics_gate": "FREEZE_AND_VERIFY_UNITS_ACCUMULATION_DAILY_UTC_BINNING_HOURLY_COMPLETENESS_TERRAIN_MASK_SPATIAL_WEIGHTS_AND_SOURCE_VARIABLE_AVAILABILITY_BEFORE_CANARY",
        "event_date_uncertainty_gate": "SOURCE_DOES_NOT_ESTABLISH_PROTOCOL_APPROX_PLUS_MINUS_3_DAYS",
        "recurrence_policy": "UNRESOLVED; preserve each eligible member event window and do not aggregate member outcomes in this plan",
        "climatology_washout_scope": "all eligible event-member intervals in the mapped same-site/calendar-month group; amendment must confirm whether any other catalog candidates are in scope",
        "canary_status": "NOT_DESIGNED; source executor and all-seven-variable coverage matrix are not yet verified",
        "n_analysis_units": len(geometries),
        "n_eligible_event_members": len(events),
        "n_logical_event_variable_observations": len(events) * raw_variable_count,
        "n_unique_climatology_groups": len(climatology_groups),
        "n_unique_climatology_variable_selections": len(climatology_groups) * raw_variable_count,
        "n_unique_spillover_groups": len(spillover_groups),
        "n_unique_spillover_variable_selections": len(spillover_groups) * raw_variable_count,
        "provider_request_count": None,
        "provider_request_count_reason": "Unresolved until the pinned Icechunk reader's grouped/irregular-time selector semantics and executor are implemented and tested.",
        "estimate": {
            "assumed_bytes_per_value": BYTES_PER_VALUE,
            "raw_decoded_array_bytes": raw_array_bytes,
            "planning_overhead_factor": PLANNING_OVERHEAD_FACTOR,
            "estimated_bytes_with_overhead": estimate_bytes,
            "estimated_mb_decimal": round(estimate_bytes / 1_000_000, 2),
            "cap_bytes_from_unsigned_basis_amendment": BYTE_CAP,
            "below_basis_cap_on_model_estimate": estimate_bytes < BYTE_CAP,
            "note": "planning estimate only; compressed source bytes, NetCDF encoding, request overhead, and aggregate storage enforcement remain unmeasured",
        },
        "analysis_units": geometries,
        "event_windows": events,
        "climatology_selections": climatology_groups,
        "antecedent_spillover_selections": spillover_groups,
        "required_owner_and_engine_gates": [
            "successor acquisition amendment explicitly binds this selector inventory and remains owner-pending until signed",
            "new Route-B daily feature/data contract replaces the unrelated inherited regime feature-contract reference",
            "define the length-matched reference distribution and scale estimator for the seven-day primary precipitation z-score",
            "freeze daily UTC timestamp assignment, precipitation/snowfall accumulation conversion, box weighting, 500-hPa terrain mask, and valid-cell threshold",
            "freeze how recurrent member windows contribute to one analysis unit without dropping either date",
            "define whether climatology washout uses only the 15 eligible mapped members or a broader event-candidate catalog",
            "confirm source variable availability, units, accumulation conventions, time coverage and grid coordinates using a separately authorized canary",
            "implement aggregate byte-cap enforcement across all workers before publication",
            "freeze source-supported date-uncertainty rationale or revise the antecedent-window rationale in a successor protocol",
            "define the source-call batching strategy; the five-request canary must exercise every required variable and both spillover cases",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--episode-map", required=True, type=Path)
    parser.add_argument("--protocol", required=True, type=Path)
    parser.add_argument("--decision", required=True, type=Path)
    parser.add_argument("--basis-amendment", required=True, type=Path)
    args = parser.parse_args()

    mapping = json.loads(args.episode_map.read_text())
    protocol = json.loads(args.protocol.read_text())
    decision = json.loads(args.decision.read_text())
    plan = build_plan(
        mapping,
        protocol,
        decision,
        episode_mapping_sha256=sha256_file(args.episode_map),
        protocol_sha256=sha256_file(args.protocol),
        decision_sha256=sha256_file(args.decision),
        basis_amendment_sha256=sha256_file(args.basis_amendment),
    )
    print(json.dumps(plan, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
