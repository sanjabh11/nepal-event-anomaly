"""Contract tests for the V1 India source registry successor."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import india_source_registry as sr  # noqa: E402


REGISTRY = Path(__file__).resolve().parents[1] / (
    "docs/science/INDIA_INVENTORY_REGISTRY_V1.json")
LEGACY = Path(__file__).resolve().parents[1] / (
    "docs/science/INDIA_INVENTORY_METADATA_V0.json")


def test_pinned_v1_registry_validates_and_keeps_authority_closed():
    document = json.loads(REGISTRY.read_text(encoding="utf-8"))
    assert sr.validate_registry(document) == []
    assert document["authority"] == sr.AUTHORITY_FLAGS
    assert document["acquisition"]["payload_requests_issued"] is False
    assert document["supersedes"] == "docs/science/INDIA_INVENTORY_METADATA_V0.json"


def test_v1_registry_keeps_pinned_vintages_and_adds_distinct_products():
    document = json.loads(REGISTRY.read_text(encoding="utf-8"))
    by_id = {s["id"]: s for s in document["sources"]}
    assert by_id["NRSC_GLA_IHR"]["reported_counts"]["mapped_lakes_ge_0_25ha"] == 28043
    assert by_id["CWC_GLWB_SEP_2024"]["reported_counts"][
        "monitored_glacial_lakes_and_water_bodies"] == 902
    # The 2026 parliamentary product (>10 ha monitored lakes) is a different
    # scope — present as its own source, never merged into a trend.
    assert by_id["CWC_LOKSABHA_AU883_2026"]["reported_counts"][
        "monitored_lakes_gt_10ha"] == 2485
    # The ICIMOD catalog is present as a Himalayan-wide source, pinned by
    # version and access terms, without row-level ingestion.
    assert by_id["ICIMOD_HMAGLOFDB_V130"]["source_version"] == "1.3.0"
    assert all(s["row_level_inventory_ingested"] is False
               for s in document["sources"])
    assert all(s["source_bytes_retained"] is False
               for s in document["sources"])
    assert all(s["local_payload_sha256"] is None
               for s in document["sources"])


def test_v0_registry_unchanged_and_still_valid():
    # The V0 registry is retained byte-identical; its own validator still
    # governs it.
    import india_inventory_metadata as im
    document = json.loads(LEGACY.read_text(encoding="utf-8"))
    assert im.validate_registry(document) == []


@pytest.mark.parametrize("mutator", [
    lambda d: d["authority"].__setitem__("weather_download_authorized", True),
    lambda d: d["acquisition"].__setitem__("payload_requests_issued", True),
    lambda d: d["sources"][0].__setitem__("source_url", "https://example.com/x"),
    lambda d: d["sources"][0].__setitem__("row_level_inventory_ingested", True),
    lambda d: d["sources"][0].__setitem__("source_bytes_retained", True),
    lambda d: d["sources"][0].__setitem__("access_terms", ""),
    lambda d: d.__setitem__("supersedes", None),
    lambda d: d["sources"][0]["reported_counts"].__setitem__(
        "mapped_lakes_ge_0_25ha", 999),
])
def test_v1_registry_rejects_scope_or_vintage_escalation(mutator):
    document = json.loads(REGISTRY.read_text(encoding="utf-8"))
    mutator(document)
    assert sr.validate_registry(document)


def test_v1_registry_rejects_undeclared_payload_digest():
    document = json.loads(REGISTRY.read_text(encoding="utf-8"))
    document["sources"][0]["local_payload_sha256"] = "z" * 64
    assert any("local_payload_sha256" in p
               for p in sr.validate_registry(document))


def test_v1_registry_receipts_prove_metadata_only_retrieval():
    document = json.loads(REGISTRY.read_text(encoding="utf-8"))
    for source in document["sources"]:
        receipt = source["retrieval_receipt"]
        assert receipt["bytes_retained"] is False
        assert receipt["request_method"] in {"HEAD", "GET_METADATA_ONLY"}
        assert 200 <= receipt["http_status"] < 400
        assert receipt["requested_url"] == source["source_url"]


@pytest.mark.parametrize("mutator", [
    lambda r: r.__setitem__("bytes_retained", True),
    lambda r: r.__setitem__("http_status", 404),
    lambda r: r.__setitem__("request_method", "GET"),
    lambda r: r.__setitem__("response_sha256", "nothex"),
    lambda r: r.__setitem__("terms_reviewed", ""),
    lambda r: r.__setitem__("requested_url", "http://example.com/x"),
])
def test_v1_registry_rejects_receipt_escalation(mutator):
    document = json.loads(REGISTRY.read_text(encoding="utf-8"))
    mutator(document["sources"][0]["retrieval_receipt"])
    assert sr.validate_registry(document)


def test_v1_registry_receipt_absent_is_pre_retrieval_honest():
    document = json.loads(REGISTRY.read_text(encoding="utf-8"))
    document["sources"][0]["retrieval_receipt"] = None
    assert sr.validate_registry(document) == []
