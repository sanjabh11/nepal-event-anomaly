"""Regression guards for the artifact/model replay proof boundary."""

from __future__ import annotations

import ast
import importlib.util
import json
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
REPLAY_SCRIPTS = (
    ROOT / "scripts/replay_p5.py",
    ROOT / "scripts/replay_seasonal_p5.py",
)
FORBIDDEN_SYMBOLS = {"run_regimes", "freeze_regime_artifact"}


def _qualified_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _forbidden_ast_uses(tree: ast.AST) -> list[str]:
    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if alias.name.rsplit(".", 1)[-1] in FORBIDDEN_SYMBOLS:
                    findings.append(f"import:{alias.name}")
        if isinstance(node, ast.Call):
            name = _qualified_name(node.func)
            if name and name.rsplit(".", 1)[-1] in FORBIDDEN_SYMBOLS:
                findings.append(f"call:{name}")
    return findings


def _load_module_constants(script: Path):
    spec = importlib.util.spec_from_file_location(
        f"_replay_boundary_{script.stem}", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _latest_reports() -> list[Path]:
    explicit = [
        os.environ.get("P5_DAILY_REPLAY_REPORT"),
        os.environ.get("P5_SEASONAL_REPLAY_REPORT"),
    ]
    if all(explicit):
        return [Path(value) for value in explicit if value]

    daily_module = _load_module_constants(REPLAY_SCRIPTS[0])
    seasonal_module = _load_module_constants(REPLAY_SCRIPTS[1])
    daily_root = Path(daily_module.DEFAULT_ROOT)
    seasonal_root = Path(seasonal_module.DEFAULT_LANE_ROOT)
    daily = sorted(daily_root.glob("retrieval/p5_replay_report_*.json"))
    seasonal = sorted(
        seasonal_root.glob("run/seasonal_replay_report_*.json"))
    if daily and seasonal:
        return [daily[-1], seasonal[-1]]
    return []


def test_replay_modules_have_no_model_execution_symbol_use() -> None:
    for script in REPLAY_SCRIPTS:
        source = script.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(script))
        assert _forbidden_ast_uses(tree) == [], script
        assert "artifact_integrity_replay" in source
        assert "model_reexecution" in source


def test_bound_replay_reports_are_artifact_integrity_only() -> None:
    reports = _latest_reports()
    if not reports:
        pytest.skip(
            "no bound daily and seasonal replay reports configured; "
            "source-level boundary checks still run")
    for path in reports:
        document = json.loads(path.read_text(encoding="utf-8"))
        assert document["status"] == "REPLAY_OK", path
        assert document["replay_scope"] == "artifact_integrity_replay", path
        assert document["model_reexecution"]["status"] == "NOT_RUN", path
