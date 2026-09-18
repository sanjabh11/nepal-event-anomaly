"""SEISMIC-IO — verified waveform/StationXML intake tests.

Synthetic byte-level fixtures: a minimal miniSEED data-record writer
(raw big-endian encodings only) and a StationXML writer, both bound
through the seven-key source manifest.  Covers the audit probes:
malformed payloads, wrong-station/stale/missing response, undeclared
encodings, trace overlap, rate mismatch, selector rejection,
response-waiver abuse, and derived (never caller-supplied)
observability.
"""
from __future__ import annotations

import hashlib
import math
import struct
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

import nepal.seismic_sidecar as ss
import nepal.seismic_sidecar.io as io_mod
from nepal.seismic_sidecar.contracts import SeismicSidecarConfig

E0 = datetime(2020, 6, 1, tzinfo=timezone.utc)


def _btime(dt: datetime) -> bytes:
    frac = int(round(dt.microsecond / 100.0))
    return struct.pack(">HHBBBBH", dt.year,
                       int(dt.strftime("%j")), dt.hour, dt.minute,
                       dt.second, 0, frac)


def _record(*, seq=1, network="XX", station="STA1", location="00",
            channel="BHZ", start=E0, samples=None, rate=100,
            encoding=3, rec_exp=None, n_blockettes=1,
            indicator=b"D", corrupt_btime=False):
    """One minimal miniSEED data record (48B header + B1000 + data).
    ``rec_exp`` auto-sizes to fit the data when not declared."""
    samples = samples if samples is not None else \
        np.arange(100, dtype=np.int32)
    n = len(samples)
    if encoding in (1, 3, 4, 5):
        fmt, width = {1: ("i2", 2), 3: ("i4", 4), 4: ("f4", 4),
                      5: ("f8", 8)}[encoding]
        data = np.asarray(samples).astype(
            np.dtype(f">{fmt}")).tobytes()
    else:
        # Declared-but-undecodable encodings (STEIM etc.) carry a
        # dummy data section — the parser must reject at decode.
        data = b"\x00" * (n * 4)
    data_offset = 64
    need = data_offset + len(data)
    if rec_exp is None:
        rec_exp = max(9, math.ceil(math.log2(need)))
    rec_len = 1 << rec_exp
    hdr = bytearray(48)
    hdr[0:6] = f"{seq:06d}".encode()
    hdr[6:8] = indicator + b" "
    hdr[8:13] = station.encode().ljust(5)
    hdr[13:15] = location.encode().ljust(2)
    hdr[15:18] = channel.encode().ljust(3)
    hdr[18:20] = network.encode().ljust(2)
    if corrupt_btime:
        # julday 400 is outside the valid 1-366 range.
        hdr[20:30] = struct.pack(">HHBBBBH", start.year, 400,
                                 start.hour, start.minute,
                                 start.second, 0, 0)
    else:
        hdr[20:30] = _btime(start)
    hdr[30:36] = struct.pack(">Hhh", n, int(rate), 1)
    hdr[39] = n_blockettes
    hdr[44:48] = struct.pack(">HH", data_offset, 48)
    b1000 = struct.pack(">HHBBBB", 1000, 0, encoding, 1, rec_exp, 0)
    rec = bytes(hdr) + b1000 + b"\x00" * (data_offset - 56) + data
    return rec + b"\x00" * (rec_len - len(rec))


def _miniseed_file(*, station="STA1", channels=("BHE", "BHN", "BHZ"),
                   rate=50, seconds=300, start=E0, nrec=2,
                   encoding=3, seed=0, spans=None):
    """A multi-channel miniSEED payload: each channel gets ``nrec``
    contiguous records of ``seconds/nrec`` each — or, when ``spans``
    is declared, one record per (start, seconds) segment."""
    rng = np.random.default_rng(seed)
    blob = b""
    seq = 1
    for ch in channels:
        if spans is not None:
            segments = spans
        else:
            per = seconds // nrec
            segments = [(start + timedelta(seconds=i * per), per)
                        for i in range(nrec)]
        for st, secs in segments:
            samples = rng.normal(0, 100,
                                 int(secs * rate)).astype(np.int32)
            blob += _record(seq=seq, station=station, channel=ch,
                            start=st, samples=samples, rate=rate,
                            encoding=encoding)
            seq += 1
    return blob


def _stationxml(*, network="XX", station="STA1", location="00",
                channels=("BHE", "BHN", "BHZ"), rate=50,
                start="2020-01-01T00:00:00Z", end=None, stages=2):
    ch_xml = ""
    for c in channels:
        end_attr = f' endDate="{end}"' if end else ""
        ch_xml += (
            f'<Channel code="{c}" locationCode="{location}" '
            f'startDate="{start}"{end_attr}>'
            f"<SampleRate>{rate}</SampleRate><Response>"
            + "".join(f"<Stage number=\"{i + 1}\"/>"
                      for i in range(stages))
            + "</Response></Channel>")
    return (
        '<?xml version="1.0"?>'
        '<FDSNStationXML xmlns="http://www.fdsn.org/xml/station/1" '
        'schemaVersion="1.1"><Source>t</Source>'
        f'<Network code="{network}"><Station code="{station}" '
        f'startDate="{start}">{ch_xml}</Station></Network>'
        '</FDSNStationXML>').encode()


def _multi_stationxml(stations, *, network="XX", location="00",
                      channels=("BHE", "BHN", "BHZ"), rate=50,
                      start="2020-01-01T00:00:00Z", stages=2):
    """One StationXML document covering several stations — the
    multi-station fixture for the provenance/real-path tests."""
    net_xml = ""
    for s in stations:
        ch_xml = ""
        for c in channels:
            ch_xml += (
                f'<Channel code="{c}" locationCode="{location}" '
                f'startDate="{start}">'
                f"<SampleRate>{rate}</SampleRate><Response>"
                + "".join(f'<Stage number="{i + 1}"/>'
                          for i in range(stages))
                + "</Response></Channel>")
        net_xml += (
            f'<Network code="{network}"><Station code="{s}" '
            f'startDate="{start}">{ch_xml}</Station></Network>')
    return (
        '<?xml version="1.0"?>'
        '<FDSNStationXML xmlns="http://www.fdsn.org/xml/station/1" '
        'schemaVersion="1.1"><Source>t</Source>'
        + net_xml + '</FDSNStationXML>').encode()


def _evidence(tmp, files: dict, feature_allowlist=None) -> tuple:
    root = Path(tmp)
    entries = []
    for rel, blob in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(blob)
        entries.append({"relpath": rel,
                        "sha256": hashlib.sha256(blob).hexdigest()})
    allowlist = feature_allowlist or (
        list(ss.SEISMIC_NONBAND_FEATURES) +
        list(ss.band_feature_names(ss.SEISMIC_DEFAULT_BANDS)))
    manifest = {
        "source_id": "fdsn-synthetic",
        "source_digests": sorted(
            hashlib.sha256(b).hexdigest() for b in files.values()),
        "units": "counts",
        "feature_allowlist": allowlist,
        "lineage": "synthetic-test",
        "evidence_root": str(root),
        "source_files": entries}
    return root, manifest


class TestMiniseedParser:
    def test_roundtrip_decodes_samples(self):
        blob = _miniseed_file(station="STA1", rate=50, seconds=120,
                              nrec=2)
        metas, problems = io_mod.parse_miniseed_records(blob)
        assert problems == []
        assert len(metas) == 6  # 3 channels x 2 records
        traces, problems = io_mod.decode_records(
            blob, metas, relpath="w.bin",
            source_sha256=hashlib.sha256(blob).hexdigest())
        assert problems == []
        assert len(traces) == 3
        for t in traces:
            assert t.n_samples == 120 * 50
            assert t.sample_rate_hz == 50.0
            assert t.trace_id == f"XX.STA1.00.{t.channel}"

    def test_gap_splits_segments(self):
        r1 = _record(seq=1, channel="BHZ", start=E0,
                     samples=np.ones(100, np.int32), rate=50)
        r2 = _record(seq=2, channel="BHZ",
                     start=E0 + timedelta(seconds=30),
                     samples=np.ones(100, np.int32), rate=50)
        metas, problems = io_mod.parse_miniseed_records(r1 + r2)
        assert problems == []
        traces, problems = io_mod.decode_records(
            r1 + r2, metas, relpath="w.bin", source_sha256="x" * 64)
        assert problems == []
        assert len(traces) == 2  # gap -> two segments, never bridged

    def test_overlap_rejects(self):
        r1 = _record(seq=1, channel="BHZ", start=E0,
                     samples=np.ones(200, np.int32), rate=50)
        r2 = _record(seq=2, channel="BHZ",
                     start=E0 + timedelta(seconds=1),
                     samples=np.ones(200, np.int32), rate=50)
        metas, _ = io_mod.parse_miniseed_records(r1 + r2)
        _, problems = io_mod.decode_records(
            r1 + r2, metas, relpath="w.bin", source_sha256="x" * 64)
        assert any("overlapping" in p for p in problems)

    def test_truncated_and_malformed_reject(self):
        metas, problems = io_mod.parse_miniseed_records(b"\x00" * 10)
        assert problems
        blob = _miniseed_file(rate=50, seconds=60, nrec=1)
        metas, problems = io_mod.parse_miniseed_records(blob[:100])
        assert problems

    def test_non_data_indicator_rejects(self):
        rec = _record(indicator=b"\x00")
        metas, problems = io_mod.parse_miniseed_records(rec)
        assert problems and "not a data record" in problems[0]

    def test_invalid_btime_rejects(self):
        rec = _record(corrupt_btime=True)
        metas, problems = io_mod.parse_miniseed_records(rec)
        assert problems and "invalid start time" in problems[0]

    def test_steim_fails_closed_without_obspy(self):
        blob = _miniseed_file(encoding=11, rate=50, seconds=60,
                              nrec=1)
        metas, problems = io_mod.parse_miniseed_records(blob)
        assert problems == []
        traces, problems = io_mod.decode_records(
            blob, metas, relpath="w.bin", source_sha256="x" * 64)
        assert traces == []
        assert any("STEIM" in p and "obspy" in p
                   for p in problems)


class TestStationXML:
    def test_valid_xml_parses(self):
        recs, problems = io_mod.parse_stationxml(_stationxml())
        assert problems == []
        assert len(recs) == 3
        assert all(r.n_stages == 2 for r in recs)
        assert recs[0].trace_id == "XX.STA1.00.BHE"

    def test_malformed_xml_rejects(self):
        recs, problems = io_mod.parse_stationxml(b"<broken")
        assert problems and "well-formed" in problems[0]

    def test_wrong_root_rejects(self):
        recs, problems = io_mod.parse_stationxml(b"<Other/>")
        assert any("FDSNStationXML" in p for p in problems)

    def test_zero_stage_response_rejects(self):
        recs, problems = io_mod.parse_stationxml(
            _stationxml(stages=0))
        assert any("zero Stage" in p for p in problems)

    def test_missing_rate_rejects(self):
        xml = _stationxml().replace(
            b"<SampleRate>50</SampleRate>", b"")
        recs, problems = io_mod.parse_stationxml(xml)
        assert any("SampleRate" in p for p in problems)


class TestBundle:
    def _cfg(self, **kw):
        base = dict(
            waveform_relpaths=("wave/STA1.mseed",),
            response_relpaths=("resp/STA1.xml",))
        base.update(kw)
        return SeismicSidecarConfig(**base)

    def _fixture(self, tmp_path):
        wave = _miniseed_file(rate=50, seconds=120, nrec=2)
        xml = _stationxml()
        root, manifest = _evidence(
            tmp_path, {"wave/STA1.mseed": wave,
                       "resp/STA1.xml": xml})
        return root, manifest, wave, xml

    def test_happy_path_bundle(self, tmp_path):
        root, manifest, wave, xml = self._fixture(tmp_path)
        b = io_mod.read_verified_waveform_bundle(
            evidence_root=root, source_manifest=manifest,
            waveform_relpaths=("wave/STA1.mseed",),
            response_relpaths=("resp/STA1.xml",),
            config=self._cfg())
        assert b.problems == ()
        assert len(b.traces) == 3
        assert len(b.stationxml) == 3
        assert len(b.waveform_bytes_digest) == 64
        assert len(b.stationxml_bytes_digest) == 64
        assert b.parser_id == io_mod.PARSER_ID
        assert b.response_mode == "RAW_COUNTS"

    def test_undeclared_relpath_rejects(self, tmp_path):
        root, manifest, *_ = self._fixture(tmp_path)
        b = io_mod.read_verified_waveform_bundle(
            evidence_root=root, source_manifest=manifest,
            waveform_relpaths=("wave/GHOST.mseed",),
            response_relpaths=("resp/STA1.xml",),
            config=self._cfg(
                waveform_relpaths=("wave/GHOST.mseed",)))
        assert any("source_files" in p for p in b.problems)

    def test_byte_substitution_rejects(self, tmp_path):
        root, manifest, *_ = self._fixture(tmp_path)
        (root / "wave" / "STA1.mseed").write_bytes(
            _miniseed_file(rate=50, seconds=60, nrec=1, seed=9))
        b = io_mod.read_verified_waveform_bundle(
            evidence_root=root, source_manifest=manifest,
            waveform_relpaths=("wave/STA1.mseed",),
            response_relpaths=("resp/STA1.xml",),
            config=self._cfg())
        assert any("sha256" in p for p in b.problems)

    def test_wrong_station_xml_rejects(self, tmp_path):
        wave = _miniseed_file(rate=50, seconds=60, nrec=1)
        xml = _stationxml(station="OTHER")
        root, manifest = _evidence(
            tmp_path, {"wave/STA1.mseed": wave,
                       "resp/STA1.xml": xml})
        b = io_mod.read_verified_waveform_bundle(
            evidence_root=root, source_manifest=manifest,
            waveform_relpaths=("wave/STA1.mseed",),
            response_relpaths=("resp/STA1.xml",),
            config=self._cfg())
        assert any("no StationXML channel matches" in p
                   for p in b.problems)

    def test_stale_response_epoch_rejects(self, tmp_path):
        wave = _miniseed_file(rate=50, seconds=60, nrec=1)
        xml = _stationxml(start="2019-01-01T00:00:00Z",
                          end="2019-06-01T00:00:00Z")
        root, manifest = _evidence(
            tmp_path, {"wave/STA1.mseed": wave,
                       "resp/STA1.xml": xml})
        b = io_mod.read_verified_waveform_bundle(
            evidence_root=root, source_manifest=manifest,
            waveform_relpaths=("wave/STA1.mseed",),
            response_relpaths=("resp/STA1.xml",),
            config=self._cfg())
        assert any("outside the StationXML channel epoch" in p
                   for p in b.problems)

    def test_rate_mismatch_xml_rejects(self, tmp_path):
        wave = _miniseed_file(rate=50, seconds=60, nrec=1)
        xml = _stationxml(rate=100)
        root, manifest = _evidence(
            tmp_path, {"wave/STA1.mseed": wave,
                       "resp/STA1.xml": xml})
        b = io_mod.read_verified_waveform_bundle(
            evidence_root=root, source_manifest=manifest,
            waveform_relpaths=("wave/STA1.mseed",),
            response_relpaths=("resp/STA1.xml",),
            config=self._cfg())
        assert any("SampleRate" in p and "disagrees" in p
                   for p in b.problems)

    def test_response_waiver_rejects_nonfixture(self, tmp_path):
        wave = _miniseed_file(rate=50, seconds=60, nrec=1)
        xml = _stationxml()
        root, manifest = _evidence(
            tmp_path, {"wave/STA1.mseed": wave,
                       "resp/STA1.xml": xml})
        b = io_mod.read_verified_waveform_bundle(
            evidence_root=root, source_manifest=manifest,
            waveform_relpaths=("wave/STA1.mseed",),
            response_relpaths=(),
            config=self._cfg(require_response=False))
        assert any("response waiver" in p for p in b.problems)

    def test_selector_rejects_undeclared(self, tmp_path):
        root, manifest, *_ = self._fixture(tmp_path)
        b = io_mod.read_verified_waveform_bundle(
            evidence_root=root, source_manifest=manifest,
            waveform_relpaths=("wave/STA1.mseed",),
            response_relpaths=("resp/STA1.xml",),
            station_selectors=("STA9",),
            config=self._cfg())
        assert any("station_selectors" in p for p in b.problems)

    def test_selector_for_absent_station_rejects(self, tmp_path):
        """A declared selector with zero matching traces must not
        silently pass — a typo would lose a station undetected."""
        root, manifest, *_ = self._fixture(tmp_path)
        b = io_mod.read_verified_waveform_bundle(
            evidence_root=root, source_manifest=manifest,
            waveform_relpaths=("wave/STA1.mseed",),
            response_relpaths=("resp/STA1.xml",),
            station_selectors=("STA1", "GHOST"),
            config=self._cfg())
        assert any("matched no parsed trace" in p
                   for p in b.problems)

    def test_inconsistent_channel_rates_reject(self, tmp_path):
        blob = (_record(seq=1, channel="BHE", rate=50,
                        samples=np.ones(100, np.int32)) +
                _record(seq=2, channel="BHZ", rate=100,
                        samples=np.ones(100, np.int32)))
        xml = _stationxml(channels=("BHE", "BHZ"), rate=50)
        root, manifest = _evidence(
            tmp_path, {"wave/STA1.mseed": blob,
                       "resp/STA1.xml": xml})
        b = io_mod.read_verified_waveform_bundle(
            evidence_root=root, source_manifest=manifest,
            waveform_relpaths=("wave/STA1.mseed",),
            response_relpaths=("resp/STA1.xml",),
            config=self._cfg())
        assert any("rate" in p.lower() for p in b.problems)


class TestDerivedObservability:
    def test_derived_values_not_caller_supplied(self, tmp_path):
        wave = _miniseed_file(rate=50, seconds=120, nrec=2)
        xml = _stationxml()
        root, manifest = _evidence(
            tmp_path, {"wave/STA1.mseed": wave,
                       "resp/STA1.xml": xml})
        cfg = SeismicSidecarConfig(
            waveform_relpaths=("wave/STA1.mseed",),
            response_relpaths=("resp/STA1.xml",),
            min_coverage_fraction=0.5, min_snr_db=-100.0)
        b = io_mod.read_verified_waveform_bundle(
            evidence_root=root, source_manifest=manifest,
            waveform_relpaths=("wave/STA1.mseed",),
            response_relpaths=("resp/STA1.xml",), config=cfg)
        assert b.problems == ()
        rec = io_mod.derive_station_observability(
            bundle=b, station_id="STA1",
            target_latitude=28.0, target_longitude=85.0,
            station_latitude=27.9, station_longitude=85.3,
            source_manifest_digest_value=
            io_mod.source_manifest_digest(manifest),
            source_manifest=manifest, config=cfg)
        assert rec.station_id == "STA1"
        assert rec.component_count == 3
        assert rec.orientation_status == "ORTHO_3C"
        assert rec.response_status == "BOUND"
        assert rec.response_relpath == "resp/STA1.xml"
        assert 0.9 <= rec.coverage_fraction <= 1.0
        assert rec.distance_km > 0
        assert rec.snr_by_band  # derived, never caller-set
        # The record carries no caller-settable coverage/SNR —
        # its fields are computed from the parsed traces.
        assert rec.problems == ()

    def test_absent_station_is_unobservable(self, tmp_path):
        wave = _miniseed_file(rate=50, seconds=60, nrec=1)
        xml = _stationxml()
        root, manifest = _evidence(
            tmp_path, {"wave/STA1.mseed": wave,
                       "resp/STA1.xml": xml})
        cfg = SeismicSidecarConfig(
            waveform_relpaths=("wave/STA1.mseed",),
            response_relpaths=("resp/STA1.xml",))
        b = io_mod.read_verified_waveform_bundle(
            evidence_root=root, source_manifest=manifest,
            waveform_relpaths=("wave/STA1.mseed",),
            response_relpaths=("resp/STA1.xml",), config=cfg)
        rec = io_mod.derive_station_observability(
            bundle=b, station_id="GHOST",
            target_latitude=28.0, target_longitude=85.0,
            source_manifest_digest_value=
            io_mod.source_manifest_digest(manifest),
            source_manifest=manifest, config=cfg)
        assert rec.status == "UNOBSERVABLE"
        assert rec.problems
