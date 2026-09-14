"""Negative import and runtime-surface tests for research_v0 (G19/A17).

research_v0 must be import-isolated: no import of the frozen
``nepal.framework_v1`` package, no quarantined legacy modules, no
network/download/process modules, no filesystem write surface, and no
dynamic-import or eval/exec escape hatches.
"""
from __future__ import annotations

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "nepal" / "research_v0"

FORBIDDEN_IMPORT_FRAGMENTS = (
    "framework_v1",
    "requests", "urllib", "http.client", "httpx", "socket",
    "cdsapi", "boto3", "s3fs", "requests_cache", "ftplib",
    "subprocess", "os", "shutil", "tempfile", "importlib",
    "multiprocessing", "threading",
)

# Quarantined legacy module names observed in the frozen package's
# research-boundary tests.
QUARANTINED_NAMES = (
    "nepal_anomaly", "legacy_runner", "era5_download",
    "change_point_detector", "run_nepal_test",
)

# Attribute call names that indicate filesystem mutation.  Names that
# are also common non-path methods (str.replace, shutil copy/move —
# shutil itself is import-banned) are excluded; path-object mutation
# methods remain flagged.
_WRITE_ATTRS = frozenset({
    "write_text", "write_bytes", "touch", "unlink", "rename",
    "mkdir", "rmdir", "rmtree", "makedirs", "remove",
    "removedirs", "symlink_to", "hardlink_to", "chmod", "chown",
    "Popen", "system", "exec", "eval"})


def _module_sources():
    sources = sorted(PKG.glob("*.py"))
    assert sources, "research_v0 package is missing"
    return sources


def _trees():
    return [(src, ast.parse(src.read_text(encoding="utf-8")))
            for src in _module_sources()]


def _imported_modules(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import inside the package
                continue
            names.append(node.module or "")
    return names


def test_no_framework_v1_imports():
    for src, tree in _trees():
        for mod in _imported_modules(tree):
            assert "framework_v1" not in mod, (
                f"{src.name} imports {mod}: research_v0 must be "
                "import-isolated from the frozen package")


def test_no_quarantined_or_network_or_os_imports():
    for src, tree in _trees():
        for mod in _imported_modules(tree):
            for frag in FORBIDDEN_IMPORT_FRAGMENTS:
                assert frag not in mod, (
                    f"{src.name} imports {mod} containing {frag!r}")
            for name in QUARANTINED_NAMES:
                assert name not in mod, (
                    f"{src.name} imports quarantined module {mod}")


def test_no_dynamic_import_or_eval_exec():
    # Bare-name dynamic escape hatches only; ``re.compile`` and similar
    # attribute calls are legitimate.
    forbidden_names = {"__import__", "eval", "exec", "compile"}
    forbidden_attrs = {"import_module"}
    for src, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    assert func.id not in forbidden_names, (
                        f"{src.name} uses dynamic import/eval: "
                        f"{func.id}")
                elif isinstance(func, ast.Attribute):
                    assert func.attr not in forbidden_attrs, (
                        f"{src.name} uses dynamic import: "
                        f"{func.attr}")


def test_no_filesystem_mutation_surface():
    # Reads are permitted (envelope validation); every write/delete/
    # mutation call surface is prohibited.
    for src, tree in _trees():
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and \
                    isinstance(node.func, ast.Attribute):
                assert node.func.attr not in _WRITE_ATTRS, (
                    f"{src.name} calls {node.func.attr} — research_v0 "
                    "has no filesystem write surface")


def test_no_write_mode_open():
    for src, tree in _trees():
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and
                    ((isinstance(node.func, ast.Name) and
                      node.func.id == "open") or
                     (isinstance(node.func, ast.Attribute) and
                      node.func.attr == "open"))):
                continue
            mode = None
            if len(node.args) >= 2 and \
                    isinstance(node.args[1], ast.Constant):
                mode = node.args[1].value
            for kw in node.keywords:
                if kw.arg == "mode" and \
                        isinstance(kw.value, ast.Constant):
                    mode = kw.value.value
            if isinstance(mode, str):
                assert not any(m in mode for m in "wax+"), (
                    f"{src.name} opens a file in write mode {mode!r}")


def test_runtime_import_smoke():
    # Importing the package must not perform network, subprocess, or
    # filesystem writes: it imports cleanly and exposes no run API.
    import nepal.research_v0 as pkg
    import nepal.research_v0.cli as cli
    assert not hasattr(pkg, "download")
    assert not hasattr(cli, "intake")
    parser = cli.build_parser()
    help_text = parser.format_help()
    for forbidden in ("download", "intake", "freeze", "cluster",
                      "run-pipeline"):
        assert forbidden not in help_text


def test_no_runtime_side_effects(monkeypatch, tmp_path, capsys):
    # B25: every network/process/write surface is monkeypatched to
    # explode; package operations must still complete — proving no
    # runtime dependency on them.
    import socket
    import nepal.research_v0.cli as cli
    import nepal.research_v0.policy as policy

    def _boom(*a, **k):
        raise AssertionError("runtime side effect attempted")

    monkeypatch.setattr(socket, "socket", _boom)
    if hasattr(socket, "create_connection"):
        monkeypatch.setattr(socket, "create_connection", _boom)
    import builtins
    real_open = builtins.open

    def _guarded_open(file, mode="r", *a, **k):
        if any(m in mode for m in "wax+"):
            raise AssertionError(f"write open attempted: {mode}")
        return real_open(file, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", _guarded_open)
    cwd = tmp_path
    monkeypatch.chdir(cwd)
    # Exercise the package: horizons, embargo, canonical hashing, CLI.
    from nepal.research_v0.policy import EventTimeClass, eligible_horizons
    assert eligible_horizons(
        EventTimeClass.INTERVAL_8_30D, event_uncertainty_seconds=12*86400,
        observation_latency_seconds=0, processing_latency_seconds=0)
    assert policy.embargo_seconds(
        max_horizon_seconds=86400, max_label_interval_seconds=86400,
        max_observation_latency_seconds=0, max_cascade_seconds=0)
    assert cli.main(["horizons", "--event-class", "EXACT_DAY",
                     "--uncertainty-seconds", "86400"]) == 0
    assert list(cwd.iterdir()) == [], "CLI wrote files to cwd"
