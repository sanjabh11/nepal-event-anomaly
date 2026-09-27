"""Waveform-array feature extraction for the seismic sidecar.

Pure NumPy over already-acquired, byte-verified waveform arrays — no
network I/O, no FDSN client, no ObsPy dependency.  Every function is
fail-closed: empty, non-finite, too-short, or Nyquist-violating input
raises ``ValueError``; windows below the declared coverage floor are
dropped into a ledger, never zero-filled.

Feature definitions (per one-minute window by default):

- ``seis_rsam`` — RMS of the component-averaged amplitude trace;
- ``seis_sta_lta`` — max short-term/long-term energy ratio on the
  component-averaged energy trace (declared STA/LTA lengths);
- ``seis_band_*hz`` — fraction of total window power inside each
  declared band (fractions sum to <= 1 across bands);
- ``seis_centroid_hz``/``seis_median_hz``/``seis_dominant_hz`` —
  power-weighted spectral moments;
- ``seis_entropy`` — normalized spectral entropy in [0, 1];
- ``seis_kurtosis`` — Fisher excess kurtosis of the amplitude trace;
- ``seis_crest_factor`` — peak |x| / RMS over all samples/components;
- ``seis_duration_s`` — covered duration in the window;
- ``seis_snr_db`` — 10*log10(signal power / declared noise floor^2);
- ``seis_coverage_fraction``/``seis_gap_fraction`` — declared
  coverage semantics, never inferred;
- ``seis_coherence`` — OPTIONAL mean magnitude-squared coherence,
  emitted only when two calibrated stations overlap.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

import numpy as np

from nepal.research_v0._hashing import sha256_canonical
from nepal.research_v0.policy import parse_strict_utc

from .contracts import SEISMIC_IDENTITY_COLUMNS


def _fmt_band_edge(hz: float) -> str:
    """Deterministic band-edge label: 0.5 -> '0p5', 2.0 -> '2'."""
    text = f"{float(hz):g}".replace(".", "p")
    return text


def band_feature_names(bands: Sequence[Sequence[float]]) -> tuple:
    """Declared ``seis_band_*hz`` names for the configured bands —
    names are generated from the declared band edges, never free
    text."""
    return tuple(
        f"seis_band_{_fmt_band_edge(lo)}_{_fmt_band_edge(hi)}hz"
        for lo, hi in bands)


def validate_waveform_input(samples: Any,
                           sample_rate_hz: Any,
                           *,
                           min_samples: int,
                           max_band_hz: float | None = None
                           ) -> np.ndarray:
    """Coerce and validate a waveform array to float64 (n, c) shape.

    Rejects: non-numeric input, empty arrays, wrong dimensionality,
    any NaN/inf sample, non-positive or non-finite sample rate,
    Nyquist violation when ``max_band_hz`` is declared, and arrays
    shorter than ``min_samples``.  Returns the validated float64
    array.
    """
    try:
        arr = np.asarray(samples, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"waveform samples must be numeric: {exc}")
    if arr.size == 0:
        raise ValueError("waveform array is empty — missing data is "
                         "never a zero-valued feature")
    if arr.ndim == 1:
        arr = arr.reshape(-1, 1)
    if arr.ndim != 2:
        raise ValueError(f"waveform array must be 1-D or 2-D; got "
                         f"{arr.ndim} dims")
    if not np.isfinite(arr).all():
        raise ValueError("waveform array contains NaN or infinity — "
                         "non-finite samples reject, they are never "
                         "clipped or filled")
    if isinstance(sample_rate_hz, bool) or \
            not isinstance(sample_rate_hz, (int, float)) or \
            not math.isfinite(sample_rate_hz) or sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be a positive finite "
                         "number")
    if max_band_hz is not None and \
            sample_rate_hz < 2.0 * float(max_band_hz):
        raise ValueError(
            f"sample_rate_hz {sample_rate_hz} violates Nyquist for "
            f"declared band edge {max_band_hz} Hz — the band "
            "contract cannot be evaluated")
    if arr.shape[0] < min_samples:
        raise ValueError(
            f"waveform window has {arr.shape[0]} samples; at least "
            f"{min_samples} required — a too-short window is "
            "inadmissible, never padded")
    return arr


def _moving_mean(x: np.ndarray, width: int) -> np.ndarray:
    """Causal moving mean; positions with fewer than ``width``
    trailing samples are undefined (dropped by the caller)."""
    csum = np.cumsum(np.concatenate(([0.0], x)))
    out = np.full(len(x), np.nan)
    if len(x) >= width:
        out[width - 1:] = (csum[width:] - csum[:-width]) / width
    return out


def window_features(samples: np.ndarray,
                    sample_rate_hz: float,
                    *,
                    noise_floor_rms: float,
                    sta_seconds: float,
                    lta_seconds: float,
                    bands: Sequence[Sequence[float]],
                    covered_samples: int | None = None,
                    expected_samples: int | None = None) -> dict:
    """Extract the declared feature vector for one analysis window.

    ``samples`` is the (n, c) waveform block (n samples, c channels —
    the caller supplies only covered samples or passes
    ``covered_samples`` explicitly).  ``noise_floor_rms`` is the
    declared noise floor in amplitude units — required, never
    defaulted.  ``expected_samples`` declares the full window length
    so coverage/gap are computed against the declared window, never
    inferred.  Returns the ``seis_*`` feature mapping (without
    ``seis_coherence``, which is a cross-station quantity bound by
    the runner).
    """
    fs = float(sample_rate_hz)
    if isinstance(sta_seconds, bool) or \
            not isinstance(sta_seconds, (int, float)) or \
            not math.isfinite(sta_seconds) or sta_seconds <= 0:
        raise ValueError("sta_seconds must be a positive finite "
                         "number")
    if isinstance(lta_seconds, bool) or \
            not isinstance(lta_seconds, (int, float)) or \
            not math.isfinite(lta_seconds) or lta_seconds <= 0:
        raise ValueError("lta_seconds must be a positive finite "
                         "number")
    if sta_seconds >= lta_seconds:
        raise ValueError("sta_seconds must be shorter than "
                         "lta_seconds")
    if not isinstance(noise_floor_rms, (int, float)) or \
            isinstance(noise_floor_rms, bool) or \
            not math.isfinite(noise_floor_rms) or noise_floor_rms <= 0:
        raise ValueError("noise_floor_rms must be a positive finite "
                         "number — a declared noise floor is required "
                         "for SNR")
    if not isinstance(expected_samples, int) or \
            isinstance(expected_samples, bool) or \
            expected_samples <= 0:
        raise ValueError("expected_samples must be a positive int — "
                         "the declared window length is required for "
                         "coverage semantics")
    band_edges = []
    for band in bands:
        if not isinstance(band, (list, tuple)) or len(band) != 2 or \
                not 0.0 < float(band[0]) < float(band[1]):
            raise ValueError(f"band {band!r} must be a "
                             "(low_hz, high_hz) pair with 0<low<high")
        band_edges.append((float(band[0]), float(band[1])))
    arr = validate_waveform_input(
        samples, fs,
        min_samples=max(2, int(math.ceil(lta_seconds * fs))),
        max_band_hz=max(hi for _, hi in band_edges))
    n = arr.shape[0]
    if covered_samples is None:
        covered = n
    else:
        if isinstance(covered_samples, bool) or \
                not isinstance(covered_samples, int) or \
                not 0 <= covered_samples <= expected_samples or \
                covered_samples > n:
            raise ValueError(
                "covered_samples must be an int in "
                "[0, expected_samples] and may not exceed the "
                "supplied sample count")
        covered = covered_samples
    if covered <= 0:
        raise ValueError("zero covered samples — an empty window is "
                         "inadmissible, never a zero feature")

    # Component-averaged energy and amplitude traces.  For a rotated
    # three-component record (HH1/HH2) the per-component energies are
    # still well-defined; orientation is an observability concern,
    # not a feature-time concern.
    energy = np.mean(arr ** 2, axis=1)
    amplitude = np.sqrt(energy)

    rsam = float(np.sqrt(np.mean(energy)))

    sta_n = max(1, int(round(sta_seconds * fs)))
    lta_n = max(1, int(round(lta_seconds * fs)))
    sta = _moving_mean(energy, sta_n)
    lta = _moving_mean(energy, lta_n)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = sta / lta
    ratio = ratio[np.isfinite(ratio) & (lta > 0)]
    sta_lta = float(np.max(ratio)) if ratio.size else 0.0

    # Component-averaged one-sided power spectrum (Hann window).
    window = np.hanning(n)
    power_sum = None
    for c in range(arr.shape[1]):
        xw = (arr[:, c] - arr[:, c].mean()) * window
        p = np.abs(np.fft.rfft(xw)) ** 2
        power_sum = p if power_sum is None else power_sum + p
    power = power_sum / arr.shape[1]
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    # DC is excluded from all spectral moments — a mean offset is not
    # ground motion.
    power[0] = 0.0
    total = float(power.sum())

    band_feats = {}
    for (lo, hi), name in zip(band_edges,
                            band_feature_names(band_edges)):
        mask = (freqs >= lo) & (freqs < hi)
        share = float(power[mask].sum() / total) if total > 0 else 0.0
        band_feats[name] = share

    if total > 0:
        p_norm = power / total
        centroid = float(np.sum(freqs * p_norm))
        cumsum = np.cumsum(p_norm)
        median = float(freqs[int(np.searchsorted(cumsum, 0.5))])
        dominant = float(freqs[int(np.argmax(power))])
        nz = p_norm[p_norm > 0]
        entropy = float(-np.sum(nz * np.log2(nz)) /
                        math.log2(len(p_norm)))
    else:
        centroid = median = dominant = entropy = 0.0

    a_center = amplitude - amplitude.mean()
    var = float(np.mean(a_center ** 2))
    if var > 0:
        kurt = float(np.mean(a_center ** 4) / var ** 2 - 3.0)
    else:
        kurt = 0.0
    crest = float(np.max(np.abs(arr)) / rsam) if rsam > 0 else 0.0

    if rsam <= 0:
        raise ValueError("zero-energy window — silence is not a "
                         "feature value")
    snr_db = float(10.0 * math.log10((rsam / noise_floor_rms) ** 2))
    coverage = covered / expected_samples
    duration = covered / fs

    feats = {
        "seis_rsam": rsam,
        "seis_sta_lta": sta_lta,
        "seis_centroid_hz": centroid,
        "seis_median_hz": median,
        "seis_dominant_hz": dominant,
        "seis_entropy": entropy,
        "seis_kurtosis": kurt,
        "seis_crest_factor": crest,
        "seis_duration_s": float(duration),
        "seis_snr_db": snr_db,
        "seis_coverage_fraction": float(min(1.0, coverage)),
        "seis_gap_fraction": float(max(0.0, 1.0 - coverage)),
    }
    feats.update(band_feats)
    for name, value in feats.items():
        if isinstance(value, float) and math.isnan(value):
            raise ValueError(f"feature {name} evaluated to NaN — "
                             "the window is inadmissible")
    return feats


def cross_station_coherence(samples_a: np.ndarray,
                            samples_b: np.ndarray,
                            sample_rate_hz: float,
                            bands: Sequence[Sequence[float]]) -> float:
    """Mean magnitude-squared coherence between two stations'
    component-averaged amplitude traces over the declared bands.

    Both inputs are validated identically — equal-length requirement
    reflects synchronous coverage; unequal overlap is inadmissible
    rather than truncated.
    """
    fs = float(sample_rate_hz)
    a = validate_waveform_input(samples_a, fs, min_samples=2)
    b = validate_waveform_input(samples_b, fs, min_samples=2)
    if a.shape[0] != b.shape[0]:
        raise ValueError(
            "cross-station coherence requires synchronous equal-"
            "length windows — overlapping coverage is a declared "
            "precondition")
    n = a.shape[0]
    xa = np.sqrt(np.mean(a ** 2, axis=1))
    xb = np.sqrt(np.mean(b ** 2, axis=1))
    # Welch-style segment averaging: single-FFT coherence is
    # degenerate — |A*conj(B)|^2/(|A|^2|B|^2) is identically 1.0 per
    # bin regardless of correlation.  Cross- and auto-spectra must be
    # averaged over segments first; independent signals then converge
    # to ~1/n_segments while coherent pairs stay near 1.
    n_segments = min(8, max(1, n // 128))
    seg = n // n_segments
    if seg < 1:
        raise ValueError("window too short for segmented coherence")
    xa = xa[:n_segments * seg].reshape(n_segments, seg)
    xb = xb[:n_segments * seg].reshape(n_segments, seg)
    w = np.hanning(seg)
    A = np.fft.rfft((xa - xa.mean(axis=1, keepdims=True)) * w,
                    axis=1)
    B = np.fft.rfft((xb - xb.mean(axis=1, keepdims=True)) * w,
                    axis=1)
    Pxy = (A * np.conj(B)).mean(axis=0)
    Pa = (np.abs(A) ** 2).mean(axis=0)
    Pb = (np.abs(B) ** 2).mean(axis=0)
    freqs = np.fft.rfftfreq(seg, d=1.0 / fs)
    denom = Pa * Pb
    with np.errstate(divide="ignore", invalid="ignore"):
        msc = np.where(denom > 0,
                       np.abs(Pxy) ** 2 / denom, np.nan)
    values = []
    for lo, hi in bands:
        mask = (freqs >= lo) & (freqs < hi) & np.isfinite(msc)
        if mask.any():
            values.append(float(np.mean(msc[mask])))
    if not values:
        raise ValueError("no finite coherence estimate inside the "
                         "declared bands")
    return float(min(1.0, max(0.0, np.mean(values))))


def season_of_date(iso_date: str) -> str:
    """Deterministic meteorological season for an ISO date —
    DJF/MAM/JJA/SON by calendar month (declared rule, never
    locale-dependent)."""
    month = int(str(iso_date)[5:7])
    if month in (12, 1, 2):
        return "DJF"
    if month in (3, 4, 5):
        return "MAM"
    if month in (6, 7, 8):
        return "JJA"
    return "SON"


def build_window_rows(
        *,
        station_id: str,
        unit_id: str,
        basin_group: str,
        samples: np.ndarray,
        sample_rate_hz: float,
        epoch_start_iso: str,
        window_seconds: int,
        noise_floor_rms: float,
        sta_seconds: float,
        lta_seconds: float,
        bands: Sequence[Sequence[float]],
        min_coverage_fraction: float,
        present_mask: np.ndarray | None = None,
        coherence_by_window: Mapping[int, float] | None = None
        ) -> tuple[list, list]:
    """Slice a continuous multi-component record into declared
    windows and emit feature rows plus a dropped-window ledger.

    ``epoch_start_iso`` is the explicit-UTC epoch of sample 0.
    ``present_mask`` (bool per sample) marks covered samples; absent
    samples are never zero-filled — a window below
    ``min_coverage_fraction`` is dropped into the returned ledger
    with its identity and reason, not into the feature frame.
    ``coherence_by_window`` maps a window index to a precomputed
    cross-station coherence value; it is only attached where the
    caller declared one (>= 2 calibrated overlapping stations).
    """
    epoch = parse_strict_utc(epoch_start_iso)
    if epoch is None:
        raise ValueError("epoch_start_iso must be an explicit-UTC "
                         "timestamp")
    arr = validate_waveform_input(
        samples, float(sample_rate_hz), min_samples=1)
    n_total = arr.shape[0]
    if present_mask is not None:
        mask = np.asarray(present_mask, dtype=bool)
        if mask.shape != (n_total,):
            raise ValueError("present_mask must be a bool array of "
                             "length n_samples")
    else:
        mask = np.ones(n_total, dtype=bool)
    win = int(window_seconds * float(sample_rate_hz))
    if win <= 0:
        raise ValueError("window_seconds * sample_rate_hz must be a "
                         "positive sample count")
    coherence = dict(coherence_by_window or {})
    epoch_dt = datetime.fromtimestamp(epoch, tz=timezone.utc)

    rows: list[dict] = []
    dropped: list[dict] = []
    n_windows = n_total // win
    for i in range(n_windows):
        lo, hi = i * win, (i + 1) * win
        wmask = mask[lo:hi]
        covered = int(wmask.sum())
        coverage = covered / win
        start_dt = epoch_dt + timedelta(seconds=i * window_seconds)
        end_dt = start_dt + timedelta(seconds=window_seconds)
        identity = {
            "window_start": start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "window_end": end_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "station_id": station_id,
            "unit_id": unit_id,
            "basin_group": basin_group,
            "date": start_dt.strftime("%Y-%m-%d")}
        if coverage < min_coverage_fraction:
            dropped.append({
                **identity,
                "coverage_fraction": round(coverage, 6),
                "reason": f"coverage {coverage:.4f} below declared "
                          f"minimum {min_coverage_fraction} — the "
                          "window is unobservable, never zero-filled"})
            continue
        try:
            feats = window_features(
                arr[lo:hi][wmask], sample_rate_hz,
                noise_floor_rms=noise_floor_rms,
                sta_seconds=sta_seconds, lta_seconds=lta_seconds,
                bands=bands, covered_samples=covered,
                expected_samples=win)
        except ValueError as exc:
            dropped.append({
                **identity,
                "coverage_fraction": round(coverage, 6),
                "reason": f"window rejected by feature extraction: "
                          f"{exc}"})
            continue
        row = {**identity, **feats}
        if i in coherence:
            row["seis_coherence"] = float(coherence[i])
        rows.append(row)
    return rows, dropped


def aggregate_daily(rows: Sequence[Mapping[str, Any]],
                    value_cols: Sequence[str]) -> list:
    """Aggregate window-level rows to the regime's (unit_id, date)
    grain — arithmetic mean per declared value column plus the
    declared ``seis_window_count`` effort column.  Days with zero
    qualified windows produce no row (absence, never zeros)."""
    groups: dict[tuple, dict] = {}
    for row in rows:
        key = (row["station_id"], row["unit_id"], row["basin_group"],
               row["date"])
        acc = groups.setdefault(key, {"n": 0})
        acc["n"] += 1
        for col in value_cols:
            v = row.get(col)
            if v is None:
                continue
            acc.setdefault(col, []).append(float(v))
    out = []
    for (station, unit, group, date), acc in sorted(groups.items()):
        row = {
            "station_id": station,
            "unit_id": unit,
            "basin_group": group,
            "date": date,
            "season": season_of_date(date),
            "seis_window_count": acc["n"]}
        for col in value_cols:
            vals = acc.get(col)
            row[col] = float(np.mean(vals)) if vals else float("nan")
        out.append(row)
    return out


def semantic_feature_digest(rows: Sequence[Mapping[str, Any]],
                            feature_cols: Sequence[str]) -> str:
    """Canonical digest of the semantic feature surface — station +
    window identity plus 6-decimal-rounded feature values, sorted so
    station/row order can never change the digest.  Catalog context
    and label columns are excluded by construction: only the
    declared identity and feature columns are bound."""
    material = []
    for row in rows:
        ident = [str(row[c]) for c in SEISMIC_IDENTITY_COLUMNS]
        vals = []
        for col in feature_cols:
            v = row.get(col)
            if v is None:
                vals.append(None)
            elif isinstance(v, float) and math.isnan(v):
                vals.append(None)
            elif isinstance(v, float):
                vals.append(round(v, 6))
            else:
                vals.append(v)
        material.append(ident + vals)
    material.sort()
    return sha256_canonical(material)
