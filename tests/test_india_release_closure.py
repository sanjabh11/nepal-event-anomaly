"""Detached India release-closure validation (R-04/R-05).

The closure lives outside the repository (external evidence root) so it
can bind the release HEAD without circularity.  This test validates the
document's bindings when the closure artifact is present and skips —
visibly — when the machine lacks the evidence root.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_india_release_closure import EVIDENCE_DIR, SCHEMA  # noqa: E402


def _latest_closure() -> Path | None:
    closures = sorted(EVIDENCE_DIR.glob("INDIA_PHASE0_RELEASE_CLOSURE_V*.json"))
    return closures[-1] if closures else None


def test_detached_release_closure_binds_live_state():
    """When the published closure exists, it must bind the live release
    HEAD, tested content HEAD, receipt, manifest, and exclusions — and
    fail closed on any drift."""
    closure = _latest_closure()
    if closure is None:
        pytest.skip("no published India release closure on this machine")
    from build_india_release_closure import validate_closure  # noqa: E402
    report = validate_closure(closure,
                              Path(__file__).resolve().parents[1])
    assert report["status"] == "CLOSURE_OK", report["problems"]
    doc = json.loads(closure.read_text(encoding="utf-8"))
    assert doc["schema"] == SCHEMA
    assert all(v is False for v in doc["authority"].values())


def test_closure_schema_constant_stable():
    assert SCHEMA == "INDIA_PHASE0_RELEASE_CLOSURE_V0"
