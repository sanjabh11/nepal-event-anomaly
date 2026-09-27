"""Contract tests for the metadata-only India inventory registry."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import india_inventory_metadata as im  # noqa: E402


REGISTRY = Path(__file__).resolve().parents[1] / (
    "docs/science/INDIA_INVENTORY_METADATA_V0.json"
)


def test_pinned_registry_validates_and_keeps_authority_closed():
    document = json.loads(REGISTRY.read_text(encoding="utf-8"))
    assert im.validate_registry(document) == []
    assert document["authority"] == im.AUTHORITY_FLAGS
    assert document["acquisition"]["payload_requests_issued"] is False
    assert document["acquisition"]["metadata_only_queries_run"] is True


def test_registry_separates_inventory_counts_from_lake_rows():
    document = json.loads(REGISTRY.read_text(encoding="utf-8"))
    nrsc = next(item for item in document["sources"] if item["id"] == "NRSC_GLA_IHR")
    cwc = next(item for item in document["sources"] if item["id"] == "CWC_GLWB_SEP_2024")
    assert nrsc["reported_counts"]["mapped_lakes_ge_0_25ha"] == 28043
    assert cwc["reported_counts"]["monitored_glacial_lakes_and_water_bodies"] == 902
    assert nrsc["row_level_inventory_ingested"] is False
    assert cwc["row_level_inventory_ingested"] is False


@pytest.mark.parametrize(
    "mutator",
    [
        lambda doc: doc["authority"].__setitem__("weather_download_authorized", True),
        lambda doc: doc["acquisition"].__setitem__("payload_requests_issued", True),
        lambda doc: doc["sources"][0].__setitem__("source_url", "https://example.com/not-official"),
        lambda doc: doc["sources"][0].__setitem__("row_level_inventory_ingested", True),
    ],
)
def test_registry_rejects_scope_or_authority_escalation(mutator):
    document = json.loads(REGISTRY.read_text(encoding="utf-8"))
    mutator(document)
    assert im.validate_registry(document)


def test_registry_rejects_unpinned_source_bytes():
    document = json.loads(REGISTRY.read_text(encoding="utf-8"))
    document["sources"][0]["local_payload_sha256"] = "a" * 64
    problems = im.validate_registry(document)
    assert any("local_payload_sha256" in problem for problem in problems)
