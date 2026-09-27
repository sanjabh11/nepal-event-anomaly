"""Station observability assessment for the seismic sidecar.

Fail-closed: ``build_station_observability`` derives the gate verdict
from DECLARED metadata and measured evidence — it never infers
coverage, never substitutes catalog metadata for waveform evidence,
and never emits ``OBSERVABLE`` with a recorded gate failure.  Missing
channels, missing response metadata, excessive gaps, failed SNR,
invalid timestamps, or unavailable waveform bytes all produce
``UNOBSERVABLE`` with every reason recorded.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from nepal.research_v0._hashing import sha256_canonical
from nepal.research_v0.policy import parse_strict_utc

from .contracts import (
    SEISMIC_WAVEFORM_RETROSPECTIVE, SeismicSidecarConfig,
    StationObservabilityV0)

_EARTH_RADIUS_KM = 6371.0088

#: Channel-suffix classes for orientation derivation.  FDSN broadband
#: codes end in E/N/Z (orthogonal) or 1/2/Z (rotated horizontals,
#: e.g. XQ's HH1/HH2/HHZ); a rotated three-component record is usable
#: but is never silently relabelled E/N.
_HORIZONTAL_SUFFIXES = frozenset({"E", "N", "1", "2"})
_VERTICAL_SUFFIXES = frozenset({"Z"})


def great_circle_km(lat1: float, lon1: float,
                    lat2: float, lon2: float) -> float:
    """Haversine great-circle distance in kilometres (deterministic,
    mean Earth radius — sufficient for station-corridor distance
    reporting; not a propagation model)."""
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = rlat2 - rlat1
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2.0) ** 2 + \
        math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2.0) ** 2
    return 2.0 * _EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def derive_orientation_status(channel_set: Sequence[str]) -> str:
    """Derive the orientation class from channel codes.

    Three channels ending in E/N/Z -> ``ORTHO_3C``; three channels
    ending in 1/2/Z (rotated horizontals) -> ``ROTATED_3C``; a partial
    complement -> ``PARTIAL``; empty/unknown -> ``MISSING``.
    """
    channels = [str(c).strip().upper() for c in channel_set
                if isinstance(c, str) and c.strip()]
    if not channels:
        return "MISSING"
    suffixes = {c[-1] for c in channels}
    horiz = sum(1 for c in channels if c[-1] in _HORIZONTAL_SUFFIXES)
    vert = sum(1 for c in channels if c[-1] in _VERTICAL_SUFFIXES)
    if len(channels) >= 3 and horiz >= 2 and vert >= 1:
        if "E" in suffixes and "N" in suffixes:
            return "ORTHO_3C"
        if "1" in suffixes and "2" in suffixes:
            return "ROTATED_3C"
        # Mixed or ambiguous-but-complete three-component set.
        return "ORTHO_3C" if suffixes & {"E", "N"} else "ROTATED_3C"
    return "PARTIAL"


def build_station_observability(
        *,
        station_id: str,
        network: str,
        station: str,
        location: str = "",
        channel_set: Sequence[str] = (),
        station_latitude: float,
        station_longitude: float,
        target_latitude: float,
        target_longitude: float,
        sample_rate_hz: float,
        response_relpath: str = "",
        response_sha256: str = "",
        coverage_start: str,
        coverage_end: str,
        coverage_fraction: float,
        gap_fraction: float,
        latency_seconds: float,
        noise_floor_by_band: Mapping[str, float] | None = None,
        snr_by_band: Mapping[str, float] | None = None,
        waveform_available: bool = True,
        source_manifest_digest: str,
        config: SeismicSidecarConfig | None = None,
) -> StationObservabilityV0:
    """Build a ``StationObservabilityV0`` from declared station
    metadata and measured coverage/SNR evidence.

    The gate verdict is DERIVED — never caller-asserted:

    - component count below ``config.min_component_count`` or a
      PARTIAL/MISSING orientation under a >=3-component policy ->
      ``UNOBSERVABLE``;
    - ``require_response`` and no bound StationXML path+digest ->
      ``UNOBSERVABLE``;
    - ``coverage_fraction < min_coverage_fraction`` or
      ``gap_fraction > max_gap_fraction`` -> ``UNOBSERVABLE``;
    - any declared ``snr_by_band`` value below ``min_snr_db`` or a
      non-finite measurement -> ``UNOBSERVABLE``;
    - unparseable, naive, or inverted coverage timestamps ->
      ``UNOBSERVABLE``;
    - ``waveform_available`` false -> ``UNOBSERVABLE``;
    - ``latency_seconds`` above the declared ceiling ->
      ``UNOBSERVABLE``.

    ``UNOBSERVABLE`` is an honest verdict — missing data is never
    silently converted into zero-valued features downstream.
    """
    cfg = config or SeismicSidecarConfig()
    problems: list[str] = []

    channels = tuple(str(c).strip() for c in channel_set
                     if isinstance(c, str) and c.strip())
    if len(set(channels)) != len(channels):
        problems.append("duplicate channel codes — one binding per "
                        "channel")
    component_count = len(set(channels))
    orientation = derive_orientation_status(channels)
    if component_count < cfg.min_component_count:
        problems.append(
            f"component_count {component_count} below required "
            f"{cfg.min_component_count} — the declared component "
            "policy cannot be met")
    if cfg.min_component_count >= 3 and \
            orientation in ("PARTIAL", "MISSING"):
        problems.append(
            f"orientation {orientation} cannot satisfy the "
            "three-component policy")

    response_status = "BOUND" if response_relpath and \
        response_sha256 else "MISSING"
    if cfg.require_response and response_status != "BOUND":
        problems.append(
            "no bound StationXML response (response_relpath/"
            "response_sha256) — response-corrected features "
            "require declared response metadata")

    cs = parse_strict_utc(coverage_start)
    ce = parse_strict_utc(coverage_end)
    if cs is None or ce is None:
        problems.append("coverage_start/coverage_end must be "
                        "explicit-UTC timestamps")
    elif ce <= cs:
        problems.append("coverage_end precedes coverage_start — "
                        "inverted coverage interval")

    if not isinstance(coverage_fraction, (int, float)) or \
            isinstance(coverage_fraction, bool) or \
            not math.isfinite(coverage_fraction) or \
            not 0.0 <= coverage_fraction <= 1.0:
        problems.append("coverage_fraction must be a finite number "
                        "in [0, 1]")
    elif coverage_fraction < cfg.min_coverage_fraction:
        problems.append(
            f"coverage_fraction {coverage_fraction:.4f} below "
            f"declared minimum {cfg.min_coverage_fraction}")
    if not isinstance(gap_fraction, (int, float)) or \
            isinstance(gap_fraction, bool) or \
            not math.isfinite(gap_fraction) or \
            not 0.0 <= gap_fraction <= 1.0:
        problems.append("gap_fraction must be a finite number in "
                        "[0, 1]")
    elif gap_fraction > cfg.max_gap_fraction:
        problems.append(
            f"gap_fraction {gap_fraction:.4f} exceeds declared "
            f"maximum {cfg.max_gap_fraction}")

    if not isinstance(latency_seconds, (int, float)) or \
            isinstance(latency_seconds, bool) or \
            not math.isfinite(latency_seconds) or latency_seconds < 0:
        problems.append("latency_seconds must be a finite "
                        "non-negative number")
    elif cfg.max_latency_seconds is not None and \
            latency_seconds > cfg.max_latency_seconds:
        problems.append(
            f"latency_seconds {latency_seconds} exceeds declared "
            f"ceiling {cfg.max_latency_seconds}")

    noise = dict(noise_floor_by_band or {})
    snr = dict(snr_by_band or {})
    for band, value in noise.items():
        if not isinstance(value, (int, float)) or \
                isinstance(value, bool) or not math.isfinite(value) \
                or value <= 0:
            problems.append(
                f"noise_floor_by_band[{band!r}] must be a positive "
                "finite number")
    if cfg.min_snr_db is not None:
        if not snr:
            problems.append(
                "snr_by_band is empty — SNR evidence is required "
                "under the declared minimum")
        for band, value in snr.items():
            if not isinstance(value, (int, float)) or \
                    isinstance(value, bool) or \
                    not math.isfinite(value):
                problems.append(
                    f"snr_by_band[{band!r}] must be finite")
            elif value < cfg.min_snr_db:
                problems.append(
                    f"snr_by_band[{band!r}] {value:.2f} dB below "
                    f"declared minimum {cfg.min_snr_db} dB")

    if not waveform_available:
        problems.append("waveform bytes unavailable — metadata "
                        "presence is not waveform evidence")

    try:
        distance = great_circle_km(
            float(station_latitude), float(station_longitude),
            float(target_latitude), float(target_longitude))
    except (TypeError, ValueError):
        distance = float("nan")
        problems.append("station/target coordinates must be finite "
                        "numbers")

    status = "OBSERVABLE" if not problems else "UNOBSERVABLE"
    return StationObservabilityV0(
        station_id=station_id,
        network=network,
        station=station,
        location=location,
        channel_set=tuple(sorted(set(channels))),
        target_latitude=float(target_latitude),
        target_longitude=float(target_longitude),
        distance_km=distance if math.isfinite(distance) else -1.0,
        component_count=component_count,
        sample_rate_hz=float(sample_rate_hz),
        response_relpath=response_relpath,
        response_sha256=response_sha256,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        coverage_fraction=float(coverage_fraction),
        gap_fraction=float(gap_fraction),
        latency_seconds=float(latency_seconds),
        noise_floor_by_band=noise,
        snr_by_band=snr,
        orientation_status=orientation,
        response_status=response_status,
        status=status,
        source_manifest_digest=source_manifest_digest,
        problems=tuple(problems))


def reassess_observability(
        rec: StationObservabilityV0,
        config: SeismicSidecarConfig | None = None) -> list[str]:
    """Re-derive the gate verdict from a CARRIED record's declared
    fields — the runner never trusts a caller-supplied ``status``.

    An ``OBSERVABLE`` record is honest only when every recomputed
    gate is clean; a record carrying ``OBSERVABLE`` over a failing
    gate (forged status) or carrying internally inconsistent gate
    fields (declared channels disagreeing with component_count,
    response_status claiming BOUND with no bound response) is
    inadmissible.  Waveform-byte availability is proven by the
    manifest payload read — not asserted by this record — so it is
    not re-derived here.
    """
    cfg = config or SeismicSidecarConfig()
    problems: list[str] = []

    channels = [str(c).strip() for c in rec.channel_set
                if isinstance(c, str) and c.strip()]
    unique_channels = len(set(channels))
    if len(channels) != unique_channels:
        problems.append("duplicate channel codes — one binding per "
                        "channel")
    if rec.component_count != unique_channels:
        problems.append(
            f"component_count {rec.component_count} disagrees with "
            f"the declared channel_set ({unique_channels} unique "
            "channels) — the record's gate fields are inconsistent")
    if unique_channels < cfg.min_component_count:
        problems.append(
            f"component_count {unique_channels} below required "
            f"{cfg.min_component_count} — the declared component "
            "policy cannot be met")
    orientation = derive_orientation_status(channels)
    if orientation != rec.orientation_status:
        problems.append(
            f"orientation_status {rec.orientation_status!r} does "
            f"not match the derived {orientation!r} from the "
            "declared channel_set")
    if cfg.min_component_count >= 3 and \
            orientation in ("PARTIAL", "MISSING"):
        problems.append(
            f"orientation {orientation} cannot satisfy the "
            "three-component policy")

    bound = bool(rec.response_relpath and rec.response_sha256)
    if cfg.require_response and not bound:
        problems.append(
            "no bound StationXML response (response_relpath/"
            "response_sha256) — response-corrected features "
            "require declared response metadata")
    if bound and rec.response_status != "BOUND":
        problems.append(
            f"response bound but response_status is "
            f"{rec.response_status!r} — a bound response must be "
            "marked BOUND")
    if not bound and rec.response_status == "BOUND":
        problems.append(
            "response_status claims BOUND with no bound "
            "response_relpath/response_sha256")

    cs = parse_strict_utc(rec.coverage_start)
    ce = parse_strict_utc(rec.coverage_end)
    if cs is None or ce is None:
        problems.append("coverage_start/coverage_end must be "
                        "explicit-UTC timestamps")
    elif ce <= cs:
        problems.append("coverage_end precedes coverage_start — "
                        "inverted coverage interval")

    for name, value, lo, hi in (
            ("coverage_fraction", rec.coverage_fraction, 0.0, 1.0),
            ("gap_fraction", rec.gap_fraction, 0.0, 1.0)):
        if not isinstance(value, (int, float)) or \
                isinstance(value, bool) or not math.isfinite(value) \
                or not lo <= value <= hi:
            problems.append(f"{name} must be a finite number in "
                            f"[{lo:g}, {hi:g}]")
    if isinstance(rec.coverage_fraction, (int, float)) and \
            not isinstance(rec.coverage_fraction, bool) and \
            math.isfinite(rec.coverage_fraction) and \
            rec.coverage_fraction < cfg.min_coverage_fraction:
        problems.append(
            f"coverage_fraction {rec.coverage_fraction:.4f} below "
            f"declared minimum {cfg.min_coverage_fraction}")
    if isinstance(rec.gap_fraction, (int, float)) and \
            not isinstance(rec.gap_fraction, bool) and \
            math.isfinite(rec.gap_fraction) and \
            rec.gap_fraction > cfg.max_gap_fraction:
        problems.append(
            f"gap_fraction {rec.gap_fraction:.4f} exceeds declared "
            f"maximum {cfg.max_gap_fraction}")

    if not isinstance(rec.latency_seconds, (int, float)) or \
            isinstance(rec.latency_seconds, bool) or \
            not math.isfinite(rec.latency_seconds) or \
            rec.latency_seconds < 0:
        problems.append("latency_seconds must be a finite "
                        "non-negative number")
    elif cfg.max_latency_seconds is not None and \
            rec.latency_seconds > cfg.max_latency_seconds:
        problems.append(
            f"latency_seconds {rec.latency_seconds} exceeds "
            f"declared ceiling {cfg.max_latency_seconds}")

    for band, value in rec.noise_floor_by_band.items():
        if not isinstance(value, (int, float)) or \
                isinstance(value, bool) or not math.isfinite(value) \
                or value <= 0:
            problems.append(
                f"noise_floor_by_band[{band!r}] must be a positive "
                "finite number")
    if cfg.min_snr_db is not None:
        if not rec.snr_by_band:
            problems.append(
                "snr_by_band is empty — SNR evidence is required "
                "under the declared minimum")
        for band, value in rec.snr_by_band.items():
            if not isinstance(value, (int, float)) or \
                    isinstance(value, bool) or \
                    not math.isfinite(value):
                problems.append(f"snr_by_band[{band!r}] must be "
                                "finite")
            elif value < cfg.min_snr_db:
                problems.append(
                    f"snr_by_band[{band!r}] {value:.2f} dB below "
                    f"declared minimum {cfg.min_snr_db} dB")
    return problems


def source_manifest_digest(source_manifest: Mapping[str, Any]) -> str:
    """Canonical digest binding an observability record (and the
    receipt) to the exact seven-key source manifest."""
    return sha256_canonical(dict(source_manifest))
