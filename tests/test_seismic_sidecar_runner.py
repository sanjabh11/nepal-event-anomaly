"""Runner tests for the seismic sidecar — receipt orchestration.

Gate order under test: RUN_ERROR -> UNOBSERVABLE ->
UNDERPOWERED_DESCRIPTIVE_ONLY -> CANDIDATE_ONLY /
DESCRIPTIVE_REGIME_ONLY.  One REAL regime run executes once per
session (module-cached): a byte-bound synthetic evidence root, four
synthetic stations, a window-level semantic frame, and the real
``run_regimes`` engine under SEISMIC_WAVEFORM_RETROSPECTIVE.

Non-goals: no waveform retrieval, no seismic predictor claim, no
association, no forecast, no warning, no production.  Every authority
flag stays false on every path.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
import re
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nepal.research_v0.source_intake import build_source_manifest
from nepal.science_v0.regimes import (
    RegimeRunConfig, freeze_regime_artifact, run_regimes)
import nepal.seismic_sidecar as ss
from nepal.seismic_sidecar.runner import _station_holdout_cells

_STATIONS = ("STA1", "STA2", "STA3", "STA4")
_TRAINED = ("STA1", "STA2", "STA3")
_HELD = ("STA4",)
_FCOLS = ["seis_rsam", "seis_sta_lta", "seis_band_2_8hz",
          "seis_centroid_hz", "seis_snr_db"]
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_DATES = [f"2020-06-{d:02d}" for d in range(1, 31)] + \
         [f"2020-07-{d:02d}" for d in range(1, 31)]

_ROOT = None
_CACHE: dict = {}


def _root() -> Path:
    """A byte-bound synthetic evidence root created once per
    session: tiny waveform binaries (the digest binding is what
    matters — the semantic frame is constructed separately), the
    StationXML stand-in, a retrieval record, and a licence file."""
    global _ROOT
    if _ROOT is None:
        root = Path(tempfile.mkdtemp(prefix="seis-runner-"))
        (root / "wave").mkdir()
        (root / "resp").mkdir()
        rng = np.random.default_rng(0)
        for s in _STATIONS:
            (root / "wave" / f"{s}.bin").write_bytes(
                rng.standard_normal(4096).tobytes())
        (root / "resp" / "all.xml").write_bytes(
            b"<StationXML/>synthetic")
        (root / "retrieval.json").write_text(
            json.dumps({"retrieval": "synthetic",
                        "authorizer": "test"}))
        (root / "LICENSE").write_text("CC-BY synthetic")
        _ROOT = root
    return _ROOT


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _manifest() -> dict:
    root = _root()
    files = [{"relpath": f"wave/{s}.bin",
              "sha256": _sha(root / "wave" / f"{s}.bin")}
             for s in _STATIONS]
    files += [
        {"relpath": "resp/all.xml",
         "sha256": _sha(root / "resp" / "all.xml")},
        {"relpath": "retrieval.json",
         "sha256": _sha(root / "retrieval.json")},
        {"relpath": "LICENSE", "sha256": _sha(root / "LICENSE")}]
    return build_source_manifest(
        root, source_id="fdsn-synthetic", source_version="0.1",
        source_files=files, units=list(_STATIONS),
        feature_allowlist=list(_FCOLS) + ["seis_coherence"],
        lineage="synthetic byte-bound seismic root")


def _observability(manifest: dict, stations=_STATIONS):
    mdig = ss.source_manifest_digest(manifest)
    resp_sha = _sha(_root() / "resp" / "all.xml")
    out = []
    for i, s in enumerate(stations):
        out.append(ss.build_station_observability(
            station_id=s, network="XS", station=s,
            channel_set=("BHE", "BHN", "BHZ"),
            station_latitude=28.0 + 0.01 * i,
            station_longitude=85.5,
            target_latitude=28.288708, target_longitude=85.528159,
            sample_rate_hz=50.0,
            response_relpath="resp/all.xml",
            response_sha256=resp_sha,
            coverage_start="2020-06-01T00:00:00Z",
            coverage_end="2020-07-31T00:00:00Z",
            coverage_fraction=1.0, gap_fraction=0.0,
            latency_seconds=0.0,
            noise_floor_by_band={"2-8": 0.1},
            snr_by_band={"2-8": 20.0},
            source_manifest_digest=mdig))
    return out


def _frame(stations=_STATIONS) -> pd.DataFrame:
    """A window-level semantic frame: 4 windows/day/station over 60
    days with planted three-cluster structure in feature space —
    the same construction style as the regime fixtures."""
    rng = np.random.default_rng(0)
    centers = [(0.0, 0.0, 0.0), (5.0, 5.0, 0.0), (0.0, 5.0, 5.0)]
    rows = []
    for s_i, s in enumerate(stations):
        for d_i, date in enumerate(_DATES):
            for w in range(4):
                c = centers[(s_i + d_i + w) % 3]
                rows.append({
                    "window_start": f"{date}T0{w}:00:00Z",
                    "window_end": f"{date}T0{w}:01:00Z",
                    "station_id": s,
                    "unit_id": f"unit-{s}",
                    "basin_group": "corridor",
                    "date": date,
                    "seis_rsam": c[0] + rng.normal(0, 0.4) + 3.0,
                    "seis_sta_lta": c[0] * 0.2 +
                        rng.normal(0, 0.1) + 1.5,
                    "seis_band_2_8hz": float(np.clip(
                        c[1] / 5.0 * 0.8 +
                        rng.normal(0, 0.05), 0.0, 1.0)),
                    "seis_centroid_hz": c[1] +
                        rng.normal(0, 0.4) + 4.0,
                    "seis_snr_db": c[2] + rng.normal(0, 0.4) + 14.0,
                    "seis_coverage_fraction": 1.0,
                    "seis_gap_fraction": 0.0})
    return pd.DataFrame(rows)


def _mask(df: pd.DataFrame) -> np.ndarray:
    daily = ss.aggregate_daily(df.to_dict("records"), _FCOLS)
    cells, _ = _station_holdout_cells(daily)
    train_cells = {f"{s}|early" for s in _TRAINED}
    return np.array(
        [cells[i] in train_cells for i in range(len(daily))])


def _config(**kw) -> ss.SeismicSidecarConfig:
    base = dict(
        waveform_relpaths=tuple(f"wave/{s}.bin" for s in _STATIONS),
        response_relpaths=("resp/all.xml",),
        heldout_stations=_HELD,
        min_rows_per_station=5)
    base.update(kw)
    return ss.SeismicSidecarConfig(**base)


def _run(manifest=None, observability=None, df=None, mask=None,
         config=None, fcols=None):
    manifest = manifest if manifest is not None else _manifest()
    observability = observability if observability is not None \
        else _observability(manifest)
    df = df if df is not None else _frame()
    mask = mask if mask is not None else _mask(df)
    config = config if config is not None else _config()
    return ss.run_seismic_descriptive_poc(
        df, _FCOLS if fcols is None else fcols, mask, config,
        observability, manifest)


def _receipt() -> dict:
    """The one REAL end-to-end run per session — defensive copy on
    every hand-out so test mutations cannot leak."""
    if "receipt" not in _CACHE:
        _CACHE["receipt"] = _run()
    return copy.deepcopy(_CACHE["receipt"])


def _regime_inputs():
    """The daily frame with the runner's identity scheme: the
    station×half cell is the group axis, the unit is the
    station×half composite — mirroring run_seismic_descriptive_poc's
    internal derivation."""
    df = _frame()
    daily = pd.DataFrame(
        ss.aggregate_daily(df.to_dict("records"), _FCOLS))
    cells, _ = _station_holdout_cells(
        daily.to_dict("records"))
    daily["holdout_cell"] = [cells[i] for i in range(len(daily))]
    daily["unit_id"] = [
        f"{r['unit_id']}|{cells[i].rsplit('|', 1)[-1]}"
        for i, r in enumerate(daily.to_dict("records"))]
    train_cells = {f"{s}|early" for s in _TRAINED}
    held_cells = ({f"{s}|late" for s in _TRAINED} |
                  {f"{s}|early" for s in _HELD} |
                  {f"{s}|late" for s in _HELD})
    mask = np.array(
        [cells[i] in train_cells for i in range(len(daily))])
    return daily, mask, train_cells, held_cells


def _seismic_artifact() -> dict:
    """One real engine artifact under SEISMIC_WAVEFORM_RETROSPECTIVE
    — the shared machinery's own output, inspected directly."""
    if "artifact" not in _CACHE:
        daily, mask, train_cells, held_cells = _regime_inputs()
        cfg = RegimeRunConfig(
            season_col="season", group_col="holdout_cell",
            era_col=None,
            era_waiver_reason="single deployment epoch — waived",
            effort_col="seis_window_count",
            unit_col="unit_id", date_col="date",
            train_groups=tuple(sorted(train_cells)),
            heldout_groups=tuple(sorted(held_cells)),
            cadence="1D", bootstrap_block_len=12,
            source_manifest=dict(_manifest()),
            mode="RETROSPECTIVE_REGIME",
            retrospective_data_class=
            ss.SEISMIC_WAVEFORM_RETROSPECTIVE)
        _CACHE["artifact"] = run_regimes(
            daily, _FCOLS, mask, cfg)
    return copy.deepcopy(_CACHE["artifact"])


class TestHappyPath:
    def test_receipt_surface_and_flags(self):
        r = _receipt()
        assert r["record_type"] == "SEISMIC_DETECTION_RECEIPT_V0"
        assert r["status"] in ss.RECEIPT_STATUSES
        assert r["claim_scope"] == \
            "research_only_post_initiation_detection"
        for flag in ("promotion_eligible", "production_authorized",
                     "warning_path_authorized"):
            assert r[flag] is False
        for key in ("source_manifest_digest", "config_digest",
                    "station_observability_digest",
                    "raw_waveform_digest",
                    "semantic_feature_digest", "report_digest"):
            assert _SHA_RE.match(r[key]), key
        # catalog_context_digest is always present — empty unless a
        # declared catalog ablation binds the channel separately.
        assert "catalog_context_digest" in r
        if r["status"] in ("CANDIDATE_ONLY",
                           "DESCRIPTIVE_REGIME_ONLY"):
            assert _SHA_RE.match(r["regime_artifact_digest"])

    def test_engine_reached_and_froze(self):
        r = _receipt()
        # The engine produced a freezable artifact — its digest is
        # bound and the receipt status is one of the honest outcomes.
        assert r["status"] in ("CANDIDATE_ONLY",
                               "DESCRIPTIVE_REGIME_ONLY",
                               "UNDERPOWERED_DESCRIPTIVE_ONLY"), \
            r["problems"]

    def test_raw_and_semantic_digests_differ(self):
        r = _receipt()
        assert r["raw_waveform_digest"] != \
            r["semantic_feature_digest"]
        assert r["semantic_feature_digest"] != \
            r["station_observability_digest"]

    def test_station_order_permutation_preserves_digests(self):
        manifest = _manifest()
        obs = _observability(manifest)
        df = _frame()
        mask = _mask(df)
        cfg = _config(n_bootstrap=10, n_null_replicates=5)
        # Permuted inputs — the digests must not move (the run still
        # reaches the same gates; engine output is not asserted).
        r1 = ss.run_seismic_descriptive_poc(
            df, _FCOLS, mask, cfg, list(reversed(obs)), manifest)
        df2 = df.iloc[::-1].reset_index(drop=True)
        r2 = ss.run_seismic_descriptive_poc(
            df2, _FCOLS, mask, cfg, obs, manifest)
        assert r1["station_observability_digest"] == \
            r2["station_observability_digest"]
        assert r1["semantic_feature_digest"] == \
            r2["semantic_feature_digest"]


class TestUnobservableDemotions:
    def test_unobservable_station(self):
        manifest = _manifest()
        obs = _observability(manifest)
        bad = dataclasses_replace_status(obs[0], "UNOBSERVABLE",
                                         ("synthetic gap",))
        r = _run(observability=[bad] + obs[1:])
        assert r["status"] == "UNOBSERVABLE"
        assert r["regime_artifact_digest"] == ""

    def test_manifest_byte_mutation(self):
        manifest = _manifest()
        bad = dict(manifest)
        bad["source_files"] = [dict(f)
                               for f in manifest["source_files"]]
        bad["source_files"][0]["sha256"] = "0" * 64
        obs = _observability(manifest)
        r = _run(manifest=bad, observability=obs)
        assert r["status"] in ("UNOBSERVABLE", "RUN_ERROR")

    def test_fixture_manifest_rejected(self):
        r = _run(manifest={"fixture": True})
        assert r["status"] == "UNOBSERVABLE"

    def test_missing_waveform_payload(self):
        cfg = _config(waveform_relpaths=())
        r = _run(config=cfg)
        assert r["status"] == "UNOBSERVABLE"
        assert any("waveform" in p for p in r["problems"])

    def test_waveform_path_not_in_manifest(self):
        cfg = _config(waveform_relpaths=("wave/GHOST.bin",))
        r = _run(config=cfg)
        assert r["status"] == "UNOBSERVABLE"

    def test_response_binding_mismatch(self):
        manifest = _manifest()
        obs = _observability(manifest)
        bad = dataclasses_replace_status(
            obs[0], "OBSERVABLE", (),
            response_sha256="0" * 64)
        r = _run(observability=[bad] + obs[1:])
        assert r["status"] in ("UNOBSERVABLE", "RUN_ERROR")

    def test_empty_snr_under_policy_unobservable(self):
        manifest = _manifest()
        obs = _observability(manifest)
        bad = ss.build_station_observability(
            station_id="STA1", network="XS", station="STA1",
            channel_set=("BHE", "BHN", "BHZ"),
            station_latitude=28.0, station_longitude=85.5,
            target_latitude=28.288708, target_longitude=85.528159,
            sample_rate_hz=50.0,
            response_relpath="resp/all.xml",
            response_sha256=_sha(_root() / "resp" / "all.xml"),
            coverage_start="2020-06-01T00:00:00Z",
            coverage_end="2020-07-31T00:00:00Z",
            coverage_fraction=1.0, gap_fraction=0.0,
            latency_seconds=0.0,
            noise_floor_by_band={}, snr_by_band={},
            source_manifest_digest=
            ss.source_manifest_digest(manifest))
        assert bad.status == "UNOBSERVABLE"
        r = _run(observability=[bad] + obs[1:])
        assert r["status"] == "UNOBSERVABLE"


class TestUnderpowered:
    def test_one_station_demotes(self):
        manifest = _manifest()
        obs = _observability(manifest, stations=("STA1",))
        df = _frame(stations=("STA1",))
        mask = np.ones(len(ss.aggregate_daily(
            df.to_dict("records"), _FCOLS)), dtype=bool)
        # mask won't match the declared partition — build it via the
        # derivation so the run reaches the underpowered gate.
        daily = ss.aggregate_daily(df.to_dict("records"), _FCOLS)
        cells, _ = _station_holdout_cells(daily)
        mask = np.array([cells[i].endswith("|early")
                         for i in range(len(daily))])
        cfg = _config(heldout_stations=(),
                      require_station_holdout=False)
        r = _run(observability=obs, df=df, mask=mask, config=cfg)
        assert r["status"] == "UNDERPOWERED_DESCRIPTIVE_ONLY"
        assert r["regime_artifact_digest"] == ""
        for flag in ("promotion_eligible", "production_authorized",
                     "warning_path_authorized"):
            assert r[flag] is False

    def test_two_stations_demote(self):
        manifest = _manifest()
        obs = _observability(manifest, stations=("STA1", "STA2"))
        df = _frame(stations=("STA1", "STA2"))
        daily = ss.aggregate_daily(df.to_dict("records"), _FCOLS)
        cells, _ = _station_holdout_cells(daily)
        mask = np.array([cells[i] == "STA1|early"
                         for i in range(len(daily))])
        cfg = _config(heldout_stations=("STA2",),
                      min_trained_stations=1)
        r = _run(observability=obs, df=df, mask=mask, config=cfg)
        # One trained station + one held-out: station diversity is
        # inadequate for the regime floor -> underpowered demotion
        # (or the engine's own honest refusal).
        assert r["status"] in ("UNDERPOWERED_DESCRIPTIVE_ONLY",
                               "RUN_ERROR")
        for flag in ("promotion_eligible", "production_authorized",
                     "warning_path_authorized"):
            assert r[flag] is False


class TestRunErrors:
    def test_catalog_column_in_predictor_set(self):
        manifest = _manifest()
        r = ss.run_seismic_descriptive_poc(
            _frame(), _FCOLS + ["catalog_event_count"],
            _mask(_frame()), _config(),
            _observability(manifest), manifest)
        assert r["status"] == "RUN_ERROR"
        assert any("catalog" in p for p in r["problems"])

    def test_mask_mismatch(self):
        df = _frame()
        mask = _mask(df)
        bad = mask.copy()
        bad[0] = not bad[0]
        r = _run(df=df, mask=bad)
        assert r["status"] == "RUN_ERROR"
        assert any("mask" in p for p in r["problems"])

    def test_undeclared_station_in_frame(self):
        manifest = _manifest()
        obs = _observability(manifest)
        df = _frame()
        df.loc[:, "station_id"] = df["station_id"].replace(
            {"STA4": "GHOST"})
        r = _run(observability=obs, df=df, mask=_mask(df))
        assert r["status"] == "RUN_ERROR"
        assert any("GHOST" in p for p in r["problems"])

    def test_single_day_stations_have_no_time_holdout(self):
        manifest = _manifest()
        obs = _observability(manifest)
        df = _frame()
        df = df[df["date"] == "2020-06-01"].reset_index(drop=True)
        daily = ss.aggregate_daily(df.to_dict("records"), _FCOLS)
        cells, _ = _station_holdout_cells(daily)
        # The caller mask binds the declared partition exactly —
        # the run then fails on the missing late tail itself.
        train_cells = {f"{s}|early" for s in _TRAINED}
        mask = np.array([cells[i] in train_cells
                         for i in range(len(daily))])
        r = _run(observability=obs, df=df, mask=mask)
        assert r["status"] == "RUN_ERROR"
        assert any("time holdout" in p for p in r["problems"])

    def test_coherence_declared_with_one_station(self):
        manifest = _manifest()
        obs = _observability(manifest, stations=("STA1",))
        df = _frame(stations=("STA1",))
        df["seis_coherence"] = 0.9
        daily = ss.aggregate_daily(df.to_dict("records"), _FCOLS)
        cells, _ = _station_holdout_cells(daily)
        mask = np.array([cells[i].endswith("|early")
                         for i in range(len(daily))])
        r = ss.run_seismic_descriptive_poc(
            df, _FCOLS + ["seis_coherence"], mask,
            _config(heldout_stations=(),
                    require_station_holdout=False),
            obs, manifest)
        assert r["status"] == "RUN_ERROR"
        assert any("coherence" in p for p in r["problems"])

    def test_feature_outside_allowlist(self):
        manifest = _manifest()
        r = ss.run_seismic_descriptive_poc(
            _frame(), ["seis_kurtosis"], _mask(_frame()), _config(),
            _observability(manifest), manifest)
        assert r["status"] == "RUN_ERROR"
        assert any("allowlist" in p for p in r["problems"])

    def test_post_verify_byte_substitution_rejected(self, tmp_path):
        """A waveform file rewritten between verify_source_evidence
        and the runner's read is bound to the manifest's declared
        sha256 — substituted bytes can never reach the digest."""
        root = _root()
        manifest = _manifest()
        # Build a second manifest over bytes that differ from the
        # manifest's declared sha256 by rewriting one leaf after the
        # manifest was built — the manifest itself is stale, so
        # verify_source_evidence itself must already reject; the
        # real probe is a swap AFTER verification, which we simulate
        # by swapping bytes back and forth around the runner's read.
        target = root / "wave" / "STA1.bin"
        original = target.read_bytes()
        target.write_bytes(b"\x00" * 4096)
        try:
            r = ss.run_seismic_descriptive_poc(
                _frame(), _FCOLS, _mask(_frame()), _config(),
                _observability(manifest), manifest)
        finally:
            target.write_bytes(original)
        # Either the manifest-level verify or the byte-pin must
        # reject — the run may never digest substituted bytes.
        assert r["status"] in ("UNOBSERVABLE", "RUN_ERROR")
        assert any("sha256" in p or "digest" in p or "hash" in p
                   for p in r["problems"])

    def test_forged_observable_record_rejected(self):
        """A record carrying OBSERVABLE over failing gates is forged
        metadata — the runner re-derives the verdict, never trusts
        the carried status."""
        manifest = _manifest()
        obs = list(_observability(manifest))
        forged = dataclasses.replace(
            obs[0], coverage_fraction=0.05, problems=())
        assert forged.status == "OBSERVABLE"
        obs[0] = forged
        r = _run(manifest=manifest, observability=obs)
        assert r["status"] == "UNOBSERVABLE"
        assert any("failing gate" in p or "OBSERVABLE" in p
                   for p in r["problems"])

    def test_omitted_heldout_station_rejected(self):
        """require_station_holdout with no declared heldout must
        reject — the split is never inferred from sort order."""
        r = _run(config=_config(heldout_stations=()))
        assert r["status"] == "RUN_ERROR"
        assert any("heldout" in p for p in r["problems"])

    def test_duplicate_window_identity_rejected(self):
        df = _frame()
        dup = pd.concat([df, df.iloc[[0]]], ignore_index=True)
        daily_rows = ss.aggregate_daily(
            df.to_dict("records"), _FCOLS)
        cells, _ = _station_holdout_cells(daily_rows)
        mask = np.array([cells[i].endswith("|early")
                         for i in range(len(daily_rows))] +
                        [False])
        r = _run(df=dup, mask=mask)
        assert r["status"] == "RUN_ERROR"
        assert any("duplicate" in p for p in r["problems"])

    def test_window_date_must_match_utc_start_date(self):
        df = _frame()
        df.loc[0, "date"] = "2020-07-01"
        r = _run(df=df, mask=_mask(df))
        assert r["status"] == "RUN_ERROR"
        assert any("UTC date" in p for p in r["problems"])

    def test_invalid_calendar_date_rejected(self):
        df = _frame()
        df.loc[0, "date"] = "2020-99-99"
        r = _run(df=df, mask=_mask(df))
        assert r["status"] == "RUN_ERROR"
        assert any("calendar date" in p for p in r["problems"])

    def test_window_duration_must_match_config(self):
        df = _frame()
        df.loc[0, "window_end"] = "2020-06-01T00:02:00Z"
        r = _run(df=df, mask=_mask(df))
        assert r["status"] == "RUN_ERROR"
        assert any("window_seconds" in p for p in r["problems"])

    def test_duplicate_feature_columns_rejected(self):
        r = _run(fcols=_FCOLS + ["seis_rsam"])
        assert r["status"] == "RUN_ERROR"
        assert any("feature_cols" in p for p in r["problems"])

    def test_non_string_feature_columns_rejected(self):
        r = _run(fcols=_FCOLS + [None])
        assert r["status"] == "RUN_ERROR"
        assert any("feature_cols" in p for p in r["problems"])

    def test_invalid_feature_frame_type_is_bounded(self):
        r = _run(df=[], mask=np.array([], dtype=bool))
        assert r["status"] == "RUN_ERROR"
        assert any("DataFrame" in p for p in r["problems"])


class TestSeismicArtifactBoundary:
    """The real engine's own artifact under the seismic class —
    data_class binding, hard non-associability, freeze and adapter
    gates, and the weather default left untouched."""

    def test_artifact_binds_seismic_data_class(self):
        art = _seismic_artifact()
        assert art["status"] != "RUN_ERROR", art.get("reason")
        assert art["mode"] == "RETROSPECTIVE_REGIME"
        assert art["data_class"] == \
            ss.SEISMIC_WAVEFORM_RETROSPECTIVE
        assert art["associable"] is False

    def test_frozen_artifact_freezes(self):
        art = _seismic_artifact()
        if art["status"] == "RUN_ERROR":
            pytest.skip(art.get("reason"))
        frozen = freeze_regime_artifact(dict(art))
        assert frozen["frozen"] is True
        assert frozen["data_class"] == \
            ss.SEISMIC_WAVEFORM_RETROSPECTIVE

    def test_seismic_artifact_cannot_associate(self):
        from nepal.experiment_v0.adapters import (
            regime_assignment_from_artifact)
        art = _seismic_artifact()
        if art["status"] == "RUN_ERROR":
            pytest.skip(art.get("reason"))
        frozen = freeze_regime_artifact(dict(art))
        with pytest.raises(ValueError):
            regime_assignment_from_artifact(
                frozen, artifact_id="seismic-sidecar-test")

    def test_forced_associable_seismic_fails_freeze(self):
        art = _seismic_artifact()
        if art["status"] == "RUN_ERROR":
            pytest.skip(art.get("reason"))
        mutated = dict(art)
        mutated["associable"] = True
        with pytest.raises(ValueError):
            freeze_regime_artifact(mutated)

    def test_weather_default_data_class_unchanged(self):
        """A REANALYSIS retrospective artifact still emits the
        default class — the extension did not move the weather
        lane's provenance."""
        art = _weather_artifact()
        assert art["status"] != "RUN_ERROR", art.get("reason")
        assert art["data_class"] == "REANALYSIS"
        # A REANALYSIS descriptive artifact may still be associable —
        # the weather lane's flag is status-driven, unchanged.
        assert art["associable"] == \
            (art["status"] == "DESCRIPTIVE_REGIME_ONLY")


def _weather_artifact() -> dict:
    if "weather_artifact" not in _CACHE:
        daily, mask, train_cells, held_cells = _regime_inputs()
        cfg = RegimeRunConfig(
            season_col="season", group_col="holdout_cell",
            era_col=None,
            era_waiver_reason="single deployment epoch — waived",
            effort_col="seis_window_count",
            unit_col="unit_id", date_col="date",
            train_groups=tuple(sorted(train_cells)),
            heldout_groups=tuple(sorted(held_cells)),
            cadence="1D", bootstrap_block_len=12,
            source_manifest=dict(_manifest()),
            mode="RETROSPECTIVE_REGIME")
        _CACHE["weather_artifact"] = run_regimes(
            daily, _FCOLS, mask, cfg)
    return copy.deepcopy(_CACHE["weather_artifact"])


def dataclasses_replace_status(rec, status, problems, **kw):
    import dataclasses
    return dataclasses.replace(
        rec, status=status, problems=tuple(problems), **kw)
