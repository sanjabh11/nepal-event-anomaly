"""Conformance checks for the Run-B promotion protocol and new scaffolds."""

from __future__ import annotations

import re
from pathlib import Path

from nepal.research_v0.gates import REQUIRED_REGIME_GATE_NAMES
from nepal.science_v0.regimes import LORO_ARI_MIN, STATUSES


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "docs/science/run_b/REGIME_PROMOTION_PROTOCOL_V0.md"
WORKFLOW = ROOT / ".github/workflows/p5-verification.yml"
ARM_C_TEMPLATE = ROOT / "docs/science/P5_ARM_C_AMENDMENT_TEMPLATE_V0.md"
OPTION3_TEMPLATE = ROOT / "docs/science/P5_OPTION3_EXECUTION_TEMPLATE_V0.md"
BACKTICK = chr(96)


def _protocol_text() -> str:
    return PROTOCOL.read_text(encoding="utf-8")


def test_protocol_names_exact_engine_gate_set() -> None:
    text = _protocol_text()
    expected = set(REQUIRED_REGIME_GATE_NAMES)
    assert len(expected) == 13

    # First prove that every engine name is present as a documented code
    # token.  The second assertion catches a newly introduced gate-like
    # token in the binding criteria without maintaining a second hand-copied
    # list in this test.
    actual_mentions = {
        name for name in expected
        if f"{BACKTICK}{name}{BACKTICK}" in text
    }
    assert actual_mentions == expected

    start = text.index("### 3.1")
    end = text.index("### 3.3")
    binding_section = text[start:end]
    tokens = set(re.findall(
        rf"{re.escape(BACKTICK)}([a-z][a-z0-9_]+)"
        rf"{re.escape(BACKTICK)}",
        binding_section,
    ))
    non_gate_tokens = {
        "required_gates",
        "loro_policy",
        "fold_seed_policy",
    }
    gate_like = {
        token for token in tokens
        if token not in non_gate_tokens
        and (
            token in expected
            or token == "loro"
            or token.endswith((
                "_ari",
                "_bootstrap",
                "_coverage",
                "_drift",
                "_effort",
                "_elevation",
                "_missingness",
                "_null",
                "_refits",
                "_unanimous",
            ))
        )
    }
    assert gate_like == expected


def test_protocol_binds_loro_threshold() -> None:
    text = _protocol_text()
    assert "strictly > 0.6" in text
    assert f"{LORO_ARI_MIN:g}" == "0.6"


def test_protocol_names_exact_terminal_status_vocabulary() -> None:
    text = _protocol_text()
    expected = set(STATUSES)
    assert len(expected) == 4
    mentioned = {
        status for status in expected
        if re.search(rf"\b{re.escape(status)}\b", text)
    }
    assert mentioned == expected
    assert "DESCRIPTIVE_REGIME_ONLY" in text
    assert "association eligibility" in text


def test_verification_workflow_is_manual_read_only_draft() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in text
    assert "pull_request_target" not in text
    assert "secrets." not in text
    assert "contents: read" in text
    assert "PYTHONDONTWRITEBYTECODE" in text
    assert "python -B -m pytest tests/ -q -rs" in text
    assert "claim-scan docs/science" in text
    assert "claim-scan README.md" in text
    assert "verify-manifest docs/science/ARTIFACT_MANIFEST_V0.json" in text
    assert "DRAFT ONLY" in text


def test_owner_templates_are_non_authorizing_scaffolds() -> None:
    arm_c = ARM_C_TEMPLATE.read_text(encoding="utf-8")
    option3 = OPTION3_TEMPLATE.read_text(encoding="utf-8")
    for text in (arm_c, option3):
        assert "TEMPLATE_ONLY_NOT_AUTHORIZED" in text
        assert "TBD_OWNER" in text
        assert "authorizes nothing" in text
        assert "DESCRIPTIVE_REGIME_ONLY" in text or "OBSERVABILITY_PASS" in text
    assert "CDS" in arm_c
    assert "negative-control" in arm_c
    assert "StationXML" in option3
    assert "MiniSEED" in option3
    assert "BLOCKED" in option3
    assert "NO_QUALIFIED_SIGNAL" in option3
