"""B2 — metadata-first forecast archive admission adapter tests.

All inputs are synthetic metadata requests; nothing downloads,
authenticates, or touches real archive payloads.  Admission is
fail-closed: unknown/blocked providers, inadmissible data classes,
missing fields, ordering inversions, unmet archive-delay floors, and
early retrieval all reject with explicit problems.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fixtures.synthetic_exp_b2 import synthetic_vintage_request

from nepal.experiment_v0.vintages import (
    PROVIDER_REGISTRY, REQUIRED_REQUEST_FIELDS, ProviderPolicy,
    VintageRequest, admission_problems, build_vintage,
    cutoff_for_vintage, ledger_problems)
from nepal.research_v0.gates import scan_claims_text
from nepal.research_v0.records import CutoffRecordV0, ForecastVintageV0

_MODULE = Path(__file__).resolve().parents[1] / "nepal" / \
    "experiment_v0" / "vintages.py"


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc)


def _request(**overrides) -> VintageRequest:
    return VintageRequest(**synthetic_vintage_request(**overrides))


# ---------------------------------------------------------------------
# 1. Valid TIGGE request: admitted and builds a problem-free vintage
# ---------------------------------------------------------------------

def test_tigge_request_admitted_and_builds() -> None:
    req = _request()
    assert admission_problems(req) == []
    vintage = build_vintage(req, "vintage-tigge-00")
    assert type(vintage) is ForecastVintageV0
    assert vintage.problems() == []
    assert vintage.provider == "tigge"
    assert vintage.data_class == "ARCHIVED_OPERATIONAL"
    assert vintage.vintage_id == "vintage-tigge-00"


def test_request_round_trip() -> None:
    req = _request()
    clone = VintageRequest.from_dict(req.to_dict())
    assert clone == req
    assert clone.to_dict() == req.to_dict()


def test_request_from_dict_rejects_unknown_fields() -> None:
    payload = synthetic_vintage_request()
    payload["surprise_field"] = "x"
    with pytest.raises(ValueError):
        VintageRequest.from_dict(payload)


@pytest.mark.parametrize("field_name",
                         ["provider", "issue_time",
                          "archive_availability",
                          "declared_delay_seconds"])
def test_request_from_dict_rejects_missing_fields(
        field_name: str) -> None:
    payload = synthetic_vintage_request()
    del payload[field_name]
    with pytest.raises(ValueError):
        VintageRequest.from_dict(payload)


def test_request_from_dict_rejects_bare_provider_only() -> None:
    with pytest.raises(ValueError):
        VintageRequest.from_dict({"provider": "tigge"})


@pytest.mark.parametrize("field_name,bad_value", [
    ("provider", 7),
    ("issue_time", 1590976800),
    ("valid_start", None),
    ("cycle", True),
    ("archive_payload_sha256", ["a" * 64]),
    ("archive_payload_path", 3.14),
])
def test_request_from_dict_rejects_wrong_str_types(
        field_name: str, bad_value: object) -> None:
    payload = synthetic_vintage_request()
    payload[field_name] = bad_value
    with pytest.raises((ValueError, TypeError)):
        VintageRequest.from_dict(payload)


@pytest.mark.parametrize("bad_value",
                         ["false", "3600", "1.5", True, False, None,
                          float("nan"), float("inf"), [86400]])
def test_request_from_dict_rejects_bad_declared_delay(
        bad_value: object) -> None:
    payload = synthetic_vintage_request()
    payload["declared_delay_seconds"] = bad_value
    with pytest.raises((ValueError, TypeError)):
        VintageRequest.from_dict(payload)


def test_request_from_dict_accepts_int_declared_delay() -> None:
    payload = synthetic_vintage_request()
    payload["declared_delay_seconds"] = 3600
    req = VintageRequest.from_dict(payload)
    assert req.declared_delay_seconds == 3600
    assert VintageRequest.from_dict(req.to_dict()) == req


# ---------------------------------------------------------------------
# 2. Early archive availability rejects (issue, and issue + floor)
# ---------------------------------------------------------------------

def test_archive_before_issue_rejected() -> None:
    issue = _parse(_request().issue_time)
    req = _request(
        archive_availability=_iso(issue - timedelta(hours=1)))
    assert admission_problems(req)
    with pytest.raises(ValueError):
        build_vintage(req, "vintage-x")


def test_archive_inside_delay_floor_rejected() -> None:
    # TIGGE floor is 48 h; issue+47 h is early even though it is
    # after issue_time.
    issue = _parse(_request().issue_time)
    req = _request(
        archive_availability=_iso(issue + timedelta(hours=47)))
    problems = admission_problems(req)
    assert any("delay floor" in p for p in problems)


def test_archive_at_exact_floor_admitted() -> None:
    issue = _parse(_request().issue_time)
    req = _request(
        archive_availability=_iso(issue + timedelta(hours=48)))
    assert admission_problems(req) == []


# ---------------------------------------------------------------------
# 3. Retrieval before archive availability rejects
# ---------------------------------------------------------------------

def test_retrieval_before_archive_rejected() -> None:
    archive = _parse(_request().archive_availability)
    req = _request(
        local_retrieval_time=_iso(archive - timedelta(seconds=1)))
    problems = admission_problems(req)
    assert any("retrieval" in p for p in problems)
    with pytest.raises(ValueError):
        build_vintage(req, "vintage-x")


# ---------------------------------------------------------------------
# 4. Every required field rejects when empty
# ---------------------------------------------------------------------

@pytest.mark.parametrize("field_name", REQUIRED_REQUEST_FIELDS)
def test_missing_required_field_rejected(field_name: str) -> None:
    req = _request(**{field_name: ""})
    assert admission_problems(req), field_name


# ---------------------------------------------------------------------
# 5. Ordering inversions reject
# ---------------------------------------------------------------------

def test_initialization_after_issue_rejected() -> None:
    issue = _parse(_request().issue_time)
    req = _request(
        initialization_time=_iso(issue + timedelta(hours=1)))
    problems = admission_problems(req)
    assert any("order" in p for p in problems)


def test_valid_end_before_valid_start_rejected() -> None:
    start = _parse(_request().valid_start)
    req = _request(valid_end=_iso(start - timedelta(hours=1)))
    problems = admission_problems(req)
    assert any("order" in p for p in problems)


def test_issue_after_valid_start_rejected() -> None:
    start = _parse(_request().valid_start)
    req = _request(issue_time=_iso(start + timedelta(hours=1)))
    problems = admission_problems(req)
    assert any("order" in p for p in problems)


def test_malformed_timestamp_rejected() -> None:
    req = _request(issue_time="2020-06-01 06:00:00")  # naive, not UTC
    problems = admission_problems(req)
    assert any("issue_time" in p for p in problems)


# ---------------------------------------------------------------------
# 6. Ledger integrity: duplicate ids and digest reuse reject
# ---------------------------------------------------------------------

def test_ledger_clean() -> None:
    v1 = build_vintage(_request(), "vintage-a")
    v2 = build_vintage(
        _request(archive_payload_sha256="b" * 64,
                 retrieval_record_sha256="c" * 64),
        "vintage-b")
    assert ledger_problems([v1, v2]) == []


def test_ledger_duplicate_vintage_id_rejected() -> None:
    req_a = _request()
    req_b = _request(archive_payload_sha256="d" * 64,
                     retrieval_record_sha256="e" * 64)
    v1 = build_vintage(req_a, "vintage-dup")
    v2 = build_vintage(req_b, "vintage-dup")
    problems = ledger_problems([v1, v2])
    assert any("duplicate vintage_id" in p for p in problems)


def test_ledger_shared_payload_digest_rejected() -> None:
    req_a = _request()
    req_b = _request(retrieval_record_sha256="f" * 64)
    v1 = build_vintage(req_a, "vintage-1")
    v2 = build_vintage(req_b, "vintage-2")
    assert v1.archive_payload_sha256 == v2.archive_payload_sha256
    problems = ledger_problems([v1, v2])
    assert any("archive_payload_sha256" in p for p in problems)


def test_ledger_duplicate_retrieval_record_rejected() -> None:
    req_a = _request()
    req_b = _request(archive_payload_sha256="a" * 64)
    v1 = build_vintage(req_a, "vintage-1")
    v2 = build_vintage(req_b, "vintage-2")
    assert v1.retrieval_record_sha256 == v2.retrieval_record_sha256
    problems = ledger_problems([v1, v2])
    assert any("retrieval_record_sha256" in p for p in problems)


# ---------------------------------------------------------------------
# 7. Inadmissible classes, blocked and unknown providers reject
# ---------------------------------------------------------------------

@pytest.mark.parametrize("data_class", ["REANALYSIS", "CURRENT_FEED"])
def test_never_forecast_data_classes_rejected(data_class: str) -> None:
    req = _request(data_class=data_class)
    problems = admission_problems(req)
    assert any("data_class" in p for p in problems)
    with pytest.raises(ValueError):
        build_vintage(req, "vintage-x")


def test_external_blocked_provider_rejected() -> None:
    req = _request(provider="ecmwf_mars_operational")
    problems = admission_problems(req)
    assert any("blocked" in p for p in problems)
    with pytest.raises(ValueError):
        build_vintage(req, "vintage-x")


def test_unknown_provider_rejected() -> None:
    req = _request(provider="no_such_provider")
    problems = admission_problems(req)
    assert any("registry" in p for p in problems)


def test_malformed_digest_rejected() -> None:
    req = _request(archive_payload_sha256="not-a-digest")
    problems = admission_problems(req)
    assert any("sha256" in p for p in problems)


# ---------------------------------------------------------------------
# 8. Per-provider class allowlists and declared-delay floors
# ---------------------------------------------------------------------

def test_gefsv12_admitted_as_reforecast_only() -> None:
    ok = _request(provider="ncep_gefsv12_reforecast",
                  data_class="REFORECAST")
    assert admission_problems(ok) == []
    vintage = build_vintage(ok, "vintage-gefs")
    assert vintage.problems() == []
    bad = _request(provider="ncep_gefsv12_reforecast",
                   data_class="ARCHIVED_OPERATIONAL")
    assert admission_problems(bad)


@pytest.mark.parametrize(
    "provider", ["ncar_gfs_025", "ncei_nomads", "c3s_seasonal"])
def test_declared_providers_require_declared_delay(
        provider: str) -> None:
    req = _request(provider=provider, declared_delay_seconds=-1.0)
    problems = admission_problems(req)
    assert any("declared_delay_seconds" in p for p in problems)


@pytest.mark.parametrize(
    "provider", ["ncar_gfs_025", "ncei_nomads", "c3s_seasonal"])
def test_declared_providers_admit_with_declared_delay(
        provider: str) -> None:
    req = _request(provider=provider, declared_delay_seconds=86400.0)
    assert admission_problems(req) == []
    vintage = build_vintage(req, f"vintage-{provider}")
    assert vintage.problems() == []


def test_s2s_per_centre_delay_floors() -> None:
    issue = _parse(_request().issue_time)
    # BoM/CNRM/UKMO legs carry a 1-week floor; issue+48 h is too early.
    early = _request(provider="s2s", centre="bom",
                     archive_availability=_iso(issue +
                                               timedelta(hours=48)))
    assert admission_problems(early)
    ok = _request(provider="s2s", centre="bom",
                  archive_availability=_iso(issue +
                                            timedelta(days=7)),
                  local_retrieval_time=_iso(issue +
                                            timedelta(days=7,
                                                      hours=1)))
    assert admission_problems(ok) == []
    # Unlisted centres fall back to the 48 h default floor.
    default = _request(provider="s2s", centre="ecmf")
    assert admission_problems(default) == []


def test_registry_shape() -> None:
    assert PROVIDER_REGISTRY["tigge"].delay_floor_seconds == 172800.0
    assert PROVIDER_REGISTRY["s2s"].centre_delay_seconds["bom"] == \
        604800.0
    assert PROVIDER_REGISTRY["ecmwf_mars_operational"].access_mode == \
        "external_blocked"
    assert all(isinstance(p, ProviderPolicy)
               for p in PROVIDER_REGISTRY.values())


# ---------------------------------------------------------------------
# 9. cutoff_for_vintage produces a problem-free ordered CutoffRecordV0
# ---------------------------------------------------------------------

def test_cutoff_for_vintage_clean() -> None:
    vintage = build_vintage(_request(), "vintage-cutoff")
    cutoff = cutoff_for_vintage(
        vintage, cutoff_id="cutoff-0",
        source_observation_end="2020-05-30T00:00:00Z",
        source_processing_complete="2020-05-30T06:00:00Z",
        source_publication="2020-05-31T00:00:00Z",
        feature_availability="2020-05-31T12:00:00Z",
        source_id="synthetic-inventory",
        event_id="synthetic-event-0",
        event_time_start="2020-06-02T00:00:00Z",
        event_time_end="2020-06-02T06:00:00Z")
    assert type(cutoff) is CutoffRecordV0
    assert cutoff.problems() == []
    # Forecast-side fields are bound from the vintage verbatim.
    assert cutoff.forecast_initialization == vintage.initialization_time
    assert cutoff.forecast_issue == vintage.issue_time
    assert cutoff.forecast_valid_start == vintage.valid_start
    assert cutoff.forecast_valid_end == vintage.valid_end
    assert cutoff.archive_availability == vintage.archive_availability
    assert cutoff.forecast_vintage_id == vintage.vintage_id
    assert cutoff.local_retrieval_time == vintage.archive_availability


def test_cutoff_for_vintage_rejects_broken_chain() -> None:
    vintage = build_vintage(_request(), "vintage-cutoff")
    with pytest.raises(ValueError):
        # feature availability after forecast initialization breaks
        # the operational ordering chain.
        cutoff_for_vintage(
            vintage, cutoff_id="cutoff-bad",
            source_observation_end="2020-05-30T00:00:00Z",
            source_processing_complete="2020-05-30T06:00:00Z",
            source_publication="2020-05-31T00:00:00Z",
            feature_availability="2020-06-02T00:00:00Z",
            source_id="synthetic-inventory",
            event_id="synthetic-event-0",
            event_time_start="2020-06-02T00:00:00Z",
            event_time_end="2020-06-02T06:00:00Z")


def test_cutoff_for_vintage_requires_event_binding() -> None:
    vintage = build_vintage(_request(), "vintage-cutoff")
    with pytest.raises(ValueError):
        cutoff_for_vintage(
            vintage, cutoff_id="cutoff-noevent",
            source_observation_end="2020-05-30T00:00:00Z",
            source_processing_complete="2020-05-30T06:00:00Z",
            source_publication="2020-05-31T00:00:00Z",
            feature_availability="2020-05-31T12:00:00Z")


# ---------------------------------------------------------------------
# 10. Module source passes the normalized claim scan
# ---------------------------------------------------------------------

def test_module_source_claim_scan_clean() -> None:
    assert scan_claims_text(_MODULE.read_text(encoding="utf-8")) == []
