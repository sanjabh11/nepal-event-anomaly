"""Incident-surface descriptor regression tests (G-10/G-26).

The validator must accept a well-formed descriptor with matching
hashes/sizes/sidecar and fail closed on tampering, size drift, missing
artifacts, wrong schema, or a non-excluded release flag.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_incident_surface.py"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_sidecar(path: Path, data: bytes) -> None:
    path.write_text(f"{_sha(data)}  {path.name}\n", encoding="utf-8")


def _make_surface(tmp_path: Path, *, tamper: bytes | None = None,
                  drop_entry: str | None = None,
                  exclude_flag: bool = True) -> Path:
    surface = tmp_path / "surface"
    surface.mkdir()
    blob = surface / "artifact.patch"
    blob.write_bytes(b"patch-bytes\n")
    other = surface / "preserved.json"
    other.write_bytes(b'{"mutated": true}\n')
    entries = [
        {"name": blob.name, "bytes": blob.stat().st_size,
         "sha256": _sha(blob.read_bytes()), "purpose": "dirty diff"},
        {"name": other.name, "bytes": other.stat().st_size,
         "sha256": _sha(other.read_bytes()), "purpose": "mutated copy"}]
    if drop_entry:
        entries = [e for e in entries if e["name"] != drop_entry]
    descriptor = {
        "schema": "INCIDENT_SURFACE_V1",
        "title": "test surface",
        "generated_utc": "2026-09-21T00:00:00Z",
        "main_release_excluded": exclude_flag,
        "exclusion_reason": "incident artifacts; not scientific evidence",
        "entries": entries}
    raw = json.dumps(descriptor, indent=2, sort_keys=True).encode() + b"\n"
    if tamper is not None:
        raw = raw + tamper
    desc_path = surface / "INCIDENT_SURFACE_V1.json"
    desc_path.write_bytes(raw)
    _write_sidecar(Path(str(desc_path) + ".sha256"), raw)
    return desc_path


def _run(descriptor: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--descriptor", str(descriptor)],
        capture_output=True, text=True, check=False)


def test_valid_surface_passes(tmp_path):
    result = _run(_make_surface(tmp_path))
    assert result.returncode == 0, result.stdout
    assert "INCIDENT_SURFACE_OK" in result.stdout
    assert '"artifact_count": 2' in result.stdout


def test_tampered_artifact_fails(tmp_path):
    desc = _make_surface(tmp_path)
    surface_dir = desc.parent
    (surface_dir / "artifact.patch").write_bytes(b"tampered\n")
    result = _run(desc)
    assert result.returncode == 1
    assert "artifact digest mismatch: artifact.patch" in result.stdout


def test_size_drift_fails(tmp_path):
    desc = _make_surface(tmp_path)
    doc = json.loads(desc.read_text(encoding="utf-8"))
    doc["entries"][0]["bytes"] = 999
    raw = json.dumps(doc, indent=2, sort_keys=True).encode() + b"\n"
    desc.write_bytes(raw)
    (Path(str(desc) + ".sha256")).write_text(
        f"{_sha(raw)}  {desc.name}\n", encoding="utf-8")
    result = _run(desc)
    assert result.returncode == 1
    assert "artifact size mismatch" in result.stdout


def test_missing_artifact_fails(tmp_path):
    desc = _make_surface(tmp_path)
    (desc.parent / "preserved.json").unlink()
    result = _run(desc)
    assert result.returncode == 1
    assert "artifact missing: preserved.json" in result.stdout


def test_wrong_schema_fails(tmp_path):
    desc = _make_surface(tmp_path)
    doc = json.loads(desc.read_text(encoding="utf-8"))
    doc["schema"] = "INCIDENT_SURFACE_V0"
    raw = json.dumps(doc, indent=2, sort_keys=True).encode() + b"\n"
    desc.write_bytes(raw)
    (Path(str(desc) + ".sha256")).write_text(
        f"{_sha(raw)}  {desc.name}\n", encoding="utf-8")
    result = _run(desc)
    assert result.returncode == 1
    assert "schema must be INCIDENT_SURFACE_V1" in result.stdout


def test_release_inclusion_flag_fails(tmp_path):
    desc = _make_surface(tmp_path, exclude_flag=False)
    result = _run(desc)
    assert result.returncode == 1
    assert "main_release_excluded must be exactly true" in result.stdout


def test_missing_sidecar_fails(tmp_path):
    desc = _make_surface(tmp_path)
    Path(str(desc) + ".sha256").unlink()
    result = _run(desc)
    assert result.returncode == 1
    assert "self sidecar missing" in result.stdout


def test_stale_sidecar_fails(tmp_path):
    desc = _make_surface(tmp_path)
    sidecar = Path(str(desc) + ".sha256")
    stale = sidecar.read_text(encoding="utf-8").replace(
        sidecar.read_text(encoding="utf-8").split()[0], "0" * 64)
    sidecar.write_text(stale, encoding="utf-8")
    result = _run(desc)
    assert result.returncode == 1
    assert "self sidecar digest mismatch" in result.stdout


def test_missing_descriptor_fails_cleanly(tmp_path):
    result = _run(tmp_path / "nope" / "INCIDENT_SURFACE_V1.json")
    assert result.returncode == 1
    assert "INCIDENT_SURFACE_FAIL" in result.stdout
