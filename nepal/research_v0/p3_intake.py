"""P3 real HMAGLOFDB intake — byte-bound event labels from acquired bytes.

Chain of custody (R11.1): the acquisition zip and its provenance
records are byte-verified via ``verify_source_evidence`` BEFORE any
member byte is read; each parsed member's sha256 is cross-checked
against the retrieval record the acquirer wrote; the CSV is decoded
cp1252 per ``schema.ini`` (ANSI, no charset field — recorded, not
guessed silently: a decode failure is a hard intake error).

Outputs (all deterministic from the bound bytes):
* the six required intake reports (Nepal count, precision
  distribution, GL_ID/LakeDB_ID/G_ID coverage, recurrence, cascades,
  removed rows, post-2025 rows, actual bytes);
* ``EventLabelV0`` records for every loadable row (UNADJUDICATED —
  reviewer identities are owner-supplied, never invented);
* ``ObservationOpportunityV0`` records over the PDGL opportunity
  frame (each inventory lake x JJA 2001-2025, state UNKNOWN until
  observation frames are bound);
* ``ControlWindowV0`` records derived (never asserted) from the real
  opportunity state and event intervals;
* a ``HoldoutPlanV0`` whose groups are assigned BEFORE eligibility
  filtering, with cascade groups kept atomic;
* a linkage-uncertainty ledger: every excluded row is recorded with a
  reason — nothing is silently dropped.

Basin assignment follows the operative rule in
``docs/science/P3_BASIN_ASSIGNMENT_RULE_V0.md``: an explicit
river-name mapping table onto the predeclared basin universe, the
Nepal ``Province`` field as a cross-check (conflicts recorded, the
hydrological basin governs), and no agent-chosen coordinates.
"""
from __future__ import annotations

import calendar  # noqa: F401  (kept for month-width reporting)
import csv
import hashlib
import io
import json
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from .policy import derive_control_state
from .records import (
    ControlWindowV0, EventLabelV0, HoldoutPlanV0,
    ObservationOpportunityV0)
from .source_intake import build_source_manifest

#: Byte-bound artifact relpaths inside the P5 evidence root.
_ZIP_RELPATH = "glof-events/HMAGLOFDB-v1.3.0.zip"
_RETRIEVAL_RELPATH = "retrieval/retrieval_record_hmaglofdb.json"
_ANCHOR_RELPATH = "retrieval/anchor_derivation_record.json"

#: Zip member paths (verified against the retrieval record).
_MEMBER_MAIN = ("fidelsteiner-HMAGLOFDB-1d975de/Database/GLOFs/"
                "HMAGLOFDB.csv")
_MEMBER_REMOVED = ("fidelsteiner-HMAGLOFDB-1d975de/Database/GLOFs/"
                   "HMAGLOFDB_removed.csv")
_MEMBER_SCHEMA = ("fidelsteiner-HMAGLOFDB-1d975de/Database/GLOFs/"
                  "schema.ini")
_MEMBER_ENCODING = "cp1252"  # schema.ini ANSI; decode failure is fatal

_SOURCE_ID = "icimod_hmaglofdb_v1_3_0"
_SOURCE_VERSION = "1.3.0"

_NA = {"NA", ""}
_DAY = 86400

#: Explicit river-name -> predeclared-basin-universe mapping.  Names
#: are whitespace-normalized ``River_Basin`` strings.  This table maps
#: only rivers whose parent basin is unambiguous; every other river is
#: ledger-recorded as outside the operative universe (never dropped
#: silently).  Sub-basin granularity is retained in the ledger via the
#: raw source value; the operative regime frame is the parent basin.
RIVER_BASIN_TO_UNIVERSE = {
    # Koshi system (Saptakoshi), incl. Tibetan headwaters
    "dudh koshi": "koshi",
    "tamor": "koshi",
    "arun": "koshi",
    "tama koshi": "koshi",
    "poiqu": "koshi",      # Sun Koshi headwater (Tibet)
    "pumqu": "koshi",      # Arun tributary system (Tibet)
    # Karnali system (Ghaghara)
    "humla": "karnali",
    "mugu karnali": "karnali",
    "bheri": "karnali",
    "dhauliganga": "karnali",
    "west seti": "karnali",
    "karnali": "karnali",
    # Gandaki system (Narayani)
    "kali gandaki": "gandaki",
    "marsyangdi": "gandaki",
    "upper mustang": "gandaki",   # Kali Gandaki headwaters
    "budhi gandaki": "gandaki",
    "gandaki": "gandaki",
    "seti": "gandaki",            # Pokhara Seti (West Seti above)
    # Bagmati system
    "melamchi": "bagmati",        # Indrawati/Bagmati tributary
}

#: Nepal province expected for each mapped basin (cross-check only —
#: the hydrological basin governs; conflicts are ledger-recorded).
_PROVINCE_CROSSCHECK = {
    "koshi": {"Koshi", "Province No. 1", ""},
    "karnali": {"Karnali", ""},
    "gandaki": {"Gandaki", ""},
    "bagmati": {"Bagmati", ""},
}

#: Placeholder values that occupy GL_ID where no glacial-lake ID
#: exists (Ephemeral 210 / No lake 10 / Not mapped 146 / Unknown 2 in
#: the acquired v1.3.0 bytes — counted, never treated as lake IDs).
_GL_PLACEHOLDERS = {"Ephemeral", "No lake", "Not mapped", "Unknown"}

#: Opportunity frame: PDGL inventory lakes x JJA seasons.
_OPP_PLATFORM = "satellite_optical_frames_unbound"
_OPP_STATE = "UNKNOWN"
_EMBARGO_SECONDS = 1209600.0  # 14 d — recorded rationale: day-precision
# event intervals plus the earliest admissible horizons make 14 d the
# narrow embargo that still separates adjacent seasons across splits.


def _norm(value: str) -> str:
    """Whitespace-normalize a source string (cp1252 artifacts)."""
    return " ".join(str(value).split())


def _utc(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(iso: str) -> float:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


@dataclass
class HMAGLOFDBIntake:
    """Complete P3 intake package built from verified bytes."""

    report: dict[str, Any]
    events: list[EventLabelV0] = field(default_factory=list)
    opportunities: list[ObservationOpportunityV0] = field(
        default_factory=list)
    controls: list[ControlWindowV0] = field(default_factory=list)
    holdout: HoldoutPlanV0 | None = None
    ledger: list[dict[str, Any]] = field(default_factory=list)

    def package_dict(self) -> dict[str, Any]:
        """Serializable evidence package (validated records only)."""
        problems = [e.problems() for e in self.events if e.problems()]
        if problems:
            raise ValueError(
                f"{len(problems)} event labels failed validation: "
                f"{problems[0]}")
        bad_opp = [o.problems() for o in self.opportunities
                   if o.problems()]
        if bad_opp:
            raise ValueError(
                f"{len(bad_opp)} opportunities failed validation: "
                f"{bad_opp[0]}")
        bad_ctrl = [c.problems() for c in self.controls if c.problems()]
        if bad_ctrl:
            raise ValueError(
                f"{len(bad_ctrl)} controls failed validation: "
                f"{bad_ctrl[0]}")
        if self.holdout is None or self.holdout.problems():
            raise ValueError(
                "holdout plan invalid: "
                f"{self.holdout.problems() if self.holdout else None}")
        return {
            "schema": "P3_EVENT_PACKAGE_V0",
            "source_id": _SOURCE_ID,
            "source_version": _SOURCE_VERSION,
            "report": self.report,
            "event_labels": [e.to_dict() for e in self.events],
            "observation_opportunities": [
                o.to_dict() for o in self.opportunities],
            "controls": [c.to_dict() for c in self.controls],
            "holdout_plan": self.holdout.to_dict(),
            "linkage_uncertainty_ledger": self.ledger,
        }


def _verified_bytes(evidence_root: Path) -> tuple[
        bytes, dict[str, Any], dict[str, Any]]:
    """Byte-verify the acquisition zip and provenance records BEFORE
    any zip member is read.  The zip's declared digest comes from the
    acquirer's retrieval record — never recomputed from the current
    bytes — so a post-acquisition swap fails verification; the two
    provenance records themselves are bound at their current bytes.
    Returns (zip_bytes, retrieval_record, anchor_record)."""
    from ._hashing import read_evidence_file, verify_source_evidence

    root_resolved = Path(evidence_root).resolve()
    retrieval_raw = read_evidence_file(root_resolved, _RETRIEVAL_RELPATH)
    anchor_raw = read_evidence_file(root_resolved, _ANCHOR_RELPATH)
    retrieval = json.loads(retrieval_raw.decode("utf-8"))
    anchor = json.loads(anchor_raw.decode("utf-8"))
    zip_sha = retrieval.get("sha256")
    if not isinstance(zip_sha, str) or len(zip_sha) != 64 or \
            not all(c in "0123456789abcdef" for c in zip_sha):
        raise ValueError(
            "retrieval record lacks a 64-hex zip sha256 — no "
            "acquirer-bound digest to verify the payload against")
    manifest = build_source_manifest(
        evidence_root,
        source_id=_SOURCE_ID,
        source_version=_SOURCE_VERSION,
        source_files=[
            {"relpath": _ZIP_RELPATH, "sha256": zip_sha},
            {"relpath": _RETRIEVAL_RELPATH,
             "sha256": hashlib.sha256(retrieval_raw).hexdigest()},
            {"relpath": _ANCHOR_RELPATH,
             "sha256": hashlib.sha256(anchor_raw).hexdigest()}],
        units=["glof-events"],
        feature_allowlist=["t2m", "d2m", "u10", "v10", "tp", "sf", "sd"],
        lineage=("P5-A acquisition: HMAGLOFDB v1.3.0 "
                 "(RDS DOI 10.26066/RDS.1973283), verified intake"))
    problems = verify_source_evidence(manifest)
    if problems:
        raise ValueError(
            "evidence verification failed before parse: "
            + "; ".join(problems))
    zip_bytes = read_evidence_file(root_resolved, _ZIP_RELPATH)
    return zip_bytes, retrieval, anchor


def _member_digests(zip_bytes: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        return {info.filename: hashlib.sha256(zf.read(info.filename))
                .hexdigest() for info in zf.infolist()
                if not info.is_dir()}


def _crosscheck_members(zip_bytes: bytes, retrieval: Mapping[str, Any],
                        problems: list[str]) -> None:
    """Every parsed member's sha256 must equal the acquirer-recorded
    member digest — the zip-level binding alone is not sufficient
    because the retrieval record is the published member manifest."""
    declared = {m["name"]: m["sha256"]
                for m in retrieval.get("members", [])}
    live = _member_digests(zip_bytes)
    for name in (_MEMBER_MAIN, _MEMBER_REMOVED, _MEMBER_SCHEMA):
        if name not in declared:
            problems.append(
                f"member {name!r} absent from the retrieval record — "
                "unrecorded bytes may not be parsed")
        elif declared[name] != live.get(name):
            problems.append(
                f"member {name!r} sha256 mismatch against the retrieval "
                "record — quarantine, never parse")




def _read_member(zip_bytes: bytes, member: str) -> list[dict[str, str]]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        raw = zf.read(member)
    try:
        text = raw.decode(_MEMBER_ENCODING)
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"member {member!r} does not decode as {_MEMBER_ENCODING} "
            f"(schema.ini ANSI): {exc}") from exc
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ValueError(f"member {member!r} parsed to zero rows")
    return rows


def _derive_timing(row: Mapping[str, str],
                   ledger: list[dict[str, Any]]
                   ) -> tuple[str, str, str] | None:
    """Deterministic interval derivation from Year_exact/Year_approx,
    Month, Day.  Returns (start, end, precision) or None (ledgered)."""
    gf = row["GF_ID"]
    year_s = (row["Year_exact"] if row["Year_exact"] not in _NA
              else (row["Year_approx"]
                    if row["Year_approx"] not in _NA else ""))
    if year_s == "":
        ledger.append({"gf_id": gf, "reason": "unresolved_timing",
                       "detail": "no Year_exact and no Year_approx"})
        return None
    try:
        year = int(year_s)
    except ValueError:
        ledger.append({"gf_id": gf, "reason": "unparseable_year",
                       "detail": f"year {year_s!r}"})
        return None
    month_s = row["Month"] if row["Month"] not in _NA else ""
    day_s = row["Day"] if row["Day"] not in _NA else ""
    try:
        if month_s and day_s:
            start = datetime(year, int(month_s), int(day_s),
                             tzinfo=timezone.utc)
            end = start + timedelta(days=1)
            return _utc(start), _utc(end), "day"
        if month_s:
            month = int(month_s)
            start = datetime(year, month, 1, tzinfo=timezone.utc)
            ny, nm = ((year + 1, 1) if month == 12 else (year, month + 1))
            end = datetime(ny, nm, 1, tzinfo=timezone.utc)
            # Class-consistent declaration (C14): a 28-30 day calendar
            # month measures INTERVAL_8_30D and must be declared
            # 'interval'; a 31-day month measures COARSE and keeps
            # 'month'.  The basis string preserves the source semantics
            # (calendar-month bracket) either way.
            width = (end - start).total_seconds()
            precision = "interval" if width <= 30 * _DAY else "month"
            return _utc(start), _utc(end), precision
        start = datetime(year, 1, 1, tzinfo=timezone.utc)
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        return _utc(start), _utc(end), "year"
    except ValueError as exc:
        ledger.append({"gf_id": gf, "reason": "unparseable_date",
                       "detail": f"{year_s}-{month_s}-{day_s}: {exc}"})
        return None


def _derive_basin(row: Mapping[str, str],
                  ledger: list[dict[str, Any]]) -> str | None:
    """Explicit-table basin assignment with the Province cross-check.
    The hydrological basin governs; conflicts are ledger-recorded."""
    gf = row["GF_ID"]
    river = _norm(row["River_Basin"]).lower()
    basin = RIVER_BASIN_TO_UNIVERSE.get(river)
    if basin is None:
        ledger.append({
            "gf_id": gf, "reason": "basin_outside_operative_universe",
            "detail": f"River_Basin {row['River_Basin']!r}"})
        return None
    province = _norm(row["Province"])
    if row["Country"] == "Nepal" and \
            province not in _PROVINCE_CROSSCHECK[basin]:
        ledger.append({
            "gf_id": gf, "reason": "province_conflict",
            "detail": f"basin {basin} but Province {province!r} — "
                      "hydrological basin governs (rule doc section 3)"})
    return basin


def _recurrence_series(
        rows: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Recurrent events at the same lake (Repeat == 'Y', identical
    reported lake coordinates) form one atomic cascade group.  Returns
    gf_id -> {cascade_group_id, parent_event_id}."""
    lakes: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if row["Repeat"] == "Y":
            lakes.setdefault(
                (row["Lat_lake"], row["Lon_lake"]), []).append(row)
    series: dict[str, dict[str, str]] = {}
    for _key, members in lakes.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda r: (r["_interval_start"],
                                    int(r["GF_ID"])))
        root = members[0]
        gid = f"recur:{root['GF_ID']}"
        for row in members:
            series[row["GF_ID"]] = {
                "cascade_group_id": gid,
                "parent_event_id": ("" if row is root else
                                    f"{_SOURCE_ID}:{_SOURCE_VERSION}:"
                                    f"{root['GF_ID']}")}
    return series


def _build_labels(
        rows: list[dict[str, Any]],
        series: dict[str, dict[str, str]]
) -> tuple[list[EventLabelV0], list[dict[str, Any]], dict[str, int]]:
    """EventLabelV0 per loadable row; excluded rows are ledgered."""
    labels: list[EventLabelV0] = []
    ledger: list[dict[str, Any]] = []
    by_basin: dict[str, int] = {}
    nepal_loadable = 0
    for row in rows:
        timing = _derive_timing(row, ledger)
        if timing is None:
            continue
        basin = _derive_basin(row, ledger)
        if basin is None:
            continue
        start, end, precision = timing
        width = _epoch(end) - _epoch(start)
        gf = row["GF_ID"]
        s = series.get(gf, {})
        labels.append(EventLabelV0(
            event_id=f"{_SOURCE_ID}:{_SOURCE_VERSION}:{gf}",
            vertical_id="glof",
            source_id=_SOURCE_ID,
            source_version=_SOURCE_VERSION,
            event_time_start=start,
            event_time_end=end,
            uncertainty_seconds=float(width),
            event_time_precision=precision,
            event_time_basis=(
                "HMAGLOFDB v1.3.0 Year_exact/Year_approx+Month+Day "
                f"({precision} precision)"),
            geometry_role="lake_point",
            coordinate_uncertainty="lake_centroid_as_published",
            latitude=(float(row["Lat_lake"])
                      if row["Lat_lake"] not in _NA else None),
            longitude=(float(row["Lon_lake"])
                       if row["Lon_lake"] not in _NA else None),
            basin_id=basin,
            cascade_group_id=s.get("cascade_group_id", ""),
            parent_event_id=s.get("parent_event_id", ""),
            adjudication_state="UNADJUDICATED",
            adjudication_notes="",
            reviewer_ids=()))
        by_basin[basin] = by_basin.get(basin, 0) + 1
        if row["Country"] == "Nepal":
            nepal_loadable += 1
    counts = {"nepal_loadable": nepal_loadable}
    counts.update(by_basin)
    return labels, ledger, counts




def _build_opportunities_and_controls(
        anchor: Mapping[str, Any],
        events: list[EventLabelV0],
        ledger: list[dict[str, Any]]) -> tuple[
            list[ObservationOpportunityV0], list[ControlWindowV0]]:
    """PDGL opportunity frame x JJA seasons 2001-2025; control states
    are derived from the real opportunity state and event intervals —
    never caller-asserted.  With no observation frames bound this
    cycle every opportunity is UNKNOWN and every control is honestly
    CENSORED_OR_AMBIGUOUS (a NEGATIVE control requires an
    OBSERVED_FULL opportunity)."""
    coverage = anchor.get("basin_coverage", {})
    gl_ids: list[str] = []
    for basin_record in coverage.values():
        gl_ids.extend(basin_record.get("gl_ids", []))
    if not gl_ids:
        ledger.append({"gf_id": "", "reason": "opportunity_frame_empty",
                       "detail": "anchor record carries no PDGL gl_ids"})
    intervals = [{"start": _epoch(e.event_time_start),
                  "end": _epoch(e.event_time_end)} for e in events]
    opportunities: list[ObservationOpportunityV0] = []
    controls: list[ControlWindowV0] = []
    for gl in sorted(gl_ids):
        unit = f"pdgl:{gl}"
        for year in range(2001, 2026):
            start = datetime(year, 6, 1, tzinfo=timezone.utc)
            end = datetime(year, 10, 1, tzinfo=timezone.utc)
            opp_id = f"opp:{unit}:{year}"
            opportunities.append(ObservationOpportunityV0(
                opportunity_id=opp_id,
                unit_id=unit,
                platform=_OPP_PLATFORM,
                window_start=_utc(start),
                window_end=_utc(end),
                coverage_fraction=None,
                state=_OPP_STATE,
                source_id="",
                source_as_of="",
                frame_ids=()))
            derived = derive_control_state(
                start.timestamp(), end.timestamp(), intervals,
                opportunity_state=_OPP_STATE)
            controls.append(ControlWindowV0(
                control_id=f"ctrl:{opp_id}",
                unit_id=unit,
                window_start=_utc(start),
                window_end=_utc(end),
                opportunity_id=opp_id,
                opportunity_state=_OPP_STATE,
                state=derived.value))
    return opportunities, controls



def _build_holdout(events: list[EventLabelV0]) -> HoldoutPlanV0:
    """Basin-grouped holdout assigned BEFORE eligibility filtering.

    Splits: test = {koshi, gandaki} (>=2 named evaluation regions),
    validation = {karnali}, train = every other observed group.
    Cascade groups are atomic by construction — members share a lake
    and therefore a basin group.
    """
    assignments: dict[str, str] = {}
    groups: set[str] = set()
    for e in events:
        assignments[e.event_id] = e.basin_id
        groups.add(e.basin_id)
    test = tuple(sorted(g for g in groups if g in ("koshi", "gandaki")))
    validation = tuple(sorted(g for g in groups if g == "karnali"))
    train = tuple(sorted(groups - set(test) - set(validation)))
    return HoldoutPlanV0(
        holdout_plan_id="p3-hmaglofdb-basin-holdout-v0",
        assignment_rule="basin",
        train_groups=train,
        validation_groups=validation,
        test_groups=test,
        event_assignments=assignments,
        evaluation_region_names=test,
        assigned_before_filtering=True,
        test_locked=True,
        embargo_seconds=_EMBARGO_SECONDS)


def _parse_report(
        rows: list[dict[str, str]],
        removed: list[dict[str, str]],
        zip_len: int, member_sizes: Mapping[str, int],
        series: Mapping[str, Mapping[str, str]],
        ledger: list[dict[str, Any]],
        basin_counts: Mapping[str, int],
        nepal_loadable: int) -> dict[str, Any]:
    """The six required intake reports, computed over the FULL row
    census — not only the loadable subset."""
    nepal = sum(1 for r in rows if r["Country"] == "Nepal")
    precision: dict[str, int] = {}
    for r in rows:
        if r["Year_exact"] in _NA and r["Year_approx"] in _NA:
            p = "unresolved"
        elif r["Month"] not in _NA and r["Day"] not in _NA:
            p = "day"
        elif r["Month"] not in _NA:
            p = "month"
        else:
            p = "year"
        precision[p] = precision.get(p, 0) + 1
    gl_valid = sum(1 for r in rows if r["GL_ID"].startswith("GL"))
    gl_placeholder = sum(
        1 for r in rows
        if r["GL_ID"] not in _NA and r["GL_ID"] in _GL_PLACEHOLDERS)
    gl_na = sum(1 for r in rows if r["GL_ID"] in _NA)
    lake_db = sum(1 for r in rows if r["LakeDB_ID"] not in _NA)
    g_ids = sum(1 for r in rows if r["G_ID"] not in _NA)
    repeat_y = sum(1 for r in rows if r["Repeat"] == "Y")
    main_gfs = {r["GF_ID"] for r in rows}
    removed_overlap = sum(1 for r in removed if r["GF_ID"] in main_gfs)
    post_2025 = sum(1 for r in rows
                    if r["Year_exact"] not in _NA
                    and int(r["Year_exact"]) > 2025)
    z_suffix = any(not r["GF_ID"].isdigit() for r in rows)
    ledger_reasons: dict[str, int] = {}
    for entry in ledger:
        ledger_reasons[entry["reason"]] = (
            ledger_reasons.get(entry["reason"], 0) + 1)
    return {
        "actual_bytes": {
            "zip": zip_len,
            "main_csv": member_sizes[_MEMBER_MAIN],
            "removed_csv": member_sizes[_MEMBER_REMOVED],
            "encoding": _MEMBER_ENCODING,
        },
        "row_census": {"total": len(rows), "nepal_count": nepal,
                       "nepal_loadable": nepal_loadable},
        "precision_distribution": precision,
        "id_coverage": {
            "GL_ID_valid": gl_valid,
            "GL_ID_placeholder": gl_placeholder,
            "GL_ID_na": gl_na,
            "LakeDB_ID_non_na": lake_db,
            "G_ID_non_na": g_ids,
            "gf_id_z_suffix_present": z_suffix,
        },
        "recurrence": {"repeat_y": repeat_y,
                       "repeat_n": len(rows) - repeat_y},
        "cascades": {
            "rule": ("Repeat=='Y' events sharing identical reported "
                     "lake coordinates form one atomic cascade group"),
            "groups": len({v["cascade_group_id"]
                           for v in series.values()}),
            "events_in_groups": len(series),
        },
        "removed_rows": {"count": len(removed),
                         "gf_ids_also_in_main": removed_overlap},
        "post_2025_rows": post_2025,
        "basin_assignment": dict(basin_counts),
        "ledger_reasons": ledger_reasons,
    }


def run_intake(evidence_root: Path) -> HMAGLOFDBIntake:
    """Full P3 intake over the verified acquisition bytes."""
    evidence_root = Path(evidence_root)
    zip_bytes, retrieval, anchor = _verified_bytes(evidence_root)
    problems: list[str] = []
    _crosscheck_members(zip_bytes, retrieval, problems)
    if problems:
        raise ValueError("; ".join(problems))
    main_rows = _read_member(zip_bytes, _MEMBER_MAIN)
    removed_rows = _read_member(zip_bytes, _MEMBER_REMOVED)
    ledger: list[dict[str, Any]] = []
    for row in main_rows:
        row["_interval_start"] = ""
    for row in main_rows:
        timing = _derive_timing(row, ledger)
        if timing is not None:
            row["_interval_start"] = timing[0]
    series = _recurrence_series(main_rows)
    labels, ledger2, counts = _build_labels(main_rows, series)
    ledger.extend(ledger2)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        member_sizes = {info.filename: info.file_size
                        for info in zf.infolist() if not info.is_dir()}
    report = _parse_report(main_rows, removed_rows, len(zip_bytes),
                           member_sizes, series, ledger, counts,
                           counts.get("nepal_loadable", 0))
    opportunities, controls = _build_opportunities_and_controls(
        anchor, labels, ledger)
    holdout = _build_holdout(labels)
    return HMAGLOFDBIntake(report=report, events=labels,
                           opportunities=opportunities, controls=controls,
                           holdout=holdout, ledger=ledger)

