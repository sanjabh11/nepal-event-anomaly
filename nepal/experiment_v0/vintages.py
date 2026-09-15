"""Metadata-first admission adapter for archived forecast vintages.

Implements the gate model of ``docs/science/run_c/FORECAST_ARCHIVE_MATRIX_V0``
against the ``nepal.research_v0`` contracts
(``ForecastVintageV0`` / ``CutoffRecordV0`` /
``ForecastDataClass``).  This adapter is *metadata admission only*:
no archive is downloaded, no credential is used, and no payload byte
is accessed.  A ``VintageRequest`` describes provider-declared
metadata; admission checks the provider registry, the data-class
allowlist, the strict-UTC ordering chain, the provider archive-delay
floor (fixed, per-centre, or caller-declared), licence/mechanism
capture, and byte-binding digest shape.  Anything failing a rule is
rejected with an explicit problem string — fail-closed by
construction.

Reanalysis products and rolling feeds are never forecast evidence.
Externally blocked providers (e.g. service-agreement archives for a
non-member state) admit no vintage at all.  ``local_retrieval_time``
is provenance only and can never make a late product historically
available.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, fields
from typing import Any, Mapping, Optional, Sequence

from nepal.research_v0.policy import (ForecastDataClass,
                                      parse_strict_utc,
                                      require_finite_seconds)
from nepal.research_v0.records import (CutoffRecordV0,
                                      ForecastVintageV0)

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Only these data classes may ever become forecast evidence (policy §7).
ADMISSIBLE_DATA_CLASSES = frozenset({
    ForecastDataClass.REFORECAST.value,
    ForecastDataClass.ARCHIVED_OPERATIONAL.value})
# Structurally inadmissible: reanalysis is retrospective regime input;
# a rolling feed is not a historical archive.
NEVER_FORECAST_CLASSES = frozenset({
    ForecastDataClass.REANALYSIS.value,
    ForecastDataClass.CURRENT_FEED.value})

ACCESS_MODES = frozenset(
    {"registration", "anonymous", "declared", "external_blocked"})

# Every field below must be a non-empty string before timing/digest
# rules are even evaluated.
REQUIRED_REQUEST_FIELDS = (
    "provider", "data_class", "model_version",
    "initialization_time", "issue_time", "valid_start", "valid_end",
    "archive_availability", "local_retrieval_time",
    "license_id", "archive_mechanism",
    "archive_payload_sha256", "retrieval_record_sha256",
    "archive_payload_path", "retrieval_record_path")

_TIMESTAMP_FIELDS = (
    "initialization_time", "issue_time", "valid_start", "valid_end",
    "archive_availability", "local_retrieval_time")

_ORDER_PAIRS = (
    ("initialization_time", "issue_time"),
    ("issue_time", "valid_start"),
    ("valid_start", "valid_end"))

_REQUEST_TYPE_TAG = "VintageRequest"


@dataclass(frozen=True)
class VintageRequest:
    """Provider metadata request (metadata-first; no payloads)."""

    provider: str               # registry key
    centre: str = ""            # e.g. "ecmf"; "" for single-centre providers
    data_class: str = ""        # "REFORECAST" | "ARCHIVED_OPERATIONAL"
    model_version: str = ""
    cycle: str = ""             # issue cycle label, e.g. "00"
    initialization_time: str = ""  # ISO-8601 UTC "YYYY-MM-DDTHH:MM:SSZ"
    issue_time: str = ""
    valid_start: str = ""
    valid_end: str = ""
    archive_availability: str = ""
    local_retrieval_time: str = ""
    license_id: str = ""
    archive_mechanism: str = ""
    archive_payload_sha256: str = ""      # 64-hex (synthetic digests OK)
    retrieval_record_sha256: str = ""
    archive_payload_path: str = ""
    retrieval_record_path: str = ""
    declared_delay_seconds: float = -1.0  # provider-declared delay; required
                                        # when provider has no fixed floor

    def to_dict(self) -> dict:
        d = asdict(self)
        d["request_type"] = _REQUEST_TYPE_TAG
        return d

    @classmethod
    def from_dict(cls, d: Mapping) -> "VintageRequest":
        """Strict reconstruction of a serialized request.

        Every declared field must be present (a serialized request
        always carries all fields), every string field must actually be
        a ``str``, and ``declared_delay_seconds`` must be a finite
        ``int``/``float`` — never a bool or a string like ``"false"``.
        Unknown keys, missing fields, and wrong primitive types all
        raise ``ValueError``; nothing is silently coerced.
        """
        if not isinstance(d, Mapping):
            raise ValueError("VintageRequest payload must be a mapping")
        payload = dict(d)
        tag = payload.pop("request_type", None)
        if tag is not None and tag != _REQUEST_TYPE_TAG:
            raise ValueError(f"request_type {tag!r} is not "
                             f"{_REQUEST_TYPE_TAG!r}")
        declared = {f.name for f in fields(cls)}
        extra = set(payload) - declared
        if extra:
            raise ValueError(f"VintageRequest: unknown fields "
                             f"{sorted(extra)}")
        missing = declared - set(payload)
        if missing:
            raise ValueError(f"VintageRequest: missing fields "
                             f"{sorted(missing)}")
        for name in declared:
            value = payload[name]
            if name == "declared_delay_seconds":
                if isinstance(value, bool) or \
                        not isinstance(value, (int, float)) or \
                        not math.isfinite(float(value)):
                    raise ValueError(
                        "declared_delay_seconds must be a finite "
                        "int/float — not a bool, str, or non-finite "
                        "number")
            elif not isinstance(value, str):
                raise ValueError(
                    f"{name} must be a str, got "
                    f"{type(value).__name__!r}")
        return cls(**payload)


@dataclass(frozen=True)
class ProviderPolicy:
    provider: str
    access_mode: str            # "registration" | "anonymous" | "declared" | "external_blocked"
    license_note: str
    allowed_data_classes: tuple[str, ...]
    delay_floor_seconds: float  # -1 => per-centre map or declared required
    centre_delay_seconds: Mapping[str, float]  # e.g. S2S per-centre; {} if n/a


# Provider registry — verified facts from FORECAST_ARCHIVE_MATRIX_V0
# (metadata review 2026-09-15).  Delay floors are dissemination-delay
# lower bounds for the issue+availability margin; they are floors, not
# the margin itself.
PROVIDER_REGISTRY: dict[str, ProviderPolicy] = {
    "tigge": ProviderPolicy(
        provider="tigge",
        access_mode="registration",
        license_note=("CC BY-NC (ECMWF contribution); per-centre "
                      "provider terms — research/non-commercial"),
        allowed_data_classes=(
            ForecastDataClass.ARCHIVED_OPERATIONAL.value,),
        delay_floor_seconds=172800.0,      # 48 h, all centres
        centre_delay_seconds={}),
    "s2s": ProviderPolicy(
        provider="s2s",
        access_mode="registration",
        license_note="per-centre CC BY 4.0 or CC BY-NC 4.0",
        allowed_data_classes=(
            ForecastDataClass.REFORECAST.value,
            ForecastDataClass.ARCHIVED_OPERATIONAL.value),
        delay_floor_seconds=-1.0,
        centre_delay_seconds={             # real-time legs
            "default": 172800.0,           # 48 h
            "bom": 604800.0,               # 1 week
            "cnrm": 604800.0,              # 1 week
            "ukmo": 604800.0,              # 1 week
        }),
    "ncep_gefsv12_reforecast": ProviderPolicy(
        provider="ncep_gefsv12_reforecast",
        access_mode="anonymous",
        license_note="NOAA open data (NODD)",
        allowed_data_classes=(ForecastDataClass.REFORECAST.value,),
        delay_floor_seconds=0.0,           # fixed retrospective product
        centre_delay_seconds={}),
    "ncar_gfs_025": ProviderPolicy(
        provider="ncar_gfs_025",
        access_mode="declared",
        license_note="CC BY 4.0; bounded archive 2015–2025",
        allowed_data_classes=(
            ForecastDataClass.ARCHIVED_OPERATIONAL.value,),
        delay_floor_seconds=-1.0,
        centre_delay_seconds={}),
    "ncei_nomads": ProviderPolicy(
        provider="ncei_nomads",
        access_mode="declared",
        license_note="NOAA/NCEI open",
        allowed_data_classes=(
            ForecastDataClass.REFORECAST.value,
            ForecastDataClass.ARCHIVED_OPERATIONAL.value),
        delay_floor_seconds=-1.0,
        centre_delay_seconds={}),
    "c3s_seasonal": ProviderPolicy(
        provider="c3s_seasonal",
        access_mode="declared",
        license_note="C3S terms; seasonal context",
        allowed_data_classes=(
            ForecastDataClass.ARCHIVED_OPERATIONAL.value,),
        delay_floor_seconds=-1.0,
        centre_delay_seconds={}),
    "ecmwf_mars_operational": ProviderPolicy(
        provider="ecmwf_mars_operational",
        access_mode="external_blocked",
        license_note=("service agreement required; Nepal is a "
                      "non-member state — procurement or fee waiver "
                      "only"),
        allowed_data_classes=(
            ForecastDataClass.ARCHIVED_OPERATIONAL.value,),
        delay_floor_seconds=-1.0,
        centre_delay_seconds={}),
}


def _effective_delay_floor(req: VintageRequest,
                           policy: ProviderPolicy,
                           problems: list[str]) -> Optional[float]:
    """Resolve the provider archive-delay floor in seconds.

    Fixed floors win; otherwise a per-centre map resolves by ``centre``
    with a ``"default"`` fallback; otherwise a caller-declared
    non-negative ``declared_delay_seconds`` is mandatory.  Returns
    ``None`` (and appends a problem) when no floor can be established.
    """
    fixed = require_finite_seconds(policy.delay_floor_seconds)
    if fixed is not None and fixed >= 0:
        return fixed
    centres = dict(policy.centre_delay_seconds or {})
    if centres:
        key = req.centre if req.centre in centres else "default"
        value = require_finite_seconds(centres.get(key))
        if value is not None and value >= 0:
            return value
        problems.append(
            f"provider {req.provider!r} has no usable delay floor for "
            f"centre {req.centre!r} and no default — the archive "
            "latency margin cannot be established")
        return None
    declared = require_finite_seconds(req.declared_delay_seconds)
    if declared is None or declared < 0:
        problems.append(
            f"provider {req.provider!r} has no fixed archive delay "
            "floor — a non-negative declared_delay_seconds is required "
            "to bound the issue+dissemination latency margin")
        return None
    return declared


def admission_problems(req: VintageRequest) -> list[str]:
    """Return every admission problem for a provider metadata request.

    An empty list means the request may be built into a
    ``ForecastVintageV0`` via :func:`build_vintage`.  Every rule is
    fail-closed: unknown or externally blocked providers, inadmissible
    data classes, missing fields, malformed timestamps, ordering
    inversions, unmet archive-delay floors, and early retrieval all
    produce explicit problems.
    """
    if type(req) is not VintageRequest:
        return [f"admission requires a VintageRequest, got "
                f"{type(req).__name__!r}"]
    problems: list[str] = []
    policy = PROVIDER_REGISTRY.get(req.provider)
    if policy is None:
        problems.append(
            f"provider {req.provider!r} is not in the forecast archive "
            "provider registry — unknown providers admit no vintages")
    elif policy.access_mode == "external_blocked":
        # Procurement-blocked providers can never admit a vintage.
        problems.append(
            f"provider {req.provider!r} is externally blocked "
            f"({policy.license_note}) — no vintage may be admitted")
        return problems

    for name in REQUIRED_REQUEST_FIELDS:
        value = getattr(req, name)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{name} is required")

    # Data-class gate: structural first, then provider allowlist.
    dc = req.data_class
    if dc in NEVER_FORECAST_CLASSES:
        problems.append(
            f"data_class {dc!r} is never forecast evidence — "
            "reanalysis is retrospective-only input and a rolling "
            "feed is not a historical archive")
    elif dc and dc not in ADMISSIBLE_DATA_CLASSES:
        problems.append(
            f"data_class {dc!r} is not an admissible forecast data "
            f"class {sorted(ADMISSIBLE_DATA_CLASSES)}")
    elif policy is not None and dc and \
            dc not in policy.allowed_data_classes:
        problems.append(
            f"provider {req.provider!r} does not admit data_class "
            f"{dc!r} (allowed: {list(policy.allowed_data_classes)})")

    for name in ("archive_payload_sha256", "retrieval_record_sha256"):
        value = getattr(req, name)
        if isinstance(value, str) and value and \
                not SHA256_RE.match(value):
            problems.append(f"{name} must be a 64-hex sha256 digest")

    parsed: dict[str, Optional[float]] = {}
    for name in _TIMESTAMP_FIELDS:
        raw = getattr(req, name)
        parsed[name] = parse_strict_utc(raw)
        if raw and parsed[name] is None:
            problems.append(
                f"{name} must be an explicit-UTC RFC3339 timestamp")
    for prev, nxt in _ORDER_PAIRS:
        if parsed[prev] is not None and parsed[nxt] is not None and \
                parsed[nxt] < parsed[prev]:
            problems.append(
                f"vintage order violated: {nxt} precedes {prev}")

    issue = parsed["issue_time"]
    archive = parsed["archive_availability"]
    retrieval = parsed["local_retrieval_time"]
    if policy is not None:
        floor = _effective_delay_floor(req, policy, problems)
        if floor is not None and issue is not None and \
                archive is not None and archive < issue + floor:
            problems.append(
                f"archive_availability precedes issue_time + delay "
                f"floor ({floor:.0f} s) — the provider archive "
                "latency margin is not satisfied")
    if archive is not None and retrieval is not None and \
            retrieval < archive:
        problems.append(
            "local_retrieval_time precedes archive_availability — "
            "retrieval cannot precede archive")
    return problems


def build_vintage(req: VintageRequest,
                  vintage_id: str) -> ForecastVintageV0:
    """Admit a metadata request into a ``ForecastVintageV0``.

    Raises ``ValueError("; ".join(problems))`` on any admission or
    record problem — the produced record itself must pass
    ``ForecastVintageV0.problems()`` (digest shape, paths, ordering).
    """
    problems = admission_problems(req)
    if not str(vintage_id or "").strip():
        problems.append("vintage_id is required")
    if problems:
        raise ValueError("; ".join(problems))
    vintage = ForecastVintageV0(
        vintage_id=vintage_id,
        provider=req.provider,
        data_class=req.data_class,
        initialization_time=req.initialization_time,
        issue_time=req.issue_time,
        valid_start=req.valid_start,
        valid_end=req.valid_end,
        archive_availability=req.archive_availability,
        archive_payload_sha256=req.archive_payload_sha256,
        retrieval_record_sha256=req.retrieval_record_sha256,
        archive_payload_path=req.archive_payload_path,
        retrieval_record_path=req.retrieval_record_path,
        model_version=req.model_version,
        license_id=req.license_id,
        archive_mechanism=req.archive_mechanism)
    rec_problems = vintage.problems()
    if rec_problems:
        raise ValueError("; ".join(rec_problems))
    return vintage


def ledger_problems(vintages: Sequence[ForecastVintageV0]) -> list[str]:
    """Cross-vintage integrity checks over an admitted ledger.

    Flags non-vintage entries, record-level problems, duplicate
    ``vintage_id`` values, the same ``archive_payload_sha256`` bound
    under different vintage_ids (one byte sequence may not back two
    vintages), and duplicate ``retrieval_record_sha256`` values.
    """
    problems: list[str] = []
    seen_ids: set[str] = set()
    payload_owner: dict[str, str] = {}
    retrieval_seen: set[str] = set()
    for index, vintage in enumerate(vintages):
        if type(vintage) is not ForecastVintageV0:
            problems.append(
                f"ledger entry {index} has type "
                f"{type(vintage).__name__!r} — not a ForecastVintageV0")
            continue
        for problem in vintage.problems():
            problems.append(f"vintage {vintage.vintage_id!r}: {problem}")
        if vintage.vintage_id in seen_ids:
            problems.append(
                f"duplicate vintage_id {vintage.vintage_id!r}")
        else:
            seen_ids.add(vintage.vintage_id)
        digest = vintage.archive_payload_sha256
        if digest:
            owner = payload_owner.get(digest)
            if owner is not None and owner != vintage.vintage_id:
                problems.append(
                    f"archive_payload_sha256 {digest[:16]}… is bound "
                    f"under vintage_ids {owner!r} and "
                    f"{vintage.vintage_id!r} — the same archive bytes "
                    "may not back two vintages")
            else:
                payload_owner[digest] = vintage.vintage_id
        record_digest = vintage.retrieval_record_sha256
        if record_digest:
            if record_digest in retrieval_seen:
                problems.append(
                    f"duplicate retrieval_record_sha256 "
                    f"{record_digest[:16]}…")
            else:
                retrieval_seen.add(record_digest)
    return problems


def cutoff_for_vintage(vintage: ForecastVintageV0, *, cutoff_id: str,
                       source_observation_end: str,
                       source_processing_complete: str,
                       source_publication: str,
                       feature_availability: str,
                       source_id: str = "", event_id: str = "",
                       event_time_start: str = "",
                       event_time_end: str = "") -> CutoffRecordV0:
    """Bind a ``CutoffRecordV0`` to an admitted vintage.

    ``forecast_initialization``, ``forecast_issue``,
    ``forecast_valid_start``, ``forecast_valid_end``,
    ``archive_availability``, and ``forecast_vintage_id`` are taken
    from the vintage.  ``local_retrieval_time`` is set to the
    vintage's ``archive_availability`` — the earliest admissible
    provenance bound, since metadata admission performs no real
    retrieval event.

    Raises ``ValueError("; ".join(problems))`` on vintage or record
    problems (including an unsatisfied upstream ordering chain or a
    missing event/source association, which a vintage-bound cutoff
    requires).
    """
    if type(vintage) is not ForecastVintageV0:
        raise ValueError("cutoff_for_vintage requires a "
                         "ForecastVintageV0")
    vintage_problems = vintage.problems()
    if vintage_problems:
        raise ValueError("; ".join(vintage_problems))
    cutoff = CutoffRecordV0(
        cutoff_id=cutoff_id,
        source_observation_end=source_observation_end,
        source_processing_complete=source_processing_complete,
        source_publication=source_publication,
        feature_availability=feature_availability,
        forecast_initialization=vintage.initialization_time,
        forecast_issue=vintage.issue_time,
        forecast_valid_start=vintage.valid_start,
        forecast_valid_end=vintage.valid_end,
        archive_availability=vintage.archive_availability,
        local_retrieval_time=vintage.archive_availability,
        forecast_vintage_id=vintage.vintage_id,
        source_id=source_id,
        event_id=event_id,
        event_time_start=event_time_start or None,
        event_time_end=event_time_end or None)
    problems = cutoff.problems()
    if problems:
        raise ValueError("; ".join(problems))
    return cutoff
