"""Focused tests for clean-room export verification."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPORTER = ROOT / "scripts/export_release_bundle.py"
VERIFIER = ROOT / "scripts/verify_exported_bundle.py"


def _sidecar(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_name(path.name + ".sha256").write_text(
        f"{digest}  {path.name}\n", encoding="ascii")
    return digest


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    daily = tmp_path / "daily"
    repo = tmp_path / "repo"
    (daily / "retrieval").mkdir(parents=True)
    (repo / "docs/science").mkdir(parents=True)

    payload = daily / "retrieval/payload.bin"
    payload.write_bytes(b"payload-bytes")
    payload_digest = _sidecar(payload)
    payload_sidecar_digest = hashlib.sha256(
        payload.with_name(payload.name + ".sha256").read_bytes()).hexdigest()

    index = daily / "retrieval/index.json"
    index.write_text(json.dumps({
        "schema": "P5_EVIDENCE_INDEX_V2",
        "files": [{
            "root_id": "daily",
            "relpath": "retrieval/payload.bin",
            "sha256": payload_digest,
            "size_bytes": payload.stat().st_size,
            "sidecar_sha256": payload_sidecar_digest,
            "sidecar_exception": None,
        }],
    }), encoding="utf-8")
    index_digest = _sidecar(index)

    closure = daily / "retrieval/closure.json"
    closure.write_text(json.dumps({
        "schema": "P5_RELEASE_CLOSURE_V4",
        "root_of_trust": {
            "index_schema": "P5_EVIDENCE_INDEX_V2",
            "root_id": "daily",
            "relpath": "retrieval/index.json",
            "sha256": index_digest,
        },
    }), encoding="utf-8")
    _sidecar(closure)

    manifest = repo / "docs/science/ARTIFACT_MANIFEST_V0.json"
    manifest.write_text('{"schema":"fixture-manifest"}\n', encoding="utf-8")

    output = tmp_path / "export"
    command = [
        sys.executable, str(EXPORTER),
        "--bundle-name", "fixture-round3",
        "--output-root", str(output),
        "--repo-root", str(repo),
        "--manifest", "docs/science/ARTIFACT_MANIFEST_V0.json",
        "--root", f"daily={daily}",
    ]
    for relpath in (
        "retrieval/payload.bin",
        "retrieval/index.json",
        "retrieval/closure.json",
    ):
        command.extend(["--include", f"daily:{relpath}"])
    result = subprocess.run(command, cwd=ROOT, check=False,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return output, daily


def _verify(output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VERIFIER), str(output)],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_exported_bundle_resolves_index_and_closure_refs(tmp_path: Path) -> None:
    output, _ = _fixture(tmp_path)
    result = _verify(output)
    assert result.returncode == 0, result.stderr + result.stdout
    assert '"status": "EXPORTED_BUNDLE_OK"' in result.stdout
    assert '"digest_bound_reference_count": 2' in result.stdout


def test_payload_tampering_fails_before_release_claim(tmp_path: Path) -> None:
    output, _ = _fixture(tmp_path)
    (output / "roots/daily/retrieval/payload.bin").write_bytes(b"tampered")
    result = _verify(output)
    assert result.returncode != 0
    assert "SHA-256 mismatch" in result.stdout


def test_root_map_is_required_to_resolve_logical_roots(tmp_path: Path) -> None:
    output, _ = _fixture(tmp_path)
    root_map = json.loads((output / "root_map.json").read_text(encoding="utf-8"))
    root_map["logical_roots"][0]["export_relpath"] = "roots/other"
    (output / "root_map.json").write_text(
        json.dumps(root_map), encoding="utf-8")
    result = _verify(output)
    assert result.returncode != 0
    assert "logical_roots differ" in result.stdout


def test_symlinked_export_directory_is_not_a_clean_room(tmp_path: Path) -> None:
    output, _ = _fixture(tmp_path)
    external = tmp_path / "external"
    external.mkdir()
    shutil.rmtree(output / "roots/daily")
    (output / "roots/daily").symlink_to(external, target_is_directory=True)
    result = _verify(output)
    assert result.returncode != 0
    assert "symlink" in result.stdout


def test_duplicate_logical_root_mapping_is_rejected(tmp_path: Path) -> None:
    output, _ = _fixture(tmp_path)
    root_map = json.loads((output / "root_map.json").read_text(encoding="utf-8"))
    values = list(root_map["host_path_to_logical_id"])
    assert len(values) >= 2
    root_map["host_path_to_logical_id"][values[-1]] = "daily"
    (output / "root_map.json").write_text(
        json.dumps(root_map), encoding="utf-8")
    result = _verify(output)
    assert result.returncode != 0
    assert "exactly once" in result.stdout
