"""Verified waveform/StationXML byte intake for the seismic sidecar.

This module is the ONLY path from raw evidence bytes to typed seismic
records.  It performs three fail-closed jobs:

1. ``read_verified_waveform_bundle`` re-reads every declared
   ``waveform_relpaths``/``response_relpaths`` member through
   ``read_evidence_file`` (pinned lexical containment + sha256 equality
   against the manifest) and parses the bytes locally — miniSEED data
   records and FDSN StationXML — with NO network I/O anywhere.

2. StationXML CONTENT validation (S05): path+digest binding is not
   enough — the XML must parse, declare the matching
   network/station/location/channel, carry >=1 response stage, and its
   epoch must cover the trace's time span.  A well-formed XML for the
   wrong station is rejected exactly like a missing one.

3. Derived observability (S07): coverage, gaps, orientation, channel
   set, sample rate, and timing are COMPUTED from parsed records by
   ``derive_station_observability`` — caller-supplied coverage/SNR
   values cannot enter the record.

Supported waveform encodings are declared, not implicit: raw
INT16/INT32/FLOAT32/FLOAT64 (big-endian, per the SEED data-record
contract) decode natively.  STEIM1/STEIM2 admission is **closed by
contract** (G3-F1): the optional ``obspy`` decoder is resolved and
exercised only to produce an accurate rejection reason, and its output
is deliberately not admitted, because no ratified decision yet binds a
pinned decoder version plus representative-byte qualification to the
record.  A STEIM payload therefore always fails closed as unobservable
— with the real reason reported — rather than being silently decoded
under an unpinned dependency.  All other encodings are rejected as
undeclared.  Rotation policy for v0 admits both orthogonal (E/N/Z) and
rotated (1/2/Z) three-component records — component-averaged energy
features are orientation-agnostic; rotated channels are never silently
relabelled E/N.
"""
from __future__ import annotations

import hashlib
import math
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from nepal.research_v0._hashing import (
    read_evidence_file, sha256_bytes, sha256_canonical)

from .contracts import (
    SeismicSidecarConfig, StationObservabilityV0)
from .observability import (
    build_station_observability, source_manifest_digest)

PARSER_ID = "nepal.seismic_sidecar.io"
PARSER_VERSION = "1.0.0"

#: Declared raw-integer/float encodings decoded natively (big-endian,
#: per the SEED data-record fixed-header contract).  Everything else —
#: STEIM1/STEIM2 compression, CDSN/SRO legacy, ASCII — requires the
#: optional obspy decoder.
_NATIVE_ENCODINGS = {1: ("i2", 2), 3: ("i4", 4),
                     4: ("f4", 4), 5: ("f8", 8)}
_STEIM_ENCODINGS = frozenset({10, 11})

#: v0 response mode — raw instrument-domain counts only; physical
#: velocity claims are prohibited until a response-corrected pipeline
#: is separately qualified (see contracts.SEISMIC_FEATURE_UNITS).
RESPONSE_MODES = frozenset({"RAW_COUNTS", "VELOCITY_M_S"})

#: v0 rotation policy — component-averaged energy features are
#: orientation-agnostic, so orthogonal (E/N/Z) and rotated (1/2/Z)
#: three-component records are BOTH admitted; rotated channels are
#: never silently relabelled E/N.  ``rotate_declared`` is reserved
#: for a future declared rotation implementation.
ROTATION_POLICIES = frozenset({"rotated_3c_admitted", "rotate_declared"})

_MAX_RECORDS_PER_FILE = 1_000_000
_MIN_RECORD_BYTES = 48


def _btime(year: int, julday: int, hour: int, minute: int,
           second: int, fract10k: int) -> float | None:
    """SEED BTime -> epoch seconds (fract field is 0.0001 s units)."""
    try:
        base = datetime(year, 1, 1, tzinfo=timezone.utc) + \
            timedelta(days=julday - 1, hours=hour, minutes=minute,
                      seconds=second)
    except (ValueError, OverflowError):
        return None
    if not 0 <= fract10k <= 9999 or not 1 <= julday <= 366:
        return None
    return base.timestamp() + fract10k / 10000.0


def _iso(epoch: float) -> str:
    """Epoch seconds -> RFC3339 UTC (0.01 s resolution — enough for
    trace bookkeeping; window rows use whole seconds)."""
    dt = datetime.fromtimestamp(round(epoch, 2), tz=timezone.utc)
    text = dt.strftime("%Y-%m-%dT%H:%M:%S")
    frac = dt.microsecond
    if frac:
        text += f".{frac // 1000:03d}Z" if frac % 1000 == 0 else \
            f".{frac:06d}Z"
    else:
        text += "Z"
    return text


@dataclass(frozen=True)
class MiniseedRecordMeta:
    """Parsed identity of one miniSEED data record — no sample data."""
    trace_id: str
    network: str
    station: str
    location: str
    channel: str
    start_epoch: float
    n_samples: int
    sample_rate_hz: float
    encoding: int
    record_bytes: int
    header_offset: int


@dataclass(frozen=True)
class ParsedTraceV0:
    """One decoded, contiguous trace — samples held out of the
    canonical digest surface (digested via the source bytes)."""
    trace_id: str
    network: str
    station: str
    location: str
    channel: str
    sample_rate_hz: float
    start_epoch: float
    end_epoch: float
    n_samples: int
    relpath: str
    source_sha256: str
    samples: Any = field(repr=False, compare=False)

    def to_dict(self) -> dict:
        return {
            "trace_id": self.trace_id,
            "network": self.network, "station": self.station,
            "location": self.location, "channel": self.channel,
            "sample_rate_hz": self.sample_rate_hz,
            "start": _iso(self.start_epoch),
            "end": _iso(self.end_epoch),
            "n_samples": self.n_samples,
            "relpath": self.relpath,
            "source_sha256": self.source_sha256}


@dataclass(frozen=True)
class StationXMLRecordV0:
    """One parsed StationXML channel epoch — content-level identity."""
    network: str
    station: str
    location: str
    channel: str
    epoch_start_epoch: float
    epoch_end_epoch: float | None
    sample_rate_hz: float
    n_stages: int
    relpath: str = ""

    @property
    def trace_id(self) -> str:
        return f"{self.network}.{self.station}.{self.location}" \
               f".{self.channel}"

    def covers(self, start: float, end: float) -> bool:
        return self.epoch_start_epoch <= start and \
            (self.epoch_end_epoch is None or
             end <= self.epoch_end_epoch)


@dataclass(frozen=True)
class WaveformBundleV0:
    """The verified, parsed intake surface.  ``problems`` non-empty
    means the bundle is inadmissible — the caller maps that to
    UNOBSERVABLE, never to partial use."""
    traces: tuple = ()
    stationxml: tuple = ()
    waveform_bytes_digest: str = ""
    stationxml_bytes_digest: str = ""
    parser_id: str = PARSER_ID
    parser_version: str = PARSER_VERSION
    response_mode: str = "RAW_COUNTS"
    rotation_policy: str = "rotated_3c_admitted"
    resampling_policy: str = "resample_forbidden"
    sample_rate_policy: str = "consistent_rate_required"
    problems: tuple = ()

    def to_dict(self) -> dict:
        return {
            "record_type": "WaveformBundleV0",
            "traces": [t.to_dict() for t in self.traces],
            "stationxml": [
                {"network": r.network, "station": r.station,
                 "location": r.location, "channel": r.channel,
                 "epoch_start": _iso(r.epoch_start_epoch),
                 "epoch_end": (_iso(r.epoch_end_epoch)
                               if r.epoch_end_epoch is not None
                               else None),
                 "sample_rate_hz": r.sample_rate_hz,
                 "n_stages": r.n_stages}
                for r in self.stationxml],
            "waveform_bytes_digest": self.waveform_bytes_digest,
            "stationxml_bytes_digest": self.stationxml_bytes_digest,
            "parser_id": self.parser_id,
            "parser_version": self.parser_version,
            "response_mode": self.response_mode,
            "rotation_policy": self.rotation_policy,
            "resampling_policy": self.resampling_policy,
            "sample_rate_policy": self.sample_rate_policy,
            "problems": list(self.problems)}


def _parse_record_header(buf: bytes, offset: int,
                         rec_len: int) -> tuple[MiniseedRecordMeta | None,
                                                str | None, int]:
    """Parse one miniSEED fixed header + blockette 1000.

    Returns (meta, problem, next_offset); meta is None on a problem.
    ``rec_len`` is the running record-length hint — blockette 1000 in
    each record declares the authoritative length.
    """
    if offset + _MIN_RECORD_BYTES > len(buf):
        return None, "truncated record header", len(buf)
    hdr = buf[offset:offset + _MIN_RECORD_BYTES]
    if hdr[6:7] not in (b"D", b"R", b"Q", b"M"):
        return None, f"record at offset {offset} is not a data " \
                     "record (indicator not D/R/Q/M)", len(buf)
    def _ascii(sl: bytes) -> str:
        return sl.decode("ascii", errors="strict").strip()
    try:
        station = _ascii(hdr[8:13])
        location = _ascii(hdr[13:15])
        channel = _ascii(hdr[15:18])
        network = _ascii(hdr[18:20])
    except UnicodeDecodeError:
        return None, f"record at offset {offset}: non-ASCII " \
                     "identity fields", len(buf)
    year, julday = struct.unpack(">HH", hdr[20:24])
    hour, minute, sec = hdr[24], hdr[25], hdr[26]
    fract10k = struct.unpack(">H", hdr[28:30])[0]
    start = _btime(year, julday, hour, minute, sec, fract10k)
    if start is None:
        return None, f"record at offset {offset}: invalid " \
                     "start time", len(buf)
    n_samples, srf, srm = struct.unpack(">Hhh", hdr[30:36])
    if srf == 0:
        rate = 0.0
    elif srf > 0 and srm > 0:
        rate = float(srf * srm)
    elif srf > 0 and srm < 0:
        rate = srf / float(-srm)
    elif srf < 0 and srm > 0:
        rate = (-srm) / float(-srf)
    else:
        rate = 1.0 / float(srf * srm)
    n_blockettes = hdr[39]
    data_offset, boff = struct.unpack(">HH", hdr[44:48])
    # Blockette 1000 declares encoding + authoritative record length.
    encoding = -1
    this_rec_len = rec_len
    if boff and offset + boff + 8 <= len(buf):
        btype, _bnext, enc, _wo, rexp = struct.unpack(
            ">HHBBB", buf[offset + boff:offset + boff + 7])
        if btype == 1000:
            encoding = enc
            if 0 < rexp < 31:
                this_rec_len = 1 << rexp
    if encoding < 0:
        return None, f"record at offset {offset}: missing " \
                     "blockette 1000 — encoding undeclared", \
            offset + this_rec_len
    if data_offset < _MIN_RECORD_BYTES or \
            offset + data_offset > len(buf):
        return None, f"record at offset {offset}: invalid data " \
                     "offset", offset + this_rec_len
    if offset + this_rec_len > len(buf):
        return None, f"record at offset {offset}: declared record " \
                     "length overruns the file", len(buf)
    meta = MiniseedRecordMeta(
        trace_id=f"{network}.{station}.{location}.{channel}",
        network=network, station=station, location=location,
        channel=channel, start_epoch=start, n_samples=n_samples,
        sample_rate_hz=rate, encoding=encoding,
        record_bytes=this_rec_len, header_offset=offset)
    return meta, None, offset + this_rec_len


def parse_miniseed_records(
        data: bytes) -> tuple[list[MiniseedRecordMeta], list[str]]:
    """Walk every data record in a miniSEED payload; return parsed
    record metadata and content problems (fail-closed — a malformed
    record makes the whole file inadmissible)."""
    problems: list[str] = []
    metas: list[MiniseedRecordMeta] = []
    if not data:
        return metas, ["empty waveform payload"]
    offset, rec_len = 0, 512
    n = 0
    while offset < len(data):
        if n >= _MAX_RECORDS_PER_FILE:
            problems.append("record count exceeds the declared "
                            "safety bound")
            break
        meta, problem, next_off = _parse_record_header(
            data, offset, rec_len)
        if problem is not None:
            problems.append(problem)
            if next_off <= offset:
                break
            offset = next_off
            continue
        assert meta is not None
        rec_len = meta.record_bytes
        metas.append(meta)
        n += 1
        offset = next_off
    return metas, problems


def _decode_native(buf: bytes, meta: MiniseedRecordMeta) -> np.ndarray:
    """Decode one record's data section for a declared raw encoding."""
    fmt, width = _NATIVE_ENCODINGS[meta.encoding]
    start = meta.header_offset + \
        struct.unpack(">H", buf[meta.header_offset + 44:
                              meta.header_offset + 46])[0]
    nbytes = meta.n_samples * width
    chunk = buf[start:start + nbytes]
    if len(chunk) < nbytes:
        raise ValueError("data section shorter than declared "
                         "sample count")
    return np.frombuffer(chunk, dtype=np.dtype(f">{fmt}")) \
        .astype(np.float64)


def _decode_obspy(data: bytes) -> tuple[list[ParsedTraceV0], str | None]:
    """Optional-dependency seam: decode any encoding obspy supports.
    Returns (traces, problem); never raises."""
    try:
        import obspy  # type: ignore
    except ImportError:
        return [], "optional decoder 'obspy' is not installed — " \
                  "compressed miniSEED encodings are unsupported " \
                  "in this environment"
    try:
        import io as _io
        st = obspy.read(_io.BytesIO(data))
    except Exception as exc:
        return [], f"obspy failed to parse the payload: {exc}"
    traces = []
    for tr in st:
        stats = tr.stats
        traces.append(ParsedTraceV0(
            trace_id=f"{stats.network}.{stats.station}."
                     f"{stats.location}.{stats.channel}",
            network=stats.network, station=stats.station,
            location=stats.location, channel=stats.channel,
            sample_rate_hz=float(stats.sampling_rate),
            start_epoch=float(stats.starttime.timestamp),
            end_epoch=float(stats.endtime.timestamp),
            n_samples=int(stats.npts),
            relpath="", source_sha256="",
            samples=np.asarray(tr.data, dtype=np.float64)))
    return traces, None


def decode_records(
        data: bytes,
        metas: Sequence[MiniseedRecordMeta],
        *,
        relpath: str,
        source_sha256: str) -> tuple[list[ParsedTraceV0], list[str]]:
    """Decode records into per-trace contiguous segments.

    Consecutive records of the same trace_id merge when the next
    start matches the expected sample boundary within one sample
    period; gaps split the trace into separate segments (never
    bridged); overlaps and duplicate identities reject.
    """
    problems: list[str] = []
    by_id: dict[str, list[MiniseedRecordMeta]] = {}
    for m in metas:
        if m.n_samples == 0:
            continue
        if m.sample_rate_hz <= 0 or not math.isfinite(
                m.sample_rate_hz):
            problems.append(
                f"{m.trace_id}: non-positive sample rate")
            continue
        by_id.setdefault(m.trace_id, []).append(m)

    traces: list[ParsedTraceV0] = []
    for trace_id, recs in sorted(by_id.items()):
        recs = sorted(recs, key=lambda m: m.start_epoch)
        need_obspy = any(m.encoding not in _NATIVE_ENCODINGS
                         for m in recs)
        if need_obspy:
            if any(m.encoding in _STEIM_ENCODINGS for m in recs):
                # G3-F1: the decoder is exercised to report the REAL
                # reason, and then its output is discarded — STEIM
                # admission is closed by contract.  Reporting the
                # decoder's success as a failure (or formatting a
                # None problem as "— None") would fabricate evidence.
                encodings = sorted({m.encoding for m in recs})
                _, decoder_problem = _decode_obspy(data)
                if decoder_problem is None:
                    decoder_problem = (
                        "the optional obspy decoder parsed the payload, "
                        "but STEIM admission is closed by contract "
                        "pending a ratified G3-F1 decision")
                problems.append(
                    f"{trace_id}: STEIM encoding {encodings} is "
                    f"inadmissible — {decoder_problem}")
            else:
                problems.append(
                    f"{trace_id}: encoding "
                    f"{sorted({m.encoding for m in recs})} is not a "
                    "declared decodable encoding")
            continue
        # Contiguity grouping: segments start where the record start
        # deviates from the expected boundary beyond one period.
        segments: list[list[MiniseedRecordMeta]] = [[recs[0]]]
        for m in recs[1:]:
            prev = segments[-1][-1]
            expected = prev.start_epoch + \
                prev.n_samples / prev.sample_rate_hz
            tol = 1.0 / m.sample_rate_hz
            if m.sample_rate_hz != prev.sample_rate_hz:
                problems.append(
                    f"{trace_id}: sample-rate changes inside one "
                    "trace id — the consistent-rate policy rejects")
                break
            if m.start_epoch < expected - tol:
                problems.append(
                    f"{trace_id}: overlapping or out-of-order "
                    "records — overlap is inadmissible")
                break
            if m.start_epoch <= expected + tol:
                segments[-1].append(m)
            else:
                segments.append([m])
        else:
            for seg in segments:
                chunks = []
                for m in seg:
                    try:
                        chunks.append(_decode_native(data, m))
                    except ValueError as exc:
                        problems.append(f"{trace_id}: {exc}")
                        break
                else:
                    samples = np.concatenate(chunks)
                    traces.append(ParsedTraceV0(
                        trace_id=trace_id,
                        network=seg[0].network,
                        station=seg[0].station,
                        location=seg[0].location,
                        channel=seg[0].channel,
                        sample_rate_hz=seg[0].sample_rate_hz,
                        start_epoch=seg[0].start_epoch,
                        end_epoch=seg[0].start_epoch +
                        len(samples) / seg[0].sample_rate_hz,
                        n_samples=len(samples),
                        relpath=relpath,
                        source_sha256=source_sha256,
                        samples=samples))
    return traces, problems


def parse_stationxml(data: bytes) -> tuple[list[StationXMLRecordV0],
                                           list[str]]:
    """Parse FDSN StationXML bytes into channel-epoch response
    records — content validation, not just well-formedness."""
    problems: list[str] = []
    records: list[StationXMLRecordV0] = []
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        return records, [f"StationXML is not well-formed XML: {exc}"]
    tag = root.tag.rsplit("}", 1)[-1]
    if tag != "FDSNStationXML":
        problems.append(f"root element {tag!r} is not "
                        "FDSNStationXML")
        return records, problems
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}", 1)[0] + "}"

    def _find(el, name):
        return el.find(f"{ns}{name}")

    def _findall(el, name):
        return el.findall(f"{ns}{name}")

    def _parse_epoch(text: str | None) -> float | None:
        if not text:
            return None
        t = text.strip()
        if not t.endswith("Z"):
            t += "Z"
        try:
            return datetime.fromisoformat(
                t.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None

    networks = _findall(root, "Network")
    if not networks:
        problems.append("StationXML declares no Network elements")
        return records, problems
    for net in networks:
        ncode = (net.get("code") or "").strip()
        for sta in _findall(net, "Station"):
            scode = (sta.get("code") or "").strip()
            for ch in _findall(sta, "Channel"):
                ccode = (ch.get("code") or "").strip()
                loc = (ch.get("locationCode") or "").strip()
                es = _parse_epoch(ch.get("startDate") or
                                  sta.get("startDate"))
                raw_end = ch.get("endDate") or sta.get("endDate")
                ee = _parse_epoch(raw_end)
                if raw_end and ee is None:
                    problems.append(
                        f"{ncode}.{scode}.{loc}.{ccode}: "
                        "unparsable epoch endDate — a corrupt "
                        "bound is not an open-ended epoch")
                    continue
                if ee is not None and es is not None and ee <= es:
                    problems.append(
                        f"{ncode}.{scode}.{loc}.{ccode}: "
                        "epoch endDate precedes startDate — "
                        "inverted response epoch")
                    continue
                if es is None:
                    problems.append(
                        f"{ncode}.{scode}.{loc}.{ccode}: missing "
                        "or unparsable epoch startDate")
                    continue
                sr_el = _find(ch, "SampleRate")
                try:
                    rate = float(sr_el.text.strip()) \
                        if sr_el is not None and sr_el.text else 0.0
                except ValueError:
                    rate = 0.0
                if rate <= 0:
                    problems.append(
                        f"{ncode}.{scode}.{loc}.{ccode}: missing "
                        "or non-positive SampleRate")
                    continue
                resp = _find(ch, "Response")
                if resp is None:
                    problems.append(
                        f"{ncode}.{scode}.{loc}.{ccode}: no "
                        "Response element — response content is "
                        "required, not merely declared")
                    continue
                stages = _findall(resp, "Stage")
                if not stages:
                    problems.append(
                        f"{ncode}.{scode}.{loc}.{ccode}: Response "
                        "carries zero Stage elements")
                    continue
                records.append(StationXMLRecordV0(
                    network=ncode, station=scode, location=loc,
                    channel=ccode, epoch_start_epoch=es,
                    epoch_end_epoch=ee, sample_rate_hz=rate,
                    n_stages=len(stages)))
    if not records and not problems:
        problems.append("StationXML contains no usable channel "
                        "response records")
    return records, problems


def read_verified_waveform_bundle(
        *,
        evidence_root: Path,
        source_manifest: Mapping[str, Any],
        waveform_relpaths: Sequence[str],
        response_relpaths: Sequence[str],
        station_selectors: Sequence[str] = (),
        config: SeismicSidecarConfig | None = None,
) -> WaveformBundleV0:
    """Read + verify + parse the declared waveform and StationXML
    payloads — the additive real-input path.

    Every relpath must be a declared ``source_files`` member; bytes
    must equal the manifest sha256; content must parse; every parsed
    trace must match a StationXML channel whose epoch covers the
    trace span under the declared response policy.  Returns a
    bundle whose ``problems`` decide admissibility — it never raises
    for content failures.
    """
    problems: list[str] = []
    cfg = config or SeismicSidecarConfig()
    if not isinstance(source_manifest, Mapping):
        return WaveformBundleV0(problems=(
            "source_manifest must be a mapping",))
    declared = {
        f.get("relpath"): f.get("sha256")
        for f in source_manifest.get("source_files", ())
        if isinstance(f, Mapping)}
    wave = [str(r) for r in waveform_relpaths]
    resp = [str(r) for r in response_relpaths]
    if not wave:
        problems.append("waveform_relpaths is empty — no waveform "
                        "payload is bound")
    if cfg.require_response and not resp:
        problems.append("response_relpaths is empty under "
                        "require_response — StationXML evidence is "
                        "mandatory on the real path")
    if cfg.require_response is False:
        # S16: a response-free path exists only for fixture/negative
        # tests.  A byte-bound (non-fixture) manifest may never ride
        # the response waiver — raw counts still need the instrument
        # record that produced them.
        fx = bool(source_manifest.get("fixture"))
        if not fx:
            problems.append(
                "require_response=False on a non-fixture manifest "
                "— the response waiver is restricted to fixture/"
                "negative tests; real runs must bind StationXML")
    for rel in wave + resp:
        if rel not in declared:
            problems.append(f"{rel!r} is not a declared "
                            "source_files member")
    if problems:
        return WaveformBundleV0(problems=tuple(problems))

    root = Path(str(evidence_root)).resolve()
    wave_hash = hashlib.sha256()
    wave_parts: list[bytes] = []
    for rel in sorted(wave):
        try:
            b = read_evidence_file(root, rel,
                                   label="waveform payload")
        except ValueError as exc:
            problems.append(f"waveform payload {rel!r}: {exc}")
            return WaveformBundleV0(problems=tuple(problems))
        if sha256_bytes(b) != declared[rel]:
            problems.append(
                f"waveform payload {rel!r}: bytes do not match "
                "the manifest's declared sha256")
            return WaveformBundleV0(problems=tuple(problems))
        wave_hash.update(b)
        wave_parts.append(b)
    resp_hash = hashlib.sha256()
    resp_parts: list[bytes] = []
    for rel in sorted(resp):
        try:
            b = read_evidence_file(root, rel,
                                   label="StationXML payload")
        except ValueError as exc:
            problems.append(f"StationXML payload {rel!r}: {exc}")
            return WaveformBundleV0(problems=tuple(problems))
        if sha256_bytes(b) != declared[rel]:
            problems.append(
                f"StationXML payload {rel!r}: bytes do not match "
                "the manifest's declared sha256")
            return WaveformBundleV0(problems=tuple(problems))
        resp_hash.update(b)
        resp_parts.append(b)
    wave_bytes_digest = wave_hash.hexdigest()
    resp_bytes_digest = resp_hash.hexdigest()

    # ---- parse -----------------------------------------------------
    all_metas: list[tuple[str, str, list]] = []
    for rel, blob in zip(sorted(wave), wave_parts):
        metas, p = parse_miniseed_records(blob)
        problems.extend(f"{rel}: {x}" for x in p)
        all_metas.append((rel, sha256_bytes(blob), metas))
    sx_records: list[StationXMLRecordV0] = []
    for rel, blob in zip(sorted(resp), resp_parts):
        recs, p = parse_stationxml(blob)
        problems.extend(f"{rel}: {x}" for x in p)
        sx_records.extend(replace(r, relpath=rel) for r in recs)
    if problems:
        return WaveformBundleV0(
            waveform_bytes_digest=wave_bytes_digest,
            stationxml_bytes_digest=resp_bytes_digest,
            problems=tuple(problems))

    traces: list[ParsedTraceV0] = []
    for rel, sha, metas in all_metas:
        blob = next(b for r, b in zip(sorted(wave), wave_parts)
                    if r == rel)
        trs, p = decode_records(blob, metas,
                                relpath=rel, source_sha256=sha)
        problems.extend(p)
        traces.extend(trs)
    if problems:
        return WaveformBundleV0(
            waveform_bytes_digest=wave_bytes_digest,
            stationxml_bytes_digest=resp_bytes_digest,
            problems=tuple(problems))
    if not traces:
        problems.append("no decodable waveform traces — metadata "
                        "presence is not waveform evidence")
        return WaveformBundleV0(
            waveform_bytes_digest=wave_bytes_digest,
            stationxml_bytes_digest=resp_bytes_digest,
            problems=tuple(problems))

    # ---- selector + identity + response-content validation ---------
    selectors = {str(s).strip() for s in station_selectors
                 if str(s).strip()}
    if selectors:
        unknown = sorted(
            {t.trace_id for t in traces
             if t.station not in selectors and
             f"{t.network}.{t.station}" not in selectors and
             t.trace_id not in selectors})
        if unknown:
            problems.append(
                f"parsed traces {unknown} are outside the declared "
                "station_selectors — undeclared stations cannot "
                "enter the bundle")
        matched = {
            s for s in selectors
            if any(t.station == s or
                   f"{t.network}.{t.station}" == s or
                   t.trace_id == s for t in traces)}
        silent = sorted(selectors - matched)
        if silent:
            problems.append(
                f"station_selectors {silent} matched no parsed "
                "trace — a declared station with no waveform "
                "evidence cannot silently pass")
        traces = [t for t in traces
                  if t.station in selectors or
                  f"{t.network}.{t.station}" in selectors or
                  t.trace_id in selectors]
    sx_by_id = {r.trace_id: r for r in sx_records}
    stations = {t.station for t in traces}
    for t in traces:
        rec = sx_by_id.get(t.trace_id)
        if rec is None:
            problems.append(
                f"{t.trace_id}: no StationXML channel matches the "
                "parsed trace identity — a well-formed XML for the "
                "wrong channel is not response evidence")
            continue
        if not rec.covers(t.start_epoch, t.end_epoch):
            problems.append(
                f"{t.trace_id}: trace span is outside the "
                "StationXML channel epoch — the response is stale "
                "for this record")
        if abs(rec.sample_rate_hz - t.sample_rate_hz) > 1e-6:
            problems.append(
                f"{t.trace_id}: StationXML SampleRate "
                f"{rec.sample_rate_hz} disagrees with the record's "
                f"{t.sample_rate_hz} Hz")
    for sta in stations:
        rates = {t.sample_rate_hz for t in traces
                 if t.station == sta}
        if len(rates) > 1:
            problems.append(
                f"station {sta}: channels carry inconsistent "
                f"sample rates {sorted(rates)} — resampling is "
                "forbidden by the declared policy")
    if len(stations) > 1:
        st_rates = {t.sample_rate_hz for t in traces}
        if len(st_rates) > 1:
            problems.append(
                "cross-station sample-rate mismatch "
                f"{sorted(st_rates)} — resampling is forbidden "
                "by the declared policy")
    if problems:
        return WaveformBundleV0(
            traces=tuple(traces), stationxml=tuple(sx_records),
            waveform_bytes_digest=wave_bytes_digest,
            stationxml_bytes_digest=resp_bytes_digest,
            problems=tuple(problems))

    return WaveformBundleV0(
        traces=tuple(traces), stationxml=tuple(sx_records),
        waveform_bytes_digest=wave_bytes_digest,
        stationxml_bytes_digest=resp_bytes_digest,
        problems=())


#: Declared noise-floor rule — the 10th percentile of per-window RMS
#: amplitude over the record is the measured noise floor; SNR is the
#: record's 95th-percentile RMS against it.  The rule is a constant,
#: never caller-supplied.
NOISE_FLOOR_PERCENTILE = 10.0
SIGNAL_PERCENTILE = 95.0


def derive_station_observability(
        *,
        bundle: WaveformBundleV0,
        station_id: str,
        target_latitude: float,
        target_longitude: float,
        source_manifest_digest_value: str,
        source_manifest: Mapping[str, Any] | None = None,
        station_latitude: float | None = None,
        station_longitude: float | None = None,
        config: SeismicSidecarConfig | None = None,
) -> StationObservabilityV0:
    """Derive one station's observability record FROM PARSED TRACES —
    coverage, gaps, orientation, channel set, rate, and SNR are
    computed here; no caller may supply or override them.
    """
    cfg = config or SeismicSidecarConfig()
    declared_files = manifest_sha_lookup(source_manifest) \
        if isinstance(source_manifest, Mapping) else {}
    traces = [t for t in bundle.traces if t.station == station_id
              or f"{t.network}.{t.station}" == station_id]
    if not traces:
        return build_station_observability(
            station_id=station_id, network="", station=station_id,
            channel_set=(), station_latitude=station_latitude or 0.0,
            station_longitude=station_longitude or 0.0,
            target_latitude=target_latitude,
            target_longitude=target_longitude, sample_rate_hz=0.0,
            coverage_start="1970-01-01T00:00:00Z",
            coverage_end="1970-01-01T00:00:01Z",
            coverage_fraction=0.0, gap_fraction=1.0,
            latency_seconds=0.0, waveform_available=False,
            source_manifest_digest=source_manifest_digest_value,
            config=cfg)

    first = traces[0]
    span_lo = min(t.start_epoch for t in traces)
    span_hi = max(t.end_epoch for t in traces)
    covered = sum(t.n_samples / t.sample_rate_hz for t in traces)
    span = max(span_hi - span_lo, 1e-9)
    coverage = min(1.0, covered / span)
    gap = max(0.0, 1.0 - coverage)
    channel_set = tuple(sorted({t.channel for t in traces}))

    # Measured noise floor + SNR per declared band — computed over
    # the component-averaged amplitude series of every trace, then
    # pooled.  No caller input.
    rms_pool: list[float] = []
    for t in traces:
        s = np.asarray(t.samples, dtype=np.float64)
        if s.ndim == 1:
            amp = np.abs(s)
        else:
            amp = np.sqrt(np.mean(s ** 2, axis=1))
        w = max(1, int(cfg.window_seconds * t.sample_rate_hz))
        for lo in range(0, len(amp) - w + 1, w):
            rms_pool.append(float(np.sqrt(
                np.mean(amp[lo:lo + w] ** 2))))
    noise: dict[str, float] = {}
    snr: dict[str, float] = {}
    if rms_pool:
        pool = np.asarray(rms_pool)
        floor = float(np.percentile(pool, NOISE_FLOOR_PERCENTILE))
        signal = float(np.percentile(pool, SIGNAL_PERCENTILE))
        floor = max(floor, np.finfo(np.float64).tiny)
        for lo_hz, hi_hz in cfg.bands:
            label = f"{float(lo_hz):g}-{float(hi_hz):g}hz".replace(
                ".", "p")
            noise[label] = floor
            snr[label] = float(
                20.0 * math.log10(signal / floor))

    # Response binding: the manifest relpath whose parsed records
    # cover this station's traces, plus that file's declared sha256 —
    # the record binds path+sha while the bundle binds content.
    resp_rel, resp_sha = "", ""
    sx_ids = {r.trace_id: r for r in bundle.stationxml}
    covering = {sx_ids[t.trace_id].relpath for t in traces
                if t.trace_id in sx_ids}
    if covering and declared_files:
        rel = sorted(covering)[0]
        resp_rel = rel
        resp_sha = declared_files.get(rel, "")

    return build_station_observability(
        station_id=station_id,
        network=first.network, station=first.station,
        location=first.location, channel_set=channel_set,
        station_latitude=station_latitude or 0.0,
        station_longitude=station_longitude or 0.0,
        target_latitude=target_latitude,
        target_longitude=target_longitude,
        sample_rate_hz=first.sample_rate_hz,
        response_relpath=resp_rel, response_sha256=resp_sha,
        coverage_start=_iso(span_lo), coverage_end=_iso(span_hi),
        coverage_fraction=coverage, gap_fraction=gap,
        latency_seconds=0.0,
        noise_floor_by_band=noise, snr_by_band=snr,
        waveform_available=True,
        source_manifest_digest=source_manifest_digest_value,
        config=cfg)


def manifest_sha_lookup(
        source_manifest: Mapping[str, Any]) -> dict[str, str]:
    """The declared relpath -> sha256 map (typed convenience for
    callers that bind response paths)."""
    return {
        str(f.get("relpath")): str(f.get("sha256"))
        for f in source_manifest.get("source_files", ())
        if isinstance(f, Mapping)}


__all__ = [
    "PARSER_ID", "PARSER_VERSION", "RESPONSE_MODES",
    "ROTATION_POLICIES", "NOISE_FLOOR_PERCENTILE",
    "SIGNAL_PERCENTILE", "MiniseedRecordMeta", "ParsedTraceV0",
    "StationXMLRecordV0", "WaveformBundleV0",
    "parse_miniseed_records", "decode_records", "parse_stationxml",
    "read_verified_waveform_bundle", "derive_station_observability",
    "manifest_sha_lookup", "source_manifest_digest"]
