"""Documentation gates for the seismic event-detection sidecar.

This test proves the addendum's boundary only. It does not retrieve
waveforms, inspect station payloads, or claim seismic validity.
"""
from __future__ import annotations

from pathlib import Path

from nepal.research_v0.gates import scan_claims_text


_ROOT = Path(__file__).resolve().parents[1]
_ADDENDUM = _ROOT / "docs/science/run_b/SEISMIC_EVENT_DETECTION_ADDENDUM_V0.md"
_QUALIFICATION = _ROOT / "docs/science/run_b/SOURCE_QUALIFICATION_RECORDS_V0.md"
_FEASIBILITY = _ROOT / "docs/science/SOURCE_FEASIBILITY_RECORDS_V0.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_seismic_addendum_has_research_only_boundary():
    text = _text(_ADDENDUM)
    normalized = " ".join(text.casefold().split())
    required = (
        "no_qualifying_pilot_source",
        "warning_path_authorized: no",
        "no seismic predictor",
        "no waveform payload bytes have been acquired",
        "UNOBSERVABLE",
        "P5 authorization",
        "station response metadata",
        "catalog context",
    )
    for marker in required:
        assert marker.casefold() in normalized, marker
    assert scan_claims_text(text) == []


def test_seismic_addendum_distinguishes_t2a_and_nepal_roles():
    text = _text(_ADDENDUM)
    normalized = " ".join(text.casefold().split())
    assert "catalog context, not continuous ground motion" in text
    assert "default-disabled" in text
    assert "not validated nepal/langtang parameters" in normalized
    assert "must not be copied into this sidecar" in text
    assert "feature_contract.py" in text
    assert "framework_v1" in text


def test_source_records_point_to_the_canonical_addendum():
    marker = "SEISMIC_EVENT_DETECTION_ADDENDUM_V0.md"
    assert marker in _text(_QUALIFICATION)
    assert marker in _text(_FEASIBILITY)


def test_addendum_does_not_claim_waveform_intake_or_authority():
    text = _text(_ADDENDUM)
    normalized = " ".join(text.casefold().split())
    assert "no waveform payload bytes have been acquired" in text
    assert "does not authorize network access" in text
    assert "No source is `EVIDENCE_VERIFIED`" in text
    assert "no forecast, warning, production or promotion status" in normalized
