"""Typed contracts for the seismic event-detection sidecar.

Research-only: this module binds the vocabulary the seismic sidecar is
allowed to emit — the ``StationObservabilityV0`` gate record, the
waveform feature-column contract, the catalog-context contract (kept
strictly separate), the ``SeismicSidecarConfig`` run declaration, and
the ``SEISMIC_DETECTION_RECEIPT_V0`` receipt shape.

Nothing here downloads data, asserts observability, authorizes intake,
or emits forecast/warning/production/promotion authority.  All records
are frozen dataclasses; ``validate`` returns problem strings — a
non-empty list means the record is inadmissible.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from nepal.research_v0.policy import (
    SEISMIC_WAVEFORM_RETROSPECTIVE_CLASS, parse_strict_utc)

#: The retrospective-only data class seismic regime artifacts carry.
SEISMIC_WAVEFORM_RETROSPECTIVE = SEISMIC_WAVEFORM_RETROSPECTIVE_CLASS

SEISMIC_RECEIPT_TYPE = "SEISMIC_DETECTION_RECEIPT_V0"
STATION_OBSERVABILITY_TYPE = "StationObservabilityV0"

#: The receipt's fixed claim scope — post-initiation abnormal-event
#: detection only; never cause attribution, calibrated forecasting,
#: community warning, association, or operations.
SEISMIC_CLAIM_SCOPE = "research_only_post_initiation_detection"

OBSERVABILITY_STATUSES = frozenset({"OBSERVABLE", "UNOBSERVABLE"})
RECEIPT_STATUSES = frozenset({
    "RUN_ERROR", "UNOBSERVABLE", "CANDIDATE_ONLY",
    "UNDERPOWERED_DESCRIPTIVE_ONLY", "DESCRIPTIVE_REGIME_ONLY"})

#: Three-component orientation vocabulary: ``ORTHO_3C`` is the
#: standard E/N/Z layout; ``ROTATED_3C`` covers rotated horizontals
#: (e.g. FDSN HH1/HH2/HHZ) — still a usable three-component record but
#: never silently renamed to E/N; ``PARTIAL``/``MISSING`` fail the
#: component gate under a three-component policy.
ORIENTATION_STATUSES = frozenset(
    {"ORTHO_3C", "ROTATED_3C", "PARTIAL", "MISSING"})
RESPONSE_STATUSES = frozenset({"BOUND", "MISSING", "INVALID"})

#: Identity/provenance columns carried on the window-level frame —
#: partition carriers and provenance, never predictors.
SEISMIC_IDENTITY_COLUMNS = (
    "window_start", "window_end", "station_id", "unit_id",
    "basin_group", "date")

#: Waveform-derived value features OUTSIDE the band-energy family —
#: the band columns are generated from the declared band edges by
#: ``features.band_feature_names``; the contract fixes the naming
#: RULE, not a single band set.  ``seis_coherence`` is OPTIONAL —
#: present only when two or more calibrated stations overlap; it is
#: never zero-filled to simulate unavailable cross-station evidence.
SEISMIC_NONBAND_FEATURES = (
    "seis_rsam", "seis_sta_lta",
    "seis_centroid_hz", "seis_median_hz", "seis_dominant_hz",
    "seis_entropy", "seis_kurtosis", "seis_crest_factor",
    "seis_duration_s", "seis_snr_db",
    "seis_coverage_fraction", "seis_gap_fraction", "seis_coherence")

#: The declared default bands — the sidecar's documented starting
#: configuration; custom deployments declare their own bands in
#: ``SeismicSidecarConfig.bands`` and the generated names stay in the
#: same ``seis_band_<lo>_<hi>hz`` family.
SEISMIC_DEFAULT_BANDS = ((0.5, 2.0), (2.0, 8.0), (8.0, 20.0))

#: The canonical default-band feature surface — the declared contract
#: columns for a default-band deployment.
SEISMIC_VALUE_FEATURES = (
    "seis_rsam", "seis_sta_lta",
    "seis_band_0p5_2hz", "seis_band_2_8hz", "seis_band_8_20hz",
    "seis_centroid_hz", "seis_median_hz", "seis_dominant_hz",
    "seis_entropy", "seis_kurtosis", "seis_crest_factor",
    "seis_duration_s", "seis_snr_db",
    "seis_coverage_fraction", "seis_gap_fraction", "seis_coherence")

#: Declared units for each value feature (FMX audit binding).
#: UNIT POLICY — raw instrument-domain diagnostics: features are
#: computed on raw samples in instrument units; StationXML response
#: metadata is bound as evidence (byte digest) but NO response
#: correction is applied.  ``seis_rsam`` and ``seis_crest_factor``
#: are therefore instrument-domain quantities — physical-unit
#: (m/s) claims are prohibited until a response-corrected pipeline
#: is separately qualified.
SEISMIC_FEATURE_UNITS = {
    "seis_rsam": "counts (instrument domain) — component-averaged "
                "RMS amplitude; raw instrument units, NOT "
                "response-corrected physical units",
    "seis_sta_lta": "1 — max short-term/long-term energy ratio",
    "seis_band_0p5_2hz": "1 — fraction of window power in 0.5-2 Hz",
    "seis_band_2_8hz": "1 — fraction of window power in 2-8 Hz",
    "seis_band_8_20hz": "1 — fraction of window power in 8-20 Hz",
    "seis_centroid_hz": "Hz — power-weighted spectral centroid",
    "seis_median_hz": "Hz — power-weighted median frequency",
    "seis_dominant_hz": "Hz — frequency of maximum band power",
    "seis_entropy": "1 — normalized spectral entropy",
    "seis_kurtosis": "1 — Fisher excess kurtosis of the energy trace",
    "seis_crest_factor": "1 — peak amplitude / RMS",
    "seis_duration_s": "s — covered waveform duration in the window",
    "seis_snr_db": "dB — 10*log10(signal/noise) vs declared noise floor",
    "seis_coverage_fraction": "1 — covered fraction of the window",
    "seis_gap_fraction": "1 — uncovered fraction of the window",
    "seis_coherence": "1 — mean magnitude-squared cross-station coherence",
}

#: Derived (non-waveform) columns the runner adds for the regime
#: surface — declared here so they can never be confused with
#: waveform features.
SEISMIC_DERIVED_COLUMNS = ("seis_window_count", "season", "holdout_cell")

#: Catalog context (USGS-style event metadata) is a SEPARATE channel —
#: never a waveform feature and never inside the waveform feature
#: digest unless a declared catalog ablation binds it separately.
CATALOG_CONTEXT_COLUMNS = (
    "catalog_event_count", "catalog_nearest_magnitude",
    "catalog_nearest_distance_km", "catalog_hours_since_origin")

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _req(problems: list[str], name: str, value: Any) -> None:
    if value is None or value == "" or value == [] or value == ():
        problems.append(f"{name} is required")


def _finite(value: Any) -> bool:
    return not isinstance(value, bool) and \
        isinstance(value, (int, float)) and math.isfinite(value)


@dataclass(frozen=True)
class StationObservabilityV0:
    """One station's typed observability gate record.

    Bound to the byte-bound source manifest via
    ``source_manifest_digest``.  ``status`` is derived at build time:
    ``OBSERVABLE`` only when every declared gate (channels, response,
    coverage, gaps, SNR, timestamps, waveform availability) passes —
    otherwise ``UNOBSERVABLE`` with every failure recorded in
    ``problems``.  Missing evidence is a verdict, never a zero.
    """

    station_id: str
    network: str
    station: str
    location: str = ""
    channel_set: tuple = ()
    target_latitude: float = 0.0
    target_longitude: float = 0.0
    distance_km: float = 0.0
    component_count: int = 0
    sample_rate_hz: float = 0.0
    response_relpath: str = ""
    response_sha256: str = ""
    coverage_start: str = ""
    coverage_end: str = ""
    coverage_fraction: float = 0.0
    gap_fraction: float = 1.0
    latency_seconds: float = 0.0
    noise_floor_by_band: Mapping = field(default_factory=dict)
    snr_by_band: Mapping = field(default_factory=dict)
    orientation_status: str = "MISSING"
    response_status: str = "MISSING"
    status: str = "UNOBSERVABLE"
    source_manifest_digest: str = ""
    problems: tuple = ()

    def validate(self) -> list[str]:
        """Structural (non-gate) validation of a carried record."""
        problems: list[str] = []
        for name in ("station_id", "network", "station"):
            _req(problems, name, getattr(self, name))
        if not isinstance(self.channel_set, (list, tuple)) or \
                not self.channel_set or \
                any(not isinstance(c, str) or not c.strip()
                    for c in self.channel_set):
            problems.append("channel_set must be a non-empty tuple of "
                            "channel codes")
        elif len(set(self.channel_set)) != len(self.channel_set):
            problems.append("channel_set contains duplicate channel "
                            "codes — one binding per channel")
        for name in ("target_latitude", "target_longitude",
                     "distance_km", "sample_rate_hz",
                     "coverage_fraction", "gap_fraction",
                     "latency_seconds"):
            v = getattr(self, name)
            if not _finite(v):
                problems.append(f"{name} must be a finite number; got "
                                f"{v!r}")
        if _finite(self.target_latitude) and \
                not -90.0 <= self.target_latitude <= 90.0:
            problems.append("target_latitude outside [-90, 90]")
        if _finite(self.target_longitude) and \
                not -180.0 <= self.target_longitude <= 180.0:
            problems.append("target_longitude outside [-180, 180]")
        if _finite(self.distance_km) and self.distance_km < 0:
            problems.append("distance_km must be non-negative")
        if _finite(self.sample_rate_hz) and self.sample_rate_hz <= 0:
            problems.append("sample_rate_hz must be positive")
        if _finite(self.coverage_fraction) and \
                not 0.0 <= self.coverage_fraction <= 1.0:
            problems.append("coverage_fraction outside [0, 1]")
        if _finite(self.gap_fraction) and \
                not 0.0 <= self.gap_fraction <= 1.0:
            problems.append("gap_fraction outside [0, 1]")
        if _finite(self.latency_seconds) and self.latency_seconds < 0:
            problems.append("latency_seconds must be non-negative")
        if isinstance(self.component_count, bool) or \
                not isinstance(self.component_count, int) or \
                self.component_count < 0:
            problems.append("component_count must be a non-negative "
                            "integer")
        cs = parse_strict_utc(self.coverage_start)
        ce = parse_strict_utc(self.coverage_end)
        if cs is None:
            problems.append("coverage_start must be an explicit-UTC "
                            "timestamp")
        if ce is None:
            problems.append("coverage_end must be an explicit-UTC "
                            "timestamp")
        if cs is not None and ce is not None and ce <= cs:
            problems.append("coverage_end must be after "
                            "coverage_start")
        if self.response_relpath and \
                not _SHA256_RE.match(str(self.response_sha256)):
            problems.append("response_sha256 must be a 64-hex sha256 "
                            "when response_relpath is bound")
        for name, bandmap in (("noise_floor_by_band",
                               self.noise_floor_by_band),
                              ("snr_by_band", self.snr_by_band)):
            if not isinstance(bandmap, Mapping):
                problems.append(f"{name} must be a mapping of band "
                                "label to finite number")
            else:
                for band, value in bandmap.items():
                    if not _finite(value):
                        problems.append(
                            f"{name}[{band!r}] must be finite")
        if self.orientation_status not in ORIENTATION_STATUSES:
            problems.append(
                f"orientation_status {self.orientation_status!r} not "
                f"in {sorted(ORIENTATION_STATUSES)}")
        if self.response_status not in RESPONSE_STATUSES:
            problems.append(
                f"response_status {self.response_status!r} not in "
                f"{sorted(RESPONSE_STATUSES)}")
        if self.status not in OBSERVABILITY_STATUSES:
            problems.append(
                f"status {self.status!r} not in "
                f"{sorted(OBSERVABILITY_STATUSES)}")
        if self.status == "OBSERVABLE" and self.problems:
            problems.append("an OBSERVABLE record may not carry "
                            "recorded gate problems")
        if not _SHA256_RE.match(str(self.source_manifest_digest)):
            problems.append("source_manifest_digest must be a 64-hex "
                            "sha256 — the record binds the byte-bound "
                            "source manifest it was assessed against")
        if not isinstance(self.problems, (list, tuple)) or \
                any(not isinstance(p, str) for p in self.problems):
            problems.append("problems must be a sequence of strings")
        return problems

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["channel_set"] = list(self.channel_set)
        d["problems"] = list(self.problems)
        d["noise_floor_by_band"] = dict(self.noise_floor_by_band)
        d["snr_by_band"] = dict(self.snr_by_band)
        d["record_type"] = STATION_OBSERVABILITY_TYPE
        return d


_OBSERVABILITY_FIELDS = frozenset(
    {f for f in StationObservabilityV0.__dataclass_fields__})


def station_observability_from_dict(
        payload: Mapping[str, Any]) -> StationObservabilityV0:
    """Exact typed deserialization of a serialized observability
    record — the ``record_type`` tag must name
    ``StationObservabilityV0``, every declared field must be present,
    and unknown fields reject."""
    if not isinstance(payload, Mapping):
        raise ValueError("observability payload must be a mapping")
    tag = payload.get("record_type")
    if tag != STATION_OBSERVABILITY_TYPE:
        raise ValueError(
            f"record_type {tag!r} must name "
            f"{STATION_OBSERVABILITY_TYPE}")
    keys = set(payload) - {"record_type"}
    missing = _OBSERVABILITY_FIELDS - keys
    extra = keys - _OBSERVABILITY_FIELDS
    if missing:
        raise ValueError(f"observability payload missing fields "
                         f"{sorted(missing)}")
    if extra:
        raise ValueError(f"observability payload carries undeclared "
                         f"fields {sorted(extra)}")
    kwargs = dict(payload)
    kwargs.pop("record_type")
    kwargs["channel_set"] = tuple(kwargs["channel_set"])
    kwargs["problems"] = tuple(kwargs["problems"])
    kwargs["noise_floor_by_band"] = dict(kwargs["noise_floor_by_band"])
    kwargs["snr_by_band"] = dict(kwargs["snr_by_band"])
    return StationObservabilityV0(**kwargs)


@dataclass(frozen=True)
class SeismicSidecarConfig:
    """Declared sidecar run configuration — every gate is explicit.

    ``waveform_relpaths``/``response_relpaths`` name which
    ``source_files`` entries are the waveform payload and the
    StationXML response evidence respectively; the seven-key manifest
    shape is untouched.
    """

    # windowing / feature extraction
    window_seconds: int = 60
    bands: tuple = ((0.5, 2.0), (2.0, 8.0), (8.0, 20.0))
    sta_seconds: float = 5.0
    lta_seconds: float = 30.0
    min_component_count: int = 3
    require_response: bool = True
    # STEIM admission gate — compressed encodings (10/11) are admitted
    # only when this flag is explicitly set AND the optional decoder has
    # been qualified in the governed environment; False fails closed.
    allow_steim_decoding: bool = False
    min_coverage_fraction: float = 0.9
    max_gap_fraction: float = 0.1
    min_snr_db: float = 0.0
    max_latency_seconds: float | None = None
    # byte-bound payload roles inside the source manifest
    waveform_relpaths: tuple = ()
    response_relpaths: tuple = ()
    # regime delegation (mirrors RegimeRunConfig floors)
    seeds: tuple = (42, 7, 2024)
    k_candidates: tuple = (1, 2, 3)
    n_bootstrap: int = 200
    n_null_replicates: int = 50
    null_alpha: float = 0.05
    bootstrap_block_len: int = 12
    missingness_policy: str = "listwise"
    # frame partition columns
    station_col: str = "station_id"
    unit_col: str = "unit_id"
    date_col: str = "date"
    # holdout policy: the runner derives ``holdout_cell`` =
    # station × temporal half; trained stations' LATE cells and every
    # cell of held-out stations form the declared holdout — one
    # partition carries both the time holdout and the station holdout.
    heldout_stations: tuple = ()
    require_station_holdout: bool = True
    min_trained_stations: int = 3
    min_rows_per_station: int = 30
    # catalog context ablation — when declared, the named catalog
    # columns are digested SEPARATELY and never merged into the
    # waveform feature digest or predictor set.
    catalog_ablation: bool = False
    catalog_cols: tuple = ()

    def validate(self) -> list[str]:
        problems: list[str] = []
        for name in ("require_response", "require_station_holdout",
                     "catalog_ablation", "allow_steim_decoding"):
            if not isinstance(getattr(self, name), bool):
                problems.append(f"{name} must be a strict bool")
        if isinstance(self.window_seconds, bool) or \
                not isinstance(self.window_seconds, int) or \
                self.window_seconds <= 0:
            problems.append("window_seconds must be a positive int")
        if not isinstance(self.bands, (list, tuple)) or \
                not self.bands:
            problems.append("bands must be a non-empty tuple of "
                            "(low_hz, high_hz) pairs")
        else:
            for i, band in enumerate(self.bands):
                if not isinstance(band, (list, tuple)) or \
                        len(band) != 2 or not _finite(band[0]) or \
                        not _finite(band[1]) or \
                        not 0.0 < band[0] < band[1]:
                    problems.append(
                        f"bands[{i}] must be a finite "
                        "(low_hz, high_hz) pair with 0 < low < high")
        for name in ("sta_seconds", "lta_seconds"):
            v = getattr(self, name)
            if not _finite(v) or v <= 0:
                problems.append(f"{name} must be a positive finite "
                                "number")
        if _finite(self.sta_seconds) and _finite(self.lta_seconds) \
                and self.sta_seconds >= self.lta_seconds:
            problems.append("sta_seconds must be shorter than "
                            "lta_seconds")
        if _finite(self.lta_seconds) and \
                isinstance(self.window_seconds, int) and \
                self.lta_seconds > self.window_seconds:
            problems.append("lta_seconds may not exceed "
                            "window_seconds — the long-term window "
                            "must fit inside the analysis window")
        if isinstance(self.min_component_count, bool) or \
                not isinstance(self.min_component_count, int) or \
                self.min_component_count < 1:
            problems.append("min_component_count must be a positive "
                            "int")
        for name in ("min_coverage_fraction", "max_gap_fraction",
                     "min_snr_db", "null_alpha"):
            v = getattr(self, name)
            if not _finite(v):
                problems.append(f"{name} must be finite")
        if _finite(self.min_coverage_fraction) and \
                not 0.0 < self.min_coverage_fraction <= 1.0:
            problems.append("min_coverage_fraction must be in (0, 1]")
        if _finite(self.max_gap_fraction) and \
                not 0.0 <= self.max_gap_fraction < 1.0:
            problems.append("max_gap_fraction must be in [0, 1)")
        if self.max_latency_seconds is not None and \
                (not _finite(self.max_latency_seconds) or
                 self.max_latency_seconds < 0):
            problems.append("max_latency_seconds must be a "
                            "non-negative finite number when declared")
        sequence_valid: dict[str, bool] = {}
        for name in ("waveform_relpaths", "response_relpaths",
                     "heldout_stations", "catalog_cols"):
            v = getattr(self, name)
            valid = isinstance(v, (list, tuple)) and \
                all(isinstance(x, str) and x.strip() for x in v)
            sequence_valid[name] = valid
            if not valid:
                problems.append(f"{name} must be a tuple of non-empty "
                                "strings")
            elif len(set(v)) != len(v):
                problems.append(f"{name} must contain unique values")
        if sequence_valid.get("waveform_relpaths") and \
                sequence_valid.get("response_relpaths") and \
                set(self.waveform_relpaths) & set(self.response_relpaths):
            problems.append("waveform_relpaths and response_relpaths must "
                            "be disjoint role paths")
        if self.require_response is True and \
                sequence_valid.get("response_relpaths") and \
                self.waveform_relpaths and not self.response_relpaths:
            problems.append("require_response requires at least one "
                            "response_relpath")
        seeds_valid = isinstance(self.seeds, (list, tuple)) and \
            all(isinstance(s, int) and not isinstance(s, bool) and s >= 0
                for s in self.seeds)
        if not seeds_valid or len(self.seeds) < 3 or \
                (seeds_valid and len(set(self.seeds)) < 3):
            problems.append("seeds must be >= 3 distinct non-negative "
                            "ints")
        if not isinstance(self.k_candidates, (list, tuple)) or \
                not self.k_candidates or \
                any(isinstance(k, bool) or not isinstance(k, int)
                    or k < 1 for k in self.k_candidates) or \
                1 not in self.k_candidates:
            problems.append("k_candidates must be declared ints >= 1 "
                            "and contain the K=1 null")
        for name in ("n_bootstrap", "n_null_replicates",
                     "bootstrap_block_len", "min_trained_stations",
                     "min_rows_per_station"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
                problems.append(f"{name} must be a positive int")
        if isinstance(self.min_trained_stations, int) and \
                not isinstance(self.min_trained_stations, bool) and \
                self.min_trained_stations < 3:
            problems.append("min_trained_stations must be >= 3 — the "
                            "regime geographic-diversity floor applies "
                            "to trained station groups")
        if _finite(self.null_alpha) and \
                not 0.0 < self.null_alpha < 1.0:
            problems.append("null_alpha must be in (0, 1)")
        if self.missingness_policy not in (
                "listwise", "bounded_impute", "stratified"):
            problems.append(
                f"missingness_policy {self.missingness_policy!r} not "
                "a declared regime policy")
        for name in ("station_col", "unit_col", "date_col"):
            v = getattr(self, name)
            if not isinstance(v, str) or not v.strip():
                problems.append(f"{name} must be a non-empty string")
        if self.catalog_ablation is True and not self.catalog_cols:
            problems.append("catalog_ablation requires declared "
                            "catalog_cols")
        if sequence_valid.get("catalog_cols"):
            unknown_catalog = sorted(
                set(self.catalog_cols) - set(CATALOG_CONTEXT_COLUMNS))
            if unknown_catalog:
                problems.append(
                    f"catalog_cols {unknown_catalog} are outside the "
                    "declared catalog-context vocabulary")
        return problems


def seismic_receipt_skeleton() -> dict[str, Any]:
    """The receipt's exact field surface with fail-closed defaults —
    every authority flag is false by construction and the status is
    RUN_ERROR until the runner promotes it through the gates."""
    return {
        "record_type": SEISMIC_RECEIPT_TYPE,
        "status": "RUN_ERROR",
        "claim_scope": SEISMIC_CLAIM_SCOPE,
        "source_manifest_digest": "",
        "config_digest": "",
        "station_observability_digest": "",
        "raw_waveform_digest": "",
        "semantic_feature_digest": "",
        "catalog_context_digest": "",
        "regime_artifact_digest": "",
        "report_digest": "",
        "promotion_eligible": False,
        "production_authorized": False,
        "warning_path_authorized": False,
        "problems": []}
