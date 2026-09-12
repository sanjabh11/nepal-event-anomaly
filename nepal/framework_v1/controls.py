"""nepal.framework_v1.controls — controls configuration and controls lock.

Controls (negative-control definitions, detectability thresholds, observation
coverage rules) must be locked *before* any candidate or event score is
observed.  The lock is a canonical-JSON sha256 commitment; E refuses to run
against a controls payload whose hash does not match its lock.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional

from .contract import FRAMEWORK_VERSION, HASH_ALGORITHM, OutputStatus
from .provenance import canonical_json, sha256_canonical


class ObservationCoverage(str, Enum):
    """Observation coverage class for controls and catalog rows.

    UNKNOWN coverage is never treated as observed: rows with UNKNOWN or NONE
    coverage are excluded from observed-denominators and flagged."""
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"
    NONE = "NONE"


COVERAGE_ADEQUATE_FOR_OBSERVED = frozenset({ObservationCoverage.FULL})
COVERAGE_USABLE_FOR_COMPARISON = frozenset({ObservationCoverage.FULL,
                                            ObservationCoverage.PARTIAL})


@dataclass
class ControlsConfig:
    """Frozen controls definition for framework_v1 A/B/E.

    All defaults are deliberately conservative; nothing here may be tuned
    after candidate scores are observed."""
    min_detectable_size_m3: float = 1.0e6
    expected_winter_acquisitions: Optional[int] = None
    expected_winter_pairs: Optional[int] = None
    dry_season_min_scenes: int = 3
    hanging_ice_fraction_threshold: float = 0.5
    require_full_coverage_for_observable: bool = True
    logo_split: str = "geographic_leave_one_group_out"
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "min_detectable_size_m3": float(self.min_detectable_size_m3),
            "expected_winter_acquisitions": self.expected_winter_acquisitions,
            "expected_winter_pairs": self.expected_winter_pairs,
            "dry_season_min_scenes": int(self.dry_season_min_scenes),
            "hanging_ice_fraction_threshold": float(self.hanging_ice_fraction_threshold),
            "require_full_coverage_for_observable": bool(
                self.require_full_coverage_for_observable),
            "logo_split": self.logo_split,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ControlsConfig":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


def coverage_is_adequate_for_observable(coverage: "str | ObservationCoverage") -> bool:
    """Only FULL coverage counts as 'observed' for detectable denominators."""
    if isinstance(coverage, str):
        try:
            coverage = ObservationCoverage(coverage)
        except ValueError:
            return False
    return coverage in COVERAGE_ADEQUATE_FOR_OBSERVED


def coverage_is_usable_for_comparison(coverage: "str | ObservationCoverage") -> bool:
    """Controls with UNKNOWN/NONE coverage cannot participate in matched
    comparisons and are flagged, never silently imputed."""
    if isinstance(coverage, str):
        try:
            coverage = ObservationCoverage(coverage)
        except ValueError:
            return False
    return coverage in COVERAGE_USABLE_FOR_COMPARISON


# ---------------------------------------------------------------------------
# Controls lock
# ---------------------------------------------------------------------------

class ControlsLockMismatch(RuntimeError):
    """Raised when a controls payload does not match its pre-locked hash."""


def controls_config_hash(config: ControlsConfig) -> str:
    return sha256_canonical(config.to_dict())


@dataclass
class ControlsLock:
    """Deterministic commitment to the controls configuration."""
    controls: dict[str, Any]
    algorithm: str = HASH_ALGORITHM
    framework_version: str = FRAMEWORK_VERSION
    sha256: str = ""

    def __post_init__(self) -> None:
        if not self.sha256:
            self.sha256 = self._expected_hash()

    def _expected_hash(self) -> str:
        return sha256_canonical(self.controls)

    def verify(self) -> bool:
        return (self.algorithm == HASH_ALGORITHM
                and self.framework_version == FRAMEWORK_VERSION
                and self.sha256 == self._expected_hash())

    def verify_payload(self, payload: Mapping[str, Any]) -> bool:
        return canonical_json(dict(payload)) == canonical_json(self.controls)

    def to_dict(self) -> dict[str, Any]:
        return {
            "controls": self.controls,
            "algorithm": self.algorithm,
            "framework_version": self.framework_version,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "ControlsLock":
        return cls(controls=dict(d["controls"]), algorithm=d.get(
            "algorithm", HASH_ALGORITHM),
            framework_version=d.get("framework_version", FRAMEWORK_VERSION),
            sha256=d.get("sha256", ""))


def create_controls_lock(config: ControlsConfig) -> ControlsLock:
    """Freeze the controls configuration.  Call this *before* observing any
    candidate or event score."""
    return ControlsLock(controls=config.to_dict())


def load_controls_lock(document: Mapping[str, Any]) -> ControlsLock:
    """Load and verify a serialized controls lock document."""
    lock = ControlsLock.from_dict(document)
    if not lock.verify():
        raise ControlsLockMismatch(
            "controls lock sha256 does not match controls payload; the locked "
            "controls were modified after freezing")
    return lock
