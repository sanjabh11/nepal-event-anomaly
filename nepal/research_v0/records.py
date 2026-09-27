"""Record types for the additive research-only namespace (P4).

The record types bind the v0 science-design contracts to data.
All records are frozen dataclasses; ``to_dict`` produces the canonical
JSON-able form hashed into claim envelopes.  Validation surfaces as
``problems()`` — a non-empty list means the record is inadmissible.

Semantic hardening (audit rounds A02–A24, B01–B42): authority flags are
enforced false-only at construction; timestamps are explicit-UTC;
declared precision must agree with measured uncertainty and the
uncertainty must cover the whole event interval; cascades and event
universes must be completely and uniquely mapped; negative controls
require a linked full-observation opportunity; envelopes bind a
status-to-required-record graph and cross-record foreign keys; nothing
is "verified" from metadata alone.

Nothing in this module authorizes intake, freeze, clustering, or any
operational claim.
"""
from __future__ import annotations

import re
from datetime import datetime as _dt
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional

from .policy import (HORIZON_SECONDS, OPPORTUNITY_STATES, EventTimeClass,
                     ForecastDataClass, RegimeMode,
                     SEISMIC_WAVEFORM_RETROSPECTIVE_CLASS, TargetState,
                     classify_event_time, parse_strict_utc,
                     require_finite_seconds)

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

SOURCE_POSTURES = ("CANDIDATE_ONLY", "EVIDENCE_VERIFIED", "REJECTED")
PILOT_GATE_STATUSES = (
    "CANDIDATE_ONLY",
    "PILOT_CANDIDATE_PENDING_EVIDENCE",
    "NO_QUALIFYING_PILOT_SOURCE",
    "DEFERRED_NO_OPEN_TIMED_SOURCE",
    "PILOT_GATE_PASSED",
)
ADJUDICATION_STATES = (
    "UNADJUDICATED", "TWO_REVIEW_AGREE", "THIRD_PARTY_ADJUDICATED",
    "DISAGREEMENT_RETAINED")
POSITIVE_ADMISSIBLE_ADJUDICATION = frozenset(
    {"TWO_REVIEW_AGREE", "THIRD_PARTY_ADJUDICATED"})
FEATURE_NAMESPACES = ("occurrence", "exposure", "impact", "context")
EVIDENCE_REVIEW_STATES = ("UNREVIEWED", "REVIEWED", "INDEPENDENTLY_VERIFIED")

#: Data classes admissible for a forecast-mode regime artifact
#: (FCST-01): the issue-time forecast archive classes only —
#: reanalysis is retrospective and a rolling feed is not a
#: historical archive.
FORECAST_REGIME_DATA_CLASSES = frozenset({
    ForecastDataClass.REFORECAST.value,
    ForecastDataClass.ARCHIVED_OPERATIONAL.value})

#: Retrospective-regime data classes (SEISMIC-01): ``REANALYSIS`` is
#: the default for the weather/GLOF lane; the seismic waveform class
#: is admitted for the seismic sidecar's retrospective artifacts only.
#: Both classes are retrospective — neither may ever appear on a
#: FORECAST_REGIME artifact or a forecast vintage.
SEISMIC_WAVEFORM_RETROSPECTIVE_DATA_CLASS = (
    SEISMIC_WAVEFORM_RETROSPECTIVE_CLASS)
RETROSPECTIVE_REGIME_DATA_CLASSES = frozenset({
    ForecastDataClass.REANALYSIS.value,
    SEISMIC_WAVEFORM_RETROSPECTIVE_DATA_CLASS})

# Controlled vertical ontology (G03): a spec may only claim a declared
# vertical and one of its compatible mechanisms — arbitrary pairs reject.
VERTICAL_IDS = frozenset({
    "snow_avalanche", "ice_rock_avalanche", "glof", "landslide_rainfall",
    "landslide_coseismic", "ldof", "dam_breach_engineered"})
MECHANISM_IDS = frozenset({
    "snow_release", "ice_rock_failure", "glacier_detachment",
    "lake_outburst", "slope_initiation_rainfall",
    "slope_initiation_coseismic", "natural_dam_breach",
    "engineered_breach", "embankment_breach"})
VERTICAL_MECHANISM_COMPAT = {
    "snow_avalanche": frozenset({"snow_release"}),
    "ice_rock_avalanche": frozenset({"ice_rock_failure",
                                     "glacier_detachment"}),
    "glof": frozenset({"lake_outburst"}),
    "landslide_rainfall": frozenset({"slope_initiation_rainfall"}),
    "landslide_coseismic": frozenset({"slope_initiation_coseismic"}),
    "ldof": frozenset({"natural_dam_breach"}),
    "dam_breach_engineered": frozenset({"engineered_breach",
                                        "embankment_breach"}),
}

GEOMETRY_ROLES = frozenset({
    "source_point", "deposit_polygon", "runout_polygon", "lake_point",
    "breach_point", "impact_point", "catchment", "admin_unit",
    "slope_generalized"})

# Declared event-time precision terms must be consistent with the
# measured uncertainty class.
PRECISION_TERMS = frozenset({
    "exact_timestamp", "day", "interval", "month", "season", "year",
    "unresolved"})
_PRECISION_TO_CLASS = {
    "exact_timestamp": EventTimeClass.EXACT_TIMESTAMP,
    "day": EventTimeClass.EXACT_DAY,
    "interval": None,   # resolved by uncertainty width
    "month": EventTimeClass.COARSE_OR_UNRESOLVED,
    "season": EventTimeClass.COARSE_OR_UNRESOLVED,
    "year": EventTimeClass.COARSE_OR_UNRESOLVED,
    "unresolved": EventTimeClass.COARSE_OR_UNRESOLVED,
}

ASSIGNMENT_RULES = frozenset(
    {"basin", "catchment", "macroregion", "fixed_spatial"})
SPLIT_NAMES = frozenset({"train", "validation", "test"})
EXPERIMENT_TARGETS = frozenset({"occurrence"})
ARTIFACT_TYPES = frozenset({
    "feature_matrix", "power_report", "stability_report",
    "k_selection", "forecast_output", "evaluation_report",
    "uncertainty_report", "source_evidence", "regime_model",
    "preprocessing", "null_model"})

# Which artifact type may satisfy which digest role (E04) — role
# confusion between artifact classes is rejected.
ARTIFACT_ROLE_TYPES = {
    "feature": "feature_matrix",
    "power": "power_report",
    "uncertainty": "uncertainty_report",
    "forecast_output": "forecast_output",
    "evaluation": "evaluation_report",
    "stability": "stability_report",
    "k_selection": "k_selection",
    "preprocessing": "preprocessing",
    "null_model": "null_model",
    "regime_model": "regime_model",
    "source_evidence": "source_evidence",
}

# Neutral research statuses — the only statuses a claim envelope may
# carry.  READY-shaped or authority-shaped statuses are absent by
# construction.  ``METADATA_REVIEW_COMPLETE`` is deliberately named so
# metadata inventory cannot be confused with source qualification (A23).
NEUTRAL_RESEARCH_STATUSES = frozenset({
    "BASELINE_RECONCILED",
    "METADATA_REVIEW_COMPLETE",
    "NO_QUALIFYING_PILOT_SOURCE",
    "DESIGN_DRAFT_COMPLETE",
    "RESEARCH_PATH_ISOLATED",
    "EVENT_INTAKE_VALIDATED",
    "INTAKE_COMPLETE",
    "RESEARCH_FEATURE_MATRIX_FROZEN",
    "FMX_BLOCKED_CUTOFF",
    "DESCRIPTIVE_REGIME_ONLY",
    "UNSUPERVISED_STRUCTURE_NOT_STABLE",
    "REGIME_ASSOCIATION_SUPPORTED",
    "UNSUPERVISED_PATH_NOT_SUPPORTED",
    "FORECAST_EXPERIMENT_ONLY",
    "UNDERPOWERED_DESCRIPTIVE_ONLY",
    "DEFERRED_NO_OPEN_TIMED_SOURCE",
})

# Statuses that may coexist with unresolved blockers (design/blocked
# stages).  Execution statuses may not carry blockers.
BLOCKER_TOLERANT_STATUSES = frozenset({
    "BASELINE_RECONCILED", "METADATA_REVIEW_COMPLETE",
    "NO_QUALIFYING_PILOT_SOURCE", "DESIGN_DRAFT_COMPLETE",
    "RESEARCH_PATH_ISOLATED", "FMX_BLOCKED_CUTOFF",
    "UNDERPOWERED_DESCRIPTIVE_ONLY", "DEFERRED_NO_OPEN_TIMED_SOURCE"})

# Status → required record classes (B04/C06): an execution-shaped
# envelope with no records, or missing any record type its status
# implies, fails closed.  Forecast statuses require the full upstream
# chain — source, labels, opportunities, controls, holdout, vintages,
# and a bound feature artifact.
STATUS_REQUIRED_RECORDS = {
    "METADATA_REVIEW_COMPLETE": ("SourceRecordV0",),
    "EVENT_INTAKE_VALIDATED": ("EventLabelV0", "ObservationOpportunityV0",
                               "ControlWindowV0", "HoldoutPlanV0",
                               "SourceRecordV0"),
    "INTAKE_COMPLETE": ("EventLabelV0", "ObservationOpportunityV0",
                        "ControlWindowV0", "HoldoutPlanV0",
                        "SourceRecordV0"),
    "RESEARCH_FEATURE_MATRIX_FROZEN": ("CutoffRecordV0", "HoldoutPlanV0",
                                       "EventLabelV0",
                                       "EvidenceArtifactV0"),
    "FMX_BLOCKED_CUTOFF": ("CutoffRecordV0",),
    "DESCRIPTIVE_REGIME_ONLY": ("RegimeArtifactV0", "EvidenceArtifactV0"),
    "UNSUPERVISED_STRUCTURE_NOT_STABLE": ("RegimeArtifactV0",),
    "REGIME_ASSOCIATION_SUPPORTED": ("RegimeArtifactV0", "HoldoutPlanV0",
                                     "EventLabelV0",
                                     "EvidenceArtifactV0"),
    "UNSUPERVISED_PATH_NOT_SUPPORTED": ("RegimeArtifactV0",
                                        "HoldoutPlanV0"),
    "FORECAST_EXPERIMENT_ONLY": (
        "ForecastExperimentV0", "ForecastVintageV0", "HoldoutPlanV0",
        "EventLabelV0", "ObservationOpportunityV0", "ControlWindowV0",
        "SourceRecordV0", "EvidenceArtifactV0", "HazardVerticalSpecV0",
        "CutoffRecordV0"),
    "UNDERPOWERED_DESCRIPTIVE_ONLY": ("ForecastExperimentV0",),
}

# Statuses that demand byte-bound evidence: any envelope carrying them
# requires an evidence_root and verified artifact bytes.
EXECUTION_STATUSES = frozenset(
    NEUTRAL_RESEARCH_STATUSES - BLOCKER_TOLERANT_STATUSES)

# Status → required EvidenceArtifactV0 types (E04/E18): role-specific
# artifacts only — a feature_matrix cannot satisfy a power_report slot.
STATUS_REQUIRED_ARTIFACT_TYPES = {
    "RESEARCH_FEATURE_MATRIX_FROZEN": {"feature_matrix"},
    "DESCRIPTIVE_REGIME_ONLY": {"regime_model", "stability_report",
                                "k_selection", "preprocessing",
                                "null_model"},
    "REGIME_ASSOCIATION_SUPPORTED": {"regime_model", "stability_report",
                                     "k_selection", "evaluation_report",
                                     "preprocessing", "null_model"},
    "FORECAST_EXPERIMENT_ONLY": {"feature_matrix", "forecast_output",
                                 "power_report", "uncertainty_report"},
    "UNDERPOWERED_DESCRIPTIVE_ONLY": {"power_report"},
}


def _req(problems: list[str], name: str, value: Any) -> None:
    if value is None or value == "" or value == [] or value == ():
        problems.append(f"{name} is required")


def _sha(problems: list[str], name: str, value: Any) -> None:
    if not isinstance(value, str) or not SHA256_RE.match(value):
        problems.append(f"{name} must be a 64-hex sha256 digest")


def _ts(problems: list[str], name: str, value: Any) -> Optional[float]:
    parsed = parse_strict_utc(value)
    if parsed is None:
        problems.append(
            f"{name} must be an explicit-UTC RFC3339 timestamp or "
            "finite epoch seconds")
        return None
    return parsed


def _date(problems: list[str], name: str, value: Any) -> None:
    if not isinstance(value, str) or \
            not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        problems.append(f"{name} must be an ISO YYYY-MM-DD date")
        return
    try:  # calendar-aware: 2026-99-99 and 2026-02-30 reject (E07)
        _dt.strptime(value, "%Y-%m-%d")
    except ValueError:
        problems.append(f"{name} is not a real calendar date")


@dataclass(frozen=True)
class HazardVerticalSpecV0:
    """One hazard vertical's science ontology boundary.  The
    vertical–mechanism pair must be declared in the compatibility
    matrix — arbitrary combinations reject."""

    vertical_id: str
    physical_event_unit: str
    mechanism: str = ""
    exclusions: tuple[str, ...] = ()
    compound_relations: tuple[str, ...] = ()
    candidate_source_ids: tuple[str, ...] = ()
    pilot_gate_status: str = "CANDIDATE_ONLY"

    def problems(self) -> list[str]:
        problems: list[str] = []
        _req(problems, "vertical_id", self.vertical_id)
        _req(problems, "physical_event_unit", self.physical_event_unit)
        if self.vertical_id and self.vertical_id not in VERTICAL_IDS:
            problems.append(
                f"vertical_id {self.vertical_id!r} not in controlled "
                f"ontology {sorted(VERTICAL_IDS)}")
        _req(problems, "mechanism", self.mechanism)
        if self.mechanism and self.mechanism not in MECHANISM_IDS:
            problems.append(
                f"mechanism {self.mechanism!r} not in controlled "
                f"mechanism allowlist {sorted(MECHANISM_IDS)}")
        elif (self.vertical_id in VERTICAL_MECHANISM_COMPAT and
              self.mechanism and
              self.mechanism not in
              VERTICAL_MECHANISM_COMPAT[self.vertical_id]):
            problems.append(
                f"mechanism {self.mechanism!r} is incompatible with "
                f"vertical {self.vertical_id!r}")
        if not self.exclusions:
            problems.append("exclusions must be non-empty — every "
                            "vertical declares what it is not")
        if self.pilot_gate_status not in PILOT_GATE_STATUSES:
            problems.append(
                f"pilot_gate_status {self.pilot_gate_status!r} not in "
                f"{PILOT_GATE_STATUSES}")
        if self.pilot_gate_status == "PILOT_GATE_PASSED" and \
                not self.candidate_source_ids:
            problems.append("PILOT_GATE_PASSED requires candidate sources")
        return problems

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["record_type"] = type(self).__name__
        return d


@dataclass(frozen=True)
class SourceRecordV0:
    """Metadata-only record for one candidate event inventory.

    ``posture`` is CANDIDATE_ONLY until exact version, license,
    geography, fields, and timing semantics are verified at intake.
    EVIDENCE_VERIFIED requires a real evidence sidecar (path + bytes
    digest + as-of date) and INDEPENDENTLY_VERIFIED review — a bare
    hex string is not evidence.
    """

    source_id: str
    provider: str
    doi_or_url: str
    version: str = ""
    as_of_date: str = ""
    license_id: str = ""
    redistribution: str = ""
    geography: str = ""
    temporal_coverage: str = ""
    event_time_class: str = EventTimeClass.COARSE_OR_UNRESOLVED.value
    spatial_semantics: str = ""
    observation_method: str = ""
    non_event_frame: str = ""
    update_cadence: str = ""
    access_status: str = ""
    posture: str = "CANDIDATE_ONLY"
    license_notes: str = ""
    evidence_sidecar_path: str = ""
    evidence_sidecar_sha256: str = ""
    evidence_as_of: str = ""
    evidence_review_state: str = "UNREVIEWED"

    def problems(self) -> list[str]:
        problems: list[str] = []
        for name in ("source_id", "provider", "doi_or_url"):
            _req(problems, name, getattr(self, name))
        if self.posture not in SOURCE_POSTURES:
            problems.append(f"posture {self.posture!r} not in "
                            f"{SOURCE_POSTURES}")
        if self.event_time_class not in {c.value for c in EventTimeClass}:
            problems.append(
                f"event_time_class {self.event_time_class!r} invalid")
        if self.evidence_review_state not in EVIDENCE_REVIEW_STATES:
            problems.append(
                f"evidence_review_state {self.evidence_review_state!r} "
                f"not in {EVIDENCE_REVIEW_STATES}")
        if self.posture == "EVIDENCE_VERIFIED":
            # Every qualification field is required — metadata-only
            # records can never reach this posture (B07).
            for name in ("version", "as_of_date", "license_id",
                         "redistribution", "geography",
                         "temporal_coverage", "spatial_semantics",
                         "observation_method", "non_event_frame",
                         "update_cadence", "access_status"):
                _req(problems, f"EVIDENCE_VERIFIED requires {name}",
                     getattr(self, name))
            _req(problems, "EVIDENCE_VERIFIED requires "
                           "evidence_sidecar_path",
                 self.evidence_sidecar_path)
            _sha(problems, "evidence_sidecar_sha256",
                 self.evidence_sidecar_sha256)
            _date(problems, "evidence_as_of", self.evidence_as_of)
            if self.evidence_review_state != "INDEPENDENTLY_VERIFIED":
                problems.append("EVIDENCE_VERIFIED requires "
                                "INDEPENDENTLY_VERIFIED review state")
        return problems

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["record_type"] = type(self).__name__
        return d


@dataclass(frozen=True)
class EventLabelV0:
    """One adjudicated (or pending) event label.

    Event intervals are explicit-UTC and ordered; the declared
    uncertainty must cover the whole bracket (a 31-day interval cannot
    claim one-hour precision).  Reviewer identities must be unique.
    """

    event_id: str
    vertical_id: str
    source_id: str
    source_version: str
    event_time_start: str
    event_time_end: str
    uncertainty_seconds: Optional[float] = None
    event_time_precision: str = ""
    event_time_basis: str = ""
    geometry_role: str = ""
    coordinate_uncertainty: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    basin_id: str = ""
    cascade_group_id: str = ""
    parent_event_id: str = ""
    duplicate_of: str = ""
    adjudication_state: str = "UNADJUDICATED"
    adjudication_notes: str = ""
    reviewer_ids: tuple[str, ...] = ()
    # P5-A2 field separation (hydrology adjudication): raw source and
    # administrative geography are preserved verbatim and are never
    # conflated with the derived hydrological group.  ``basin_id`` is
    # a compatibility projection that MUST equal ``basin_group`` —
    # it is not an independent source of truth.
    raw_river_basin: str = ""
    administrative_district: str = ""
    administrative_province: str = ""
    basin_group: str = ""
    hydro_subbasin: str = ""

    @property
    def adjudicated(self) -> bool:
        return self.adjudication_state in POSITIVE_ADMISSIBLE_ADJUDICATION

    def problems(self) -> list[str]:
        problems: list[str] = []
        for name in ("event_id", "vertical_id", "source_id",
                     "source_version", "event_time_start",
                     "event_time_end", "event_time_precision",
                     "event_time_basis", "geometry_role", "basin_id"):
            _req(problems, name, getattr(self, name))
        if self.vertical_id and self.vertical_id not in VERTICAL_IDS:
            problems.append(f"vertical_id {self.vertical_id!r} not in "
                            "controlled ontology")
        if self.geometry_role and self.geometry_role not in GEOMETRY_ROLES:
            problems.append(f"geometry_role {self.geometry_role!r} not in "
                            f"{sorted(GEOMETRY_ROLES)}")
        start = _ts(problems, "event_time_start", self.event_time_start)
        end = _ts(problems, "event_time_end", self.event_time_end)
        # P5-A2: the compatibility projection can never diverge from
        # the adjudicated hydrological group — a mismatch is a hard
        # defect, not a warning.
        if self.basin_group and self.basin_id != self.basin_group:
            problems.append(
                f"basin_id {self.basin_id!r} != basin_group "
                f"{self.basin_group!r} — the compatibility projection "
                "may never diverge from the hydrological group")
        interval_width: Optional[float] = None
        if start is not None and end is not None:
            if end < start:
                problems.append("event_time_end precedes "
                                "event_time_start — inverted interval")
            else:
                interval_width = end - start
        if self.event_time_precision and \
                self.event_time_precision not in PRECISION_TERMS:
            problems.append(
                f"event_time_precision {self.event_time_precision!r} not "
                f"in {sorted(PRECISION_TERMS)}")
        # Explicit uncertainty is mandatory (C14): a label without a
        # declared uncertainty cannot establish its precision class.
        unc = require_finite_seconds(self.uncertainty_seconds)
        if unc is None:
            problems.append("uncertainty_seconds is required and must "
                            "be finite")
        elif unc < 0:
            problems.append("uncertainty_seconds must be non-negative")
        # Interval convention (B09): declared uncertainty must cover
        # the entire event interval — an interval is a bound on possible
        # release times, not a precise timestamp.
        if unc is not None and interval_width is not None and \
                unc < interval_width:
            problems.append(
                f"uncertainty_seconds ({unc}) is narrower than the "
                f"event interval ({interval_width}) — uncertainty must "
                "cover the whole bracket")
        if unc is not None and self.event_time_precision in \
                _PRECISION_TO_CLASS:
            declared = _PRECISION_TO_CLASS[self.event_time_precision]
            measured = classify_event_time(unc)
            if self.event_time_precision == "interval":
                # "interval" must bind to a real interval class — it
                # cannot smuggle sub-day or coarse uncertainty (C14).
                if measured not in (EventTimeClass.INTERVAL_LE_7D,
                                    EventTimeClass.INTERVAL_8_30D):
                    problems.append(
                        f"precision 'interval' requires uncertainty in "
                        f"(1d, 30d]; measured class is {measured.value}")
            elif declared is not None and declared != measured:
                problems.append(
                    f"event_time_precision {self.event_time_precision!r} "
                    f"inconsistent with measured class {measured.value}")
        for axis, value in (("latitude", self.latitude),
                            ("longitude", self.longitude)):
            if value is not None:
                v = require_finite_seconds(value)
                bound = 90.0 if axis == "latitude" else 180.0
                if v is None or abs(v) > bound:
                    problems.append(
                        f"{axis} {value!r} out of range [±{bound}]")
        if self.adjudication_state not in ADJUDICATION_STATES:
            problems.append(
                f"adjudication_state {self.adjudication_state!r} not in "
                f"{ADJUDICATION_STATES}")
        if isinstance(self.reviewer_ids, str) or not isinstance(
                self.reviewer_ids, (list, tuple)):
            problems.append("reviewer_ids must be a collection of "
                            "reviewer identities — a bare string is "
                            "not a reviewer set")
        elif self.adjudication_state in POSITIVE_ADMISSIBLE_ADJUDICATION:
            if len(self.reviewer_ids) < 2:
                problems.append(
                    "adjudicated labels require >=2 reviewer_ids")
            elif len(set(self.reviewer_ids)) != len(self.reviewer_ids):
                problems.append(
                    "reviewer_ids must be unique — duplicate identities "
                    "are not independent reviews")
        return problems

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["record_type"] = type(self).__name__
        return d


@dataclass(frozen=True)
class ObservationOpportunityV0:
    """Whether a forecast unit was actually observable over a window,
    bound to actual source frames — not a caller assertion."""

    opportunity_id: str
    unit_id: str
    platform: str
    window_start: str
    window_end: str
    coverage_fraction: Optional[float] = None
    coverage_quality: str = ""
    detection_threshold: str = ""
    state: str = "UNKNOWN"
    source_id: str = ""
    source_as_of: str = ""
    frame_ids: tuple[str, ...] = ()

    def problems(self) -> list[str]:
        problems: list[str] = []
        for name in ("opportunity_id", "unit_id", "platform",
                     "window_start", "window_end"):
            _req(problems, name, getattr(self, name))
        ws = _ts(problems, "window_start", self.window_start)
        we = _ts(problems, "window_end", self.window_end)
        if ws is not None and we is not None and we <= ws:
            problems.append("window_end must be after window_start")
        if self.state not in OPPORTUNITY_STATES:
            problems.append(f"state {self.state!r} not in "
                            f"{OPPORTUNITY_STATES}")
        cov = self.coverage_fraction
        c: Optional[float] = None
        if cov is not None:
            c = require_finite_seconds(cov)
            if c is None or not 0.0 <= c <= 1.0:
                problems.append("coverage_fraction must be finite in "
                                "[0,1]")
                c = None
        if self.state == "OBSERVED_FULL":
            if c is None or c < 0.999:
                problems.append("OBSERVED_FULL requires "
                                "coverage_fraction ~= 1.0")
            if not self.frame_ids:
                problems.append("OBSERVED_FULL requires actual "
                                "frame_ids — observation is bound to "
                                "real source frames")
        elif self.state == "OBSERVED_PARTIAL":
            if c is None or not 0.0 < c < 1.0:
                problems.append("OBSERVED_PARTIAL requires "
                                "coverage_fraction in (0,1)")
            if not self.frame_ids:
                problems.append("OBSERVED_PARTIAL requires the "
                                "observed frame_ids")
        elif self.state == "UNOBSERVED" and c not in (None, 0.0):
            problems.append("UNOBSERVED requires coverage_fraction 0.0 "
                            "or absent")
        if self.state in ("OBSERVED_FULL", "OBSERVED_PARTIAL"):
            _req(problems, "source_id", self.source_id)
            _date(problems, "source_as_of", self.source_as_of)
        if isinstance(self.frame_ids, str) or not isinstance(
                self.frame_ids, (list, tuple)) or \
                any(not isinstance(f, str) for f in self.frame_ids):
            problems.append("frame_ids must be a collection of frame "
                            "identifiers — a bare string is not a "
                            "frame set")
        elif len(set(self.frame_ids)) != len(self.frame_ids):
            problems.append("frame_ids must be unique")
        return problems

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["record_type"] = type(self).__name__
        return d


@dataclass(frozen=True)
class ControlWindowV0:
    """A non-event control.  ``state`` may be NEGATIVE only when the
    linked observation opportunity is OBSERVED_FULL; use
    ``policy.derive_control_state`` to derive it from the real
    opportunity record and event intervals — never a caller claim."""

    control_id: str
    unit_id: str
    window_start: str
    window_end: str
    opportunity_id: str
    opportunity_state: str = "UNKNOWN"
    state: str = TargetState.CENSORED_OR_AMBIGUOUS.value
    matched_covariates: tuple[str, ...] = ()
    cascade_group_id: str = ""

    def problems(self) -> list[str]:
        problems: list[str] = []
        for name in ("control_id", "unit_id", "window_start",
                     "window_end", "opportunity_id"):
            _req(problems, name, getattr(self, name))
        ws = _ts(problems, "window_start", self.window_start)
        we = _ts(problems, "window_end", self.window_end)
        if ws is not None and we is not None and we <= ws:
            problems.append("window_end must be after window_start")
        if self.opportunity_state not in OPPORTUNITY_STATES:
            problems.append(f"opportunity_state {self.opportunity_state!r}"
                            f" not in {OPPORTUNITY_STATES}")
        if self.state not in {s.value for s in TargetState}:
            problems.append(f"state {self.state!r} is not a target state")
        if self.state == TargetState.POSITIVE.value:
            problems.append("control windows cannot be POSITIVE")
        if self.state == TargetState.NEGATIVE.value and \
                self.opportunity_state != "OBSERVED_FULL":
            problems.append(
                "NEGATIVE control requires a linked OBSERVED_FULL "
                "observation opportunity — partial/unknown/unobserved "
                "coverage can never be a negative")
        return problems

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["record_type"] = type(self).__name__
        return d


@dataclass(frozen=True)
class CutoffRecordV0:
    """The complete timestamp model bound to one unit of evidence.

    All fields are explicit-UTC and required — including
    ``archive_availability`` (when the provider archive became
    retrievable, must be >= forecast_issue) and ``local_retrieval_time``
    (provenance; must be >= archive_availability and can never make a
    late product historically available).
    """

    cutoff_id: str
    source_observation_end: str
    source_processing_complete: str
    source_publication: str
    feature_availability: str
    forecast_initialization: str
    forecast_issue: str
    forecast_valid_start: str
    forecast_valid_end: str
    archive_availability: str = ""
    local_retrieval_time: str = ""
    forecast_vintage_id: str = ""
    source_id: str = ""              # E20 lineage
    event_id: str = ""               # E20 event-unit linkage
    event_time_start: Optional[str] = None
    event_time_end: Optional[str] = None

    def problems(self) -> list[str]:
        from .policy import cutoff_order_problems
        problems: list[str] = []
        _req(problems, "cutoff_id", self.cutoff_id)
        problems.extend(cutoff_order_problems(asdict(self)))
        # Optional event association fields are validated when present
        # (C11) — malformed optional times must not pass silently.
        for name in ("event_time_start", "event_time_end"):
            value = getattr(self, name)
            if value is not None:
                _ts(problems, name, value)
        es = parse_strict_utc(self.event_time_start)
        ee = parse_strict_utc(self.event_time_end)
        if es is not None and ee is not None and ee < es:
            problems.append("event_time_end precedes event_time_start")
        # A cutoff bound to a forecast vintage must associate an event
        # and its source lineage (E20).
        if self.forecast_vintage_id:
            if es is None or ee is None:
                problems.append("a vintage-bound cutoff requires "
                                "event_time_start/event_time_end")
            _req(problems, "event_id", self.event_id)
            _req(problems, "source_id", self.source_id)
        return problems

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["record_type"] = type(self).__name__
        return d


@dataclass(frozen=True)
class HoldoutPlanV0:
    """Basin/catchment-grouped holdout assigned before eligibility
    filtering.  Every declared group must receive at least one event,
    every event must map to a declared group, cascade groups are atomic,
    and evaluation regions are named — a count is not evidence."""

    holdout_plan_id: str
    assignment_rule: str              # basin/catchment/macroregion/fixed_spatial
    train_groups: tuple[str, ...] = ()
    validation_groups: tuple[str, ...] = ()
    test_groups: tuple[str, ...] = ()
    event_assignments: dict[str, str] = field(default_factory=dict)
    evaluation_region_names: tuple[str, ...] = ()
    assigned_before_filtering: bool = True
    test_locked: bool = True
    embargo_seconds: Optional[float] = None
    # P5-A2: typed contract exception — "evaluation_only" permits an
    # empty train partition ONLY when a waiver reason is declared
    # (events are evaluation labels, never fit inputs — an empty
    # event-train after hydrological adjudication is truthful, not a
    # gap).  Not a generic relaxation: validation/test groups remain
    # mandatory, disjoint, and fully assigned.
    holdout_mode: str = "standard"
    train_waiver_reason: str = ""

    def problems(self) -> list[str]:
        problems: list[str] = []
        _req(problems, "holdout_plan_id", self.holdout_plan_id)
        if self.assignment_rule not in ASSIGNMENT_RULES:
            problems.append(
                f"assignment_rule {self.assignment_rule!r} not in "
                f"{sorted(ASSIGNMENT_RULES)} — random row splits are "
                "prohibited")
        if self.holdout_mode not in ("standard", "evaluation_only"):
            problems.append(
                f"holdout_mode {self.holdout_mode!r} not in "
                "('standard', 'evaluation_only')")
        elif self.holdout_mode == "evaluation_only":
            if not self.train_waiver_reason or \
                    not self.train_waiver_reason.strip():
                problems.append("evaluation_only mode requires a "
                                "declared train_waiver_reason")
        else:
            if not self.train_groups:
                problems.append("train groups must be non-empty")
            if self.train_waiver_reason:
                problems.append("train_waiver_reason declared but "
                                "holdout_mode is 'standard' — a waiver "
                                "without its mode is invalid")
        if not self.validation_groups:
            problems.append("validation groups must be non-empty")
        if not self.test_groups:
            problems.append("locked test groups must be non-empty")
        for label, groups in (("train", self.train_groups),
                              ("validation", self.validation_groups),
                              ("test", self.test_groups),
                              ("evaluation_region",
                               self.evaluation_region_names)):
            if isinstance(groups, str) or not isinstance(
                    groups, (list, tuple)) or \
                    any(not isinstance(g, str) for g in groups):
                problems.append(f"{label} groups must be a "
                                "collection of group names")
            elif len(set(groups)) != len(groups):
                problems.append(f"{label} groups contain duplicates")
        if not isinstance(self.event_assignments, Mapping):
            problems.append("event_assignments must be a mapping of "
                            "event_id -> declared group")
        if not self.assigned_before_filtering:
            problems.append("group assignment must precede eligibility "
                            "filtering")
        if not self.test_locked:
            problems.append("test groups must be locked")
        if self.embargo_seconds is None:
            problems.append("embargo is unknown; experiment is blocked")
        else:
            emb = require_finite_seconds(self.embargo_seconds)
            if emb is None or emb < 0:
                problems.append("embargo_seconds must be finite and "
                                "non-negative")
        named = set(self.evaluation_region_names)
        if len(named) < 2 or \
                len(self.evaluation_region_names) != len(named):
            problems.append("at least two uniquely named independent "
                            "evaluation regions are required; a bare "
                            "count and single-box validation are not "
                            "evidence")
        # Regions must be drawn from the locked TEST groups — the
        # evaluation set is where generalization is measured — and a
        # single-box Langtang evaluation is explicitly rejected
        # (C17/E13).
        if self.evaluation_region_names:
            unmapped = named - set(self.test_groups)
            if unmapped:
                problems.append(
                    f"evaluation regions must be drawn from locked "
                    f"test groups; unmapped: {sorted(unmapped)}")
            if named == {"langtang"}:
                problems.append("Langtang-only evaluation is "
                                "prohibited")
        overlap = (set(self.train_groups) & set(self.validation_groups)
                   | set(self.train_groups) & set(self.test_groups)
                   | set(self.validation_groups) & set(self.test_groups))
        if overlap:
            problems.append(f"holdout groups overlap: {sorted(overlap)}")
        declared = (set(self.train_groups) | set(self.validation_groups)
                    | set(self.test_groups))
        if not self.event_assignments:
            problems.append("event_assignments is empty — the complete "
                            "event universe must be mapped before "
                            "filtering")
        else:
            stray = {g for g in self.event_assignments.values()
                     if g not in declared}
            if stray:
                problems.append(
                    f"event_assignments reference undeclared groups: "
                    f"{sorted(stray)}")
            uncovered = declared - set(self.event_assignments.values())
            if uncovered:
                problems.append(
                    f"declared groups with no assigned events: "
                    f"{sorted(uncovered)}")
        return problems

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["record_type"] = type(self).__name__
        return d


@dataclass(frozen=True)
class ForecastVintageV0:
    """One issue-time forecast source vintage, bound to actual archived
    bytes and a retrieval record — identifiers alone are not evidence.

    ``evidence_root`` names the directory under which
    ``archive_payload_path`` and ``retrieval_record_path`` resolve to
    real bytes (C14/E05).  ``problems()`` stays pure — field shapes
    only, never I/O — so a vintage admitted with
    ``evidence_root == ""`` is a metadata-only *candidate*: byte
    verification happens at the admission and evaluation boundaries
    (``build_vintage(..., require_bytes=True)``,
    ``evaluate(..., require_vintage_bytes=True)``, and
    ``nepal.research_v0._hashing.verify_vintage_evidence``), not here.
    A candidate may flow through ledgers and descriptive evaluation
    but can never ground a forecast-ready claim on its own."""

    vintage_id: str
    provider: str
    data_class: str                  # ForecastDataClass value
    initialization_time: str = ""
    issue_time: str = ""
    valid_start: str = ""
    valid_end: str = ""
    archive_availability: str = ""
    archive_payload_sha256: str = ""
    retrieval_record_sha256: str = ""
    archive_payload_path: str = ""     # under evidence_root (E05)
    retrieval_record_path: str = ""    # under evidence_root (E05)
    model_version: str = ""
    license_id: str = ""
    archive_mechanism: str = ""
    evidence_root: str = ""            # "" = metadata-only candidate

    def problems(self) -> list[str]:
        problems: list[str] = []
        _req(problems, "vintage_id", self.vintage_id)
        _req(problems, "provider", self.provider)
        _req(problems, "license_id", self.license_id)
        if self.data_class not in {c.value for c in ForecastDataClass}:
            problems.append(f"data_class {self.data_class!r} invalid")
            return problems
        if self.data_class in (ForecastDataClass.REANALYSIS.value,
                               ForecastDataClass.CURRENT_FEED.value):
            problems.append(
                f"data_class {self.data_class!r} is not admissible as a "
                "forecast vintage (reanalysis is retrospective; a "
                "rolling feed is not a historical archive)")
            return problems
        _req(problems, "model_version", self.model_version)
        _req(problems, "archive_mechanism", self.archive_mechanism)
        _sha(problems, "archive_payload_sha256",
             self.archive_payload_sha256)
        _sha(problems, "retrieval_record_sha256",
             self.retrieval_record_sha256)
        # A vintage is archive evidence: both files must be declared
        # and are byte-verified whenever an evidence_root is bound.
        _req(problems, "archive_payload_path", self.archive_payload_path)
        _req(problems, "retrieval_record_path",
             self.retrieval_record_path)
        # Shape only — evidence_root may legitimately be "" (a
        # metadata-only candidate); non-empty roots are byte-verified
        # at the admission/evaluation boundary, never here.
        if not isinstance(self.evidence_root, str):
            problems.append(
                "evidence_root must be a string naming the evidence "
                "root ('' marks a metadata-only candidate vintage)")
        times = {
            "initialization_time": _ts(problems, "initialization_time",
                                       self.initialization_time),
            "issue_time": _ts(problems, "issue_time", self.issue_time),
            "valid_start": _ts(problems, "valid_start", self.valid_start),
            "valid_end": _ts(problems, "valid_end", self.valid_end),
        }
        for name in ("initialization_time", "issue_time", "valid_start",
                     "valid_end"):
            if not getattr(self, name):
                problems.append(f"{self.data_class} requires {name}")
        for prev, nxt in (("initialization_time", "issue_time"),
                          ("issue_time", "valid_start"),
                          ("valid_start", "valid_end")):
            if times[prev] is not None and times[nxt] is not None and \
                    times[nxt] < times[prev]:
                problems.append(
                    f"vintage order violated: {nxt} precedes {prev}")
        aa = _ts(problems, "archive_availability",
                 self.archive_availability)
        if aa is not None and times["issue_time"] is not None and \
                aa < times["issue_time"]:
            problems.append("archive_availability precedes issue_time")
        return problems

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["record_type"] = type(self).__name__
        return d


@dataclass(frozen=True)
class RegimeArtifactV0:
    """An unsupervised regime artifact.  Clusters are candidate
    representations, never forecasts.  Requires >=3 independent seeds,
    a K=1 null, train-only preprocessing, label blinding, real stability
    and K-selection digests, and bound source digests — no inherited
    K=6 or prior performance may cross the boundary."""

    regime_id: str
    mode: str                        # RegimeMode value
    preprocessing_digest: str
    k_selection_digest: str = ""
    stability_report_digest: str = ""
    null_model_digest: str = ""      # K=1 null artifact (E19)
    source_digests: tuple[str, ...] = ()
    data_class: str = "REANALYSIS"   # regimes fit on reanalysis only
    fitted_on: str = "TRAIN_ONLY"
    k: int = 1                       # K=1 null is mandatory
    seeds: tuple[int, ...] = ()
    label_blinding: bool = True      # no event labels in fitting/selection
    # FCST-01: forecast-mode bindings — mandatory when
    # mode == "FORECAST_REGIME" (the bound forecast archive vintages
    # and the declared forecast feature set), forbidden otherwise.
    forecast_vintage_digests: tuple[str, ...] = ()
    forecast_feature_set: tuple[str, ...] = ()

    def problems(self) -> list[str]:
        problems: list[str] = []
        _req(problems, "regime_id", self.regime_id)
        if self.mode not in {m.value for m in RegimeMode}:
            problems.append(f"mode {self.mode!r} invalid")
        elif self.mode == RegimeMode.RETROSPECTIVE_REGIME.value:
            if self.data_class not in RETROSPECTIVE_REGIME_DATA_CLASSES:
                problems.append(
                    "retrospective regime discovery runs on a "
                    "declared retrospective data class "
                    f"{sorted(RETROSPECTIVE_REGIME_DATA_CLASSES)} — "
                    "never forecast archives or feeds")
            if self.forecast_vintage_digests or \
                    self.forecast_feature_set:
                problems.append(
                    "RETROSPECTIVE_REGIME forbids "
                    "forecast_vintage_digests/forecast_feature_set "
                    "— forecast bindings may not cross into the "
                    "retrospective lane")
        elif self.mode == RegimeMode.FORECAST_REGIME.value:
            if self.data_class not in FORECAST_REGIME_DATA_CLASSES:
                problems.append(
                    f"FORECAST_REGIME requires a forecast archive "
                    f"data_class in "
                    f"{sorted(FORECAST_REGIME_DATA_CLASSES)} — got "
                    f"{self.data_class!r}")
            if not self.forecast_vintage_digests:
                problems.append(
                    "FORECAST_REGIME requires non-empty "
                    "forecast_vintage_digests — the forecast "
                    "archive vintages the regime was fit on must "
                    "be bound")
            elif any(not SHA256_RE.match(str(d))
                     for d in self.forecast_vintage_digests):
                problems.append("every forecast_vintage_digest must "
                                "be 64-hex sha256")
            if not self.forecast_feature_set:
                problems.append(
                    "FORECAST_REGIME requires a non-empty "
                    "forecast_feature_set — the forecast features "
                    "the regime was fit on must be declared")
        if self.fitted_on != "TRAIN_ONLY":
            problems.append("regimes must be fit on training groups only")
        if not isinstance(self.k, int) or isinstance(
                self.k, bool) or self.k < 1:
            problems.append("k must be an integer >= 1; K=1 null is "
                            "required")
        if not self.seeds or len(set(self.seeds)) < 3:
            problems.append("at least three distinct seeds are "
                            "required")
        elif any(not isinstance(s, int) or isinstance(s, bool)
                 or s < 0 for s in self.seeds):
            problems.append("seeds must be distinct non-negative "
                            "integers")
        _sha(problems, "null_model_digest", self.null_model_digest)
        if not self.label_blinding:
            problems.append("event labels are prohibited in regime "
                            "fitting, K selection, and interpretation")
        _sha(problems, "preprocessing_digest", self.preprocessing_digest)
        _sha(problems, "k_selection_digest", self.k_selection_digest)
        _sha(problems, "stability_report_digest",
             self.stability_report_digest)
        if not self.source_digests:
            problems.append("source_digests is required — the regime "
                            "must bind the data it was fit on")
        elif any(not SHA256_RE.match(str(d)) for d in self.source_digests):
            problems.append("every source_digest must be 64-hex sha256")
        return problems

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["record_type"] = type(self).__name__
        return d


@dataclass(frozen=True)
class ForecastExperimentV0:
    """A predeclared archived-forecast/reforecast experiment.

    Target is restricted to ``occurrence`` — exposure/impact are
    separate analyses, never physical-occurrence targets.  Horizon must
    be a policy-admissible value.  Feature and vintage digest sets must
    be non-empty; power and uncertainty artifacts are required.
    """

    experiment_id: str
    vertical_id: str
    target: str
    horizon: str
    holdout_plan_id: str
    feature_digests: tuple[str, ...] = ()
    vintage_digests: tuple[str, ...] = ()
    baselines: tuple[str, ...] = ()
    metrics: tuple[str, ...] = ()
    missing_data_policy: str = ""
    power_report_digest: str = ""
    uncertainty_method: str = ""
    evaluation_region_count: int = 0
    test_locked: bool = True
    claim_scope: str = "research_only_no_operational_authorization"

    def problems(self) -> list[str]:
        problems: list[str] = []
        for name in ("experiment_id", "vertical_id", "target",
                     "horizon", "holdout_plan_id",
                     "missing_data_policy", "uncertainty_method"):
            _req(problems, name, getattr(self, name))
        if self.vertical_id and self.vertical_id not in VERTICAL_IDS:
            problems.append(f"vertical_id {self.vertical_id!r} not in "
                            "controlled ontology")
        if self.target and self.target not in EXPERIMENT_TARGETS:
            problems.append(
                f"target {self.target!r} not in {sorted(EXPERIMENT_TARGETS)}"
                " — exposure/impact are separate analyses, never "
                "physical-occurrence targets")
        if self.horizon and self.horizon not in HORIZON_SECONDS:
            problems.append(
                f"horizon {self.horizon!r} is not an admissible policy "
                f"horizon {sorted(HORIZON_SECONDS)}")
        if not self.feature_digests:
            problems.append("feature_digests must be non-empty — an "
                            "empty feature set cannot be an experiment")
        elif any(not SHA256_RE.match(str(d))
                 for d in self.feature_digests):
            problems.append("every feature_digest must be 64-hex sha256")
        if not self.vintage_digests:
            problems.append("every forecast experiment must bind at "
                            "least one ForecastVintageV0 digest")
        elif any(not SHA256_RE.match(str(d))
                 for d in self.vintage_digests):
            problems.append("every vintage_digest must be 64-hex sha256")
        _sha(problems, "power_report_digest", self.power_report_digest)
        required_baselines = {"climatology", "rule", "regularized_supervised"}
        if not required_baselines.issubset(set(self.baselines)):
            problems.append(
                "baselines must include climatology, rule, and "
                "regularized_supervised")
        required_metrics = {"brier", "calibration", "precision_recall",
                            "event_recall", "false_alarms_per_opportunity"}
        if not required_metrics.issubset(set(self.metrics)):
            problems.append(
                "metrics must include brier, calibration, "
                "precision_recall, event_recall, and "
                "false_alarms_per_opportunity")
        if self.evaluation_region_count < 2:
            problems.append("at least two independent evaluation regions "
                            "required; Langtang-only results prohibited")
        if not self.test_locked:
            problems.append("locked test data must not influence tuning")
        if self.claim_scope != "research_only_no_operational_authorization":
            problems.append("claim_scope is fixed to research-only")
        return problems

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["record_type"] = type(self).__name__
        return d


@dataclass(frozen=True)
class ResearchClaimEnvelopeV0:
    """The outer binding for any research claim.

    Authority flags are enforced false-only at construction; the
    envelope ID and both document digests are structurally validated —
    no code path can produce an empty, malformed, or authority-bearing
    envelope.
    """

    envelope_id: str
    status: str
    matrix_sha256: str
    policy_sha256: str
    record_digests: dict[str, str] = field(default_factory=dict)
    research_diagnostic_only: bool = True
    claim_scope: str = "research_only_no_operational_authorization"
    promotion_eligible: bool = False
    production_authorized: bool = False
    warning_path_authorized: bool = False

    def __post_init__(self) -> None:
        violations = []
        if not str(self.envelope_id or "").strip():
            violations.append("envelope_id must be non-empty")
        if not SHA256_RE.match(str(self.matrix_sha256 or "")):
            violations.append("matrix_sha256 must be a 64-hex digest")
        if not SHA256_RE.match(str(self.policy_sha256 or "")):
            violations.append("policy_sha256 must be a 64-hex digest")
        if not isinstance(self.record_digests, dict) or not all(
                isinstance(k, str) and isinstance(v, str) and
                SHA256_RE.match(v)
                for k, v in self.record_digests.items()):
            violations.append("record_digests must map string names to "
                              "64-hex digests")
        if self.research_diagnostic_only is not True:
            violations.append("research_diagnostic_only must be True")
        if self.claim_scope != "research_only_no_operational_authorization":
            violations.append("claim_scope is fixed to "
                              "research_only_no_operational_authorization")
        if self.promotion_eligible is not False:
            violations.append("promotion_eligible must be False")
        if self.production_authorized is not False:
            violations.append("production_authorized must be False")
        if self.warning_path_authorized is not False:
            violations.append("warning_path_authorized must be False")
        if self.status not in NEUTRAL_RESEARCH_STATUSES:
            violations.append(
                f"status {self.status!r} is not an approved neutral "
                "research status")
        if violations:
            raise ValueError("ResearchClaimEnvelopeV0 rejected: "
                             + "; ".join(violations))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["record_type"] = type(self).__name__
        return d


@dataclass(frozen=True)
class EvidenceArtifactV0:
    """A byte-bound reference to a real artifact (C19/C20): feature
    matrix, power report, stability report, K-selection record,
    forecast output, evaluation report, source evidence.  Digests are
    verified against actual files under an evidence root at envelope
    build time — a fabricated 64-hex string cannot bind."""

    artifact_id: str
    artifact_type: str               # ARTIFACT_TYPES
    path: str                        # path under evidence root
    sha256: str = ""
    size_bytes: Optional[int] = None
    as_of_date: str = ""

    def problems(self) -> list[str]:
        problems: list[str] = []
        _req(problems, "artifact_id", self.artifact_id)
        _req(problems, "path", self.path)
        if self.artifact_type not in ARTIFACT_TYPES:
            problems.append(f"artifact_type {self.artifact_type!r} not "
                            f"in {sorted(ARTIFACT_TYPES)}")
        _sha(problems, "sha256", self.sha256)
        if self.size_bytes is None or not isinstance(
                self.size_bytes, int) or isinstance(
                self.size_bytes, bool) or self.size_bytes < 0:
            problems.append("size_bytes must be a non-negative "
                            "integer")
        _date(problems, "as_of_date", self.as_of_date)
        return problems

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["record_type"] = type(self).__name__
        return d


# Approved record classes that may be bound into an envelope — keyed by
# class name for display, matched by exact identity at bind time (C04).
@dataclass(frozen=True)
class RunManifestV0:
    """Provenance for one executed run (S13): worker identity,
    environment, seed, and input/output byte digests — a run that
    cannot name its exact inputs is not reproducible."""

    run_id: str
    worker_id: str = ""
    created_at: str = ""
    environment_digest: str = ""   # sha256 of locked env / requirements
    seed: Optional[int] = None
    input_digests: tuple[str, ...] = ()
    output_digests: tuple[str, ...] = ()
    checkpoint_policy: str = ""    # e.g. "atomic_publish_or_quarantine"
    status: str = "PLANNED"        # PLANNED | COMPLETED | QUARANTINED

    def problems(self) -> list[str]:
        problems: list[str] = []
        _req(problems, "run_id", self.run_id)
        _req(problems, "worker_id", self.worker_id)
        _ts(problems, "created_at", self.created_at)
        _sha(problems, "environment_digest", self.environment_digest)
        if self.seed is not None and (not isinstance(self.seed, int)
                                      or isinstance(self.seed, bool)
                                      or self.seed < 0):
            problems.append("seed must be a non-negative integer")
        if not self.input_digests:
            problems.append("input_digests must be non-empty")
        elif any(not SHA256_RE.match(str(d))
                 for d in self.input_digests):
            problems.append("every input_digest must be 64-hex sha256")
        if any(not SHA256_RE.match(str(d))
               for d in self.output_digests):
            problems.append("every output_digest must be 64-hex "
                            "sha256")
        if self.status not in ("PLANNED", "COMPLETED", "QUARANTINED"):
            problems.append(f"run status {self.status!r} invalid")
        if self.status == "COMPLETED" and not self.output_digests:
            problems.append("a completed run must name its output "
                            "digests")
        return problems

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["record_type"] = type(self).__name__
        return d


RECORD_CLASSES = {
    "HazardVerticalSpecV0": HazardVerticalSpecV0,
    "SourceRecordV0": SourceRecordV0,
    "EventLabelV0": EventLabelV0,
    "ObservationOpportunityV0": ObservationOpportunityV0,
    "ControlWindowV0": ControlWindowV0,
    "CutoffRecordV0": CutoffRecordV0,
    "HoldoutPlanV0": HoldoutPlanV0,
    "ForecastVintageV0": ForecastVintageV0,
    "RegimeArtifactV0": RegimeArtifactV0,
    "ForecastExperimentV0": ForecastExperimentV0,
    "EvidenceArtifactV0": EvidenceArtifactV0,
    "RunManifestV0": RunManifestV0,
}
RECORD_TYPES = frozenset(RECORD_CLASSES)


def deserialize_record(payload: Mapping[str, Any]) -> Any:
    """Exact typed deserialization (E01): the ``record_type`` tag must
    name an approved class, every constructor field must be present,
    and unknown fields reject — no partial or extra keys."""
    if not isinstance(payload, Mapping):
        raise ValueError("record payload must be a JSON object")
    tag = payload.get("record_type")
    cls = RECORD_CLASSES.get(tag) if isinstance(tag, str) else None
    if cls is None:
        raise ValueError(f"record_type {tag!r} is not an approved "
                         f"record class")
    import dataclasses
    declared = {f.name for f in dataclasses.fields(cls)}
    payload_keys = set(payload) - {"record_type"}
    missing = declared - payload_keys
    extra = payload_keys - declared
    # Missing keys are allowed only when the field has a default.
    defaulted = {f.name for f in dataclasses.fields(cls)
                 if f.default is not dataclasses.MISSING
                 or f.default_factory is not dataclasses.MISSING}
    hard_missing = missing - defaulted
    if hard_missing:
        raise ValueError(f"{tag}: missing required fields "
                         f"{sorted(hard_missing)}")
    if extra:
        raise ValueError(f"{tag}: unknown fields {sorted(extra)}")
    kwargs = {k: payload[k] for k in payload_keys}
    try:
        return cls(**kwargs)
    except TypeError as exc:
        raise ValueError(f"{tag}: deserialization failed: {exc}") \
            from exc


# Phase-4 R1 amendment (ratified 2026-09-19): the run-evidence wrapper
# lives in run_evidence.py — a module that depends only on _hashing —
# and is re-exported here so the pinned contract surface
# ``nepal.research_v0.records.RunEvidenceManifestV0`` resolves.
from .run_evidence import (  # noqa: E402,F401
    RunEvidenceManifestV0, run_evidence_binding_problems,
    semantic_binding_problems, wrapper_declaration_problems,
    wrapper_from_mapping)
