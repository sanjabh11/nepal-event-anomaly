"""Deterministic CDS request manifest builder for ERA5 acquisitions.

Builds bounded, reproducible CDS API request manifests for ERA5-Land
and ERA5 pressure-level data. Never logs secrets. Never includes
API keys in manifests.

Request manifests are JSON-serializable and hashable for CAS registration.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


# Frozen R14 contract constants.
AOI_HASH_V1 = "aa2f5e0877ca00c7723aa84fc35d32acee33660194785088ef17d66563446e29"

# Spatial crops (N, W, S, E) for CDS area parameter.
REGION_PIR_PANJAL = {"name": "pir_panjal", "north": 35.0, "west": 73.0, "south": 33.0, "east": 75.0}
REGION_NEPAL = {"name": "nepal", "north": 29.0, "west": 85.0, "south": 27.0, "east": 87.5}

# Winter definitions (Nov–Apr).
WINTERS = {
    "2023-24": {"start": "2023-11-01", "end": "2024-04-30"},
    "2024-25": {"start": "2024-11-01", "end": "2025-04-30"},
    "2025-26": {"start": "2025-11-01", "end": "2026-04-30"},
}

# ERA5-Land: 36 variables (21 instantaneous + 15 accumulated).
ERA5_LAND_VARIABLES = [
    "2m_temperature", "2m_dewpoint_temperature", "skin_temperature",
    "soil_temperature_level_1", "soil_temperature_level_2",
    "soil_temperature_level_3", "soil_temperature_level_4",
    "volumetric_soil_water_layer_1", "volumetric_soil_water_layer_2",
    "volumetric_soil_water_layer_3", "volumetric_soil_water_layer_4",
    "snow_depth_water_equivalent", "snow_depth", "snow_density",
    "snow_albedo", "snow_cover",
    "10m_u_component_of_wind", "10m_v_component_of_wind",
    "surface_net_solar_radiation", "surface_net_thermal_radiation",
    "surface_solar_radiation_downwards", "surface_thermal_radiation_downwards",
    "snowfall", "snowmelt", "snow_evaporation",
    "total_precipitation", "runoff", "sub_surface_runoff",
    "surface_runoff", "potential_evaporation",
    "total_evaporation", "evaporation_from_bare_soil",
    "evaporation_from_open_water_surfaces_excluding_oceans",
    "evaporation_from_the_top_of_canopy",
    "evaporation_from_vegetation_transpiration",
    "lake_total_layer_temperature",
]

# ERA5 pressure levels: 6 variables × 10 levels.
ERA5_PL_VARIABLES = [
    "geopotential", "temperature", "u_component_of_wind",
    "v_component_of_wind", "specific_humidity", "relative_humidity",
]
ERA5_PL_LEVELS = ["1000", "925", "850", "700", "500", "300", "250", "200", "100", "50"]

# All 24 hourly time steps.
HOURS_24 = [f"{h:02d}:00" for h in range(24)]


@dataclass
class RequestManifest:
    """A deterministic CDS API request manifest."""

    dataset: str
    provider: str
    variables: list[str]
    year: str
    month: str
    days: list[str]
    hours: list[str]
    area: list[float]
    region: str
    winter_id: str
    pressure_levels: list[str] | None = None
    data_format: str = "grib"
    download_format: str = "unarchived"

    def to_request_dict(self) -> dict[str, Any]:
        """Convert to cdsapi.Client().retrieve() request dict."""
        req: dict[str, Any] = {
            "variable": self.variables,
            "year": self.year,
            "month": self.month,
            "day": self.days,
            "time": self.hours,
            "area": self.area,
            "data_format": self.data_format,
            "download_format": self.download_format,
        }
        if self.pressure_levels:
            req["pressure_level"] = self.pressure_levels
        return req

    def to_dict(self) -> dict[str, Any]:
        """Full manifest with metadata for CAS registration."""
        return {
            "manifest_type": "CDS_REQUEST",
            "dataset": self.dataset,
            "provider": self.provider,
            "variables": self.variables,
            "year": self.year,
            "month": self.month,
            "days": self.days,
            "hours": self.hours,
            "area": self.area,
            "region": self.region,
            "winter_id": self.winter_id,
            "pressure_levels": self.pressure_levels,
            "data_format": self.data_format,
            "download_format": self.download_format,
            "aoi_hash": AOI_HASH_V1,
        }

    def digest(self) -> str:
        """SHA-256 digest of the canonical JSON manifest."""
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    @property
    def partition_id(self) -> str:
        """Deterministic partition ID including day range."""
        day_range = f"{self.days[0]}-{self.days[-1]}"
        parts = [self.provider, self.dataset, self.winter_id, self.region, self.year, self.month, day_range]
        if self.pressure_levels:
            parts.append("pl")
        return "_".join(parts)


def _days_in_month(year: int, month: int) -> list[str]:
    """Return list of zero-padded day strings for a month."""
    import calendar
    n = calendar.monthrange(year, month)[1]
    return [f"{d:02d}" for d in range(1, n + 1)]


def _day_chunks(year: int, month: int, chunk_size: int = 10) -> list[list[str]]:
    """Split days of a month into chunks of *chunk_size* days.

    CDS cost limits prevent requesting a full month of ERA5-Land
    (36 var × 30 days × 24 hours = 25,920 fields exceeds the limit).
    10-day chunks (8,640 fields) are within the limit.
    """
    import calendar
    n = calendar.monthrange(year, month)[1]
    days = [f"{d:02d}" for d in range(1, n + 1)]
    return [days[i:i + chunk_size] for i in range(0, n, chunk_size)]


def build_era5_land_manifests(
    winter_id: str,
    region: dict[str, Any],
    chunk_size: int = 10,
) -> list[RequestManifest]:
    """Build all ERA5-Land manifests for a winter × region.

    Uses 10-day chunks to stay within CDS cost limits.
    Returns ~18 manifests (6 months × 3 chunks) per winter × region.
    """
    winter = WINTERS[winter_id]
    start_year = int(winter["start"][:4])

    manifests: list[RequestManifest] = []
    months = [
        (start_year, 11), (start_year, 12),
        (start_year + 1, 1), (start_year + 1, 2),
        (start_year + 1, 3), (start_year + 1, 4),
    ]
    for year, month in months:
        for chunk_idx, days in enumerate(_day_chunks(year, month, chunk_size)):
            manifests.append(RequestManifest(
                dataset="reanalysis-era5-land",
                provider="copernicus_cds",
                variables=ERA5_LAND_VARIABLES,
                year=f"{year}",
                month=f"{month:02d}",
                days=days,
                hours=HOURS_24,
                area=[region["north"], region["west"], region["south"], region["east"]],
                region=region["name"],
                winter_id=winter_id,
            ))
    return manifests


def build_era5_pl_manifests(
    winter_id: str,
    region: dict[str, Any],
    chunk_size: int = 10,
) -> list[RequestManifest]:
    """Build all ERA5 pressure-level manifests for a winter × region.

    Uses 10-day chunks. ERA5-PL has 6 variables × 10 levels = 60 fields
    per timestep, so 10 days × 24 hours × 60 = 14,400 fields per chunk.
    """
    winter = WINTERS[winter_id]
    start_year = int(winter["start"][:4])

    manifests: list[RequestManifest] = []
    months = [
        (start_year, 11), (start_year, 12),
        (start_year + 1, 1), (start_year + 1, 2),
        (start_year + 1, 3), (start_year + 1, 4),
    ]
    for year, month in months:
        for chunk_idx, days in enumerate(_day_chunks(year, month, chunk_size)):
            manifests.append(RequestManifest(
                dataset="reanalysis-era5-pressure-levels",
                provider="copernicus_cds",
                variables=ERA5_PL_VARIABLES,
                year=f"{year}",
                month=f"{month:02d}",
                days=days,
                hours=HOURS_24,
                area=[region["north"], region["west"], region["south"], region["east"]],
                region=region["name"],
                winter_id=winter_id,
                pressure_levels=ERA5_PL_LEVELS,
            ))
    return manifests


def all_era5_land_partitions() -> list[RequestManifest]:
    """Build all ERA5-Land partitions: 3 winters × 2 regions × ~18 chunks = ~108."""
    manifests: list[RequestManifest] = []
    for winter_id in WINTERS:
        for region in [REGION_PIR_PANJAL, REGION_NEPAL]:
            manifests.extend(build_era5_land_manifests(winter_id, region))
    return manifests


def all_era5_pl_partitions() -> list[RequestManifest]:
    """Build all ERA5-PL partitions: 3 winters × 2 regions × ~18 chunks = ~108."""
    manifests: list[RequestManifest] = []
    for winter_id in WINTERS:
        for region in [REGION_PIR_PANJAL, REGION_NEPAL]:
            manifests.extend(build_era5_pl_manifests(winter_id, region))
    return manifests
