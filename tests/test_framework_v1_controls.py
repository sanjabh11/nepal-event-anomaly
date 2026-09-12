"""Phase A controls tests: deterministic controls lock, tamper rejection,
round-trip stability, and the observation-coverage boundary.

The controls lock is the fail-closed commitment E/F consume.  Nothing here may
authorize a gate by caller-supplied booleans: only the locked payload hash is
evidence.
"""
import json

import pytest

from nepal.framework_v1.controls import (ControlsConfig, ControlsLock,
                                         ControlsLockMismatch,
                                         controls_config_hash,
                                         coverage_is_adequate_for_observable,
                                         coverage_is_usable_for_comparison,
                                         create_controls_lock,
                                         load_controls_lock)
from nepal.framework_v1.provenance import sha256_canonical


CONFIG = ControlsConfig(min_detectable_size_m3=4.0e6,
                        dry_season_min_scenes=3,
                        notes="phase-a lane lock")


class TestControlsConfig:
    def test_to_dict_is_stable_and_json_safe(self):
        first, second = CONFIG.to_dict(), CONFIG.to_dict()
        assert first == second
        json.dumps(first)  # must not raise

    def test_from_dict_roundtrip(self):
        restored = ControlsConfig.from_dict(CONFIG.to_dict())
        assert restored == CONFIG
        assert controls_config_hash(restored) == controls_config_hash(CONFIG)

    def test_from_dict_ignores_unknown_keys(self):
        payload = dict(CONFIG.to_dict(), unexpected_field="drop-me")
        assert ControlsConfig.from_dict(payload) == CONFIG

    def test_distinct_configs_hash_differently(self):
        other = ControlsConfig(min_detectable_size_m3=5.0e6)
        assert controls_config_hash(CONFIG) != controls_config_hash(other)


class TestControlsLock:
    def test_lock_is_deterministic_for_identical_input(self):
        first = create_controls_lock(CONFIG)
        second = create_controls_lock(ControlsConfig.from_dict(CONFIG.to_dict()))
        assert first.to_dict() == second.to_dict()
        assert first.sha256 == second.sha256
        assert first.sha256 == sha256_canonical(CONFIG.to_dict())

    def test_lock_verify_detects_any_payload_or_metadata_tamper(self):
        lock = create_controls_lock(CONFIG)
        assert lock.verify() is True
        for field, mutated in (
            ("controls", dict(lock.controls, min_detectable_size_m3=1.0e6)),
            ("algorithm", "md5"),
            ("framework_version", "0.9.0"),
            ("sha256", "0" * 64),
        ):
            document = lock.to_dict()
            document[field] = mutated
            assert ControlsLock.from_dict(document).verify() is False, field

    def test_load_controls_lock_rejects_tampered_document(self):
        document = create_controls_lock(CONFIG).to_dict()
        document["controls"]["dry_season_min_scenes"] = 99
        with pytest.raises(ControlsLockMismatch):
            load_controls_lock(document)

    def test_verify_payload_rejects_modified_controls(self):
        lock = create_controls_lock(CONFIG)
        assert lock.verify_payload(CONFIG.to_dict()) is True
        assert lock.verify_payload(
            dict(CONFIG.to_dict(), min_detectable_size_m3=1.0)) is False


class TestCoverageBoundary:
    def test_only_full_coverage_is_observable(self):
        assert coverage_is_adequate_for_observable("FULL") is True
        for value in ("PARTIAL", "UNKNOWN", "NONE", "nonsense"):
            assert coverage_is_adequate_for_observable(value) is False

    def test_unknown_and_none_never_usable_for_comparison(self):
        assert coverage_is_usable_for_comparison("PARTIAL") is True
        assert coverage_is_usable_for_comparison("UNKNOWN") is False
        assert coverage_is_usable_for_comparison("NONE") is False
        assert coverage_is_usable_for_comparison("garbage") is False
