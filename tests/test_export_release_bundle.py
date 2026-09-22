"""Focused tests for the explicit, exclusive-create release exporter."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/export_release_bundle.py"


def _sidecar(path: Path) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_name(path.name + ".sha256").write_text(
        f"{digest}  {path.name}\n",
        encoding="ascii",
    )


def _fixture(tmp_path: Path) -> dict[str, Path | list[str]]:
    daily = tmp_path / "daily-evidence"
    seasonal = tmp_path / "seasonal-evidence"
    repo = tmp_path / "repo"
    for directory in (
        daily / "retrieval",
        daily / "closure",
        seasonal / "replay",
        repo / "docs/science",
    ):
        directory.mkdir(parents=True)

    payloads = [
        (daily, "retrieval/index-v9.json", b"index-v9"),
        (daily, "closure/receipt-v9.json", b"receipt-v9"),
        (daily, "closure/closure-v9.json", b"closure-v9"),
        (seasonal, "replay/seasonal-v1.json", b"seasonal-replay-v1"),
    ]
    includes: list[str] = []
    for root, relpath, content in payloads:
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        _sidecar(path)
        root_id = "daily" if root == daily else "seasonal"
        includes.append(f"{root_id}:{relpath}")

    # This file demonstrates that the exporter is an allow-list, not a
    # recursive copy of the physical evidence root.
    (daily / "retrieval/unlisted-payload.json").write_bytes(b"do not export")

    manifest = repo / "docs/science/ARTIFACT_MANIFEST_V0.json"
    manifest.write_text('{"schema": "fixture-manifest"}\n', encoding="utf-8")

    return {
        "daily": daily,
        "seasonal": seasonal,
        "repo": repo,
        "manifest": manifest,
        "includes": includes,
    }


def _command(
    fixture: dict[str, Path | list[str]],
    output: Path,
    *extra: str,
) -> list[str]:
    includes = fixture["includes"]
    assert isinstance(includes, list)
    command = [
        sys.executable,
        str(SCRIPT),
        "--bundle-name",
        "fixture-v9",
        "--output-root",
        str(output),
        "--repo-root",
        str(fixture["repo"]),
        "--manifest",
        "docs/science/ARTIFACT_MANIFEST_V0.json",
        "--root",
        f"daily={fixture['daily']}",
        "--root",
        f"seasonal={fixture['seasonal']}",
    ]
    for include in includes:
        command.extend(["--include", include])
    command.extend(extra)
    return command


def _run(
    fixture: dict[str, Path | list[str]],
    output: Path,
    *extra: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _command(fixture, output, *extra),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_export_is_allow_listed_and_logical(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    output = tmp_path / "exports/fixture-v9"
    result = _run(fixture, output)
    assert result.returncode == 0, result.stderr

    bundle = json.loads((output / "bundle.json").read_text(encoding="utf-8"))
    root_map = json.loads(
        (output / "root_map.json").read_text(encoding="utf-8"))
    assert bundle["schema"] == "P5_RELEASE_BUNDLE_EXPORT_V1"
    assert bundle["source_paths_excluded_from_bundle_metadata"] is True
    assert str(fixture["daily"]) not in json.dumps(bundle)
    assert str(fixture["repo"]) not in json.dumps(bundle)
    assert root_map["host_path_to_logical_id"][str(fixture["daily"])] == "daily"
    assert root_map["host_path_to_logical_id"][str(fixture["repo"])] == (
        "repository")

    exported_paths = {
        entry["export_relpath"] for entry in bundle["files"]
    }
    assert "roots/daily/retrieval/unlisted-payload.json" not in exported_paths
    assert (
        output / "roots/daily/retrieval/index-v9.json"
    ).read_bytes() == b"index-v9"
    assert (
        output / "roots/seasonal/replay/seasonal-v1.json"
    ).read_bytes() == b"seasonal-replay-v1"
    manifest_output = (
        output / "repository/docs/science/ARTIFACT_MANIFEST_V0.json")
    assert manifest_output.exists()
    assert manifest_output.with_name(
        manifest_output.name + ".sha256").exists()


def test_export_refuses_overwrite_and_preserves_existing_bytes(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    output = tmp_path / "exports/fixture-v9"
    first = _run(fixture, output)
    assert first.returncode == 0, first.stderr
    before = (output / "bundle.json").read_bytes()

    second = _run(fixture, output)
    assert second.returncode != 0
    assert "refusing to overwrite" in second.stderr
    assert (output / "bundle.json").read_bytes() == before


def test_export_dry_run_does_not_create_output(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    output = tmp_path / "exports/dry-run"
    result = _run(fixture, output, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert '"dry_run": true' in result.stdout
    assert not output.exists()


def test_export_rejects_bad_sidecar_before_creating_output(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    bad_sidecar = (
        fixture["daily"] / "retrieval/index-v9.json.sha256"
    )
    assert isinstance(bad_sidecar, Path)
    bad_sidecar.write_text("0" * 64 + "  index-v9.json\n", encoding="ascii")
    output = tmp_path / "exports/bad-sidecar"
    result = _run(fixture, output)
    assert result.returncode != 0
    assert "sidecar digest mismatch" in result.stderr
    assert not output.exists()


def test_export_rejects_path_traversal(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    output = tmp_path / "exports/traversal"
    command = _command(fixture, output)
    command.extend(["--include", "daily:../outside.json"])
    result = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "unsafe component" in result.stderr
    assert not output.exists()


def test_export_rejects_symlink_payload(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    source = fixture["daily"] / "retrieval/index-v9.json"
    link = fixture["daily"] / "retrieval/link.json"
    assert isinstance(source, Path)
    link.symlink_to(source)
    output = tmp_path / "exports/symlink"
    command = _command(fixture, output)
    command.extend(["--include", "daily:retrieval/link.json"])
    result = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "regular non-symlink file" in result.stderr
    assert not output.exists()
