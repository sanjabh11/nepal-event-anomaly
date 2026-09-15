"""Vintage timing-discipline tests (integration-gated on agent B2).

Contract tests for ``nepal.experiment_v0.vintages``, which is owned by
sibling agent B2 and lands at integration.  In this worktree the module
does not exist, so every test here skips locally; once B2's module is
present the same assertions run unchanged against the pinned contract:

* ``archive_availability`` must be >= ``issue_time`` and must satisfy
  the provider's declared delay floor (TIGGE: 48 h after issue).
* ``local_retrieval_time`` must be >= ``archive_availability`` —
  retrieval never substitutes for provider-side availability.
* ``initialization_time <= issue_time <= valid_start <= valid_end``.
* Required fields, digests, and licence/archive declarations reject
  when absent.
* ``REANALYSIS`` and ``CURRENT_FEED`` data classes are inadmissible.
* ``ledger_problems`` rejects duplicate vintage ids and duplicate
  archive payload digests.

Synthetic timestamps only — no real provider data.
"""
from __future__ import annotations

import pytest

try:
    from nepal.experiment_v0.vintages import (
        VintageRequest, admission_problems, build_vintage,
        ledger_problems)
    _VINTAGES_AVAILABLE = True
except ImportError:  # B2's module lands at integration
    VintageRequest = None  # type: ignore[assignment]
    admission_problems = build_vintage = ledger_problems = None
    _VINTAGES_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _VINTAGES_AVAILABLE,
    reason="nepal.experiment_v0.vintages is owned by agent B2 and "
           "lands at integration")

_PAYLOAD_A = "a" * 64
_PAYLOAD_B = "b" * 64
_RETRIEVAL_A = "c" * 64
_RETRIEVAL_B = "d" * 64
_TIGGE_FLOOR = 48.0 * 3600.0  # 48 h declared-delay floor (seconds)


def _request(**over):
    """A clean, admissible TIGGE-shaped request (synthetic times).

    init 00:00 <= issue 06:00 <= valid_start 12:00 <= valid_end
    next-day 12:00; archive at issue+48h exactly (satisfies both
    init-relative and issue-relative floor readings);
    retrieval after archive.
    """
    base = dict(
        provider="tigge",
        centre="ecmwf",
        data_class="ARCHIVED_OPERATIONAL",
        model_version="synthetic-model-v0",
        cycle="00",
        initialization_time="2020-06-01T00:00:00Z",
        issue_time="2020-06-01T06:00:00Z",
        valid_start="2020-06-01T12:00:00Z",
        valid_end="2020-06-02T12:00:00Z",
        archive_availability="2020-06-03T06:00:00Z",
        local_retrieval_time="2020-06-03T07:00:00Z",
        license_id="synthetic-tigge-licence",
        archive_mechanism="synthetic-portal",
        archive_payload_sha256=_PAYLOAD_A,
        retrieval_record_sha256=_RETRIEVAL_A,
        archive_payload_path="vintages/payload-a.bin",
        retrieval_record_path="vintages/retrieval-a.json",
        declared_delay_seconds=_TIGGE_FLOOR)
    base.update(over)
    return VintageRequest(**base)


class TestAdmission:
    def test_clean_request_admitted(self):
        assert admission_problems(_request()) == []

    def test_built_vintage_is_valid(self):
        built = build_vintage(_request(), "vint-001")
        assert built.vintage_id == "vint-001"
        assert built.problems() == []

    def test_archive_before_issue_rejects(self):
        req = _request(archive_availability="2020-06-01T05:00:00Z")
        assert admission_problems(req)

    def test_archive_inside_delay_floor_rejects(self):
        # issue + 24h is inside the declared 48h TIGGE floor.
        req = _request(archive_availability="2020-06-02T06:00:00Z")
        assert admission_problems(req)

    def test_retrieval_before_archive_rejects(self):
        req = _request(local_retrieval_time="2020-06-03T05:00:00Z")
        assert admission_problems(req)

    @pytest.mark.parametrize("field", [
        "provider", "data_class", "model_version",
        "initialization_time", "issue_time", "valid_start",
        "valid_end", "archive_availability", "local_retrieval_time",
        "license_id", "archive_mechanism", "archive_payload_sha256",
        "retrieval_record_sha256", "archive_payload_path",
        "retrieval_record_path"])
    def test_missing_required_field_rejects(self, field):
        req = _request(**{field: ""})
        assert admission_problems(req), field

    def test_initialization_after_issue_rejects(self):
        req = _request(initialization_time="2020-06-01T07:00:00Z")
        assert admission_problems(req)

    def test_valid_end_before_valid_start_rejects(self):
        req = _request(valid_end="2020-06-01T11:00:00Z")
        assert admission_problems(req)

    @pytest.mark.parametrize("data_class",
                             ["REANALYSIS", "CURRENT_FEED"])
    def test_inadmissible_data_classes_reject(self, data_class):
        req = _request(data_class=data_class)
        assert admission_problems(req)

    def test_tigge_fixed_floor_ignores_declared_delay(self):
        # TIGGE's 48h floor is fixed in the provider registry; a
        # caller-declared margin is neither needed nor consulted.
        assert admission_problems(
            _request(declared_delay_seconds=-1.0)) == []

    def test_declared_floor_provider_requires_declared_delay(self):
        # Providers without a fixed/per-centre floor (e.g. the declared
        # ncar_gfs_025 archive) require a non-negative
        # declared_delay_seconds to bound the latency margin.
        req = _request(provider="ncar_gfs_025", centre="",
                       declared_delay_seconds=-1.0)
        assert admission_problems(req)
        ok = _request(provider="ncar_gfs_025", centre="",
                      declared_delay_seconds=3600.0,
                      archive_availability="2020-06-01T08:00:00Z",
                      local_retrieval_time="2020-06-01T09:00:00Z")
        assert admission_problems(ok) == []

    def test_declared_floor_enforced(self):
        # ncar_gfs_025 declared 48h; archive at issue+24h is inside the
        # declared margin.
        req = _request(provider="ncar_gfs_025", centre="",
                       declared_delay_seconds=_TIGGE_FLOOR,
                       archive_availability="2020-06-02T06:00:00Z")
        assert admission_problems(req)

    def test_unknown_provider_rejects(self):
        assert admission_problems(_request(provider="no_such_src"))


class TestLedger:
    def test_clean_ledger(self):
        v1 = build_vintage(_request(), "vint-001")
        v2 = build_vintage(_request(
            archive_payload_sha256=_PAYLOAD_B,
            retrieval_record_sha256=_RETRIEVAL_B,
            archive_payload_path="vintages/payload-b.bin",
            retrieval_record_path="vintages/retrieval-b.json",
            issue_time="2020-06-02T06:00:00Z",
            valid_start="2020-06-02T12:00:00Z",
            valid_end="2020-06-03T12:00:00Z",
            archive_availability="2020-06-04T06:00:00Z",
            local_retrieval_time="2020-06-04T07:00:00Z"), "vint-002")
        assert ledger_problems([v1, v2]) == []

    def test_duplicate_vintage_id_rejects(self):
        v1 = build_vintage(_request(), "vint-001")
        v2 = build_vintage(_request(
            archive_payload_sha256=_PAYLOAD_B), "vint-001")
        assert ledger_problems([v1, v2])

    def test_duplicate_payload_digest_rejects(self):
        v1 = build_vintage(_request(), "vint-001")
        v2 = build_vintage(_request(), "vint-002")
        assert ledger_problems([v1, v2])
