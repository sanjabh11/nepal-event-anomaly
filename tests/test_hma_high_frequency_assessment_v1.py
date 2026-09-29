"""Tests for the append-only correction to metadata access provenance."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hma_high_frequency_assessment_v1 as assessment  # noqa: E402


def _source():
    return {
        "schema": "HMA_HIGHER_FREQUENCY_ASSESSMENT_V0",
        "version": 0,
        "payload_or_network_request_made": False,
        "candidates": [{"source": "candidate",
                        "payload_or_network_request_made": False}],
    }


def test_corrected_record_distinguishes_metadata_access_from_payload_intake(monkeypatch):
    monkeypatch.setattr(assessment, "_sha256_file", lambda path: "a" * 64)
    result = assessment.build_assessment(_source(), "b" * 64)

    assert result["access_disclosure"]["metadata_web_searches_performed"] is True
    assert result["access_disclosure"]["dataset_payload_endpoint_requested"] is False
    assert result["access_disclosure"]["dataset_payload_downloaded_or_retained"] is False
    assert "payload_or_network_request_made" not in result
    assert "payload_or_network_request_made" not in result["candidates"][0]
    assert result["candidates"][0]["metadata_web_search_or_paper_access"] is True
    assert result["supersedes"]["sha256"] == "b" * 64


def test_correction_fails_closed_on_wrong_predecessor_schema():
    with pytest.raises(ValueError, match="sealed V0"):
        assessment.build_assessment({"schema": "OTHER"}, "c" * 64)


def test_metadata_disclosure_identifies_primary_pages_and_blocked_page():
    assert any("essd.copernicus.org/articles/13/741/2021" in row["url"]
               for row in assessment.METADATA_REFERENCES)
    assert any("s41561-023-01150-1" in row["url"]
               and "blocked" in row["description"]
               for row in assessment.METADATA_REFERENCES)
