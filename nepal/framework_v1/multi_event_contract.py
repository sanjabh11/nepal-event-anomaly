"""Multi-Event Contract (MEC) — Framework-native research envelope (MEC-*).

A strict, self-hashed contract for multi-event research design.  In this
tranche only **synthetic fixture envelopes** are permitted: synthetic event
IDs, synthetic catalogs, and source-supported date metadata.  No live event
catalog, dirty-main path, or production event identity may be bound.

Contract invariants enforced by :func:`verify_mec_envelope`:

* ``profile_id`` = ``SCIENCE_CONTRACT_T2_RESEARCH``, ``research_only`` and
  ``research_diagnostic_only`` true;
* non-empty source catalog identity with catalog ID, source digest, asset
  IDs, and processing-script digest; ``asset_ids`` entries may be strings
  or ``{asset_id, asset_sha256, bytes}`` records, and every asset ID must
  be unique;
* unique non-empty synthetic event IDs, each with an event group, a row
  hash, and a holdout group;
* ``row_sha256`` is always ``sha256_canonical(row_source)`` — the SHA-256
  of the canonical JSON serialization of the row bytes; when any event
  carries ``row_source`` the envelope declares ``row_schema_version``;
  in ``REAL_SOURCE_DESIGN`` mode ``row_source`` is mandatory — caller-only
  hashes cannot be verified;
* source-supported date specs only — ``day`` precision with provenance, or
  an explicit interval; artificial day-15 dates and any ``fallback``/imputed
  source are rejected unless the spec explicitly asserts
  ``exact_day15_supported`` from a real source;
* all six timing windows (acquisition, production, issue, publication,
  feature availability, target) are required on every event with
  consistent ordering: acquisition ends no later than feature
  availability, feature availability may not extend past the event date,
  the event date must fall inside the target window, and
  production <= issue <= publication;
* each event binds a WGS84 ``location`` that must fall inside the bbox
  declared for its region in ``validation_scope.regions``, which must
  contain at least two *distinct* bboxes — renamed single-box designs
  fail closed;
* holdout assignment must happen **before** eligibility filtering, with
  temporal embargo, geographic separation, and group-disjoint event
  separation metadata; ``holdout.assignment`` materializes the exact
  ``{event_id: holdout_group}`` map pinned by ``assignment_sha256``;
* in ``REAL_SOURCE_DESIGN`` mode the human-approved ``source_packet`` must
  be bound to a real file: ``packet_relpath`` resolves under
  ``packet_root`` to a regular non-symlink JSON file whose SHA-256 equals
  ``packet_sha256`` and whose approval fields match the envelope claim;
* label evidence is bound: ``adjudication`` carries ``ledger_sha256`` and
  unique ``reviewer_ids``; ``negative_controls`` carries
  ``artifact_sha256``;
* validation scope must span at least two geographic regions and two
  events — single-box and Langtang-only designs are rejected;
* no absolute paths in portable fields;
* canonical ``artifact_sha256`` self-hash verified on read.
"""
from __future__ import annotations

import json
import re
from datetime import date as _date
from pathlib import Path, PureWindowsPath
from typing import Any, Mapping, Optional, TypeGuard
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .provenance import (bind_artifact_envelope, sha256_canonical,
                         sha256_file, verify_artifact_envelope)
from .research_boundaries import lint_research_claims

MEC_ENVELOPE_TYPE = "MULTI_EVENT_CONTRACT_V1"
MEC_PROFILE_ID = "SCIENCE_CONTRACT_T2_RESEARCH"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SYNTH_ID_PREFIX = "SYNTH-"
_MODE_SYNTHETIC = "SYNTHETIC_FIXTURE"
_MODE_REAL = "REAL_SOURCE_DESIGN"
_MODES = (_MODE_SYNTHETIC, _MODE_REAL)
_SOURCE_PACKET_FIELDS = ("packet_id", "packet_sha256", "approved_by",
                         "human_approved", "approved_at")
_FORBIDDEN_SCOPE_TOKENS = ("langtang", "30km")
_FORBIDDEN_DATE_SOURCES = ("fallback", "default", "imputed", "synthesized")
_FORBIDDEN_CATALOG_IDS = ("hma_events_all", "hma", "real", "production")

_WINDOW_KEYS = ("acquisition_window", "production_time",
                "publication_time", "issue_time",
                "feature_availability_time", "target_window")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))


def _iter_strings(obj: Any, prefix: str = ""):
    if isinstance(obj, str):
        yield prefix, obj
    elif isinstance(obj, Mapping):
        for key, value in obj.items():
            yield from _iter_strings(value, f"{prefix}{key}.")
    elif isinstance(obj, (list, tuple)):
        for index, value in enumerate(obj):
            yield from _iter_strings(value, f"{prefix}[{index}].")


def _parse_date(value: Any) -> Optional[tuple[int, int, int]]:
    """Strict calendar parsing — only real Gregorian dates (``2015-02-31``
    is invalid, not merely out-of-range)."""
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        return None
    try:
        y, m, d = (int(p) for p in value.split("-"))
        _date(y, m, d)
        return (y, m, d)
    except ValueError:
        return None


def _check_timezone(tz: Any, label: str, problems: list[str]) -> None:
    """An optional timezone field must be a real IANA timezone name."""
    if tz is None:
        return
    if not isinstance(tz, str):
        problems.append(f"{label}.timezone must be an IANA timezone name")
        return
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        problems.append(f"{label}.timezone {tz!r} is not a valid IANA "
                        "timezone")


def _is_number(value: Any) -> TypeGuard[float]:
    """A real JSON number — bools are not coordinates or byte counts."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_packet_file(packet: Mapping[str, Any], relpath: str,
                       packet_root: Any, problems: list[str]) -> None:
    """Bind ``source_packet`` to a real approved file under packet_root.

    The relative path must stay inside the resolved root, may not traverse
    symlinks, and must resolve to a regular JSON file.  The file must not
    contain ``packet_sha256`` (a circular self-reference); every approval
    field it does carry must equal the envelope claim; and the envelope's
    ``packet_sha256`` must equal the file's SHA-256.
    """
    rel = Path(relpath)
    if (rel.is_absolute() or PureWindowsPath(relpath).is_absolute()
            or ".." in rel.parts or "\\" in relpath or "\x00" in relpath):
        problems.append("source_packet.packet_relpath is not a safe "
                        "relative path under packet_root")
        return
    try:
        root_resolved = Path(packet_root).resolve()
        target = (root_resolved / rel).resolve()
        target.relative_to(root_resolved)
    except (OSError, ValueError):
        problems.append("source_packet.packet_relpath escapes "
                        "packet_root")
        return
    # No path component under the root may be a symlink.
    cursor = root_resolved
    for part in rel.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            problems.append("source_packet.packet_relpath traverses a "
                            "symlink — packet files must be regular")
            return
    if not target.is_file():
        problems.append("source_packet.packet_relpath does not resolve "
                        "to a regular file under packet_root")
        return
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        problems.append("source_packet file is not readable JSON")
        return
    if not isinstance(data, Mapping):
        problems.append("source_packet file must contain a JSON object")
        return
    if "packet_sha256" in data:
        problems.append("source_packet file must not contain "
                        "packet_sha256 — a packet cannot hash itself")
    for field in ("packet_id", "approved_by", "human_approved",
                  "approved_at"):
        if field in data and data[field] != packet.get(field):
            problems.append(f"source_packet file {field} does not match "
                            "the envelope source_packet claim")
    if packet.get("packet_sha256") != sha256_file(target):
        problems.append("source_packet.packet_sha256 does not equal the "
                        "SHA-256 of the packet file under packet_root")


def _window_endpoints(value: Any, label: str, problems: list[str]
                      ) -> tuple[Optional[tuple], Optional[tuple]]:
    """Return (start, end) date tuples for a window spec, or record
    problems.  Accepts an ISO date string or {start, end} mapping."""
    if isinstance(value, str):
        d = _parse_date(value)
        if d is None:
            problems.append(f"{label} is not a valid ISO date")
            return None, None
        return d, d
    if isinstance(value, Mapping):
        _check_timezone(value.get("timezone"), label, problems)
        extra = set(value) - {"start", "end", "timezone"}
        if extra:
            problems.append(f"{label} has unknown keys {sorted(extra)}")
        start = _parse_date(value.get("start"))
        end = _parse_date(value.get("end"))
        if start is None or end is None:
            problems.append(f"{label} requires ISO start/end dates")
            return None, None
        if start > end:
            problems.append(f"{label} start is after end")
        return start, end
    problems.append(f"{label} must be an ISO date or {{start, end}}")
    return None, None


def _check_date_spec(spec: Any, label: str, problems: list[str]
                     ) -> Optional[tuple]:
    """Validate one event date_spec.  Returns the event date (or interval
    end) for post-event checks, or None on failure."""
    if not isinstance(spec, Mapping):
        problems.append(f"{label}.date_spec must be a mapping")
        return None
    source = spec.get("source")
    if not isinstance(source, str) or not source:
        problems.append(f"{label}.date_spec.source is required "
                        "(date provenance)")
    elif any(tok in source.lower() for tok in _FORBIDDEN_DATE_SOURCES):
        problems.append(f"{label}.date_spec.source {source!r} is a "
                        "fallback/imputed source; source-supported dates "
                        "are required")
    _check_timezone(spec.get("timezone"), f"{label}.date_spec", problems)
    precision = spec.get("precision")
    event_ref: Optional[tuple] = None
    if precision == "day":
        d = _parse_date(spec.get("date"))
        if d is None:
            problems.append(f"{label}.date_spec.date must be an ISO "
                            "YYYY-MM-DD date for day precision")
        else:
            if d[2] == 15 and spec.get("exact_day15_supported") is not True:
                problems.append(
                    f"{label}.date_spec uses an artificial day-15 date; "
                    "source-supported dates are required (day-15 is only "
                    "permitted when exact_day15_supported is asserted "
                    "from a non-fallback source)")
            event_ref = d
    elif precision == "interval":
        interval = spec.get("interval")
        if not isinstance(interval, Mapping):
            problems.append(f"{label}.date_spec.interval must be a "
                            "{start, end} mapping")
        else:
            start = _parse_date(interval.get("start"))
            end = _parse_date(interval.get("end"))
            if start is None or end is None:
                problems.append(f"{label}.date_spec.interval requires "
                                "ISO start/end dates")
            elif start > end:
                problems.append(f"{label}.date_spec.interval start is "
                                "after end")
            else:
                event_ref = end
    else:
        problems.append(f"{label}.date_spec.precision must be 'day' or "
                        f"'interval', got {precision!r}")
    return event_ref


def build_mec_envelope(payload: Mapping[str, Any], *,
                       packet_root: Any = None) -> dict[str, Any]:
    """Bind a MEC envelope with its canonical self-hash.  The payload is
    validated first — malformed envelopes cannot be bound.  In
    ``REAL_SOURCE_DESIGN`` mode ``packet_root`` is required so the source
    packet can be bound to its approved file."""
    ok, problems = verify_mec_envelope(payload, structural_only=True,
                                       packet_root=packet_root)
    if not ok:
        raise ValueError("MEC envelope is not valid: "
                         + "; ".join(problems[:5]))
    return bind_artifact_envelope(dict(payload))


def verify_mec_envelope(payload: Any, *,
                        structural_only: bool = False,
                        packet_root: Any = None
                        ) -> tuple[bool, list[str]]:
    """Fail-closed MEC verification.  ``structural_only`` skips the
    envelope self-hash check so :func:`build_mec_envelope` can validate a
    payload before binding it.  ``packet_root`` is the directory under
    which a real-mode ``source_packet.packet_relpath`` must resolve to a
    regular, non-symlink JSON packet file; it is required in
    ``REAL_SOURCE_DESIGN`` mode and ignored for synthetic fixtures."""
    problems: list[str] = []
    if not isinstance(payload, Mapping):
        return False, ["MEC envelope must be a mapping"]
    if not structural_only:
        ok, env_problems = verify_artifact_envelope(payload)
        if not ok:
            problems.extend(env_problems)
    # Recursive claim lint — nested forged READY/WARNING/operational
    # fields are rejected wherever they hide.
    ok_lint, lint_problems = lint_research_claims(payload)
    if not ok_lint:
        problems.extend(lint_problems)

    if payload.get("envelope_type") != MEC_ENVELOPE_TYPE:
        problems.append(f"envelope_type must be {MEC_ENVELOPE_TYPE!r}")
    if payload.get("profile_id") != MEC_PROFILE_ID:
        problems.append(f"profile_id must be {MEC_PROFILE_ID!r}")
    if payload.get("research_only") is not True:
        problems.append("research_only must be true")
    if payload.get("research_diagnostic_only") is not True:
        problems.append("research_diagnostic_only must be true")
    mode = payload.get("mode", _MODE_SYNTHETIC)
    if mode not in _MODES:
        problems.append(f"mode must be one of {_MODES}")
        mode = _MODE_SYNTHETIC

    if mode == _MODE_SYNTHETIC:
        if payload.get("synthetic_fixture") is not True:
            problems.append("synthetic_fixture must be true; live event "
                            "data is not permitted in this tranche")
    else:
        # REAL_SOURCE_DESIGN: schema exists so a future approved intake can
        # bind real catalogs, but it fails closed without a complete
        # human-approved source packet.  No file or network access happens
        # here — the packet is validated as a claim structure only.
        if payload.get("synthetic_fixture") is not False:
            problems.append("real mode requires synthetic_fixture=false")
        if packet_root is None:
            problems.append("real mode requires packet_root — the "
                            "source packet must be bound to an approved "
                            "file on disk; failing closed")
        packet = payload.get("source_packet")
        if not isinstance(packet, Mapping):
            problems.append("real mode requires a human-approved "
                            "source_packet — none supplied; failing "
                            "closed")
            packet = {}
        else:
            for field in _SOURCE_PACKET_FIELDS:
                if field not in packet:
                    problems.append(f"source_packet.{field} is required "
                                    "for real mode")
            if not isinstance(packet.get("packet_id"), str) or not                     packet.get("packet_id"):
                problems.append("source_packet.packet_id must be a "
                                "non-empty string")
            if not _is_sha256(packet.get("packet_sha256")):
                problems.append("source_packet.packet_sha256 must be a "
                                "lowercase SHA-256")
            if not isinstance(packet.get("approved_by"), str) or not                     packet.get("approved_by"):
                problems.append("source_packet.approved_by must be a "
                                "non-empty string")
            if packet.get("human_approved") is not True:
                problems.append("source_packet.human_approved must be "
                                "true — real-mode design without human "
                                "approval fails closed")
            if _parse_date(packet.get("approved_at")) is None:
                problems.append("source_packet.approved_at must be a "
                                "valid ISO calendar date")
            relpath = packet.get("packet_relpath")
            if not isinstance(relpath, str) or not relpath:
                problems.append("source_packet.packet_relpath is "
                                "required for real mode — a safe "
                                "relative path to the approved packet "
                                "file under packet_root")
            elif packet_root is not None:
                _check_packet_file(packet, relpath, packet_root,
                                   problems)

    catalog = payload.get("source_catalog")
    if not isinstance(catalog, Mapping):
        problems.append("source_catalog must be a mapping")
        catalog = {}
    else:
        cid = catalog.get("catalog_id")
        if not isinstance(cid, str) or not cid:
            problems.append("source_catalog.catalog_id is required")
        elif mode == _MODE_SYNTHETIC and any(
                tok in cid.lower() for tok in _FORBIDDEN_CATALOG_IDS):
            problems.append(f"source_catalog.catalog_id {cid!r} names a "
                            "live/production catalog; synthetic fixtures "
                            "only")
        if not _is_sha256(catalog.get("source_sha256")):
            problems.append("source_catalog.source_sha256 must be a "
                            "lowercase SHA-256")
        if not _is_sha256(catalog.get("processing_script_sha256")):
            problems.append("source_catalog.processing_script_sha256 must "
                            "be a lowercase SHA-256")
        assets = catalog.get("asset_ids")
        if not isinstance(assets, list) or not assets:
            problems.append("source_catalog.asset_ids must be a "
                            "non-empty list")
        else:
            seen_assets: set[str] = set()
            for ai, entry in enumerate(assets):
                alabel = f"source_catalog.asset_ids[{ai}]"
                if isinstance(entry, str):
                    aid = entry
                elif isinstance(entry, Mapping):
                    aid = entry.get("asset_id")
                    if not _is_sha256(entry.get("asset_sha256")):
                        problems.append(f"{alabel}.asset_sha256 must be "
                                        "a lowercase SHA-256")
                    nbytes = entry.get("bytes")
                    if not isinstance(nbytes, int) or isinstance(
                            nbytes, bool) or nbytes < 0:
                        problems.append(f"{alabel}.bytes must be a "
                                        "non-negative integer")
                else:
                    problems.append(
                        f"{alabel} must be an asset id string or a "
                        "{asset_id, asset_sha256, bytes} record")
                    continue
                if not isinstance(aid, str) or not aid:
                    problems.append(f"{alabel}.asset_id must be a "
                                    "non-empty string")
                elif aid in seen_assets:
                    problems.append(f"{alabel}.asset_id {aid!r} is a "
                                    "duplicate")
                else:
                    seen_assets.add(aid)

    # Declared collections for referential integrity — events may only
    # reference declared groups/holdouts.
    declared_groups = payload.get("event_groups")
    if not isinstance(declared_groups, list) or not declared_groups or \
            not all(isinstance(g, str) and g for g in declared_groups):
        problems.append("event_groups must be a non-empty list of "
                        "declared group ids")
        declared_groups = []
    group_set = set(g for g in declared_groups if isinstance(g, str))

    events = payload.get("events")
    if not isinstance(events, list) or not events:
        problems.append("events must be a non-empty list")
        events = []
    seen_ids: set[str] = set()
    seen_groups: set[str] = set()
    seen_holdouts: set[str] = set()
    seen_regions: set[str] = set()
    event_ids: list[str] = []
    any_row_source = False
    # (label, region_id, lat, lon) for events with valid geometry —
    # checked against validation_scope.regions bboxes below.
    event_locations: list[tuple[str, str, float, float]] = []
    for i, ev in enumerate(events):
        label = f"events[{i}]"
        if not isinstance(ev, Mapping):
            problems.append(f"{label} must be a mapping")
            continue
        eid = ev.get("event_id")
        if not isinstance(eid, str) or not eid:
            problems.append(f"{label}.event_id must be a non-empty string")
        else:
            if eid in seen_ids:
                problems.append(f"{label}.event_id {eid!r} is a duplicate")
            seen_ids.add(eid)
            event_ids.append(eid)
            if mode == _MODE_SYNTHETIC:
                if not eid.startswith(_SYNTH_ID_PREFIX) or \
                        ev.get("synthetic") is not True:
                    problems.append(
                        f"{label}.event_id {eid!r} is not a synthetic "
                        "fixture ID; live/production event identity is "
                        "forbidden")
            else:
                if eid.startswith(_SYNTH_ID_PREFIX) or \
                        ev.get("synthetic") is True:
                    problems.append(
                        f"{label}.event_id {eid!r} looks synthetic in a "
                        "real-mode envelope")
        rid = ev.get("region_id")
        if not isinstance(rid, str) or not rid:
            problems.append(f"{label}.region_id is required — event/"
                            "region counts are derived from records")
        else:
            seen_regions.add(rid)
        loc = ev.get("location")
        if not isinstance(loc, Mapping):
            problems.append(f"{label}.location must be a {{lat, lon}} "
                            "mapping — event geometry is bound to its "
                            "region bbox")
        else:
            lat = loc.get("lat")
            lon = loc.get("lon")
            if not _is_number(lat) or not (-90 <= lat <= 90):
                problems.append(f"{label}.location.lat must be a number "
                                "in [-90, 90]")
                lat = None
            if not _is_number(lon) or not (-180 <= lon <= 180):
                problems.append(f"{label}.location.lon must be a number "
                                "in [-180, 180]")
                lon = None
            if lat is not None and lon is not None and isinstance(
                    rid, str) and rid:
                event_locations.append(
                    (label, rid, float(lat), float(lon)))
        gid = ev.get("event_group_id")
        if not isinstance(gid, str) or not gid:
            problems.append(f"{label}.event_group_id is required")
        else:
            seen_groups.add(gid)
            if group_set and gid not in group_set:
                problems.append(f"{label}.event_group_id {gid!r} is not "
                                "a declared event group (orphan "
                                "reference)")
        hg = ev.get("holdout_group")
        if not isinstance(hg, str) or not hg:
            problems.append(f"{label}.holdout_group is required")
        else:
            seen_holdouts.add(hg)
        # Row bytes trump caller-supplied hashes: when the canonical
        # source row is present, row_sha256 must recompute to it exactly.
        row_source = ev.get("row_source")
        if row_source is not None:
            any_row_source = True
            if not isinstance(row_source, (Mapping, list, str)):
                problems.append(f"{label}.row_source must be canonical "
                                "row bytes/fields (mapping, list, or "
                                "string)")
            elif not _is_sha256(ev.get("row_sha256")) or \
                    sha256_canonical(row_source) != ev.get("row_sha256"):
                problems.append(f"{label}.row_sha256 does not equal "
                                "sha256 of the supplied canonical row "
                                "source bytes")
        elif mode == _MODE_REAL:
            problems.append(f"{label}.row_source is required in "
                            f"{_MODE_REAL} mode — a caller-supplied "
                            "hash without row bytes cannot be verified")
        elif not _is_sha256(ev.get("row_sha256")):
            problems.append(f"{label}.row_sha256 must be a lowercase "
                            "SHA-256")
        event_ref = _check_date_spec(ev.get("date_spec"), label, problems)
        windows = ev.get("windows")
        if not isinstance(windows, Mapping):
            problems.append(f"{label}.windows must be a mapping")
            windows = {}
        for wkey in windows:
            if wkey not in _WINDOW_KEYS:
                problems.append(f"{label}.windows.{wkey} is not a known "
                                "window key")
        endpoints: dict[str, tuple[Optional[tuple], Optional[tuple]]] = {}
        for wkey in _WINDOW_KEYS:
            if wkey not in windows:
                problems.append(f"{label}.windows.{wkey} is required")
                continue
            endpoints[wkey] = _window_endpoints(
                windows[wkey], f"{label}.windows.{wkey}", problems)
        # Ordering invariants: acquisition completes before features are
        # available; features never extend past the event date; the event
        # date sits inside the target window; and the publication chain
        # is monotone (production <= issue <= publication).
        acq_end = endpoints.get("acquisition_window", (None, None))[1]
        fat_end = endpoints.get(
            "feature_availability_time", (None, None))[1]
        if acq_end is not None and fat_end is not None \
                and acq_end > fat_end:
            problems.append(
                f"{label}.windows.acquisition_window ends after "
                "feature_availability_time — acquisition must complete "
                "before features become available")
        if fat_end is not None and event_ref is not None \
                and fat_end > event_ref:
            problems.append(
                f"{label}.windows.feature_availability_time ends "
                "after the event date — post-event features are "
                "forbidden")
        tgt = endpoints.get("target_window")
        if tgt is not None and event_ref is not None and \
                tgt[0] is not None and tgt[1] is not None and \
                not (tgt[0] <= event_ref <= tgt[1]):
            problems.append(
                f"{label}.windows.target_window does not contain the "
                "event date")
        prod_end = endpoints.get("production_time", (None, None))[1]
        iss_end = endpoints.get("issue_time", (None, None))[1]
        pub_end = endpoints.get("publication_time", (None, None))[1]
        if prod_end is not None and iss_end is not None \
                and prod_end > iss_end:
            problems.append(
                f"{label}.windows.production_time is after issue_time")
        if iss_end is not None and pub_end is not None \
                and iss_end > pub_end:
            problems.append(
                f"{label}.windows.issue_time is after publication_time")

    # When any event carries canonical row bytes, the envelope must
    # declare the canonicalization version; row_sha256 is
    # sha256_canonical(row_source) — SHA-256 over canonical JSON.
    if any_row_source:
        rsv = payload.get("row_schema_version")
        if not isinstance(rsv, str) or not rsv:
            problems.append("row_schema_version is required when events "
                            "carry row_source — it declares the "
                            "canonical JSON serialization version used "
                            "for row_sha256")

    holdout = payload.get("holdout")
    if not isinstance(holdout, Mapping):
        problems.append("holdout must be a mapping")
        holdout = {}
    else:
        if holdout.get("assigned_before_filtering") is not True:
            problems.append("holdout.assigned_before_filtering must be "
                            "true — holdout groups are fixed before "
                            "eligibility filtering")
        embargo = holdout.get("temporal_embargo_days")
        if not isinstance(embargo, int) or embargo < 0:
            problems.append("holdout.temporal_embargo_days must be a "
                            "non-negative integer")
        geo = holdout.get("geographic_holdout")
        if not isinstance(geo, Mapping) or not isinstance(
                geo.get("min_separation_km"), (int, float)) or \
                geo.get("min_separation_km", 0) <= 0:
            problems.append("holdout.geographic_holdout.min_separation_km "
                            "must be positive")
        sep = holdout.get("event_separation")
        if not isinstance(sep, Mapping) or sep.get("group_disjoint") \
                is not True:
            problems.append("holdout.event_separation.group_disjoint "
                            "must be true")
        declared_holdouts = holdout.get("holdout_groups")
        if not isinstance(declared_holdouts, list) or not \
                declared_holdouts or not all(
                    isinstance(h, str) and h for h in declared_holdouts):
            problems.append("holdout.holdout_groups must be a non-empty "
                            "list of declared holdout ids")
        else:
            holdout_set = set(declared_holdouts)
            for hg in seen_holdouts:
                if hg not in holdout_set:
                    problems.append(f"holdout_group {hg!r} is not a "
                                    "declared holdout group (orphan "
                                    "reference)")
        # Materialized assignment: the {event_id: holdout_group} map must
        # cover exactly the event set and be pinned by its canonical
        # hash, so the split cannot be silently recomputed.
        assignment = holdout.get("assignment")
        if not isinstance(assignment, Mapping):
            problems.append("holdout.assignment must be an "
                            "{event_id: holdout_group} mapping")
        else:
            if set(assignment.keys()) != set(event_ids):
                problems.append("holdout.assignment must cover exactly "
                                "the event set — no omissions, no extras")
            for ev in events:
                if not isinstance(ev, Mapping):
                    continue
                eid = ev.get("event_id")
                if isinstance(eid, str) and eid in assignment and \
                        assignment[eid] != ev.get("holdout_group"):
                    problems.append(
                        f"event {eid!r} holdout_group does not match "
                        "holdout.assignment")
            try:
                expected_assign = sha256_canonical(assignment)
            except (TypeError, ValueError):
                expected_assign = None
            if not _is_sha256(holdout.get("assignment_sha256")) or \
                    expected_assign != holdout.get("assignment_sha256"):
                problems.append("holdout.assignment_sha256 must equal "
                                "sha256_canonical(holdout.assignment)")

    scope = payload.get("validation_scope")
    if not isinstance(scope, Mapping):
        problems.append("validation_scope must be a mapping")
        scope = {}
    else:
        sid = scope.get("scope_id")
        if not isinstance(sid, str) or not sid:
            problems.append("validation_scope.scope_id is required")
        elif any(tok in sid.lower() for tok in _FORBIDDEN_SCOPE_TOKENS):
            problems.append(f"validation_scope.scope_id {sid!r} is a "
                            "single-box/Langtang-only design and is "
                            "rejected")
        declared_regions = scope.get("n_geographic_regions")
        if not isinstance(declared_regions, int) or declared_regions < 2:
            problems.append("validation_scope must span at least 2 "
                            "geographic regions")
        elif declared_regions != len(seen_regions):
            problems.append(
                f"validation_scope.n_geographic_regions "
                f"{declared_regions} does not equal the "
                f"{len(seen_regions)} regions present in the event "
                "records — counts are derived, not asserted")
        declared_min = scope.get("min_events")
        if not isinstance(declared_min, int) or declared_min < 2:
            problems.append("validation_scope must cover at least 2 "
                            "events")
        elif declared_min != len(events):
            problems.append(
                f"validation_scope.min_events {declared_min} does not "
                f"equal the {len(events)} event records — counts are "
                "derived, not asserted")
        regions = scope.get("regions")
        if not isinstance(regions, Mapping) or not regions:
            problems.append("validation_scope.regions must be a "
                            "{region_id: {bbox: [west, south, east, "
                            "north]}} mapping")
        else:
            bboxes: dict[str, tuple] = {}
            for rid, rspec in regions.items():
                bbox = rspec.get("bbox") if isinstance(
                    rspec, Mapping) else None
                if not isinstance(bbox, (list, tuple)) or \
                        len(bbox) != 4 or \
                        not all(_is_number(v) for v in bbox) or \
                        not (-180 <= bbox[0] <= bbox[2] <= 180) or \
                        not (-90 <= bbox[1] <= bbox[3] <= 90):
                    problems.append(
                        f"validation_scope.regions.{rid}.bbox must be "
                        "[west, south, east, north] with west <= east "
                        "and south <= north in valid ranges")
                    continue
                bboxes[str(rid)] = tuple(float(v) for v in bbox)
            if len(set(bboxes.values())) < 2:
                problems.append("validation_scope.regions must declare "
                                "at least 2 distinct bboxes — a single "
                                "physical box is a single-box design "
                                "however it is named")
            for label, rid, lat, lon in event_locations:
                if rid not in bboxes:
                    problems.append(f"{label}.region_id {rid!r} has no "
                                    "declared bbox in "
                                    "validation_scope.regions")
                    continue
                west, south, east, north = bboxes[rid]
                if not (west <= lon <= east and south <= lat <= north):
                    problems.append(
                        f"{label}.location ({lat}, {lon}) falls outside "
                        f"the declared bbox of region {rid!r}")

    label_spec = payload.get("label_spec")
    if not isinstance(label_spec, Mapping):
        problems.append("label_spec must be a mapping (Gate Spec v0 "
                        "machine-readable claim scope)")
    else:
        if not isinstance(label_spec.get("label_source"), str) or not \
                label_spec["label_source"]:
            problems.append("label_spec.label_source is required")
        adj = label_spec.get("adjudication")
        if not isinstance(adj, Mapping):
            problems.append("label_spec.adjudication must be a mapping")
        else:
            if adj.get("required") is not True:
                problems.append("label_spec.adjudication.required must "
                                "be true")
            if not isinstance(adj.get("independent_reviewers"), int) or \
                    adj["independent_reviewers"] < 1:
                problems.append("label_spec.adjudication."
                                "independent_reviewers must be >= 1")
            if not _is_sha256(adj.get("ledger_sha256")):
                problems.append("label_spec.adjudication.ledger_sha256 "
                                "must be a lowercase SHA-256 binding the "
                                "adjudication ledger")
            rev = adj.get("reviewer_ids")
            if not isinstance(rev, list) or not rev or not all(
                    isinstance(r, str) and r for r in rev):
                problems.append("label_spec.adjudication.reviewer_ids "
                                "must be a non-empty list of strings")
            elif len(set(rev)) != len(rev):
                problems.append("label_spec.adjudication.reviewer_ids "
                                "must be unique")
        nc = label_spec.get("negative_controls")
        if not isinstance(nc, Mapping):
            problems.append("label_spec.negative_controls must be a "
                            "mapping")
        else:
            if nc.get("required") is not True:
                problems.append("label_spec.negative_controls.required "
                                "must be true")
            if not isinstance(nc.get("n_controls"), int) or \
                    nc["n_controls"] < 1:
                problems.append("label_spec.negative_controls.n_controls "
                                "must be >= 1")
            if not _is_sha256(nc.get("artifact_sha256")):
                problems.append("label_spec.negative_controls."
                                "artifact_sha256 must be a lowercase "
                                "SHA-256 binding the controls artifact")

    if not isinstance(payload.get("claim_scope"), str) or not \
            payload["claim_scope"]:
        problems.append("claim_scope is required")

    for dotted, value in _iter_strings(
            {k: v for k, v in payload.items()
             if k != "artifact_sha256"}):
        if value.startswith("/"):
            problems.append(f"absolute path in portable MEC field "
                            f"{dotted!r}")
            break
    return (not problems), problems
