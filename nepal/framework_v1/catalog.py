"""nepal.framework_v1.catalog — Phase A catalog construction and adjudication.

Clean-room additive normalizer over the raw Bashkova–Rupper compilation.  The
raw records are preserved verbatim (every normalized row carries the sha256 of
its raw record); nothing is edited in place.

Policies implemented here (see contract.CATALOG_ELIGIBILITY_RULES):
- Dates: exact day or a source-supported interval of at most 7 days is
  validation-eligible.  Missing days are never replaced with day 15; month or
  season strings are never converted into artificial dates; ambiguous raw
  strings are preserved.  Month-only rows get calendar month bounds (a bounding
  interval in time, NOT a point date) and are gate-ineligible.
- Mechanism: eligible ice-rock events require source-supported confirmation;
  pure GLOF, debris-flow, rock-only, snow-only and unresolved rows are excluded
  from the ice-rock cohort; Manaslu-type ice-into-lake events remain separately
  classified; Thame is explicitly excluded as a precursor analog.
- Holdouts: every raw row is assigned to a fixed geographic group *before*
  eligibility adjudication, the plan is frozen (hashed) before any date or
  mechanism filtering, and groups are never rebalanced afterwards.
"""
from __future__ import annotations

import re
import math
from datetime import date, timedelta
from typing import Any, Iterable, Mapping, Optional, Sequence

from . import contract as C
from .controls import ControlsConfig, create_controls_lock
from .provenance import (sha256_canonical, write_deterministic_csv,
                         write_deterministic_json, build_manifest,
                         bind_gate_artifact)



# ---------------------------------------------------------------------------
# Normalized record construction
# ---------------------------------------------------------------------------

M3_PER_SOURCE_UNIT = 1.0e6  # raw volumes are compiled in 10^6 m^3 (Mm^3)
_PLACEHOLDER_TEXT = frozenset({
    "", "-", "--", "n/a", "na", "none", "null", "unknown",
    "no information", "no_information", "not available", "unnamed",
    "unnamed glacier", "unidentified", "not identified", "no name",
})


def _meaningful_text(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in _PLACEHOLDER_TEXT else text


def _volume_factor(record: Mapping[str, Any]) -> tuple[float, str, str]:
    """Return factor, declared units and confidence without inventing units."""
    raw_units = _meaningful_text(record.get("volume_units") or record.get("units"))
    lower = raw_units.lower().replace("³", "3")
    if "km3" in lower or "km^3" in lower:
        return 1.0e9, raw_units, "DECLARED_KM3"
    if "million" in lower or "mm3" in lower or "10^6" in lower:
        return 1.0e6, raw_units, "DECLARED_MILLION_M3"
    if lower in {"m3", "m^3", "cubic metre", "cubic meter"}:
        return 1.0, raw_units, "DECLARED_M3"
    # The source compilation convention is retained as an explicit assumption,
    # never as a silently asserted measurement unit.
    return M3_PER_SOURCE_UNIT, raw_units, "ASSUMED_COMPILED_MILLION_M3"


def _scalar_number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "")
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _volume_bounds(record: Mapping[str, Any]) -> tuple[Optional[float], Optional[float], str, str]:
    factor, declared_units, confidence = _volume_factor(record)
    lower = _scalar_number(record.get("volume_min"))
    upper = _scalar_number(record.get("volume_max"))
    total = _scalar_number(record.get("total_volume"))
    if lower is None and upper is None and total is not None:
        lower = upper = total
    if lower is None and upper is not None:
        lower = upper
    if upper is None and lower is not None:
        upper = lower
    if lower is not None and upper is not None and upper < lower:
        lower, upper = upper, lower
    return (lower * factor if lower is not None else None,
            upper * factor if upper is not None else None,
            declared_units or "unrecorded",
            confidence)


def _impact_locations(record: Mapping[str, Any]) -> list[dict[str, float]]:
    """Accept only explicit impact coordinates; prose is never geocoded here."""
    raw = record.get("impact_locations")
    candidates: list[Any] = raw if isinstance(raw, list) else []
    if not candidates and record.get("impact_lat") is not None and record.get("impact_lon") is not None:
        candidates = [{"lat": record.get("impact_lat"),
                       "lon": record.get("impact_lon")}]
    locations: list[dict[str, float]] = []
    for item in candidates:
        if not isinstance(item, Mapping):
            continue
        lat = _scalar_number(item.get("lat"))
        lon = _scalar_number(item.get("lon"))
        if lat is None or lon is None:
            continue
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            locations.append({"lat": lat, "lon": lon})
    return locations


def normalize_record(record: Mapping[str, Any], *,
                     language: str = "en",
                     access_date: Optional[str] = None,
                     evidence_type: str = "compiled_database",
                     rgi60_crosswalk: Optional[Mapping[str, str]] = None,
                     min_detectable_size_m3: Optional[float] = None,
                     overrides: Optional[Mapping[str, Mapping[str, Any]]] = None,
                     event_id: Optional[str] = None,
                     source_row_index: Optional[int] = None,
                     holdout_group: Optional[str] = None,
                     holdout_group_basis: Optional[str] = None,
                     controls: Optional["ControlsConfig"] = None) -> dict[str, Any]:
    """Build one additive normalized row from a preserved raw record.

    The raw record is never mutated; the normalized row carries
    ``raw_row_sha256`` tying it back to the exact raw content."""
    over = dict((overrides or {}).get(str(record.get("event_name") or ""), {})
                or (overrides or {}).get("default", {}))
    if min_detectable_size_m3 is None and controls is not None:
        min_detectable_size_m3 = controls.min_detectable_size_m3
    dates = normalize_raw_date(record)
    gid, basis = assign_holdout_group(record)
    if holdout_group is not None:
        gid = holdout_group
    if holdout_group_basis is not None:
        basis = holdout_group_basis
    mechanism = classify_mechanism(record.get("hazard_type"))

    rgi7 = record.get("rgi_v7_id") or None
    crosswalk_status = C.CrosswalkStatus.ABSENT.value
    rgi60 = None
    if rgi7 and rgi60_crosswalk and rgi7 in rgi60_crosswalk:
        rgi60 = rgi60_crosswalk[rgi7]
        crosswalk_status = C.CrosswalkStatus.DOCUMENTED.value

    lat, lon = record.get("lat"), record.get("lon")
    volume_min, volume_max, declared_volume_units, volume_confidence = \
        _volume_bounds(record)

    country = str(record.get("country") or "").strip()
    transboundary = ("–" in country) or ("-" in country and len(
        country.split("-")) > 1 and all(p.strip() for p in country.split("-")))

    row: dict[str, Any] = {
        "event_id": event_id or stable_event_id(record, source_row_index=source_row_index),
        "raw_row_sha256": raw_record_hash(record),
        "source_row_index": source_row_index,
        "event_name": record.get("event_name"),
        "source_name": _meaningful_text(record.get("glacier_name"))
                       or _meaningful_text(record.get("event_name")),
        "source_lat": lat,
        "source_lon": lon,
        "source_precision": (source_precision_class(lat, lon)
                             if lat is not None and lon is not None else "NO_COORD"),
        "source_coordinate_status": ("EXACT_OR_DECIMAL"
                                      if lat is not None and lon is not None
                                      else "MISSING"),
        # Impact fields are kept strictly separate from the source fields.
        "impact_locations": _impact_locations(record),
        "impact_precision": ("GEOREFERENCED"
                              if _impact_locations(record) else "NONE_GEOREFERENCED"),
        "impact_coordinate_status": ("GEOCODED"
                                      if _impact_locations(record) else
                                      ("TEXT_ONLY" if _meaningful_text(record.get("impact"))
                                       else "MISSING")),
        "impact_raw": str(record.get("impact") or ""),
        "country": country,
        "transboundary": bool(transboundary),
        "raw_day": record.get("day"),
        "raw_month": record.get("month"),
        "raw_year": record.get("year"),
        "raw_date_min": record.get("date_min"),
        "raw_date_max": record.get("date_max"),
        "raw_date_string": dates["raw_date_string"],
        "date_min": dates["date_min"],
        "date_max": dates["date_max"],
        "date_precision": dates["date_precision"],
        "date_parse_notes": dates["date_parse_notes"],
        "volume_min_m3": volume_min,
        "volume_max_m3": volume_max,
        "volume_units": declared_volume_units,
        "volume_unit_confidence": volume_confidence,
        "volume_source": "Bashkova–Rupper compilation (raw volume fields)",
        "volume_uncertainty_raw": str(record.get("uncertainties") or ""),
        "mechanism_primary": mechanism.value,
        "mechanism_alternatives": over.get("mechanism_alternatives", []),
        "mechanism_dissent": bool(over.get("mechanism_dissent", False)),
        "mechanism_confidence": ("SOURCE_CITATION_PRESENT"
                                  if (_meaningful_text(record.get("references"))
                                      or _meaningful_text(record.get("links")))
                                  else "UNSUPPORTED"),
        "mechanism_evidence_status": ("SOURCE_CITATION_PRESENT"
                                       if (_meaningful_text(record.get("references"))
                                           or _meaningful_text(record.get("links")))
                                       else "MISSING"),
        "mechanism_confirmation_scope": (
            "SOURCE_CITATION_ONLY_NOT_INDEPENDENT_FIELD_ADJUDICATION"),
        "mechanism_confirmed": False,     # finalized during adjudication
        "initially_misclassified": bool(over.get("initially_misclassified", False)),
        "glof_mislabel_flag": bool(over.get("glof_mislabel_flag", False)),
        "trigger_evidence": _split_list(record.get("triggers")),
        "conditioning_evidence": _split_list(record.get("comments"))[:5],
        "raw_triggers": str(record.get("triggers") or ""),
        "rgi7_id": rgi7,
        "rgi60_id": rgi60,
        "rgi60_crosswalk_status": crosswalk_status,
        "citations": _citations(record.get("references"), record.get("links"),
                                language, access_date, evidence_type),
        "language": language,
        "access_date": access_date,
        "evidence_type": evidence_type,
        "observation_availability": over.get("observation_availability",
                                             C.ObservationAvailability.UNKNOWN.value),
        "min_detectable_size_m3": min_detectable_size_m3,
        "holdout_group": gid,
        "holdout_group_basis": basis,
        "duplicate_of": None,
        "cascade_group_id": None,
        "eligibility_status": C.OutputStatus.INELIGIBLE.value,
        "eligibility_reasons": [],
    }
    return row

_MECHANISM_MAP: dict[str, Any] = {
    "ice avalanche (into lake)": C.MechanismClass.ICE_INTO_LAKE,
    "rock-ice avalanche": C.MechanismClass.ICE_ROCK_AVALANCHE,
    "rock-snow-ice avalanche": C.MechanismClass.ICE_ROCK_AVALANCHE,
    "glacier detachment": C.MechanismClass.GLACIER_DETACHMENT,
    "ice avalanche": C.MechanismClass.ICE_AVALANCHE,
    "snow-ice avalanche": C.MechanismClass.SNOW_AVALANCHE,
    "snow avalanche": C.MechanismClass.SNOW_AVALANCHE,
    "rock avalanche": C.MechanismClass.ROCK_AVALANCHE,
    "rockfall": C.MechanismClass.ROCK_AVALANCHE,
    "debris flow": C.MechanismClass.DEBRIS_FLOW,
    "glof": C.MechanismClass.GLOF,
    "glacial lake outburst flood": C.MechanismClass.GLOF,
}


def classify_mechanism(hazard_type: Any) -> Any:
    """Map a raw hazard_type string to the fixed mechanism classes.

    Unrecognized strings map to UNRESOLVED (never guessed)."""
    if hazard_type is None:
        return C.MechanismClass.UNRESOLVED
    key = re.sub(r"\s+", " ", str(hazard_type)).strip().lower()
    if not key:
        return C.MechanismClass.UNRESOLVED
    return _MECHANISM_MAP.get(key, C.MechanismClass.UNRESOLVED)


def _slug(text: str, max_len: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(text).strip().lower()).strip("_")
    return slug[:max_len] if slug else "unnamed"


def stable_event_id(record: Mapping[str, Any], *,
                    source_row_index: Optional[int] = None) -> str:
    """Deterministic stable event id from identity fields only."""
    import hashlib
    identity = "|".join([
        str(record.get("event_name") or "").strip(),
        f"{record.get('lat')}" if record.get("lat") is not None else "",
        f"{record.get('lon')}" if record.get("lon") is not None else "",
        str(record.get("year") or ""),
        str(record.get("glacier_id") or ""),
        str(source_row_index) if source_row_index is not None else "",
    ])
    return EVENT_ID_PREFIX + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]




# ---------------------------------------------------------------------------
# Holdout policy (fixed geographic groups, assigned before eligibility)
# ---------------------------------------------------------------------------

SPATIAL_GRID_DEG = 2.0  # deterministic fallback macroregion grid


def raw_record_hash(record: Mapping[str, Any]) -> str:
    """sha256 of the raw record, canonicalized for provenance."""
    return sha256_canonical(dict(record))


def assign_holdout_group(record: Mapping[str, Any]) -> tuple[str, str]:
    """Assign the fixed geographic holdout group for a raw row.

    Basis order (frozen): (1) source glacier/basin name when present, (2) fixed
    HMA macroregion (RGI region name) when present, (3) a deterministic
    2-degree spatial fallback grid keyed on source coordinates.

    Called on every raw row BEFORE eligibility adjudication; the assignment
    depends only on geography, never on date, mechanism, or eligibility."""
    glacier_name = _meaningful_text(record.get("glacier_name"))
    if glacier_name:
        return f"GL-{_slug(glacier_name)}", "source_glacier_or_basin"
    region = _meaningful_text(record.get("rgi_region_name"))
    if region:
        return f"MR-{_slug(region)}", "fixed_hma_macroregion"
    lat, lon = record.get("lat"), record.get("lon")
    try:
        if lat is None or lon is None:
            raise TypeError("coordinate absent")
        lat_f, lon_f = float(lat), float(lon)
        if math.isfinite(lat_f) and math.isfinite(lon_f):
            gi = int(lat_f // SPATIAL_GRID_DEG)
            gj = int(lon_f // SPATIAL_GRID_DEG)
            return f"GRID-{gi:+03d}-{gj:+04d}", "deterministic_spatial_fallback"
    except (TypeError, ValueError):
        pass
    return "GRID-UNKNOWN", "deterministic_spatial_fallback"


def freeze_holdout_plan(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Hash-freeze the holdout plan over ALL rows, before any filtering.

    The plan is computed from raw-row geography only and is never rebalanced
    after observing the eligible count."""
    counts: dict[str, int] = {}
    for rec in rows:
        counts[rec["holdout_group"]] = counts.get(rec["holdout_group"], 0) + 1
    plan: dict[str, Any] = {
        "groups": [{"group_id": g, "n_raw_rows": counts[g]} for g in sorted(counts)],
        "n_groups": len(counts),
        "policy": C.CATALOG_ELIGIBILITY_RULES["holdout"],
        "frozen_before_eligibility_filtering": True,
    }
    plan["plan_sha256"] = sha256_canonical({k: v for k, v in plan.items()})
    return plan


def _decimal_precision(value: Any) -> int:
    s = repr(float(value))
    if "e" in s or "E" in s:
        return 0
    frac = s.split(".")[1] if "." in s else ""
    return len(frac.rstrip("0"))


def source_precision_class(lat: Any, lon: Any) -> str:
    """Source coordinate precision class from decimal places (documented
    approximation: 4+ dp ~ <=11 m, 3 dp ~ <=111 m, else LOW)."""
    dp = min(_decimal_precision(lat), _decimal_precision(lon))
    if dp >= 4:
        return "HIGH_<=11M"
    if dp == 3:
        return "MEDIUM_<=111M"
    return "LOW"


def _split_list(raw: Any) -> list[str]:
    if raw is None:
        return []
    return [p.strip() for p in str(raw).split(";") if p.strip()]


def _citations(references: Any, links: Any, language: str,
               access_date: Optional[str], evidence_type: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = [{"type": t, "value": v}
                                 for t, v in (("reference", r) for r in _split_list(references))]
    for link in _split_list(links):
        kind = "doi" if ("doi.org" in link or link.lower().startswith("10.")) else "url"
        out.append({"type": kind, "value": link})
    out.append({"type": "language", "value": language})
    out.append({"type": "access_date", "value": access_date or "unrecorded"})
    out.append({"type": "evidence_type", "value": evidence_type})
    return out


# ---------------------------------------------------------------------------
# Raw schema (raw Bashkova–Rupper compilation, preserved as-is)
# ---------------------------------------------------------------------------

RAW_SCHEMA_FIELDS = (
    "glacier_id", "event_name", "lon", "lat", "glacier_name", "country",
    "rgi_o1_region", "rgi_o2_region", "rgi_region_name", "rgi_v7_id",
    "day", "month", "year", "date_min", "date_max", "hazard_type",
    "total_volume", "initial_volume", "large", "slope_detachment_zone",
    "triggers", "impact", "references", "links", "comments", "uncertainties",
)

RAW_ROW_HASH_FIELD = "raw_row_sha256"
EVENT_ID_PREFIX = "FE1-"

CSV_FIELDS = (
    "event_id", RAW_ROW_HASH_FIELD, "source_row_index", "event_name", "source_name",
    "source_lat", "source_lon", "source_precision", "source_coordinate_status",
    "impact_locations", "impact_precision", "impact_coordinate_status", "impact_raw",
    "country", "transboundary", "raw_day", "raw_month",
    "raw_year", "raw_date_min", "raw_date_max", "raw_date_string",
    "date_min", "date_max", "date_precision", "date_parse_notes",
    "volume_min_m3", "volume_max_m3", "volume_units", "volume_unit_confidence",
    "volume_source",
    "volume_uncertainty_raw", "mechanism_primary", "mechanism_alternatives",
    "mechanism_dissent", "mechanism_confidence", "mechanism_evidence_status",
    "mechanism_confirmation_scope",
    "mechanism_confirmed",
    "initially_misclassified", "glof_mislabel_flag", "trigger_evidence",
    "conditioning_evidence", "raw_triggers", "rgi7_id", "rgi60_id",
    "rgi60_crosswalk_status", "citations", "language", "access_date",
    "evidence_type", "observation_availability", "min_detectable_size_m3",
    "holdout_group", "holdout_group_basis", "duplicate_of", "cascade_group_id",
    "eligibility_status", "eligibility_reasons",
)


# ---------------------------------------------------------------------------
# Date policy
# ---------------------------------------------------------------------------

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
_SEASONS = {"summer", "fall", "autumn", "winter", "spring", "monsoon"}

_ISO_RE = re.compile(
    r"^(\d{4})-(\d{1,2})-(\d{1,2})"
    r"(?:[T ][0-9]{2}:[0-9]{2}"
    r"(?::[0-9]{2}(?:\.[0-9]+)?)?"
    r"(?:Z|[+-][0-9]{2}:?[0-9]{2})?)?$")
_DMY_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def parse_bound_string(raw: Any) -> tuple[Optional[date], str]:
    """Parse one raw bound string.  Returns (date|None, note).

    Only unambiguous parses are accepted: ISO YYYY-MM-DD first, then D/M/YYYY
    where the first component is > 12 (so it cannot be a month).  Slash dates
    with a leading component <= 12 are ambiguous and deliberately unresolved.
    """
    if raw is None:
        return None, "bound absent"
    s = str(raw).strip()
    if not s or s.lower() in ("nan", "none", "null", "unknown"):
        return None, "bound absent"
    m = _ISO_RE.fullmatch(s)
    if m:
        y, mo, d = (int(g) for g in m.groups())
        try:
            return date(y, mo, d), "iso"
        except ValueError:
            return None, f"invalid iso date {s!r}"
    m = _DMY_RE.fullmatch(s)
    if m:
        a, b, y = (int(g) for g in m.groups())
        if a > 12:  # unambiguous day-first
            try:
                return date(y, b, a), "d/m/y (unambiguous)"
            except ValueError:
                return None, f"invalid d/m/y date {s!r}"
        return None, f"ambiguous day/month order in {s!r}; left unresolved"
    return None, f"unparseable date string {s!r}"


def normalize_raw_date(record: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize raw date fields under the frozen date policy.

    Returns a dict with keys: raw_date_string, date_min, date_max,
    date_precision, date_parse_notes."""
    day = _to_int(record.get("day"))
    year = _to_int(record.get("year"))
    month_raw = record.get("month")
    month_name = str(month_raw).strip().lower() if month_raw is not None else ""
    notes: list[str] = []

    raw_parts = []
    for k in ("day", "month", "year", "date_min", "date_max"):
        v = record.get(k)
        if v is not None and str(v).strip() != "":
            raw_parts.append(f"{k}={v}")
    raw_string = "; ".join(raw_parts)

    def finish(bmin: Optional[date], bmax: Optional[date],
               precision: Any) -> dict[str, Any]:
        return {
            "raw_date_string": raw_string,
            "date_min": bmin.isoformat() if bmin else None,
            "date_max": bmax.isoformat() if bmax else None,
            "date_precision": precision.value,
            "date_parse_notes": notes,
        }

    exact_shape = (day is not None and year is not None
                   and month_name in _MONTHS)
    if exact_shape:
        try:
            d = date(year, _MONTHS[month_name], day)  # type: ignore[arg-type]
            if 1 <= day <= 31:  # type: ignore[operator]
                parsed_bounds: dict[str, date] = {}
                for key in ("date_min", "date_max"):
                    raw_bound = record.get(key)
                    if raw_bound is None or str(raw_bound).strip() == "":
                        continue
                    bound, bound_note = parse_bound_string(raw_bound)
                    notes.append(f"{key}: {bound_note}")
                    if bound is None:
                        notes.append(
                            "exact date conflicts with an unparseable interval bound")
                        return finish(None, None, C.DatePrecision.UNRESOLVED)
                    parsed_bounds[key] = bound
                lower = parsed_bounds.get("date_min")
                upper = parsed_bounds.get("date_max")
                if lower is not None and upper is not None and upper < lower:
                    notes.append("exact date conflicts with reversed interval bounds")
                    return finish(None, None, C.DatePrecision.UNRESOLVED)
                if ((lower is not None and d < lower) or
                        (upper is not None and d > upper)):
                    notes.append("exact date conflicts with source interval bounds")
                    return finish(None, None, C.DatePrecision.UNRESOLVED)
                return finish(d, d, C.DatePrecision.EXACT_DAY)
        except ValueError:
            notes.append(f"malformed day value {day} for {month_name} {year}")
        return finish(None, None, C.DatePrecision.MALFORMED)

    # Source-supported interval from the two raw bound strings.
    bmin, n1 = parse_bound_string(record.get("date_min"))
    bmax, n2 = parse_bound_string(record.get("date_max"))
    notes.extend([n1, n2])
    if bmin is not None and bmax is not None:
        if bmax < bmin:
            notes.append("bound order reversed; unresolved")
            return finish(None, None, C.DatePrecision.UNRESOLVED)
        span = (bmax - bmin).days
        if span > C.MAX_SOURCE_SUPPORTED_INTERVAL_DAYS:
            notes.append(
                f"interval spans {span} days (> "
                f"{C.MAX_SOURCE_SUPPORTED_INTERVAL_DAYS}); kept in catalog, "
                f"ineligible for the validation gate")
        return finish(bmin, bmax, C.DatePrecision.INTERVAL)
    if bmin is not None or bmax is not None:
        notes.append("only one interval bound resolved; date left unresolved")
        return finish(None, None, C.DatePrecision.UNRESOLVED)

    # Month-only: calendar month bounds (bounding interval, never a point).
    if month_name in _MONTHS:
        if year is None:
            notes.append("month without year; unresolved")
            return finish(None, None, C.DatePrecision.UNRESOLVED)
        mo = _MONTHS[month_name]
        first = date(year, mo, 1)
        last = (date(year, 12, 31) if mo == 12
                else date(year, mo + 1, 1) - timedelta(days=1))
        notes.append("month-only record; calendar month bounds stored as a "
                     "bounding interval, never as a point date (no day-15 "
                     "substitution)")
        return finish(first, last, C.DatePrecision.MONTH_ONLY)

    # Season-only: never converted into an artificial date.
    if month_name in _SEASONS:
        notes.append(f"season-only record ({month_name!r}); bounds "
                     f"intentionally left unset")
        return finish(None, None, C.DatePrecision.SEASON_ONLY)

    # Malformed vs unresolved.
    if day is not None or year is not None or month_name:
        if day is not None and not 1 <= day <= 31:
            notes.append(f"malformed day value {day}")
        if year is not None and not 1800 <= year <= 2200:
            notes.append(f"malformed year value {year}")
        return finish(None, None, C.DatePrecision.MALFORMED)
    notes.append("no date information in raw record")
    return finish(None, None, C.DatePrecision.UNRESOLVED)


# ---------------------------------------------------------------------------
# Duplicate / cascade relationships
# ---------------------------------------------------------------------------

def link_duplicates_and_cascades(rows: list, *,
                                 cascade_window_days: int = 90) -> None:
    """Mark duplicate rows and cascade groups deterministically (in place).

    Duplicates require an exact raw-row hash, an explicit duplicate key, or a
    conservative same-source identity (same source glacier id, exact event
    name, year and near-identical location).  A rounded location alone never
    merges distinct named events: cascades and contemporaneous failures are
    preserved as separate rows.

    Cascades: non-duplicate rows sharing a glacier identity (rgi7_id or source
    name) whose date_min values fall within ``cascade_window_days`` share a
    deterministic ``cascade_group_id``."""
    def year_of(r):
        try:
            return int(str(r.get("raw_year"))) if r.get("raw_year") is not None else None
        except (TypeError, ValueError):
            return None

    def loc_key(r):
        lat, lon = r.get("source_lat"), r.get("source_lon")
        if lat is None or lon is None:
            return None
        return (round(float(lat), 3), round(float(lon), 3))

    seen = {}
    for r in sorted(rows, key=lambda x: x["event_id"]):
        keys = []
        raw_hash = r.get("raw_row_sha256")
        if raw_hash:
            keys.append(("raw", raw_hash))
        explicit = _meaningful_text(r.get("duplicate_key"))
        if explicit:
            keys.append(("explicit", explicit.lower()))
        nm = _meaningful_text(r.get("event_name")).lower()
        source_id = _meaningful_text(r.get("rgi7_id")) or _meaningful_text(
            r.get("source_name"))
        if nm and source_id and year_of(r) is not None:
            keys.append(("source_name_year", source_id, nm, year_of(r)))
        lk = loc_key(r)
        if lk is not None and source_id and year_of(r) is not None:
            keys.append(("source_location_year", source_id, nm, lk, year_of(r)))
        canon = None
        for k in keys:
            if k in seen and seen[k] != r["event_id"]:
                canon = seen[k]
                break
        if canon is None:
            for k in keys:
                seen.setdefault(k, r["event_id"])
        else:
            r["duplicate_of"] = canon

    by_glacier = {}
    for r in rows:
        if r.get("duplicate_of") is not None:
            continue
        gid = str(r.get("rgi7_id") or r.get("source_name") or "")
        if not gid:
            continue
        by_glacier.setdefault(gid, []).append(r)
    for gid in sorted(by_glacier):
        members = sorted(by_glacier[gid], key=lambda x: x["event_id"])
        dated = []
        for m in members:
            v = m.get("date_min")
            try:
                dated.append((m, date.fromisoformat(str(v)[:10])) if v else None)
            except ValueError:
                dated.append(None)
        dated = [pair for pair in dated if pair is not None]
        for i, (m, dm) in enumerate(dated):
            for other, do in dated[i + 1:]:
                if abs((do - dm).days) <= cascade_window_days:
                    cgid = "CASC-" + hashlib_sha256(
                        "+".join(sorted([m["event_id"], other["event_id"]])))[:10]
                    m["cascade_group_id"] = m["cascade_group_id"] or cgid
                    other["cascade_group_id"] = other["cascade_group_id"] or cgid



# ---------------------------------------------------------------------------
# Eligibility adjudication (after holdout assignment and plan freeze)
# ---------------------------------------------------------------------------

def adjudicate_eligibility(rows: list) -> None:
    """Set eligibility_status / eligibility_reasons / mechanism_confirmed.

    Order-sensitive policy: holdout groups were already assigned and the plan
    frozen BEFORE this function runs; this function never regroups rows."""
    cohort = set(C.CATALOG_ELIGIBILITY_RULES["mechanism"]["ice_rock_cohort"])
    lat_lo, lat_hi = C.CATALOG_ELIGIBILITY_RULES["geography"]["hma_lat_bounds"]
    lon_lo, lon_hi = C.CATALOG_ELIGIBILITY_RULES["geography"]["hma_lon_bounds"]
    for r in rows:
        reasons: list = []
        # Duplicate rows stay in the catalog but cannot count toward the gate.
        if r.get("duplicate_of") is not None:
            reasons.append("DUPLICATE_OF:" + r["duplicate_of"])
        # Date policy.
        if r["date_precision"] == C.DatePrecision.EXACT_DAY.value:
            pass
        elif r["date_precision"] == C.DatePrecision.INTERVAL.value:
            dmin, dmax = r.get("date_min"), r.get("date_max")
            if dmin and dmax:
                span = (date.fromisoformat(dmax) - date.fromisoformat(dmin)).days
                if span > C.MAX_SOURCE_SUPPORTED_INTERVAL_DAYS:
                    reasons.append(f"DATE_INTERVAL_TOO_LONG:{span}d")
            else:
                reasons.append("DATE_UNRESOLVED")
        elif r["date_precision"] == C.DatePrecision.MONTH_ONLY.value:
            reasons.append("DATE_MONTH_ONLY")
        elif r["date_precision"] == C.DatePrecision.SEASON_ONLY.value:
            reasons.append("DATE_SEASON_ONLY")
        elif r["date_precision"] == C.DatePrecision.MALFORMED.value:
            reasons.append("DATE_MALFORMED")
        else:
            reasons.append("DATE_UNRESOLVED")
        # Geography.
        lat, lon = r.get("source_lat"), r.get("source_lon")
        if lat is None or lon is None:
            reasons.append("NO_SOURCE_COORDINATES")
        else:
            if not (lat_lo <= float(lat) <= lat_hi and lon_lo <= float(lon) <= lon_hi):
                reasons.append("OUTSIDE_HMA_BOUNDS")
        # Mechanism policy.
        if r["mechanism_primary"] not in cohort:
            reasons.append("MECHANISM_NOT_ICE_ROCK_COHORT:" + r["mechanism_primary"])
        evidence_status = r.get("mechanism_evidence_status")
        has_source_evidence = evidence_status == "SOURCE_CITATION_PRESENT"
        if not has_source_evidence:
            reasons.append("MECHANISM_NOT_SOURCE_CONFIRMED:NO_SOURCE_EVIDENCE")
        if r.get("mechanism_dissent"):
            reasons.append("MECHANISM_DISSENT")
        name = str(r.get("event_name") or "").lower()
        if any(t in name for t in C.THAME_EXCLUSION_NAMES):
            reasons.append("THAME_EXCLUDED_PRECURSOR_ANALOG")
        if r.get("glof_mislabel_flag"):
            reasons.append("GLOF_MISLABEL_FLAG")
        r["mechanism_confirmed"] = bool(
            r["mechanism_primary"] in cohort and has_source_evidence
            and not r.get("mechanism_dissent"))
        r["eligibility_reasons"] = sorted(set(reasons))
        r["eligibility_status"] = (C.OutputStatus.ELIGIBLE.value
                                   if not r["eligibility_reasons"]
                                   else C.OutputStatus.INELIGIBLE.value)


def evaluate_gate_a(rows: Sequence, holdout_plan: Mapping) -> dict:
    """A_CATALOG gate.  Never loosened by callers; a failed gate publishes the
    catalog plus gap report, permits descriptive mapping only, and blocks
    validation claims and C/D."""
    eligible = [r for r in rows if r["eligibility_status"] == C.OutputStatus.ELIGIBLE.value]
    elig_groups = sorted({r["holdout_group"] for r in eligible})
    unresolved_src = [r["event_id"] for r in eligible
                      if r.get("source_lat") is None
                      or r.get("source_lon") is None
                      or r.get("source_coordinate_status") == "MISSING"]
    text_only_impacts = [r["event_id"] for r in eligible
                         if r.get("impact_coordinate_status") == "TEXT_ONLY"]
    expected_plan_hash = sha256_canonical({
        k: v for k, v in holdout_plan.items() if k != "plan_sha256"
    })
    checks = {
        "min_eligible_events": {
            "threshold": C.GATE_A_MIN_ELIGIBLE_EVENTS,
            "observed": len(eligible),
            "passed": len(eligible) >= C.GATE_A_MIN_ELIGIBLE_EVENTS,
        },
        "min_eligible_holdout_groups": {
            "threshold": C.GATE_A_MIN_HOLDOUT_GROUPS,
            "observed": len(elig_groups),
            "passed": len(elig_groups) >= C.GATE_A_MIN_HOLDOUT_GROUPS,
        },
        "no_unresolved_source_coordinates": {
            "observed": len(unresolved_src),
            "passed": not unresolved_src,
            "detail": unresolved_src,
        },
        "impact_coordinate_status_explicit": {
            "observed_text_only": len(text_only_impacts),
            "passed": all(r.get("impact_coordinate_status") in
                           {"GEOCODED", "TEXT_ONLY", "MISSING"}
                           for r in eligible),
            "text_only_detail": text_only_impacts,
            "note": "text-only impacts are not treated as geocoded",
        },
        "no_mechanism_ambiguity": {
            "observed": sum(1 for r in eligible if r.get("mechanism_dissent")
                            or r["mechanism_primary"]
                            == C.MechanismClass.UNRESOLVED.value),
            "passed": all(not r.get("mechanism_dissent")
                          and r["mechanism_primary"]
                          != C.MechanismClass.UNRESOLVED.value
                          for r in eligible),
        },
        "holdout_plan_integrity": {
            "observed": holdout_plan.get("plan_sha256"),
            "passed": (holdout_plan.get("frozen_before_eligibility_filtering") is True
                       and holdout_plan.get("plan_sha256") == expected_plan_hash),
        },
    }
    gate = {
        "gate_id": C.GateId.A_CATALOG.value,
        "passed": all(c["passed"] for c in checks.values()),
        "checks": checks,
        "n_rows_total": len(rows),
        "n_eligible": len(eligible),
        "eligible_holdout_groups": elig_groups,
        "catalog_sha256": sha256_canonical(list(rows)),
        "holdout_plan_sha256": holdout_plan.get("plan_sha256"),
        "mechanism_validation": {
            "catalog_source_validated": True,
            "mechanism_independently_adjudicated": False,
            "scope": "SOURCE_CITATION_ONLY_NOT_INDEPENDENT_FIELD_ADJUDICATION",
        },
        "claim_scope": "source_supported_catalog_claims_only",
        "consequences_if_failed": [
            "publish adjudicated catalog and gap report",
            "descriptive mapping only",
            "validation claims blocked",
            "C/D blocked",
            "date and mechanism rules NOT loosened",
        ],
    }
    return bind_gate_artifact(gate)


# ---------------------------------------------------------------------------
# Pipeline driver + Phase A artifacts
# ---------------------------------------------------------------------------

A_ARTIFACTS = (
    "catalog_normalized.csv",
    "catalog_adjudication.json",
    "holdout_plan.json",
    "catalog_manifest.json",
    "catalog_gate.json",
    "controls_lock.json",
)


def build_catalog(raw_records: Sequence, *,
                  controls: ControlsConfig,
                  language: str = "en",
                  access_date: Optional[str] = None,
                  rgi60_crosswalk: Optional[Mapping] = None,
                  overrides: Optional[Mapping] = None) -> dict:
    """Full Phase A pipeline over preserved raw records.

    Steps (order is part of the contract):
      1. preserve raw rows; assign fixed holdout group to EVERY raw row;
      2. freeze (hash) the holdout plan before any date/mechanism filtering;
      3. normalize additively (raw rows untouched);
      4. link duplicates/cascades;
      5. adjudicate eligibility;
      6. evaluate A_CATALOG (gate failure publishes a gap report, never a
         loosened rule).
    """
    prepared = []
    for source_row_index, rec in enumerate(raw_records):
        row = {"_raw": dict(rec),
               "source_row_index": source_row_index,
               "holdout_group": None, "holdout_group_basis": None}
        gid, basis = assign_holdout_group(rec)
        row["holdout_group"], row["holdout_group_basis"] = gid, basis
        prepared.append(row)
    holdout_plan = freeze_holdout_plan(prepared)  # freeze BEFORE filtering

    rows = []
    for row in prepared:
        raw = row.pop("_raw")
        rows.append(normalize_record(
            raw, language=language, access_date=access_date,
            rgi60_crosswalk=rgi60_crosswalk,
            min_detectable_size_m3=controls.min_detectable_size_m3,
            overrides=overrides,
            source_row_index=row["source_row_index"],
            holdout_group=row["holdout_group"],
            holdout_group_basis=row["holdout_group_basis"]))
    rows.sort(key=lambda r: r["event_id"])
    link_duplicates_and_cascades(rows)
    adjudicate_eligibility(rows)
    gate = evaluate_gate_a(rows, holdout_plan)
    lock = create_controls_lock(controls)
    return {
        "rows": rows,
        "holdout_plan": holdout_plan,
        "gate": gate,
        "controls_lock": lock,
        "controls": controls,
    }


def write_phase_a_artifacts(result: Mapping, out_dir) -> dict:
    """Write the five fixed Phase A artifacts deterministically."""
    from pathlib import Path
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rows = sorted(result["rows"], key=lambda r: r["event_id"])

    csv_path = out / "catalog_normalized.csv"
    write_deterministic_csv(csv_path, CSV_FIELDS, rows)

    adjudication = {
        "framework_version": C.FRAMEWORK_VERSION,
        "mechanism_validation": result["gate"].get("mechanism_validation", {}),
        "holdout_plan": result["holdout_plan"],
        "rows": [{k: r[k] for k in CSV_FIELDS if k not in
                  ("impact_locations", "citations", "date_parse_notes",
                   "eligibility_reasons", "trigger_evidence",
                   "conditioning_evidence", "mechanism_alternatives")}
                 for r in rows],
    }
    adj_path = out / "catalog_adjudication.json"
    write_deterministic_json(adj_path, adjudication)

    holdout_path = out / "holdout_plan.json"
    write_deterministic_json(holdout_path, result["holdout_plan"])

    gate_path = out / "catalog_gate.json"
    write_deterministic_json(gate_path, result["gate"])

    lock_path = out / "controls_lock.json"
    write_deterministic_json(lock_path, result["controls_lock"].to_dict())

    manifest = build_manifest({
        csv_path.name: csv_path,
        adj_path.name: adj_path,
        holdout_path.name: holdout_path,
        gate_path.name: gate_path,
        lock_path.name: lock_path,
    }, manifest_type="artifact")
    man_path = out / "catalog_manifest.json"
    write_deterministic_json(man_path, manifest)
    return {"csv": csv_path, "adjudication": adj_path, "gate": gate_path,
            "holdout_plan": holdout_path, "controls_lock": lock_path,
            "manifest": man_path}


def hashlib_sha256(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
