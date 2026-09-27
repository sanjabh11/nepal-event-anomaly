"""Contract module for reconciled framework inputs.

Defines the artifact contract, required fields, status taxonomy, and validation rules.
This module is the single source of truth for what the reconciled handoff must contain.
"""
from __future__ import annotations

from pathlib import Path

# === SCHEMA ===
SCHEMA_VERSION = "2.0.0-reconciled"

# === STATUS TAXONOMY ===
ALLOWED_STATUSES = ("READY", "INCOMPLETE", "INVALID", "UNAVAILABLE", "PROVISIONAL")

# === ROLES ===
# A = Event catalog & literature
# B = Susceptibility screening
# C = Multi-sensor precursor (opportunistic)
# D = Physics-based runout (sensitivity)
# E = Validation & uncertainty
# F = Institutional briefing
# context = Paper 0 / context only (not a B ranking criterion)
# sidecar = Low-confidence supplementary
ALLOWED_ROLES = ("A", "B", "C", "D", "E", "F", "context", "sidecar")

# === KINDS ===
ALLOWED_KINDS = ("raster", "vector", "tabular", "timeseries", "metadata", "composite")

# === REQUIRED FIELDS PER ARTIFACT (18 fields) ===
REQUIRED_FIELDS = (
    "artifact_id",
    "role",
    "kind",
    "relative_path",
    "status",
    "sha256",
    "bytes",
    "crs",
    "units",
    "license",
    "source_url",
    "query_or_request",
    "source_record_id",
    "acquired_at",
    "observation_start",
    "observation_end",
    "publication_or_validity_date",
    "processing",
    "language_access_status",
)

# === STUDY BOX (fixed 30km box around event reference point) ===
# Event: 28.288708°N, 85.528159°E
# 30km box: ~±0.135° lat, ~±0.153° lon (at 28°N)
STUDY_BOX = {
    "center_lat": 28.288708,
    "center_lon": 85.528159,
    "min_lat": 28.02,
    "max_lat": 28.56,
    "min_lon": 85.28,
    "max_lon": 85.78,
    "buffer_km": 30,
}

# === TARGET GRID for B screening ===
TARGET_GRID = {
    "width": 300,
    "height": 300,
    "resolution_m": 100,
    "crs": "EPSG:32645",  # UTM Zone 45N for Nepal
}

# === FOCAL RGI ID ===
FOCAL_RGI_ID = "RGI2000-v7.0-G-15-05732"

# === ERA5 PROTOCOL SEPARATION ===
# Paper 0 / context: JJA thermal (existing EDH daily file)
# B ranking: winter windows for InSAR coherence (separate manifest)
ERA5_PROTOCOLS = {
    "paper0_context": {
        "description": "JJA thermal data for Paper 0 anomaly assessment. NOT a B ranking criterion.",
        "file": "era5/era5_land_nepal_jja_2001_2026.nc",
        "role": "context",
    },
    "winter_b_screening": {
        "description": "Winter ERA5 for B screening (if needed). Separate from Paper 0.",
        "role": "B",
        "status": "UNAVAILABLE",  # Not yet acquired
    },
}

# === DRY-SEASON WINDOW for Sentinel-2 ===
# Pre-event dry season: November through March (pre-monsoon)
DRY_SEASON_WINDOW = {
    "start_month": 11,  # November
    "end_month": 3,     # March
    "pre_event_years": "2024-2025",  # Most recent dry season before Aug 2026 event
    "max_cloud_cover_pct": 20,
}

# === VERIFICATION GATES ===
HANDOFF_GATES = {
    "manifest_verifies": False,
    "all_required_b_inputs_ready": False,
    "all_crs_grid_checks_pass": False,
    "no_raw_slc": False,
    "no_provisional_consumed": False,
    "source_license_access_populated": False,
    "persistent_size_ok": False,
    "temp_size_ok": False,
    "output_root_isolated": False,
}


def get_contract_sha256(contract_path: Path) -> str:
    """Get SHA-256 of the feature contract file."""
    import hashlib
    if not contract_path.exists():
        return ""
    h = hashlib.sha256()
    with open(contract_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
