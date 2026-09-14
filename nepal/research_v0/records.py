"""Record types for the additive research-only namespace (P4).

The eleven record types bind the v0 science-design contracts to data.
All records are frozen dataclasses; ``to_dict`` produces the canonical
JSON-able form hashed into claim envelopes.  Validation surfaces as
``problems()`` — a non-empty list means the record is inadmissible.

Semantic hardening (post-audit A02–A13): authority flags are enforced
false-only at construction; timestamps must be explicit-UTC; intervals
must be ordered; cascades must be completely mapped; negative controls
require a linked full-observation opportunity; envelopes validate every
record before hashing.

Nothing in this module authorizes intake, freeze, clustering, or any
operational claim.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from .policy import (OPPORTUNITY_STATES, EventTimeClass, ForecastDataClass,
                     RegimeMode, TargetState, classify_event_time,
                     parse_strict_utc, require_finite_seconds)

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

# Controlled vertical ontology (G03): a spec may only claim one of these
# physical units, with its mechanism allowlist.
VERTICAL_IDS = frozenset({
    "snow_avalanche", "ice_rock_avalanche", "glof", "landslide_rainfall",
    "landslide_coseismic", "ldof", "dam_breach_engineered"})
MECHANISM_IDS = frozenset({
    "snow_release", "ice_rock_failure", "glacier_detachment",
    "lake_outburst", "slope_initiation_rainfall",
    "slope_initiation_coseismic", "natural_dam_breach",
    "engineered_breach", "embankment_breach"})

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

# Neutral research statuses — the only statuses a claim envelope may
# carry.  READY-shaped or authority-shaped statuses are absent by
# construction.
NEUTRAL_RESEARCH_STATUSES = frozenset({
    "BASELINE_RECONCILED",
    "SOURCE_MATRIX_COMPLETE",
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
    "BASELINE_RECONCILED", "SOURCE_MATRIX_COMPLETE",
    "NO_QUALIFYING_PILOT_SOURCE", "DESIGN_DRAFT_COMPLETE",
    "RESEARCH_PATH_ISOLATED", "FMX_BLOCKED_CUTOFF",
    "UNDERPOWERED_DESCRIPTIVE_ONLY", "DEFERRED_NO_OPEN_TIMED_SOURCE"})


def _req(problems: list[str], name: str, value: Any) -> None:
    if value is None or value == "" or value == [] or value == ():
        problems.append(f"{name} is required")


def _ts(problems: list[str], name: str, value: Any) -> Optional[float]:
    parsed = parse_strict_utc(value)
    if parsed is None:
        problems.append(
            f"{name} must be an explicit-UTC RFC3339 timestamp or "
            "finite epoch seconds")
        return None
    return parsed


@dataclass(frozen=True)
class HazardVerticalSpecV0:
    """One hazard vertical's science ontology boundary."""

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
        return asdict(self)


@dataclass(frozen=True)
class SourceRecordV0:
    """Metadata-only record for one candidate event inventory.

    ``posture`` is CANDIDATE_ONLY until exact version, license,
    geography, fields, and timing semantics are verified at intake.
    EVIDENCE_VERIFIED additionally requires a versioned evidence
    sidecar digest and INDEPENDENTLY_VERIFIED review state.
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
    evidence_sidecar_sha256: str = ""
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
            for name in ("version", "license_id", "redistribution",
                         "geography", "event_time_class",
                         "non_event_frame", "access_status"):
                _req(problems, f"EVIDENCE_VERIFIED requires {name}",
                     getattr(self, name))
            if len(self.evidence_sidecar_sha256) != 64:
                problems.append("EVIDENCE_VERIFIED requires a "
                                "64-hex evidence_sidecar_sha256")
            if self.evidence_review_state != "INDEPENDENTLY_VERIFIED":
                problems.append("EVIDENCE_VERIFIED requires "
                                "INDEPENDENTLY_VERIFIED review state")
        return problems

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EventLabelV0:
    """One adjudicated (or pending) event label.

    Event intervals are explicit-UTC; ``event_time_precision`` must be a
    controlled term consistent with the measured ``uncertainty_seconds``.
    A scene interval is an uncertainty width, never a release timestamp.
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
    adjudication_state: str = "UNADJUDICATED"
    adjudication_notes: str = ""
    reviewer_ids: tuple[str, ...] = ()

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
        if start is not None and end is not None and end < start:
            problems.append("event_time_end precedes event_time_start — "
                            "inverted interval")
        if self.event_time_precision and \
                self.event_time_precision not in PRECISION_TERMS:
            problems.append(
                f"event_time_precision {self.event_time_precision!r} not "
                f"in {sorted(PRECISION_TERMS)}")
        unc = require_finite_seconds(self.uncertainty_seconds)
        if self.uncertainty_seconds is not None and unc is None:
            problems.append("uncertainty_seconds must be finite")
        elif unc is not None and unc < 0:
            problems.append("uncertainty_seconds must be non-negative")
        # Precision/consistency: declared precision class must agree
        # with the measured uncertainty width.
        if unc is not None and self.event_time_precision in \
                _PRECISION_TO_CLASS:
            declared = _PRECISION_TO_CLASS[self.event_time_precision]
            measured = classify_event_time(unc)
            if declared is not None and declared != measured:
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
        if self.adjudication_state in POSITIVE_ADMISSIBLE_ADJUDICATION \
                and len(self.reviewer_ids) < 2:
            problems.append(
                "adjudicated labels require >=2 reviewer_ids")
        return problems

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ObservationOpportunityV0:
    """Whether a forecast unit was actually observable over a window.

    ``coverage_fraction`` must be consistent with ``state``: FULL needs
    ~1.0, PARTIAL is (0,1), UNOBSERVED is 0.  Windows must be ordered.
    """

    opportunity_id: str
    unit_id: str
    platform: str
    window_start: str
    window_end: str
    coverage_fraction: Optional[float] = None
    coverage_quality: str = ""
    detection_threshold: str = ""
    state: str = "UNKNOWN"

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
        if cov is not None:
            c = require_finite_seconds(cov)
            if c is None or not 0.0 <= c <= 1.0:
                problems.append("coverage_fraction must be finite in "
                                "[0,1]")
                c = None
        else:
            c = None
        if self.state == "OBSERVED_FULL":
            if c is None or c < 0.999:
                problems.append("OBSERVED_FULL requires "
                                "coverage_fraction ~= 1.0")
        elif self.state == "OBSERVED_PARTIAL":
            if c is None or not 0.0 < c < 1.0:
                problems.append("OBSERVED_PARTIAL requires "
                                "coverage_fraction in (0,1)")
        elif self.state == "UNOBSERVED" and c not in (None, 0.0):
            problems.append("UNOBSERVED requires coverage_fraction 0.0 "
                            "or absent")
        return problems

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ControlWindowV0:
    """A non-event control.  ``state`` may be NEGATIVE only when the
    linked observation opportunity state is OBSERVED_FULL."""

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
        return asdict(self)


@dataclass(frozen=True)
class CutoffRecordV0:
    """The full timestamp model bound to one unit of evidence.

    All fields are explicit-UTC.  ``local_retrieval_time`` is provenance
    only and can never make a late product historically available.
    ``archive_availability`` records when the provider archive became
    retrievable and must be >= ``forecast_issue``.
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
    archive_availability: Optional[str] = None
    forecast_vintage_id: str = ""
    event_time_start: Optional[str] = None
    event_time_end: Optional[str] = None
    local_retrieval_time: Optional[str] = None

    def problems(self) -> list[str]:
        from .policy import cutoff_order_problems
        problems: list[str] = []
        _req(problems, "cutoff_id", self.cutoff_id)
        problems.extend(cutoff_order_problems(asdict(self)))
        return problems

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HoldoutPlanV0:
    """Basin/catchment-grouped holdout assigned before eligibility
    filtering.  Cascade groups are atomic across splits and every event
    must be mapped to exactly one group."""

    holdout_plan_id: str
    assignment_rule: str              # basin/catchment/macroregion/fixed_spatial
    train_groups: tuple[str, ...] = ()
    validation_groups: tuple[str, ...] = ()
    test_groups: tuple[str, ...] = ()
    event_assignments: dict[str, str] = field(default_factory=dict)
    evaluation_region_count: int = 0
    assigned_before_filtering: bool = True
    test_locked: bool = True
    embargo_seconds: Optional[float] = None

    def problems(self) -> list[str]:
        problems: list[str] = []
        _req(problems, "holdout_plan_id", self.holdout_plan_id)
        if self.assignment_rule not in ASSIGNMENT_RULES:
            problems.append(
                f"assignment_rule {self.assignment_rule!r} not in "
                f"{sorted(ASSIGNMENT_RULES)} — random row splits are "
                "prohibited")
        if not self.train_groups:
            problems.append("train groups must be non-empty")
        if not self.validation_groups:
            problems.append("validation groups must be non-empty")
        if not self.test_groups:
            problems.append("locked test groups must be non-empty")
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
        if self.evaluation_region_count < 2:
            problems.append("at least two independent evaluation regions "
                            "are required; no single-box validation")
        overlap = (set(self.train_groups) & set(self.validation_groups)
                   | set(self.train_groups) & set(self.test_groups)
                   | set(self.validation_groups) & set(self.test_groups))
        if overlap:
            problems.append(f"holdout groups overlap: {sorted(overlap)}")
        declared = (set(self.train_groups) | set(self.validation_groups)
                    | set(self.test_groups))
        if not self.event_assignments:
            problems.append("event_assignments is empty — every event "
                            "must map to a group")
        else:
            stray = {g for g in self.event_assignments.values()
                     if g not in declared}
            if stray:
                problems.append(
                    f"event_assignments reference undeclared groups: "
                    f"{sorted(stray)}")
        return problems

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ForecastVintageV0:
    """One issue-time forecast source vintage.

    For REFORECAST/ARCHIVED_OPERATIONAL the chain
    ``initialization <= issue <= valid_start <= valid_end`` must hold
    and archive provenance is required.
    """

    vintage_id: str
    provider: str
    data_class: str                  # ForecastDataClass value
    initialization_time: str = ""
    issue_time: str = ""
    valid_start: str = ""
    valid_end: str = ""
    archive_availability: str = ""
    model_version: str = ""
    license_id: str = ""
    archive_mechanism: str = ""

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
        seq = [("initialization_time", "issue_time"),
               ("issue_time", "valid_start"),
               ("valid_start", "valid_end")]
        for prev, nxt in seq:
            if times[prev] is not None and times[nxt] is not None and \
                    times[nxt] < times[prev]:
                problems.append(
                    f"vintage order violated: {nxt} precedes {prev}")
        if self.archive_availability:
            aa = _ts(problems, "archive_availability",
                     self.archive_availability)
            if aa is not None and times["issue_time"] is not None and \
                    aa < times["issue_time"]:
                problems.append("archive_availability precedes issue_time")
        else:
            problems.append("archive_availability is required for "
                            "forecast vintages")
        return problems

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RegimeArtifactV0:
    """An unsupervised regime artifact.  Clusters are candidate
    representations, never forecasts.  Requires >=3 independent seeds,
    a K=1 null, train-only preprocessing, and label blinding."""

    regime_id: str
    mode: str                        # RegimeMode value
    preprocessing_digest: str
    fitted_on: str = "TRAIN_ONLY"
    k: int = 1                       # K=1 null is mandatory
    seeds: tuple[int, ...] = ()
    label_blinding: bool = True      # no event labels in fitting/selection
    stability_report_digest: str = ""

    def problems(self) -> list[str]:
        problems: list[str] = []
        _req(problems, "regime_id", self.regime_id)
        if self.mode not in {m.value for m in RegimeMode}:
            problems.append(f"mode {self.mode!r} invalid")
        if self.fitted_on != "TRAIN_ONLY":
            problems.append("regimes must be fit on training groups only")
        if self.k < 1:
            problems.append("k must be >= 1; K=1 null is required")
        if len(set(self.seeds)) < 3:
            problems.append("at least three independent seeds are "
                            "required")
        if not self.label_blinding:
            problems.append("event labels are prohibited in regime "
                            "fitting, K selection, and interpretation")
        _req(problems, "preprocessing_digest", self.preprocessing_digest)
        if len(self.preprocessing_digest) != 64:
            problems.append("preprocessing_digest must be a 64-hex "
                            "sha256 of the frozen preprocessing spec")
        return problems

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ForecastExperimentV0:
    """A predeclared archived-forecast/reforecast experiment."""

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
    evaluation_region_count: int = 0
    test_locked: bool = True
    claim_scope: str = "research_only_no_operational_authorization"

    def problems(self) -> list[str]:
        problems: list[str] = []
        for name in ("experiment_id", "vertical_id", "target",
                     "horizon", "holdout_plan_id",
                     "missing_data_policy"):
            _req(problems, name, getattr(self, name))
        if self.vertical_id and self.vertical_id not in VERTICAL_IDS:
            problems.append(f"vertical_id {self.vertical_id!r} not in "
                            "controlled ontology")
        if not self.vintage_digests:
            problems.append("every forecast experiment must bind at "
                            "least one ForecastVintageV0 digest")
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
        return asdict(self)


@dataclass(frozen=True)
class ResearchClaimEnvelopeV0:
    """The outer binding for any research claim.

    Authority flags are enforced false-only at construction — no code
    path can produce an envelope asserting promotion, production, or
    warning authority.  Construct claim envelopes via
    ``gates.build_claim_envelope`` so record validation and approval
    binding are applied.
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
        return asdict(self)
