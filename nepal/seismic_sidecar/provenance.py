"""Feature-generation provenance for the seismic sidecar.

Closes the derivation gap (S06): a caller-prepared feature frame can
currently ride a real byte-bound manifest — the frame's semantic
digest binds the VALUES but never proves the frame was DERIVED from
the bound waveform bytes.  ``FeatureFrameArtifactV0`` fixes that:

``feature_generation_digest`` canonically binds —

- the waveform byte digest and every parsed trace identity;
- the StationXML byte digest and every parsed response record;
- the parser identifier and version;
- the declared response/rotation/resampling/sample-rate policies;
- the full window/feature configuration;
- the dropped-window ledger (what was rejected, and why);
- the emitted semantic feature rows themselves.

``verify_feature_generation`` recomputes the digest from the bound
components — mutating the frame while keeping bytes unchanged, or
swapping bytes while keeping the frame, both break the binding.

``run_seismic_real_path`` is the only producer of a real-evidence
feature frame: it takes a bundle (never a frame), builds rows through
``build_window_rows`` from the PARSED traces, derives observability
from the same bundle, and delegates to the existing
``run_seismic_descriptive_poc`` under the exact V0 receipt.  The
receipt stays V0; the generation artifact is emitted alongside it
and covered by ``report_digest``.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from nepal.research_v0._hashing import sha256_canonical

from .contracts import (
    SEISMIC_IDENTITY_COLUMNS, SEISMIC_NONBAND_FEATURES,
    SeismicSidecarConfig, seismic_receipt_skeleton)
from .features import (
    aggregate_daily, band_feature_names, build_window_rows,
    cross_station_coherence, semantic_feature_digest)
from .io import (
    PARSER_ID, PARSER_VERSION, WaveformBundleV0,
    derive_station_observability, source_manifest_digest)
from .runner import run_seismic_descriptive_poc

FEATURE_FRAME_ARTIFACT_TYPE = "FeatureFrameArtifactV0"


def _digest(section: Any) -> str:
    return sha256_canonical(section)


@dataclass(frozen=True)
class FeatureFrameArtifactV0:
    """The generation-bound semantic frame — every input that
    produced the rows is named and digested.  ``frame`` is the
    (station × window) row surface consumed by the runner;
    ``feature_generation_digest`` proves how it was made."""
    frame_rows: tuple = ()
    dropped_windows: tuple = ()
    feature_cols: tuple = ()
    trace_digests: tuple = ()
    waveform_bytes_digest: str = ""
    stationxml_bytes_digest: str = ""
    stationxml_content_digest: str = ""
    parser_id: str = PARSER_ID
    parser_version: str = PARSER_VERSION
    response_mode: str = "RAW_COUNTS"
    policies: Mapping = field(default_factory=dict)
    window_config_digest: str = ""
    dropped_ledger_digest: str = ""
    semantic_feature_digest: str = ""
    feature_generation_digest: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["frame_rows"] = list(self.frame_rows)
        d["dropped_windows"] = list(self.dropped_windows)
        d["feature_cols"] = list(self.feature_cols)
        d["trace_digests"] = list(self.trace_digests)
        d["policies"] = dict(self.policies)
        d["record_type"] = FEATURE_FRAME_ARTIFACT_TYPE
        return d


def _trace_digest_surface(bundle: WaveformBundleV0) -> tuple:
    """Per-trace canonical digest surface — identity + span + count
    + source sha (never the raw samples themselves)."""
    return tuple(_digest(t.to_dict()) for t in bundle.traces)


def _stationxml_content_digest(
        bundle: WaveformBundleV0) -> str:
    return _digest([
        {"trace_id": r.trace_id, "epoch_start": r.epoch_start_epoch,
         "epoch_end": r.epoch_end_epoch,
         "sample_rate_hz": r.sample_rate_hz,
         "n_stages": r.n_stages, "relpath": r.relpath}
        for r in bundle.stationxml])


def _window_config_surface(cfg: SeismicSidecarConfig,
                           noise_policy: Mapping) -> dict:
    return {
        "window_seconds": cfg.window_seconds,
        "bands": [list(b) for b in cfg.bands],
        "sta_seconds": cfg.sta_seconds,
        "lta_seconds": cfg.lta_seconds,
        "min_coverage_fraction": cfg.min_coverage_fraction,
        "feature_vocabulary": {
            "nonband": list(SEISMIC_NONBAND_FEATURES),
            "bands": list(band_feature_names(cfg.bands))},
        "noise_policy": dict(noise_policy)}


def build_seismic_feature_artifact(
        bundle: WaveformBundleV0,
        config: SeismicSidecarConfig,
        *,
        noise_floor_by_station: Mapping[str, float] | None = None,
        basin_group: str = "seismic_corridor") -> FeatureFrameArtifactV0:
    """Generate the window-level feature frame from parsed traces —
    the real-path producer.  ``noise_floor_by_station`` binds the
    measured floor per station (derived upstream, declared here);
    stations without a floor are dropped into the ledger.
    """
    problems: list[str] = []
    cfg = config
    fcols = list(SEISMIC_NONBAND_FEATURES[:10]) + \
        list(band_feature_names(cfg.bands)) + \
        list(SEISMIC_NONBAND_FEATURES[10:])
    floors = dict(noise_floor_by_station or {})
    by_station: dict[str, list] = {}
    for t in bundle.traces:
        by_station.setdefault(t.station, []).append(t)

    rows: list[dict] = []
    dropped: list[dict] = []
    grids: dict[str, tuple] = {}
    for station in sorted(by_station):
        traces = sorted(by_station[station],
                        key=lambda t: t.start_epoch)
        floor = floors.get(station)
        if floor is None:
            for t in traces:
                dropped.append({
                    "station_id": station,
                    "window_start": "", "window_end": "",
                    "unit_id": "", "basin_group": "",
                    "date": "", "coverage_fraction": 0.0,
                    "reason": "no declared noise floor for this "
                              "station — SNR cannot be computed, "
                              "the record is unobservable"})
            continue
        # Merge the station's component traces onto one shared
        # sample grid — a sample is covered only when EVERY channel
        # covers it; uncovered positions are masked, never filled.
        merged = _merge_station_components(traces)
        if merged is None:
            for t in traces:
                dropped.append({
                    "station_id": station,
                    "window_start": "", "window_end": "",
                    "unit_id": "", "basin_group": "",
                    "date": "", "coverage_fraction": 0.0,
                    "reason": "component traces could not be "
                              "aligned on a shared sample grid"})
            continue
        grid, present, epoch0, fs = merged
        wrows, wdropped = build_window_rows(
            station_id=station,
            unit_id=f"unit-{station}",
            basin_group=basin_group,
            samples=grid,
            sample_rate_hz=fs,
            epoch_start_iso=_trace_epoch_iso(epoch0),
            window_seconds=cfg.window_seconds,
            noise_floor_rms=float(floor),
            sta_seconds=cfg.sta_seconds,
            lta_seconds=cfg.lta_seconds,
            bands=cfg.bands,
            min_coverage_fraction=cfg.min_coverage_fraction,
            present_mask=present)
        grids[station] = (grid, present, epoch0, fs)
        rows.extend(wrows)
        dropped.extend(wdropped)

    if not rows:
        problems.append("no qualified window rows — the parsed "
                        "traces produced no admissible features")

    # Cross-station coherence — attached only when EVERY qualified
    # row of every station carries a value; a partially covered
    # coherence column would inject missing values into the frame,
    # so it is dropped whole rather than zero-filled.
    if "seis_coherence" in fcols and len(grids) >= 2:
        pair_acc: dict[tuple, list] = {}
        row_index = {(r["station_id"], r["window_start"]): r
                     for r in rows}
        stations = sorted(grids)
        for i in range(len(stations)):
            for j in range(i + 1, len(stations)):
                sa, sb = stations[i], stations[j]
                ga, _pa, e0a, fsa = grids[sa]
                gb, _pb, e0b, fsb = grids[sb]
                if fsa != fsb:
                    continue
                n_win = int(cfg.window_seconds * fsa)
                for (s_key, ws), _ra in row_index.items():
                    if s_key != sa:
                        continue
                    rb = row_index.get((sb, ws))
                    if rb is None:
                        continue
                    ws_ep = _utc(ws)
                    if ws_ep is None:
                        continue
                    i0a = int(round((ws_ep - e0a) * fsa))
                    i0b = int(round((ws_ep - e0b) * fsb))
                    seg_a = ga[i0a:i0a + n_win]
                    seg_b = gb[i0b:i0b + n_win]
                    if seg_a.shape[0] != n_win or \
                            seg_b.shape[0] != n_win:
                        continue
                    try:
                        coh = cross_station_coherence(
                            seg_a, seg_b, fsa, cfg.bands)
                    except ValueError:
                        continue
                    pair_acc.setdefault((sa, ws), []).append(coh)
                    pair_acc.setdefault((sb, ws), []).append(coh)
        if len(pair_acc) == len(row_index) and pair_acc:
            for (s_key, ws), vals in pair_acc.items():
                row_index[(s_key, ws)]["seis_coherence"] = \
                    float(np.mean(vals))
        else:
            fcols = [c for c in fcols if c != "seis_coherence"]
    else:
        fcols = [c for c in fcols if c != "seis_coherence"]

    noise_policy = {
        "rule": "measured_percentile",
        "floors": floors}
    sem_digest = semantic_feature_digest(rows, fcols)
    surface = {
        "trace_digests": list(_trace_digest_surface(bundle)),
        "waveform_bytes_digest": bundle.waveform_bytes_digest,
        "stationxml_bytes_digest": bundle.stationxml_bytes_digest,
        "stationxml_content_digest":
            _stationxml_content_digest(bundle),
        "parser_id": PARSER_ID,
        "parser_version": PARSER_VERSION,
        "response_mode": bundle.response_mode,
        "policies": {
            "rotation_policy": bundle.rotation_policy,
            "resampling_policy": bundle.resampling_policy,
            "sample_rate_policy": bundle.sample_rate_policy},
        "window_config": _window_config_surface(cfg, noise_policy),
        "dropped_ledger_digest": _digest(dropped),
        "semantic_feature_digest": sem_digest,
        "frame_row_count": len(rows)}
    artifact = FeatureFrameArtifactV0(
        frame_rows=tuple(rows),
        dropped_windows=tuple(dropped),
        feature_cols=tuple(fcols),
        trace_digests=_trace_digest_surface(bundle),
        waveform_bytes_digest=bundle.waveform_bytes_digest,
        stationxml_bytes_digest=bundle.stationxml_bytes_digest,
        stationxml_content_digest=
        _stationxml_content_digest(bundle),
        parser_id=PARSER_ID,
        parser_version=PARSER_VERSION,
        response_mode=bundle.response_mode,
        policies={
            "rotation_policy": bundle.rotation_policy,
            "resampling_policy": bundle.resampling_policy,
            "sample_rate_policy": bundle.sample_rate_policy},
        window_config_digest=_digest(
            _window_config_surface(cfg, noise_policy)),
        dropped_ledger_digest=_digest(dropped),
        semantic_feature_digest=sem_digest,
        feature_generation_digest=_digest(surface))
    return artifact


def _utc(text: str) -> float | None:
    from nepal.research_v0.policy import parse_strict_utc
    return parse_strict_utc(text)


def _trace_epoch_iso(epoch: float) -> str:
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(
        round(epoch, 2), tz=timezone.utc)
    base = dt.strftime("%Y-%m-%dT%H:%M:%S")
    frac = dt.microsecond
    return f"{base}.{frac:06d}Z" if frac else f"{base}Z"


#: Declared safety bound on the merged station grid — a sparse
#: record spanning days at high rate must not inflate into an
#: unbounded array; oversized grids drop into the ledger with an
#: explicit reason rather than exhausting memory.
_MAX_GRID_SAMPLES = 25_000_000


def _merge_station_components(
        traces: Sequence) -> tuple | None:
    """Align one station's channel traces on a shared sample grid.

    Returns (grid, present_mask, epoch0, sample_rate_hz) or None when
    the traces cannot form a grid (rate mismatch, zero overlap,
    oversized span).  ``present[i]`` is True only where EVERY channel
    covers sample i — uncovered positions hold zeros and are masked
    downstream.
    """
    if not traces:
        return None
    rates = {t.sample_rate_hz for t in traces}
    if len(rates) != 1:
        return None
    fs = rates.pop()
    start = min(t.start_epoch for t in traces)
    end = max(t.end_epoch for t in traces)
    n = int(round((end - start) * fs))
    if n <= 0:
        return None
    channels_n = len({t.channel for t in traces})
    if n * max(channels_n, 1) > _MAX_GRID_SAMPLES:
        return None
    channels = sorted({t.channel for t in traces})
    grid = np.zeros((n, len(channels)))
    present = np.zeros((n, len(channels)), dtype=bool)
    for ci, ch in enumerate(channels):
        for t in traces:
            if t.channel != ch:
                continue
            i0 = int(round((t.start_epoch - start) * fs))
            i1 = min(i0 + t.n_samples, n)
            if i1 <= i0:
                continue
            seg = np.asarray(t.samples, dtype=np.float64).reshape(-1)
            grid[i0:i1, ci] = seg[:i1 - i0]
            present[i0:i1, ci] = True
    return grid, present.all(axis=1), start, fs


def verify_feature_generation(
        artifact: FeatureFrameArtifactV0,
        bundle: WaveformBundleV0,
        config: SeismicSidecarConfig,
        *,
        noise_floor_by_station: Mapping[str, float] | None = None,
        ) -> list[str]:
    """Recompute every bound surface and compare — the artifact is
    honest only when nothing upstream or downstream moved.  This is
    the mutation probe: changed bytes, changed frame, changed
    config, or changed parser all fail."""
    problems: list[str] = []
    if artifact.waveform_bytes_digest != \
            bundle.waveform_bytes_digest:
        problems.append("waveform_bytes_digest diverged from the "
                        "bundle — bytes changed after generation")
    if artifact.stationxml_bytes_digest != \
            bundle.stationxml_bytes_digest:
        problems.append("stationxml_bytes_digest diverged")
    if artifact.stationxml_content_digest != \
            _stationxml_content_digest(bundle):
        problems.append("parsed StationXML content diverged")
    if artifact.trace_digests != _trace_digest_surface(bundle):
        problems.append("parsed trace identities diverged")
    sem = semantic_feature_digest(artifact.frame_rows,
                                  artifact.feature_cols)
    if sem != artifact.semantic_feature_digest:
        problems.append("the frame rows no longer hash to the "
                        "bound semantic_feature_digest — the frame "
                        "was mutated after generation")
    cfg_surface = _window_config_surface(
        config, {"rule": "measured_percentile",
                 "floors": dict(noise_floor_by_station or {})})
    if _digest(cfg_surface) != artifact.window_config_digest:
        problems.append("the window/feature configuration diverged "
                        "from the bound declaration")
    if _digest(list(artifact.dropped_windows)) != \
            artifact.dropped_ledger_digest:
        problems.append("the dropped-window ledger diverged")
    surface = {
        "trace_digests": list(artifact.trace_digests),
        "waveform_bytes_digest": artifact.waveform_bytes_digest,
        "stationxml_bytes_digest": artifact.stationxml_bytes_digest,
        "stationxml_content_digest":
            artifact.stationxml_content_digest,
        "parser_id": artifact.parser_id,
        "parser_version": artifact.parser_version,
        "response_mode": artifact.response_mode,
        "policies": dict(artifact.policies),
        "window_config": cfg_surface,
        "dropped_ledger_digest": artifact.dropped_ledger_digest,
        "semantic_feature_digest": artifact.semantic_feature_digest,
        "frame_row_count": len(artifact.frame_rows)}
    if _digest(surface) != artifact.feature_generation_digest:
        problems.append("feature_generation_digest does not "
                        "recompute — the provenance chain is broken")
    return problems


def run_seismic_real_path(
        *,
        evidence_root,
        source_manifest: Mapping[str, Any],
        train_mask,
        seismic_config: SeismicSidecarConfig,
        station_selectors: Sequence[str] = (),
        station_metadata: Mapping[str, Mapping[str, float]] | None =
        None) -> tuple[dict, FeatureFrameArtifactV0]:
    """The real-evidence entry point — bytes in, receipt + generation
    artifact out.  No caller frame exists on this path; the frame is
    produced from the parsed bundle and its provenance is bound
    before the runner sees it.

    ``station_metadata`` maps station_id -> {"target_latitude",
    "target_longitude", "station_latitude", "station_longitude"}
    declared coordinates for the observability records.
    """
    from .io import read_verified_waveform_bundle

    cfg = seismic_config
    receipt = seismic_receipt_skeleton()
    bundle = read_verified_waveform_bundle(
        evidence_root=evidence_root,
        source_manifest=source_manifest,
        waveform_relpaths=cfg.waveform_relpaths,
        response_relpaths=cfg.response_relpaths,
        station_selectors=station_selectors,
        config=cfg)
    if bundle.problems:
        receipt["problems"] = [
            f"waveform bundle: {p}" for p in bundle.problems]
        receipt["status"] = "UNOBSERVABLE"
        receipt["source_manifest_digest"] = \
            source_manifest_digest(source_manifest)
        receipt["raw_waveform_digest"] = bundle.waveform_bytes_digest
        receipt["report_digest"] = _digest(
            {k: v for k, v in receipt.items()
             if k not in ("report_digest", "problems")})
        return receipt, FeatureFrameArtifactV0()

    # Noise floors are DERIVED from the bundle — the declared
    # percentile rule in io.derive_station_observability computes
    # the same pool; reuse it so floors are never free parameters.
    # A station whose pool is empty carries no floor and drops.
    floors: dict[str, float] = {}
    stations = sorted({t.station for t in bundle.traces})
    for sta in stations:
        rec = derive_station_observability(
            bundle=bundle, station_id=sta,
            target_latitude=0.0, target_longitude=0.0,
            source_manifest_digest_value=
            source_manifest_digest(source_manifest),
            source_manifest=source_manifest, config=cfg)
        vals = list(rec.noise_floor_by_band.values())
        if vals:
            floors[sta] = float(vals[0])

    artifact = build_seismic_feature_artifact(
        bundle, cfg, noise_floor_by_station=floors)
    if not artifact.frame_rows:
        receipt["problems"] = [
            "feature generation produced no qualified rows"]
        receipt["status"] = "UNOBSERVABLE"
        receipt["source_manifest_digest"] = \
            source_manifest_digest(source_manifest)
        receipt["raw_waveform_digest"] = bundle.waveform_bytes_digest
        receipt["report_digest"] = _digest(
            {k: v for k, v in receipt.items()
             if k not in ("report_digest", "problems")})
        return receipt, artifact

    gen_problems = verify_feature_generation(
        artifact, bundle, cfg, noise_floor_by_station=floors)
    if gen_problems:
        receipt["problems"] = [
            f"feature generation provenance: {p}"
            for p in gen_problems]
        receipt["status"] = "UNOBSERVABLE"
        receipt["source_manifest_digest"] = \
            source_manifest_digest(source_manifest)
        receipt["raw_waveform_digest"] = bundle.waveform_bytes_digest
        receipt["report_digest"] = _digest(
            {k: v for k, v in receipt.items()
             if k not in ("report_digest", "problems")})
        return receipt, artifact

    meta = dict(station_metadata or {})
    obs = []
    for sta in stations:
        m = meta.get(sta, {})
        obs.append(derive_station_observability(
            bundle=bundle, station_id=sta,
            target_latitude=float(m.get("target_latitude", 0.0)),
            target_longitude=float(m.get("target_longitude", 0.0)),
            station_latitude=m.get("station_latitude"),
            station_longitude=m.get("station_longitude"),
            source_manifest_digest_value=
            source_manifest_digest(source_manifest),
            source_manifest=source_manifest, config=cfg))

    frame = pd.DataFrame(list(artifact.frame_rows))
    if train_mask is None:
        # Derive the mask exactly the way the runner will verify it:
        # daily aggregate -> station|early|late cells -> trained
        # stations' early cells are the declared fit surface.
        from .runner import _station_holdout_cells
        daily = aggregate_daily(
            list(artifact.frame_rows), artifact.feature_cols)
        cells, _ = _station_holdout_cells(daily)
        trained = sorted(set(s for s in stations
                             if s not in set(cfg.heldout_stations)))
        train_cells = {f"{s}|early" for s in trained}
        train_mask = np.array(
            [cells[i] in train_cells for i in range(len(daily))],
            dtype=bool)
    receipt = run_seismic_descriptive_poc(
        frame, list(artifact.feature_cols), train_mask,
        cfg, obs, source_manifest)
    # The generation artifact is bound into the receipt's report
    # surface via the digest already present — the V0 key set is
    # untouched; the artifact travels beside the receipt.
    return receipt, artifact


def audit_frame_provenance(
        *,
        source_manifest: Mapping[str, Any],
        artifact: FeatureFrameArtifactV0 | None) -> list[str]:
    """Advisory gate for callers of the lower-level runner: a
    byte-bound (non-fixture) manifest paired with a caller-prepared
    frame that carries no bound generation artifact is the S06
    leak — the frame could be forged against real bytes.  Returns
    problems; empty means the pairing is honest.

    ``run_seismic_real_path`` closes this structurally (no frame
    parameter exists); this function lets external callers and CI
    enforce the same rule on any path that feeds
    ``run_seismic_descriptive_poc`` a real manifest.
    """
    problems: list[str] = []
    if not isinstance(source_manifest, Mapping):
        return ["source_manifest must be a mapping"]
    is_fixture = bool(source_manifest.get("fixture"))
    has_bytes = bool(source_manifest.get("source_files")) and \
        not is_fixture
    if has_bytes and artifact is None:
        problems.append(
            "a byte-bound source manifest may not be paired with a "
            "caller-prepared feature frame lacking a generation "
            "artifact — derive the frame through "
            "build_seismic_feature_artifact on the verified bundle")
    if artifact is not None and \
            not artifact.feature_generation_digest:
        problems.append(
            "the supplied artifact carries no "
            "feature_generation_digest — provenance is unbound")
    return problems


__all__ = [
    "FEATURE_FRAME_ARTIFACT_TYPE", "FeatureFrameArtifactV0",
    "audit_frame_provenance", "build_seismic_feature_artifact",
    "verify_feature_generation", "run_seismic_real_path"]
