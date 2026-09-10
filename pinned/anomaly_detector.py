"""Cross-sensor anomaly detector with failure attribution.

Detects discrepancies between sensor sources and attributes each to a
diagnosable error bucket. Implements 5 discrepancy types plus a min-3-source
rule for flagging. Uses DBSCAN spatial clustering (sklearn) to group
anomalous cells into zones.

Discrepancy types:
  1. sar_loading_optical_bare — SAR shows snow loading but optical shows bare ground
  2. optical_snow_sar_dry — Optical shows snow cover but SAR shows dry/no snow
  3. weather_snow_no_snowcover — Weather model predicts snow but no snow cover observed
  4. rapid_loading_anomaly — Rapid snow loading beyond baseline percentile
  5. rapid_melt_anomaly — Rapid snow loss beyond baseline percentile

Attribution buckets:
  - forcing_error: weather model input is wrong
  - sensing_gap: satellite/ground sensor missed observations
  - physics_model_bias: snowpack physics model systematic error
  - terrain_transfer_error: baseline from different terrain doesn't transfer
  - threshold_miscalibration: detection thresholds need recalibration
  - unattributed: cannot diagnose with available evidence

All anomalies are advisory/review-gated. They NEVER independently raise
public risk levels.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

import numpy as np

from backend.common.verification_contracts import (
    VERIFICATION_SPINE_ENABLED,
    ANOMALY_NORMAL,
    ANOMALY_WATCH,
    ANOMALY_ANOMALY,
    ANOMALY_UNVERIFIED,
    ATTRIBUTION_FORCING_ERROR,
    ATTRIBUTION_SENSING_GAP,
    ATTRIBUTION_PHYSICS_MODEL_BIAS,
    ATTRIBUTION_TERRAIN_TRANSFER_ERROR,
    ATTRIBUTION_THRESHOLD_MISCALIBRATION,
    ATTRIBUTION_UNATTRIBUTED,
    VerificationPacket,
    EvidencePacket,
    DiscrepancyAttribution,
)

# Discrepancy type constants
DISCREPANCY_SAR_LOADING_OPTICAL_BARE = 'sar_loading_optical_bare'
DISCREPANCY_OPTICAL_SNOW_SAR_DRY = 'optical_snow_sar_dry'
DISCREPANCY_WEATHER_SNOW_NO_SNOWCOVER = 'weather_snow_no_snowcover'
DISCREPANCY_RAPID_LOADING_ANOMALY = 'rapid_loading_anomaly'
DISCREPANCY_RAPID_MELT_ANOMALY = 'rapid_melt_anomaly'

VALID_DISCREPANCY_TYPES = frozenset({
    DISCREPANCY_SAR_LOADING_OPTICAL_BARE,
    DISCREPANCY_OPTICAL_SNOW_SAR_DRY,
    DISCREPANCY_WEATHER_SNOW_NO_SNOWCOVER,
    DISCREPANCY_RAPID_LOADING_ANOMALY,
    DISCREPANCY_RAPID_MELT_ANOMALY,
})

# Minimum number of sources that must agree to flag a discrepancy
MIN_SOURCES_FOR_FLAG = 3

# Z-score thresholds
WATCH_ZSCORE = 1.5
ANOMALY_ZSCORE = 2.5

# Rapid change thresholds (per 24h)
RAPID_LOADING_THRESHOLD_CM = 30.0
RAPID_MELT_THRESHOLD_CM = 20.0


@dataclass
class AnomalyFlag:
    """A single anomaly flag for a cell."""

    cell_id: str
    discrepancy_type: str
    severity: float
    zscore: float | None
    sources: list[str]
    attribution: DiscrepancyAttribution
    timestamp: str = ''

    def __post_init__(self) -> None:
        if self.discrepancy_type not in VALID_DISCREPANCY_TYPES:
            raise ValueError(f'Invalid discrepancy type: {self.discrepancy_type}')
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            'cell_id': self.cell_id,
            'discrepancy_type': self.discrepancy_type,
            'severity': self.severity,
            'zscore': self.zscore,
            'sources': self.sources,
            'attribution': self.attribution.to_dict(),
            'timestamp': self.timestamp,
        }


@dataclass
class SensorReading:
    """Normalized sensor reading for a cell."""

    source: str
    snow_cover_fraction: float | None = None
    snow_depth_m: float | None = None
    wet_snow_fraction: float | None = None
    loading_rate_24h: float | None = None
    freshness_hours: float | None = None
    confidence: float = 1.0


def _safe_float(v: Any) -> float | None:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return float(v)


def detect_sar_loading_optical_bare(
    sar_reading: SensorReading,
    optical_reading: SensorReading,
) -> bool:
    """SAR shows snow loading but optical shows bare ground."""
    sar_loading = _safe_float(sar_reading.loading_rate_24h)
    optical_cover = _safe_float(optical_reading.snow_cover_fraction)
    if sar_loading is None or optical_cover is None:
        return False
    return sar_loading > 0.05 and optical_cover < 0.1


def detect_optical_snow_sar_dry(
    optical_reading: SensorReading,
    sar_reading: SensorReading,
) -> bool:
    """Optical shows snow cover but SAR shows dry/no snow."""
    optical_cover = _safe_float(optical_reading.snow_cover_fraction)
    sar_wet = _safe_float(sar_reading.wet_snow_fraction)
    if optical_cover is None:
        return False
    return optical_cover > 0.5 and (sar_wet is None or sar_wet < 0.1)


def detect_weather_snow_no_snowcover(
    weather_snowfall_cm: float | None,
    observed_cover: float | None,
) -> bool:
    """Weather model predicts snow but no snow cover observed."""
    if weather_snowfall_cm is None or observed_cover is None:
        return False
    return weather_snowfall_cm > 5.0 and observed_cover < 0.1


def detect_rapid_loading(
    loading_rate_24h: float | None,
    baseline_p75: float | None,
) -> bool:
    """Rapid snow loading beyond baseline 75th percentile."""
    if loading_rate_24h is None or baseline_p75 is None:
        return False
    return loading_rate_24h > max(RAPID_LOADING_THRESHOLD_CM, baseline_p75 * 1.5)


def detect_rapid_melt(
    melt_rate_24h: float | None,
    baseline_p25: float | None,
) -> bool:
    """Rapid snow loss beyond baseline 25th percentile."""
    if melt_rate_24h is None or baseline_p25 is None:
        return False
    return melt_rate_24h > max(RAPID_MELT_THRESHOLD_CM, abs(baseline_p25) * 1.5)


def attribute_discrepancy(
    discrepancy_type: str,
    evidence: EvidencePacket,
    weather_fresh: bool = True,
    sar_fresh: bool = True,
    optical_fresh: bool = True,
    physics_method: str = '',
) -> DiscrepancyAttribution:
    """Attribute a discrepancy to a specific error bucket.

    Uses evidence freshness, source availability, and physics method labels
    to diagnose the most likely cause.
    """
    reasons: list[str] = []

    if discrepancy_type == DISCREPANCY_WEATHER_SNOW_NO_SNOWCOVER:
        if not weather_fresh:
            return DiscrepancyAttribution(
                bucket=ATTRIBUTION_FORCING_ERROR,
                confidence=0.7,
                evidence=['weather data stale'],
                recommended_action='Refresh weather model input',
            )
        return DiscrepancyAttribution(
            bucket=ATTRIBUTION_SENSING_GAP,
            confidence=0.6,
            evidence=['weather predicts snow but no sensor confirmation'],
            recommended_action='Check sensor coverage and cloud mask',
        )

    if discrepancy_type in (DISCREPANCY_SAR_LOADING_OPTICAL_BARE, DISCREPANCY_OPTICAL_SNOW_SAR_DRY):
        if not optical_fresh and not sar_fresh:
            return DiscrepancyAttribution(
                bucket=ATTRIBUTION_SENSING_GAP,
                confidence=0.8,
                evidence=['both SAR and optical stale'],
                recommended_action='Wait for fresh satellite overpass',
            )
        if not optical_fresh:
            return DiscrepancyAttribution(
                bucket=ATTRIBUTION_SENSING_GAP,
                confidence=0.7,
                evidence=['optical data stale, SAR fresh'],
                recommended_action='Prioritize SAR reading until optical refresh',
            )
        return DiscrepancyAttribution(
            bucket=ATTRIBUTION_THRESHOLD_MISCALIBRATION,
            confidence=0.5,
            evidence=['both sensors fresh but disagree'],
            recommended_action='Recalibrate cross-sensor thresholds',
        )

    if discrepancy_type == DISCREPANCY_RAPID_LOADING_ANOMALY:
        if physics_method and 'synthetic' in physics_method.lower():
            return DiscrepancyAttribution(
                bucket=ATTRIBUTION_PHYSICS_MODEL_BIAS,
                confidence=0.6,
                evidence=[f'physics method={physics_method}'],
                recommended_action='Use real physics forcing, not heuristic fallback',
            )
        return DiscrepancyAttribution(
            bucket=ATTRIBUTION_TERRAIN_TRANSFER_ERROR,
            confidence=0.4,
            evidence=['loading exceeds baseline; may be terrain-specific'],
            recommended_action='Verify pseudo-control cell matching',
        )

    if discrepancy_type == DISCREPANCY_RAPID_MELT_ANOMALY:
        return DiscrepancyAttribution(
            bucket=ATTRIBUTION_THRESHOLD_MISCALIBRATION,
            confidence=0.5,
            evidence=['melt rate exceeds baseline'],
            recommended_action='Check temperature threshold calibration',
        )

    return DiscrepancyAttribution(
        bucket=ATTRIBUTION_UNATTRIBUTED,
        confidence=0.0,
        evidence=[],
        recommended_action='Manual review required',
    )


def compute_severity(zscore: float | None, source_count: int) -> float:
    """Compute anomaly severity on 0–1 scale."""
    if zscore is None:
        base = 0.3
    else:
        base = min(1.0, abs(zscore) / (ANOMALY_ZSCORE * 2))
    # Source count bonus: more sources agreeing → higher severity
    source_factor = min(1.0, source_count / MIN_SOURCES_FOR_FLAG)
    return float(base * 0.7 + source_factor * 0.3)


def determine_anomaly_state(
    zscore: float | None,
    source_count: int,
    has_discrepancy: bool,
) -> str:
    """Determine anomaly state from z-score and source count."""
    if not has_discrepancy and zscore is None:
        return ANOMALY_UNVERIFIED
    if not has_discrepancy:
        return ANOMALY_NORMAL
    if zscore is not None and abs(zscore) >= ANOMALY_ZSCORE and source_count >= MIN_SOURCES_FOR_FLAG:
        return ANOMALY_ANOMALY
    if zscore is not None and abs(zscore) >= WATCH_ZSCORE:
        return ANOMALY_WATCH
    return ANOMALY_WATCH if has_discrepancy else ANOMALY_NORMAL


def detect_anomalies(
    cell_id: str,
    region_key: str,
    readings: dict[str, SensorReading],
    baseline_p25: float | None = None,
    baseline_p50: float | None = None,
    baseline_p75: float | None = None,
    weather_snowfall_cm: float | None = None,
    physics_method: str = '',
) -> tuple[list[AnomalyFlag], VerificationPacket]:
    """Run all discrepancy checks for a single cell.

    Args:
        cell_id: Cell identifier.
        region_key: Region key.
        readings: Dict of sensor name → SensorReading.
        baseline_p25/p50/p75: Baseline percentiles for the primary sensor.
        weather_snowfall_cm: Weather model predicted snowfall in cm.
        physics_method: Snowpack physics method label.

    Returns:
        Tuple of (list of anomaly flags, verification packet).
    """
    if not VERIFICATION_SPINE_ENABLED:
        return [], VerificationPacket(cell_id=cell_id, region_key=region_key)

    flags: list[AnomalyFlag] = []
    sources_involved: set[str] = set()

    sar = readings.get('sar')
    optical = readings.get('optical')
    weather = readings.get('weather')
    gibs = readings.get('gibs')

    # Check freshness
    weather_fresh = weather is not None and (weather.freshness_hours or 999) < 24
    sar_fresh = sar is not None and (sar.freshness_hours or 999) < 72
    optical_fresh = optical is not None and (optical.freshness_hours or 999) < 72

    evidence = EvidencePacket(cell_id=cell_id)

    def _has_signal(name: str) -> bool:
        if name == 'weather' and weather_snowfall_cm is not None:
            return True
        reading = readings.get(name)
        if reading is None:
            return False
        return any(
            getattr(reading, field_name) is not None
            for field_name in (
                'snow_cover_fraction',
                'snow_depth_m',
                'wet_snow_fraction',
                'loading_rate_24h',
            )
        )

    def _flag_sources(*names: str) -> list[str]:
        unique = []
        for name in names:
            if name not in unique and _has_signal(name):
                unique.append(name)
        return unique if len(unique) >= MIN_SOURCES_FOR_FLAG else []

    # 1. SAR loading vs optical bare
    if sar and optical and detect_sar_loading_optical_bare(sar, optical):
        sources = _flag_sources('sar', 'optical', 'weather', 'gibs')
        if not sources:
            sources = []
        if sources:
            sources_involved.update(sources)
        if sources:
            attr = attribute_discrepancy(
                DISCREPANCY_SAR_LOADING_OPTICAL_BARE, evidence,
                weather_fresh=weather_fresh, sar_fresh=sar_fresh, optical_fresh=optical_fresh,
            )
            flags.append(AnomalyFlag(
                cell_id=cell_id,
                discrepancy_type=DISCREPANCY_SAR_LOADING_OPTICAL_BARE,
                severity=compute_severity(None, len(sources)),
                zscore=None,
                sources=sources,
                attribution=attr,
            ))

    # 2. Optical snow vs SAR dry
    if sar and optical and detect_optical_snow_sar_dry(optical, sar):
        sources = _flag_sources('optical', 'sar', 'weather', 'gibs')
        if sources:
            sources_involved.update(sources)
            attr = attribute_discrepancy(
                DISCREPANCY_OPTICAL_SNOW_SAR_DRY, evidence,
                weather_fresh=weather_fresh, sar_fresh=sar_fresh, optical_fresh=optical_fresh,
            )
            flags.append(AnomalyFlag(
                cell_id=cell_id,
                discrepancy_type=DISCREPANCY_OPTICAL_SNOW_SAR_DRY,
                severity=compute_severity(None, len(sources)),
                zscore=None,
                sources=sources,
                attribution=attr,
            ))

    # 3. Weather snow but no snow cover
    observed_cover = None
    if optical and optical.snow_cover_fraction is not None:
        observed_cover = optical.snow_cover_fraction
    elif gibs and gibs.snow_cover_fraction is not None:
        observed_cover = gibs.snow_cover_fraction

    if detect_weather_snow_no_snowcover(weather_snowfall_cm, observed_cover):
        sources = _flag_sources('weather', 'optical' if optical else 'gibs', 'gibs', 'sar')
        if sources:
            sources_involved.update(sources)
            attr = attribute_discrepancy(
                DISCREPANCY_WEATHER_SNOW_NO_SNOWCOVER, evidence,
                weather_fresh=weather_fresh, sar_fresh=sar_fresh, optical_fresh=optical_fresh,
            )
            flags.append(AnomalyFlag(
                cell_id=cell_id,
                discrepancy_type=DISCREPANCY_WEATHER_SNOW_NO_SNOWCOVER,
                severity=compute_severity(None, len(sources)),
                zscore=None,
                sources=sources,
                attribution=attr,
            ))

    # 4. Rapid loading
    loading_rate = sar.loading_rate_24h if sar else None
    if detect_rapid_loading(loading_rate, baseline_p75):
        sources = _flag_sources('sar', 'weather', 'optical', 'gibs')
        if sources:
            sources_involved.update(sources)
            attr = attribute_discrepancy(
                DISCREPANCY_RAPID_LOADING_ANOMALY, evidence,
                weather_fresh=weather_fresh, sar_fresh=sar_fresh, optical_fresh=optical_fresh,
                physics_method=physics_method,
            )
            flags.append(AnomalyFlag(
                cell_id=cell_id,
                discrepancy_type=DISCREPANCY_RAPID_LOADING_ANOMALY,
                severity=compute_severity(None, len(sources)),
                zscore=None,
                sources=sources,
                attribution=attr,
            ))

    # 5. Rapid melt
    melt_rate = None
    if sar and sar.loading_rate_24h is not None and sar.loading_rate_24h < 0:
        melt_rate = abs(sar.loading_rate_24h)
    if detect_rapid_melt(melt_rate, baseline_p25):
        sources = _flag_sources('sar', 'weather', 'optical', 'gibs')
        if sources:
            sources_involved.update(sources)
            attr = attribute_discrepancy(
                DISCREPANCY_RAPID_MELT_ANOMALY, evidence,
                weather_fresh=weather_fresh, sar_fresh=sar_fresh, optical_fresh=optical_fresh,
                physics_method=physics_method,
            )
            flags.append(AnomalyFlag(
                cell_id=cell_id,
                discrepancy_type=DISCREPANCY_RAPID_MELT_ANOMALY,
                severity=compute_severity(None, len(sources)),
                zscore=None,
                sources=sources,
                attribution=attr,
            ))

    # Compute z-score from primary sensor
    observed = None
    if baseline_p50 is not None and sar and sar.snow_depth_m is not None:
        observed = sar.snow_depth_m
    elif baseline_p50 is not None and weather and weather.loading_rate_24h is not None:
        observed = weather.loading_rate_24h

    zscore = None
    if observed is not None and baseline_p50 is not None:
        # Use baseline std as proxy if available
        std_proxy = max((baseline_p75 or baseline_p50) - (baseline_p25 or baseline_p50), 0.01)
        if std_proxy > 1e-9:
            zscore = (observed - baseline_p50) / std_proxy

    has_discrepancy = len(flags) > 0
    available_sources = [name for name in readings if _has_signal(name)]
    source_count = len(sources_involved) or len(available_sources)
    anomaly_state = determine_anomaly_state(zscore, source_count, has_discrepancy)

    # Pick the highest-confidence attribution
    best_attr = ATTRIBUTION_UNATTRIBUTED
    best_conf = 0.0
    disagreement_reasons: list[str] = []
    for f in flags:
        disagreement_reasons.append(f.discrepancy_type)
        if f.attribution.confidence > best_conf:
            best_attr = f.attribution.bucket
            best_conf = f.attribution.confidence

    # Source freshness
    source_freshness: dict[str, float] = {}
    for name, reading in readings.items():
        if reading.freshness_hours is not None:
            source_freshness[name] = reading.freshness_hours

    packet = VerificationPacket(
        cell_id=cell_id,
        region_key=region_key,
        baseline_p25=baseline_p25,
        baseline_p50=baseline_p50,
        baseline_p75=baseline_p75,
        observed=observed,
        residual_zscore=zscore,
        anomaly_state=anomaly_state,
        source_freshness_hours=source_freshness,
        lineage={
            'flags': [f.to_dict() for f in flags],
            'minimum_sources_required': MIN_SOURCES_FOR_FLAG,
            'minimum_source_count': source_count,
            'minimum_sources_satisfied': source_count >= MIN_SOURCES_FOR_FLAG,
        },
        disagreement_reasons=disagreement_reasons,
        attribution_bucket=best_attr,
        attribution={
            'bucket': best_attr,
            'confidence': best_conf,
            'evidence': [f.to_dict() for f in flags],
        },
        confidence=best_conf,
        contributing_sensors=sorted(available_sources),
        has_synthetic_evidence='synthetic' in physics_method.lower(),
        data_quality={
            'source_count': len(available_sources),
            'minimum_sources_required': MIN_SOURCES_FOR_FLAG,
            'minimum_sources_satisfied': len(available_sources) >= MIN_SOURCES_FOR_FLAG,
            'freshness_complete': all(
                readings[name].freshness_hours is not None for name in available_sources
            ),
        },
    )

    return flags, packet


def cluster_anomaly_zones(
    flags: Sequence[AnomalyFlag],
    cell_centers: dict[str, tuple[float, float]],
    eps_km: float = 5.0,
) -> list[list[str]]:
    """Group anomalous cells into spatial zones using DBSCAN.

    Args:
        flags: Anomaly flags for multiple cells.
        cell_centers: Dict of cell_id → (lat, lng).
        eps_km: DBSCAN epsilon in km (approx).

    Returns:
        List of zones, each a list of cell IDs.
    """
    if len(flags) < 2:
        return [[f.cell_id] for f in flags]

    try:
        from sklearn.cluster import DBSCAN
    except ImportError:
        return [[f.cell_id] for f in flags]

    cell_ids = [f.cell_id for f in flags]
    coords = []
    for cid in cell_ids:
        center = cell_centers.get(cid)
        if center is None:
            return [[f.cell_id] for f in flags]
        coords.append(center)

    coords_arr = np.array(coords, dtype=float)
    # Convert lat/lng to approximate km
    coords_km = np.column_stack([
        coords_arr[:, 1] * 111.0 * np.cos(np.radians(coords_arr[:, 0])),
        coords_arr[:, 0] * 111.0,
    ])

    db = DBSCAN(eps=eps_km, min_samples=1).fit(coords_km)
    labels = db.labels_

    zones: list[list[str]] = []
    for label in set(labels):
        if label == -1:
            continue
        zone = [cell_ids[i] for i in range(len(cell_ids)) if labels[i] == label]
        zones.append(zone)

    # Add noise points as individual zones
    for i in range(len(cell_ids)):
        if labels[i] == -1:
            zones.append([cell_ids[i]])

    return zones
