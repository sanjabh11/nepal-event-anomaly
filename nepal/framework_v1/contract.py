"""nepal.framework_v1.contract — versioned framework contract (Phase 0).

Frozen constants and rules for the framework_v1 clean-room pipeline.

Rules enforced by this module:
- The study box is a 30 km x 30 km projected square (EPSG:32645) centered on the
  frozen Langtang source reference point.  It is never selected using event
  density, S1 quality, exposure, or attractive results.
- The primary winter observation window is 1 November - 30 April.  Any narrower
  seasonal comparison is labelled SENSITIVITY_ONLY.
- Thermal, Farinotti, permafrost, bed-elevation and missing-data (data quality)
  fields are sidecar layers: they can never enter the primary ranking.
- This module must not import the legacy multi-event harness or phase runner and
  must import successfully with no data present.
"""
from __future__ import annotations

import hashlib
import math
import copy
import json
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

try:  # pyproj ships its PROJ database inside the wheel: no data required.
    from pyproj import Transformer

    _HAS_PYPROJ = True
except Exception:  # pragma: no cover - only hit in minimal environments
    _HAS_PYPROJ = False


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class FrameworkError(Exception):
    """Base class for framework_v1 contract errors."""


class ContractViolation(FrameworkError):
    """A caller attempted to violate a frozen contract rule."""


class SidechainLayerError(ContractViolation):
    """A sidecar layer (thermal/Farinotti/permafrost/bed/quality) was passed
    where only primary ranking layers are allowed."""


# ---------------------------------------------------------------------------
# Versioning
# ---------------------------------------------------------------------------

FRAMEWORK_NAME = "nepal-framework-v1"
FRAMEWORK_VERSION = "1.0.0"
CONTRACT_SCHEMA_VERSION = "1.0.0"
HASH_ALGORITHM = "sha256"


# ---------------------------------------------------------------------------
# Frozen study definition (source of truth: preregistration.md, frozen
# 2026-09-10; never modified by framework_v1)
# ---------------------------------------------------------------------------

FROZEN_SOURCE: dict[str, Any] = {
    "event_name": "Langtang Lirung north face collapse",
    "event_date": "2026-08-26",
    "lat": 28.288708,
    "lon": 85.528159,
    "crs": "EPSG:4326",
    "reference": "HiRisk RHA NP3 reference point (preregistration.md)",
    "alternative_centroid": {"lat": 28.28858, "lon": 85.52701, "reference": "Rui Li arXiv"},
    "rgi7_id": "RGI2000-v7.0-G-15-05732",
    "mechanism": "Mixed ice-rock slope failure (not GLOF)",
}

STUDY_BOX_SIZE_M = 30_000
STUDY_BOX_HALF_SIZE_M = 15_000
STUDY_BOX_CRS = "EPSG:32645"
ANALYSIS_UNIT_CELL_M = 100

PREREGISTRATION_PATH = "preregistration.md"
PREREGISTRATION_SHA256 = "0e7ce3c2e347a955f7495d719bb9232465ac9c25a5266656865800dbc963da7c"
PREREGISTRATION_DATA_SOURCE_STATUS = "POST_HOC_DATA_SOURCE_CHANGE"

# Primary winter observation window: 1 November - 30 April (spans the new year).
WINTER_WINDOW_START = (11, 1)   # (month, day)
WINTER_WINDOW_END = (4, 30)     # (month, day)
SENSITIVITY_ONLY_LABEL = "SENSITIVITY_ONLY"


def is_winter_date(day: date) -> bool:
    """True when *day* falls inside the primary winter window Nov 1 - Apr 30."""
    return day.month >= WINTER_WINDOW_START[0] or day.month <= WINTER_WINDOW_END[0]


# ---------------------------------------------------------------------------
# Fixed output statuses
# ---------------------------------------------------------------------------

class OutputStatus(str, Enum):
    """Fixed output statuses.  Values are stable strings for deterministic
    serialization; no other status strings may be emitted."""

    PASS = "PASS"
    NULL = "NULL"
    INDETERMINATE = "INDETERMINATE"
    BLOCKED = "BLOCKED"
    NOT_RUN = "NOT_RUN"
    RANKED = "RANKED"
    UNRANKED = "UNRANKED"
    UNSCREENABLE = "UNSCREENABLE"
    OBSERVABLE = "OBSERVABLE"
    UNOBSERVABLE = "UNOBSERVABLE"
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    SUPPORTED = "SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    OK = "OK"
    FAILED = "FAILED"


OUTPUT_STATUSES = tuple(s.value for s in OutputStatus)

# Phase-envelope states are intentionally separate from the row/result status
# taxonomy above.  This prevents a load or gate state from being mistaken for
# a scientific row classification.
PHASE_STATUS_A_READY = "A_READY"
PHASE_STATUS_A_BLOCKED = "A_BLOCKED"
PHASE_STATUS_LOAD_READY = "LOAD_READY"
PHASE_STATUS_SCREEN_RANKED = "SCREEN_RANKED"
PHASE_STATUS_B_TO_C_READY = "B_TO_C_READY"
PHASE_STATUS_B_TO_C_BLOCKED = "B_TO_C_BLOCKED"
PHASE_STATUS_E_READY = "E_READY"
PHASE_STATUS_E_BLOCKED = "E_BLOCKED"
PHASE_STATUS_F_READY = "F_READY"
PHASE_STATUS_F_BLOCKED = "F_BLOCKED"
B_TIMEOUT_STATUS = "TIMEOUT"
B_DEFAULT_TIMEOUT_SECONDS = 300.0
PHASE_STATUSES = (
    PHASE_STATUS_A_READY, PHASE_STATUS_A_BLOCKED,
    PHASE_STATUS_LOAD_READY, PHASE_STATUS_SCREEN_RANKED,
    PHASE_STATUS_B_TO_C_READY, PHASE_STATUS_B_TO_C_BLOCKED,
    PHASE_STATUS_E_READY, PHASE_STATUS_E_BLOCKED,
    PHASE_STATUS_F_READY, PHASE_STATUS_F_BLOCKED, B_TIMEOUT_STATUS,
)


class GateId(str, Enum):
    A_CATALOG = "A_CATALOG"
    B_SCREEN = "B_SCREEN"
    B_TO_C = "B_TO_C"
    E_VALIDATION = "E_VALIDATION"
    C_OPTIONAL = "C_OPTIONAL"
    D_OPTIONAL = "D_OPTIONAL"


GATE_IDS = tuple(g.value for g in GateId)


# ---------------------------------------------------------------------------
# Catalog enums
# ---------------------------------------------------------------------------

class DatePrecision(str, Enum):
    EXACT_DAY = "EXACT_DAY"
    INTERVAL = "INTERVAL"
    MONTH_ONLY = "MONTH_ONLY"
    SEASON_ONLY = "SEASON_ONLY"
    MALFORMED = "MALFORMED"
    UNRESOLVED = "UNRESOLVED"


class MechanismClass(str, Enum):
    ICE_ROCK_AVALANCHE = "ICE_ROCK_AVALANCHE"
    GLACIER_DETACHMENT = "GLACIER_DETACHMENT"
    ICE_AVALANCHE = "ICE_AVALANCHE"
    ICE_INTO_LAKE = "ICE_INTO_LAKE"
    SNOW_AVALANCHE = "SNOW_AVALANCHE"
    ROCK_AVALANCHE = "ROCK_AVALANCHE"
    DEBRIS_FLOW = "DEBRIS_FLOW"
    GLOF = "GLOF"
    UNRESOLVED = "UNRESOLVED"


class ObservationAvailability(str, Enum):
    AVAILABLE = "AVAILABLE"
    PARTIAL = "PARTIAL"
    NONE = "NONE"
    UNKNOWN = "UNKNOWN"


class CrosswalkStatus(str, Enum):
    DOCUMENTED = "DOCUMENTED"
    ABSENT = "ABSENT"
    UNVERIFIED = "UNVERIFIED"


# ---------------------------------------------------------------------------
# Catalog eligibility rules (Phase A)
# ---------------------------------------------------------------------------

MAX_SOURCE_SUPPORTED_INTERVAL_DAYS = 7

CATALOG_ELIGIBILITY_RULES: dict[str, Any] = {
    "date": {
        "eligible_precisions": [DatePrecision.EXACT_DAY.value, DatePrecision.INTERVAL.value],
        "max_source_supported_interval_days": MAX_SOURCE_SUPPORTED_INTERVAL_DAYS,
        "ineligible_precisions": [
            DatePrecision.MONTH_ONLY.value,
            DatePrecision.SEASON_ONLY.value,
            DatePrecision.MALFORMED.value,
            DatePrecision.UNRESOLVED.value,
        ],
        "never_replace_missing_day_with_day_15": True,
        "never_invent_date_from_month_or_season": True,
        "preserve_ambiguous_raw_strings": True,
        "interval_sensitivity_across_all_admissible_dates": True,
    },
    "mechanism": {
        "ice_rock_cohort": [MechanismClass.ICE_ROCK_AVALANCHE.value,
                            MechanismClass.GLACIER_DETACHMENT.value],
        "excluded_from_cohort": [MechanismClass.GLOF.value,
                                 MechanismClass.DEBRIS_FLOW.value,
                                 MechanismClass.ROCK_AVALANCHE.value,
                                 MechanismClass.SNOW_AVALANCHE.value,
                                 MechanismClass.UNRESOLVED.value],
        "separately_classified": [MechanismClass.ICE_INTO_LAKE.value],
        "require_source_supported_confirmation": True,
        "mechanism_dissent_disqualifies": True,
        "thame_excluded_as_precursor_analog": True,
    },
    "geography": {
        "require_source_coordinates": True,
        "hma_lat_bounds": [24.0, 46.0],
        "hma_lon_bounds": [64.0, 106.0],
    },
    "holdout": {
        "assign_before_eligibility_adjudication": True,
        "basis_order": ["source_glacier_or_basin", "fixed_hma_macroregion",
                        "deterministic_spatial_fallback"],
        "freeze_before_date_or_mechanism_filtering": True,
        "no_regroup_or_rebalance_after_observing_eligible_count": True,
        "split_method": "geographic_leave_one_group_out",
        "random_row_level_cross_validation_forbidden": True,
    },
}

GATE_A_MIN_ELIGIBLE_EVENTS = 5
GATE_A_MIN_HOLDOUT_GROUPS = 2

THAME_EXCLUSION_NAMES = ("thame",)


# ---------------------------------------------------------------------------
# Sidecar (non-promotable) layers and ranking layer registry
# ---------------------------------------------------------------------------

SIDECHAIN_LAYERS = frozenset({
    "thermal",
    "farinotti_thickness",
    "farinotti_bed",
    "bed_elevation",
    "permafrost",
})
DATA_QUALITY_LAYERS = frozenset({"data_quality", "nodata_coverage"})

THERMAL_CONTEXT: dict[str, Any] = {
    "promotion_eligible": False,
    "role": "sidecar",
    "layers": ["thermal", "farinotti_bed", "permafrost", "bed_elevation"],
    "statement": (
        "Thermal, Farinotti thickness/bed and permafrost layers are low-confidence "
        "sidecars. They cannot enter the primary ranking, the A/B/E gates, or any "
        "artifact that contributes to a gate decision."
    ),
}

BED_ELEVATION_FORMULA = "bed_elevation = surface_elevation - thickness"

# Aspect is circular and has no frozen preferred-direction model in v1.  It is
# retained as a diagnostic/context layer, never averaged with metres or
# percentages as if it were a linear susceptibility scalar.
TERRAIN_COMPONENTS = ("slope", "local_relief", "roughness",
                       "glacier_support", "hanging_ice_support")
TERRAIN_CONTEXT_COMPONENTS = ("aspect",)
EXPOSURE_COMPONENTS = ("built_up", "population", "infrastructure", "river_connectivity")
REQUIRED_TERRAIN_COMPONENTS = TERRAIN_COMPONENTS
# WorldPop is optional in the approved handoff contract.  Missing population
# is reported as an optional-component gap, never imputed as zero.
REQUIRED_EXPOSURE_COMPONENTS = ("built_up", "infrastructure", "river_connectivity")
COMPONENT_REGISTRY_VERSION = "b-v1-active-components-20260912"
ACTIVE_TERRAIN_COMPONENTS = REQUIRED_TERRAIN_COMPONENTS
ACTIVE_EXPOSURE_COMPONENTS = REQUIRED_EXPOSURE_COMPONENTS
OPTIONAL_EXPOSURE_COMPONENTS = ("population",)
RANKING_INDICES = ("terrain_index", "exposure_index", "winter_observability")


def assert_no_sidechain_layers(layers: Iterable[str]) -> None:
    """Raise :class:`SidechainLayerError` if any sidecar/quality layer is passed
    as a primary ranking layer."""
    for name in layers:
        normalized = str(name).strip().lower()
        if normalized in SIDECHAIN_LAYERS:
            raise SidechainLayerError(
                f"sidecar layer {name!r} cannot influence the primary ranking")
        if normalized in DATA_QUALITY_LAYERS:
            raise SidechainLayerError(
                f"missing-data/quality layer {name!r} cannot influence the "
                f"primary ranking")


# ---------------------------------------------------------------------------
# Analysis-unit IDs and deterministic tie-breaking
# ---------------------------------------------------------------------------

ANALYSIS_UNIT_ID_FORMAT = "AU-E{col:04d}-N{row:04d}"

TIE_BREAK_RULES: tuple[str, ...] = (
    "priority_index descending",
    "terrain_index descending",
    "exposure_index descending",
    "winter_observability descending",
    "analysis_unit_id ascending (lexicographic)",
)


def analysis_unit_id(easting: float, northing: float,
                     cell_size: int = ANALYSIS_UNIT_CELL_M,
                     origin: Optional[tuple[float, float]] = None) -> str:
    """Deterministic analysis-unit id from projected coordinates.

    The origin defaults to the study-box SW corner so ids are stable for the
    frozen box.  Ranking ties fall back to this id (ascending)."""
    box = study_box()
    minx, miny = origin if origin is not None else (box["minx"], box["miny"])
    col = int(math.floor((easting - minx) / cell_size))
    row = int(math.floor((box["maxy"] - northing) / cell_size))
    return ANALYSIS_UNIT_ID_FORMAT.format(col=col, row=row)


def sort_candidates(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Apply the frozen deterministic tie-breaking rules to candidate rows."""
    def num(r: Mapping[str, Any], name: str) -> float:
        v = r.get(name)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            return float("-inf")
        value = float(v)
        return value if math.isfinite(value) else float("-inf")

    def key(r: Mapping[str, Any]) -> tuple:
        return (-num(r, "priority_index"), -num(r, "terrain_index"),
                -num(r, "exposure_index"), -num(r, "winter_observability"),
                str(r.get("analysis_unit_id", "")))

    return [dict(r) for r in sorted(rows, key=key)]



# ---------------------------------------------------------------------------
# Study box construction (exact, from the frozen point only)
# ---------------------------------------------------------------------------

_BOX_CACHE: Optional[dict[str, Any]] = None


def _transformers() -> tuple[Any, Any]:
    if not _HAS_PYPROJ:  # pragma: no cover
        raise FrameworkError(
            "pyproj is required to build the study box; install the pinned "
            "version from requirements.txt")
    # Local import keeps the no-data/no-pyproj import contract explicit and
    # avoids a static analyser treating the optional module symbol as absent.
    from pyproj import Transformer as _Transformer
    fwd = _Transformer.from_crs("EPSG:4326", STUDY_BOX_CRS, always_xy=True)
    inv = _Transformer.from_crs(STUDY_BOX_CRS, "EPSG:4326", always_xy=True)
    return fwd, inv


def study_box(force_recompute: bool = False) -> dict[str, Any]:
    """Exact 30 km x 30 km box centered on the frozen source point.

    Computed from the frozen coordinate only: never from event density, S1
    quality, exposure or results.  Deterministic and cached."""
    global _BOX_CACHE
    if _BOX_CACHE is not None and not force_recompute:
        return copy.deepcopy(_BOX_CACHE)
    fwd, inv = _transformers()
    cx, cy = fwd.transform(FROZEN_SOURCE["lon"], FROZEN_SOURCE["lat"])
    minx, maxx = cx - STUDY_BOX_HALF_SIZE_M, cx + STUDY_BOX_HALF_SIZE_M
    miny, maxy = cy - STUDY_BOX_HALF_SIZE_M, cy + STUDY_BOX_HALF_SIZE_M
    corners_xy = [(minx, miny), (maxx, miny), (maxx, maxy), (minx, maxy)]
    corners_ll = [tuple(round(v, 9) for v in inv.transform(x, y)) for x, y in corners_xy]
    _BOX_CACHE = {
        "crs": STUDY_BOX_CRS,
        "size_m": STUDY_BOX_SIZE_M,
        "half_size_m": STUDY_BOX_HALF_SIZE_M,
        "center_easting": cx,
        "center_northing": cy,
        "center_lonlat": (FROZEN_SOURCE["lon"], FROZEN_SOURCE["lat"]),
        "minx": minx, "miny": miny, "maxx": maxx, "maxy": maxy,
        "corners_epsg32645": corners_xy,
        "corners_lonlat": corners_ll,
        "selection_basis": "frozen_source_point_only",
        "center_source": "FROZEN_SOURCE",
    }
    # Never expose the mutable cache.  A caller changing the returned dict
    # must not silently move the frozen box for all later computations.
    return copy.deepcopy(_BOX_CACHE)


def study_box_bounds() -> tuple[float, float, float, float]:
    b = study_box()
    return (b["minx"], b["miny"], b["maxx"], b["maxy"])


def study_box_hash() -> str:
    """Hash the frozen projected study-box geometry used by target rasters."""
    b = study_box()
    payload = {
        "crs": b["crs"],
        "size_m": b["size_m"],
        "bounds": list(study_box_bounds()),
        "selection_basis": b["selection_basis"],
        "center_source": b["center_source"],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def target_grid_contract() -> dict[str, Any]:
    """Return the complete frozen target-grid geometry contract."""
    b = study_box()
    return {
        "width": 300,
        "height": 300,
        "resolution_m": ANALYSIS_UNIT_CELL_M,
        "crs": STUDY_BOX_CRS,
        # GDAL/rasterio affine coefficients: a, b, c, d, e, f.
        "affine_transform": [
            float(ANALYSIS_UNIT_CELL_M), 0.0, float(b["minx"]),
            0.0, -float(ANALYSIS_UNIT_CELL_M), float(b["maxy"]),
        ],
        "bounds": list(study_box_bounds()),
        "study_box_hash": study_box_hash(),
    }



# ---------------------------------------------------------------------------
# Input schema (versioned)
# ---------------------------------------------------------------------------

INPUT_SCHEMA: dict[str, Any] = {
    "schema_version": CONTRACT_SCHEMA_VERSION,
    "notes": (
        "role=primary inputs participate in B/E. role=sidecar inputs are "
        "recorded for provenance only and can never influence the primary "
        "ranking or any gate."
    ),
    "inputs": {
        "dem": {"required": True, "kind": "geotiff", "role": "primary",
                "crs": STUDY_BOX_CRS, "source": "Copernicus DEM surface"},
        "glacier_inventory": {"required": True, "kind": "vector", "role": "primary",
                              "source": "RGI7/GLIMS"},
        "sentinel2_dry_season": {"required": True, "kind": "raster_series", "role": "primary",
                                 "source": "pre-event dry-season Sentinel-2"},
        "s1_acquisition_metadata": {"required": True, "kind": "table", "role": "primary",
                                    "forbidden_fields": ["displacement", "coherence",
                                                         "hyp3_product", "unwrapped_phase"]},
        "s1_pair_availability": {"required": True, "kind": "table", "role": "primary"},
        "exposure_ghsl": {"required": True, "kind": "raster", "role": "primary",
                          "source": "GHSL built-up surface"},
        "exposure_osm": {"required": True, "kind": "vector", "role": "primary",
                         "source": "OSM/Geofabrik infrastructure"},
        "exposure_hydrosheds": {"required": True, "kind": "vector", "role": "primary",
                                "source": "HydroSHEDS/HydroRIVERS"},
        "worldpop": {"required": False, "kind": "raster", "role": "primary",
                     "license_required": True},
        "thermal": {"required": False, "kind": "raster", "role": "sidecar"},
        "farinotti_thickness": {"required": False, "kind": "raster", "role": "sidecar",
                                "requires_rgi60_crosswalk": CrosswalkStatus.DOCUMENTED.value},
        "permafrost": {"required": False, "kind": "raster", "role": "sidecar"},
        "bed_elevation": {"required": False, "kind": "derived_sidecar", "role": "sidecar",
                          "formula": BED_ELEVATION_FORMULA, "confidence": "low"},
    },
}

_ALLOWED_ROLES = ("primary", "sidecar")


def validate_input_manifest(manifest: Mapping[str, Any]) -> list[str]:
    """Validate a candidate input manifest against the versioned schema.

    Returns a list of human-readable violations (empty when valid).  Promoting
    a sidecar to primary, or claiming derived S1 signal fields as acquisition
    metadata, are hard violations."""
    problems: list[str] = []
    if not isinstance(manifest, Mapping):
        return ["manifest must be a mapping"]
    inputs = manifest.get("inputs", {})
    if not isinstance(inputs, Mapping):
        problems.append("inputs must be a mapping")
        inputs = {}
    for name, spec in inputs.items():
        schema_entry = INPUT_SCHEMA["inputs"].get(name)
        if schema_entry is None:
            problems.append(f"unknown input {name!r} not in schema")
            continue
        if not isinstance(spec, Mapping):
            problems.append(f"input {name!r} spec must be a mapping")
            continue
        role = spec.get("role", schema_entry["role"])
        if role not in _ALLOWED_ROLES:
            problems.append(f"input {name!r} has invalid role {role!r}")
        elif role != schema_entry["role"]:
            problems.append(
                f"input {name!r} role {role!r} contradicts schema role "
                f"{schema_entry['role']!r}")
        if name == "s1_acquisition_metadata":
            forbidden = {f.lower() for f in schema_entry["forbidden_fields"]}
            records = spec.get("records", [])
            if records is None:
                records = []
            if isinstance(records, (str, bytes)) or not isinstance(
                    records, (list, tuple)):
                problems.append("s1_acquisition_metadata records must be a sequence")
                records = []
            for rec in records:
                if not isinstance(rec, Mapping):
                    problems.append(
                        "s1_acquisition_metadata records must contain mappings")
                    continue
                for key in rec:
                    if str(key).lower() in forbidden:
                        problems.append(
                            f"s1_acquisition_metadata contains derived-signal "
                            f"field {key!r}; derived products belong to optional C")
    for name, schema_entry in INPUT_SCHEMA["inputs"].items():
        if schema_entry["required"] and name not in inputs:
            problems.append(f"missing required input {name!r}")
    return problems


# ---------------------------------------------------------------------------
# Contract serialization + hashes
# ---------------------------------------------------------------------------

def _enum_free(obj: Any) -> Any:
    """Recursively convert enums to their values for JSON serialization."""
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {str(k): _enum_free(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_enum_free(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# Reconciled handoff contract (also covered by the framework hash)
# ---------------------------------------------------------------------------

DATA_STATUSES = (
    "READY", "INCOMPLETE", "INVALID", "UNAVAILABLE", "PROVISIONAL",
)
HANDOFF_ROLES = (
    "A", "B", "C", "D", "E", "F", "context", "sidecar",
)
HANDOFF_KINDS = (
    "raster", "vector", "tabular", "timeseries", "metadata", "composite",
)
PHASE_REQUIRED_ARTIFACT_IDS: dict[str, tuple[str, ...]] = {
    "A": (
        "bashkova_rupper_db", "hma_events_all", "adjudication_ledger",
        "language_coverage_log",
    ),
    "B": (
        "dem_300x300_100m_32645", "rgi15_fixed_box_subset",
        "sentinel2_dry_season_metadata", "hanging_ice_support_grid",
        "sentinel1_winter_acquisition_table",
        "sentinel1_compatible_pair_table", "sentinel1_per_unit_observability",
        "ghsl_built_up_surface", "osm_geofabrik_nepal", "hydrorivers_drainage",
        "osm_infrastructure_grid_300x300_100m_32645",
        "hydrorivers_connectivity_grid_300x300_100m_32645",
    ),
}
B_TARGET_GRID_ARTIFACT_IDS: dict[str, str] = {
    "built_up": "ghsl_built_up_surface",
    "infrastructure": "osm_infrastructure_grid_300x300_100m_32645",
    "river_connectivity": "hydrorivers_connectivity_grid_300x300_100m_32645",
    "hanging_ice_support": "hanging_ice_support_grid",
}
PHASE_ARTIFACT_CONTRACTS: dict[str, dict[str, dict[str, Any]]] = {
    "A": {
        "bashkova_rupper_db": {"role": "A", "kind": "tabular"},
        "hma_events_all": {"role": "A", "kind": "tabular"},
        "adjudication_ledger": {"role": "A", "kind": "metadata"},
        "language_coverage_log": {"role": "A", "kind": "metadata"},
    },
    "B": {
        "dem_300x300_100m_32645": {
            "role": "B", "kind": "raster", "crs": "EPSG:32645",
            "source_semantics": "Copernicus DEM", "target_grid": True,
        },
        "rgi15_fixed_box_subset": {
            "role": "B", "kind": "vector", "source_semantics": "RGI7",
            "vector_bundle": True,
        },
        "sentinel2_dry_season_metadata": {
            "role": "B", "kind": "metadata", "source_semantics": "Sentinel-2",
        },
        "hanging_ice_support_grid": {
            "role": "B", "kind": "raster", "crs": "EPSG:32645",
            "source_semantics": "Sentinel-2", "target_grid": True,
        },
        "sentinel1_winter_acquisition_table": {
            "role": "B", "kind": "metadata", "source_semantics": "Sentinel-1",
        },
        "sentinel1_compatible_pair_table": {
            "role": "B", "kind": "metadata", "source_semantics": "Sentinel-1",
        },
        "sentinel1_per_unit_observability": {
            "role": "B", "kind": "metadata", "source_semantics": "Sentinel-1",
        },
        "ghsl_built_up_surface": {
            "role": "B", "kind": "raster", "crs": "EPSG:32645",
            "source_semantics": "GHSL", "target_grid": True,
        },
        "osm_geofabrik_nepal": {
            "role": "B", "kind": "vector", "source_semantics": "OSM",
            "vector_bundle": True,
        },
        "hydrorivers_drainage": {
            "role": "B", "kind": "vector", "source_semantics": "HydroRIVERS",
            "vector_bundle": True,
        },
        "osm_infrastructure_grid_300x300_100m_32645": {
            "role": "B", "kind": "raster", "crs": "EPSG:32645",
            "source_semantics": "OSM", "target_grid": True,
        },
        "hydrorivers_connectivity_grid_300x300_100m_32645": {
            "role": "B", "kind": "raster", "crs": "EPSG:32645",
            "source_semantics": "HydroRIVERS", "target_grid": True,
        },
    },
}
PHASE_READY_PROCESSING_MARKERS = (
    "incomplete", "substitut", "needs subsetting", "unavailable",
    "not available", "not yet", "pending",
)
PHASE_VECTOR_BUNDLE_REQUIRED_SIDECARS = (".shp", ".shx", ".dbf", ".prj")
B_ARTIFACT_SEMANTICS: dict[str, dict[str, Any]] = {
    "dem_300x300_100m_32645": {
        "units": ("m", "m (elevation)", "meters", "metres"),
        "value_domain": "finite_elevation_m",
        "nodata_policy": "declared_and_masked",
    },
    "ghsl_built_up_surface": {
        "units": ("m2", "m²", "m2 built-up surface per 100m cell",
                   "m² built-up surface per 100m cell"),
        "value_domain": "nonnegative_m2",
        "nodata_policy": "declared_and_masked",
    },
    "osm_infrastructure_grid_300x300_100m_32645": {
        "units": ("binary presence (0/1)", "binary 0/1", "binary"),
        "value_domain": "binary_01",
        "nodata_policy": "declared_and_masked",
    },
    "hydrorivers_connectivity_grid_300x300_100m_32645": {
        "units": ("binary reach presence (0/1)", "binary presence (0/1)",
                   "binary 0/1", "binary"),
        "value_domain": "binary_01",
        "nodata_policy": "declared_and_masked",
    },
    "hanging_ice_support_grid": {
        "units": ("fraction", "fraction of valid scenes (0-1)",
                   "fraction of valid scenes [0,1]"),
        "value_domain": "fraction_0_1_with_minus1_unscreenable",
        "nodata_policy": "minus1_unscreenable",
        "nodata_values": (-1,),
    },
    "worldpop_population": {
        "units": ("persons per 100m cell", "persons per 100 m cell",
                   "persons"),
        "value_domain": "nonnegative_persons_with_minus1_nodata",
        "nodata_policy": "minus1_declared_and_masked",
        "nodata_values": (-1,),
    },
    "sentinel1_per_unit_observability": {
        "units": ("fraction", "analysis-unit coverage fraction",
                   "fraction of compatible pairs"),
        "value_domain": "fraction_0_1",
        "observable": "metadata_pair_coverage_fraction",
        "metadata_only": True,
        "pair_table_required": True,
    },
}
PHASE_INPUT_CONTRACT: dict[str, Any] = {
    "data_statuses": DATA_STATUSES,
    "handoff_roles": HANDOFF_ROLES,
    "handoff_kinds": HANDOFF_KINDS,
    "required_artifact_ids": PHASE_REQUIRED_ARTIFACT_IDS,
    "target_grid_artifact_ids": B_TARGET_GRID_ARTIFACT_IDS,
    "artifact_contracts": PHASE_ARTIFACT_CONTRACTS,
    "artifact_semantics": B_ARTIFACT_SEMANTICS,
    "component_registry_version": COMPONENT_REGISTRY_VERSION,
    "active_terrain_components": ACTIVE_TERRAIN_COMPONENTS,
    "active_exposure_components": ACTIVE_EXPOSURE_COMPONENTS,
    "optional_exposure_components": OPTIONAL_EXPOSURE_COMPONENTS,
    "ready_processing_markers": PHASE_READY_PROCESSING_MARKERS,
    "vector_bundle_required_sidecars": PHASE_VECTOR_BUNDLE_REQUIRED_SIDECARS,
    "target_grid": target_grid_contract(),
}


def contract_dict() -> dict[str, Any]:
    """Canonical, timestamp-free serialization of the whole contract."""
    return {
        "framework_name": FRAMEWORK_NAME,
        "framework_version": FRAMEWORK_VERSION,
        "contract_schema_version": CONTRACT_SCHEMA_VERSION,
        "hash_algorithm": HASH_ALGORITHM,
        "frozen_source": FROZEN_SOURCE,
        "preregistration_sha256": PREREGISTRATION_SHA256,
        "preregistration_data_source_status": PREREGISTRATION_DATA_SOURCE_STATUS,
        "study_box": {
            "crs": STUDY_BOX_CRS,
            "size_m": STUDY_BOX_SIZE_M,
            "half_size_m": STUDY_BOX_HALF_SIZE_M,
            "center_easting": study_box()["center_easting"],
            "center_northing": study_box()["center_northing"],
            "bounds": list(study_box_bounds()),
            "selection_basis": "frozen_source_point_only",
        },
        "winter_window": {
            "start_mmdd": list(WINTER_WINDOW_START),
            "end_mmdd": list(WINTER_WINDOW_END),
            "narrower_windows_policy": SENSITIVITY_ONLY_LABEL,
        },
        "catalog_eligibility_rules": _enum_free(CATALOG_ELIGIBILITY_RULES),
        "gate_thresholds": {
            "A_MIN_ELIGIBLE_EVENTS": GATE_A_MIN_ELIGIBLE_EVENTS,
            "A_MIN_HOLDOUT_GROUPS": GATE_A_MIN_HOLDOUT_GROUPS,
        },
        "gate_ids": list(GATE_IDS),
        "output_statuses": list(OUTPUT_STATUSES),
        "phase_statuses": list(PHASE_STATUSES),
        "b_execution": {
            "default_timeout_seconds": B_DEFAULT_TIMEOUT_SECONDS,
            "strict_checkpoint_required": True,
        },
        "thermal_context": dict(THERMAL_CONTEXT),
        "sidechain_layers": sorted(SIDECHAIN_LAYERS),
        "data_quality_layers": sorted(DATA_QUALITY_LAYERS),
        "terrain_components": list(TERRAIN_COMPONENTS),
        "terrain_context_components": list(TERRAIN_CONTEXT_COMPONENTS),
        "exposure_components": list(EXPOSURE_COMPONENTS),
        "required_terrain_components": list(REQUIRED_TERRAIN_COMPONENTS),
        "required_exposure_components": list(REQUIRED_EXPOSURE_COMPONENTS),
        "component_registry_version": COMPONENT_REGISTRY_VERSION,
        "active_terrain_components": list(ACTIVE_TERRAIN_COMPONENTS),
        "active_exposure_components": list(ACTIVE_EXPOSURE_COMPONENTS),
        "optional_exposure_components": list(OPTIONAL_EXPOSURE_COMPONENTS),
        "artifact_semantics": _enum_free(B_ARTIFACT_SEMANTICS),
        "ranking_indices": list(RANKING_INDICES),
        "bed_elevation_formula": BED_ELEVATION_FORMULA,
        "analysis_unit_id_format": ANALYSIS_UNIT_ID_FORMAT,
        "analysis_unit_cell_m": ANALYSIS_UNIT_CELL_M,
        "tie_break_rules": list(TIE_BREAK_RULES),
        "input_schema": _enum_free(INPUT_SCHEMA),
        "phase_input_contract": _enum_free(PHASE_INPUT_CONTRACT),
    }


def contract_hash() -> str:
    """Deterministic hash over the contract and the frozen preregistration."""
    from .provenance import canonical_json  # local import avoids cycles
    payload = (b"nepal-framework-v1/contract\x00"
               + canonical_json(contract_dict()).encode("utf-8")
               + b"\x00preregistration\x00"
               + PREREGISTRATION_SHA256.encode("ascii"))
    return hashlib.sha256(payload).hexdigest()


def canonical_json_bytes(obj: Any) -> bytes:
    """Canonical JSON bytes for an arbitrary contract object."""
    from .provenance import canonical_json  # local import avoids cycles
    return canonical_json(obj).encode("utf-8")


def verify_preregistration(path: str | Path = PREREGISTRATION_PATH) -> dict[str, Any]:
    """Verify the on-disk frozen preregistration matches the pinned hash."""
    p = Path(path)
    if not p.exists():
        return {"path": str(p), "present": False, "ok": False,
                "expected_sha256": PREREGISTRATION_SHA256, "actual_sha256": None}
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    return {"path": str(p), "present": True, "ok": h == PREREGISTRATION_SHA256,
            "expected_sha256": PREREGISTRATION_SHA256, "actual_sha256": h}


def verify_frozen_files(repo_root: str | Path = ".") -> dict[str, Any]:
    """Exit-gate helper: frozen files must be unmodified."""
    root = Path(repo_root)
    pre = verify_preregistration(root / PREREGISTRATION_PATH)
    return {"ok": bool(pre.get("ok")),
            "data_source_status": PREREGISTRATION_DATA_SOURCE_STATUS,
            "claims_scope": "post_hoc_source_substitution_remains_explicit",
            "files": [pre]}
