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
from .io import (
    WaveformBundleV0, derive_station_observability,
    read_verified_waveform_bundle)
from .provenance import (
    FeatureFrameArtifactV0, build_seismic_feature_artifact,
    run_seismic_real_path, verify_feature_generation)
from .events import (
    OpportunityWindowV0, SeismicEventPackageV0, SeismicEventV0,
    build_event_package)
from .one_station_contract import (
    AUTHORIZED_STATION_IDS, AUTHORIZED_WAVEFORM_WINDOW,
    ONE_STATION_SCIENTIFIC_STATUSES, ONE_STATION_STATUSES,
    ONE_STATION_STATUS_MAP, OneStationReceiptV1,
    one_station_receipt_from_dict, one_station_receipt_skeleton)

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
    "run_seismic_real_path",
    "FeatureFrameArtifactV0", "OpportunityWindowV0",
    "SeismicEventPackageV0", "SeismicEventV0",
    "WaveformBundleV0", "build_event_package",
    "build_seismic_feature_artifact",
    "derive_station_observability",
    "read_verified_waveform_bundle",
    "verify_feature_generation",
    "season_of_date", "semantic_feature_digest",
    "seismic_receipt_skeleton", "source_manifest_digest",
    "station_observability_from_dict", "validate_waveform_input",
    "window_features",
    "AUTHORIZED_STATION_IDS", "AUTHORIZED_WAVEFORM_WINDOW",
    "ONE_STATION_SCIENTIFIC_STATUSES", "ONE_STATION_STATUSES",
    "ONE_STATION_STATUS_MAP", "OneStationReceiptV1",
    "one_station_receipt_from_dict",
    "one_station_receipt_skeleton"]
