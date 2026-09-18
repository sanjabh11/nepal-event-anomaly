"""nepal.seismic_sidecar — research-only seismic event-detection sidecar.

A separate, provenance-preserving track for post-initiation
abnormal-event detection described in
``docs/science/run_b/SEISMIC_EVENT_DETECTION_ADDENDUM_V0.md``.  It is
deliberately isolated from the frozen Nepal weather/GLOF PoC: seismic
input is NOT a Nepal predictor, catalog metadata is never waveform
evidence, and no source is EVIDENCE_VERIFIED.

Pre-P5 posture: deterministic, source-independent machinery only —
typed observability gates, byte-bound manifest verification, pure-NumPy
window features, and the descriptive runner.  No network I/O, no FDSN
client, no ObsPy dependency, no waveform retrieval.  All receipts keep
``promotion_eligible``, ``production_authorized`` and
``warning_path_authorized`` false; the claim scope is fixed to
``research_only_post_initiation_detection``.
"""
from __future__ import annotations

from .contracts import (
    CATALOG_CONTEXT_COLUMNS, OBSERVABILITY_STATUSES,
    ORIENTATION_STATUSES, RECEIPT_STATUSES, RESPONSE_STATUSES,
    SEISMIC_CLAIM_SCOPE, SEISMIC_DEFAULT_BANDS,
    SEISMIC_DERIVED_COLUMNS, SEISMIC_FEATURE_UNITS,
    SEISMIC_IDENTITY_COLUMNS, SEISMIC_NONBAND_FEATURES,
    SEISMIC_RECEIPT_TYPE, SEISMIC_VALUE_FEATURES,
    SEISMIC_WAVEFORM_RETROSPECTIVE, STATION_OBSERVABILITY_TYPE,
    SeismicSidecarConfig, StationObservabilityV0,
    seismic_receipt_skeleton, station_observability_from_dict)
from .features import (
    aggregate_daily, band_feature_names, build_window_rows,
    cross_station_coherence, season_of_date,
    semantic_feature_digest, validate_waveform_input, window_features)
from .observability import (
    build_station_observability, derive_orientation_status,
    great_circle_km, reassess_observability,
    source_manifest_digest)
from .runner import run_seismic_descriptive_poc

__all__ = [
    "CATALOG_CONTEXT_COLUMNS", "OBSERVABILITY_STATUSES",
    "ORIENTATION_STATUSES", "RECEIPT_STATUSES", "RESPONSE_STATUSES",
    "SEISMIC_CLAIM_SCOPE", "SEISMIC_DEFAULT_BANDS",
    "SEISMIC_DERIVED_COLUMNS", "SEISMIC_FEATURE_UNITS",
    "SEISMIC_IDENTITY_COLUMNS", "SEISMIC_NONBAND_FEATURES",
    "SEISMIC_RECEIPT_TYPE", "SEISMIC_VALUE_FEATURES",
    "SEISMIC_WAVEFORM_RETROSPECTIVE", "STATION_OBSERVABILITY_TYPE",
    "SeismicSidecarConfig", "StationObservabilityV0",
    "aggregate_daily", "band_feature_names",
    "build_station_observability", "build_window_rows",
    "cross_station_coherence", "derive_orientation_status",
    "great_circle_km", "reassess_observability",
    "run_seismic_descriptive_poc",
    "season_of_date", "semantic_feature_digest",
    "seismic_receipt_skeleton", "source_manifest_digest",
    "station_observability_from_dict", "validate_waveform_input",
    "window_features"]
