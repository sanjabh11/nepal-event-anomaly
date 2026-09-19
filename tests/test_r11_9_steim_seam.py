"""G3-F1 STEIM admission seam — fabricated-reason regression.

The seam discarded the decoder's traces and then appended the
decoder's *problem* unconditionally, so a **successful** decode was
reported as the literal string ``STEIM encoding — None``: a fabricated
failure reason.  Independent qualification (obspy==1.5.1 on Python
3.14, STEIM1+STEIM2 round-trip byte-identical, encoding codes 10/11
detected) showed the decoder is viable while the record still
rejected.

These tests pin the corrected behaviour: STEIM stays inadmissible by
contract — fail-closed, no posture change, no bytes admitted — but the
reported reason is always a real one, either the actual decoder fault
or the explicit contract closure.  No test here admits STEIM bytes,
and none weakens the rejection.
"""
from __future__ import annotations

import pytest

import nepal.seismic_sidecar.io as io_mod
from tests.test_seismic_sidecar_io import _miniseed_file

_STEIM1, _STEIM2 = 10, 11


def _steim_blob(encoding=_STEIM2):
    return _miniseed_file(encoding=encoding, rate=50, seconds=60, nrec=1)


def _decode(blob):
    metas, parse_problems = io_mod.parse_miniseed_records(blob)
    assert parse_problems == []
    assert metas, "the STEIM record header must still parse"
    return io_mod.decode_records(
        blob, metas, relpath="w.bin", source_sha256="x" * 64)


class TestSteimSeamReportsRealReasons:
    @pytest.mark.parametrize("encoding", (_STEIM1, _STEIM2))
    def test_steim_never_reports_a_fabricated_none(self, encoding):
        traces, problems = _decode(_steim_blob(encoding))
        assert traces == []
        assert problems
        for problem in problems:
            assert "STEIM" in problem
            assert "— None" not in problem
            assert not problem.rstrip().endswith("None")

    def test_decoder_success_is_reported_as_contract_closure(
            self, monkeypatch):
        monkeypatch.setattr(io_mod, "_decode_obspy",
                            lambda data: ([], None))
        traces, problems = _decode(_steim_blob())
        assert traces == []
        assert any("closed by contract" in p for p in problems)
        assert all("— None" not in p for p in problems)

    def test_decoder_fault_is_reported_verbatim(self, monkeypatch):
        monkeypatch.setattr(
            io_mod, "_decode_obspy",
            lambda data: ([], "boom: malformed block"))
        traces, problems = _decode(_steim_blob())
        assert traces == []
        assert any("boom: malformed block" in p for p in problems)

    def test_missing_decoder_reason_names_obspy(self, monkeypatch):
        monkeypatch.setattr(
            io_mod, "_decode_obspy",
            lambda data: ([], "optional decoder 'obspy' is not "
                              "installed"))
        traces, problems = _decode(_steim_blob())
        assert traces == []
        assert any("STEIM" in p and "obspy" in p for p in problems)

    def test_encoding_codes_are_named_in_the_reason(self, monkeypatch):
        monkeypatch.setattr(io_mod, "_decode_obspy",
                            lambda data: ([], None))
        _, problems = _decode(_steim_blob(_STEIM2))
        assert any("[11]" in p for p in problems)

    def test_undeclared_encoding_still_reads_as_undeclared(
            self, monkeypatch):
        # A non-STEIM undeclared encoding keeps its own message — the
        # contract-closure wording must not leak onto other encodings.
        monkeypatch.setattr(io_mod, "_decode_obspy",
                            lambda data: ([], None))
        _, problems = _decode(_steim_blob(encoding=2))
        assert problems
        assert all("closed by contract" not in p for p in problems)