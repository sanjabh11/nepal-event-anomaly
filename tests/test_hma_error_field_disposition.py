"""Contract tests for the sealed HMA Error-field disposition artifact."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import hma_error_field_disposition as disp  # noqa: E402
from p5_safe_io import ExistingEvidenceError, write_once_json  # noqa: E402

ARTIFACT = disp.DEFAULT_OUT
EPOCHS = ("1990", "2000", "2010", "2015", "2020")


@pytest.fixture(scope="module")
def artifact() -> dict:
    if not ARTIFACT.is_file():
        pytest.fail(f"sealed artifact missing: {ARTIFACT}")
    return json.loads(ARTIFACT.read_text(encoding="utf-8"))


def test_deterministic_rerun_matches_artifact_bytes(tmp_path: Path):
    out = tmp_path / "rerun.json"
    assert disp.main(["--out", str(out)]) == 0
    assert out.read_bytes() == ARTIFACT.read_bytes()


def test_sidecar_matches_artifact():
    sidecar = Path(str(ARTIFACT) + ".sha256")
    assert sidecar.is_file()
    digest = hashlib.sha256(ARTIFACT.read_bytes()).hexdigest()
    assert sidecar.read_text().split()[0] == digest


def test_artifact_shape_and_authority(artifact: dict):
    assert artifact["schema"] == "HMA_ERROR_FIELD_DISPOSITION_V0"
    assert artifact["authority"] == disp.AUTHORITY_FLAGS
    assert not any(artifact["authority"].values())
    assert sorted(artifact["per_epoch"]) == sorted(EPOCHS)
    for epoch in EPOCHS:
        profile = artifact["per_epoch"][epoch]
        assert profile["n_features"] > 0
        assert "Perimeter" in profile["columns_present"]
        assert "Error" in profile["columns_present"]
        member = profile["xml_member"]
        assert member["manifest_binding"] == "PRESENT_MATCH"
        assert member["sha256"] == member["archive_member_sha256"]
        assert profile["xml_sha256"] == member["sha256"]
        assert profile["shp_member"]["manifest_binding"] == "PRESENT_MATCH"


def test_disposition_resolved_and_constants(artifact: dict):
    assert artifact["disposition"]["status"] == "RESOLVED_UNITS"
    assert all(artifact["disposition"]["checks"].values())
    p2000 = artifact["per_epoch"]["2000"]
    assert p2000["error_stored_unit_inferred"] == "m2"
    assert p2000["perimeter_stored_unit"] == "kilometre"
    assert p2000["recovered_formula_constant_m"] == pytest.approx(
        10.308, rel=1e-9)
    assert not p2000["xml_records_km2_conversion"]
    for epoch in ("1990", "2010", "2015", "2020"):
        profile = artifact["per_epoch"][epoch]
        assert profile["error_stored_unit_inferred"] == "km2"
        assert profile["perimeter_stored_unit"] == "metre"
        assert profile["recovered_formula_constant_m"] == pytest.approx(
            15.0, rel=1e-9)
        assert profile["xml_records_km2_conversion"]
        assert profile["pearson_r_error_vs_perimeter"] == pytest.approx(
            1.0, abs=1e-9)


def test_second_write_refuses_overwrite(tmp_path: Path):
    out = tmp_path / "dup.json"
    write_once_json(out, {"a": 1})
    with pytest.raises(ExistingEvidenceError):
        write_once_json(out, {"a": 2})
    # and the sealed artifact path itself refuses replacement
    with pytest.raises(ExistingEvidenceError):
        write_once_json(ARTIFACT, {"a": 3})
