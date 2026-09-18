"""Seismic descriptive PoC orchestration (sidecar runner).

``run_seismic_descriptive_poc`` composes the EXISTING machinery —
the seven-key byte-bound source manifest, ``verify_source_evidence``,
``read_evidence_file``, ``StationObservabilityV0``, ``run_regimes``,
``freeze_regime_artifact``, and the FMX ``audit_matrix`` — into the
research-only seismic receipt.

Gate order (most conservative wins):

1. RUN_ERROR — malformed inputs, partition/mask disagreement,
   catalog columns inside the predictor set, or engine failure.
2. UNOBSERVABLE — manifest byte verification fails, any station
   gate fails, no waveform payload is bound, or the manifest is a
   fixture (synthetic frames cannot carry waveform provenance).
3. UNDERPOWERED_DESCRIPTIVE_ONLY — observability passes but station
   diversity or window support cannot carry a station holdout.
4. CANDIDATE_ONLY / DESCRIPTIVE_REGIME_ONLY — the regime engine's
   own honest verdict, mapped through.

All authority flags are false on every path; the claim scope is
fixed to ``research_only_post_initiation_detection``.
"""
from __future__ import annotations

import math
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from nepal.research_v0._hashing import (
    read_evidence_file, sha256_bytes, sha256_canonical,
    verify_source_evidence)
from nepal.research_v0.policy import parse_strict_utc
from nepal.science_v0.fmx_audit import ColumnAudit, audit_matrix
from nepal.science_v0.regimes import (
    RegimeRunConfig, freeze_regime_artifact, run_regimes)

from .contracts import (
    CATALOG_CONTEXT_COLUMNS, SEISMIC_FEATURE_UNITS,
    SEISMIC_IDENTITY_COLUMNS, SEISMIC_NONBAND_FEATURES,
    SEISMIC_WAVEFORM_RETROSPECTIVE, SeismicSidecarConfig,
    StationObservabilityV0, seismic_receipt_skeleton,
    station_observability_from_dict)
from .features import (
    aggregate_daily, band_feature_names, semantic_feature_digest)
from .observability import (
    reassess_observability, source_manifest_digest)

_MANIFEST_KEYS = {"source_id", "source_digests", "units",
                  "feature_allowlist", "lineage",
                  "evidence_root", "source_files"}


def _digest(section: Any) -> str:
    return sha256_canonical(section)


def _report(receipt: dict) -> dict:
    receipt["report_digest"] = _digest(
        {k: v for k, v in receipt.items()
         if k not in ("report_digest", "problems")})
    return receipt


def _station_holdout_cells(
        daily_rows: Sequence[Mapping[str, Any]]) -> tuple:
    """Derive the declared ``holdout_cell`` partition — each
    station's date range is split at its median into ``early`` and
    ``late`` halves; ``holdout_cell = "<station>|<half>"``.

    Holding out the ``late`` cells of trained stations is the time
    holdout; holding out every cell of a station is the station
    holdout — one partition carries both, inside the existing
    group-disjointness machinery.
    """
    dates_by_station: dict[str, list] = {}
    for row in daily_rows:
        dates_by_station.setdefault(str(row["station_id"]),
                                    []).append(str(row["date"]))
    medians = {}
    for station, dates in dates_by_station.items():
        unique = sorted(set(dates))
        # Lower median: an even-length range splits into balanced
        # early/late halves — the late tail must be non-empty or the
        # declared time holdout cannot exist.
        medians[station] = unique[(len(unique) - 1) // 2]
    cells = {}
    for row_key, row in enumerate(daily_rows):
        station = str(row["station_id"])
        half = "early" if str(row["date"]) <= medians[station] \
            else "late"
        cells[row_key] = f"{station}|{half}"
    return cells, medians


def _validate_window_identities(
        rows: Sequence[Mapping[str, Any]],
        feature_cols: Sequence[str],
        expected_window_seconds: int | None = None) -> list[str]:
    """Canonical window-identity floor — enforced BEFORE the
    semantic digest so malformed identities can never be bound.

    Rejects: naive/non-UTC or inverted window timestamps, duplicate
    (station_id, window_start, window_end) identities, overlapping
    windows per station, non-canonical dates, empty identity fields,
    and non-finite feature values.  Windows are a declared tiling —
    overlap is inadmissible, never clipped.
    """
    problems: list[str] = []
    seen: set[tuple] = set()
    by_station: dict[str, list] = {}
    for i, row in enumerate(rows):
        ws = parse_strict_utc(str(row.get("window_start", "")))
        we = parse_strict_utc(str(row.get("window_end", "")))
        if ws is None or we is None:
            problems.append(
                f"row {i}: window_start/window_end must be "
                "explicit-UTC timestamps")
            continue
        if we <= ws:
            problems.append(
                f"row {i}: window_end precedes window_start — "
                "inverted window")
        station = str(row.get("station_id", ""))
        for col in ("station_id", "unit_id", "basin_group"):
            if not str(row.get(col, "")).strip():
                problems.append(f"row {i}: {col} is empty")
                break
        d = str(row.get("date", ""))
        parts = d.split("-")
        if len(d) != 10 or len(parts) != 3 or \
                not all(p.isdigit() for p in parts):
            problems.append(f"row {i}: date {d!r} is not a "
                            "canonical ISO date")
        else:
            try:
                parsed_date = datetime.strptime(d, "%Y-%m-%d").date()
            except ValueError:
                parsed_date = None
                problems.append(f"row {i}: date {d!r} is not a "
                                "valid calendar date")
            if parsed_date is not None:
                start_date = datetime.fromtimestamp(
                    ws, timezone.utc).date()
                if parsed_date != start_date:
                    problems.append(
                        f"row {i}: date {d!r} does not match "
                        "window_start UTC date")
        if expected_window_seconds is not None and \
                we - ws != float(expected_window_seconds):
            problems.append(
                f"row {i}: window duration {we - ws:g}s does not "
                f"match declared window_seconds "
                f"{expected_window_seconds}")
        ident = (station, str(row["window_start"]),
                 str(row["window_end"]))
        if ident in seen:
            problems.append(
                f"row {i}: duplicate window identity {ident} — "
                "one row per window")
        seen.add(ident)
        by_station.setdefault(station, []).append((ws, we, i))
        for col in feature_cols:
            v = row.get(col)
            if v is None or isinstance(v, bool) or \
                    not isinstance(v, (int, float)) or \
                    not math.isfinite(v):
                problems.append(
                    f"row {i}: feature {col} is missing or "
                    "non-finite — absent evidence is never a "
                    "zero-valued feature")
                break
    for station, wins in by_station.items():
        wins.sort()
        for j in range(1, len(wins)):
            if wins[j][0] < wins[j - 1][1]:
                problems.append(
                    f"station {station}: overlapping windows at "
                    f"row {wins[j][2]} — the declared tiling "
                    "forbids overlap")
                break
    return problems


def run_seismic_descriptive_poc(
        feature_frame,
        feature_cols: Sequence[str],
        train_mask,
        seismic_config,
        station_observability,
        source_manifest) -> dict:
    """Run the research-only seismic sidecar and emit the receipt.

    ``feature_frame`` carries the window-level semantic frame
    (identity columns + declared ``seis_*`` features).  The runner
    aggregates to the regime's (unit_id, date) grain by the declared
    rule, derives the ``holdout_cell`` partition (station × temporal
    half), and delegates to ``run_regimes`` under
    ``SEISMIC_WAVEFORM_RETROSPECTIVE``.
    """
    problems: list[str] = []
    receipt = seismic_receipt_skeleton()
    receipt["problems"] = problems

    # ---- config floor -------------------------------------------------
    if not isinstance(seismic_config, SeismicSidecarConfig):
        problems.append("seismic_config must be a "
                        "SeismicSidecarConfig")
        return _report(receipt)
    cfg_problems = seismic_config.validate()
    if cfg_problems:
        problems.extend(f"seismic_config: {p}"
                        for p in cfg_problems)
        return _report(receipt)
    cfg = seismic_config
    if not isinstance(feature_frame, pd.DataFrame):
        problems.append("feature_frame must be a pandas DataFrame")
        return _report(receipt)
    if not isinstance(feature_cols, (list, tuple)) or \
            any(not isinstance(c, str) or not c.strip()
                for c in feature_cols):
        problems.append("feature_cols must be a sequence of non-empty "
                        "strings")
        return _report(receipt)
    fcols = list(feature_cols)
    if len(set(fcols)) != len(fcols):
        problems.append("feature_cols must contain unique names")
        return _report(receipt)
    if not fcols:
        problems.append("feature_cols must be non-empty")
        return _report(receipt)
    # SEISMIC-02: the entire declared configuration — role paths,
    # thresholds, bands, seeds, holdout and catalog settings — is
    # bound into the receipt; any mutation moves the digest.
    receipt["config_digest"] = _digest(asdict(cfg))

    # ---- observability records ---------------------------------------
    obs_in = station_observability
    if isinstance(obs_in, (StationObservabilityV0, Mapping)):
        obs_in = [obs_in]
    if not isinstance(obs_in, (list, tuple)) or not obs_in:
        problems.append("station_observability must be a non-empty "
                        "sequence of StationObservabilityV0 records "
                        "or serialized mappings")
        return _report(receipt)
    records: list[StationObservabilityV0] = []
    for i, item in enumerate(obs_in):
        if isinstance(item, StationObservabilityV0):
            rec = item
        else:
            try:
                rec = station_observability_from_dict(item)
            except (TypeError, ValueError) as exc:
                problems.append(
                    f"station_observability[{i}] does not "
                    f"deserialize: {exc}")
                return _report(receipt)
        rec_problems = rec.validate()
        if rec_problems:
            problems.extend(
                f"station_observability[{i}] {rec.station_id}: {p}"
                for p in rec_problems)
            return _report(receipt)
        # SEISMIC-03: status is never trusted — the gate verdict is
        # re-derived from the record's own declared fields under
        # THIS config.  A carried OBSERVABLE over a failing or
        # inconsistent gate is forged metadata and rejects.
        gate_problems = reassess_observability(rec, cfg)
        if rec.status == "OBSERVABLE" and gate_problems:
            problems.extend(
                f"station_observability[{i}] {rec.station_id}: "
                f"carries OBSERVABLE over a failing gate — {p}"
                for p in gate_problems)
            receipt["status"] = "UNOBSERVABLE"
            return _report(receipt)
        records.append(rec)
    station_ids = [r.station_id for r in records]
    if len(set(station_ids)) != len(station_ids):
        problems.append("duplicate station_id across observability "
                        "records — one record per station")
        return _report(receipt)

    # ---- source manifest: exact seven keys, non-fixture --------------
    if not isinstance(source_manifest, Mapping):
        problems.append("source_manifest must be a mapping")
        return _report(receipt)
    if set(source_manifest) != _MANIFEST_KEYS:
        problems.append(
            "source_manifest must carry exactly the seven declared "
            "non-fixture keys — a seismic artifact may not be built "
            "on a fixture marker or an evidence-light manifest")
        receipt["status"] = "UNOBSERVABLE"
        return _report(receipt)
    receipt["source_manifest_digest"] = source_manifest_digest(
        source_manifest)
    manifest_digest = receipt["source_manifest_digest"]

    ev_problems = verify_source_evidence(dict(source_manifest))
    if ev_problems:
        problems.extend(f"source_manifest evidence: {p}"
                        for p in ev_problems)
        receipt["status"] = "UNOBSERVABLE"
        return _report(receipt)

    # ---- observability gates + manifest binding ----------------------
    observability_bad = False
    for rec in records:
        if rec.source_manifest_digest != manifest_digest:
            problems.append(
                f"station {rec.station_id}: observability record is "
                "bound to a different source manifest digest — the "
                "record must be assessed against THIS manifest")
            observability_bad = True
        if rec.status != "OBSERVABLE":
            problems.append(
                f"station {rec.station_id}: status {rec.status} — "
                + "; ".join(rec.problems))
            observability_bad = True
    if observability_bad:
        receipt["status"] = "UNOBSERVABLE"
        return _report(receipt)
    receipt["station_observability_digest"] = _digest(
        sorted((r.to_dict() for r in records),
               key=lambda d: d["station_id"]))
    stations_declared = {r.station_id for r in records}

    # ---- waveform payload binding -------------------------------------
    if not cfg.waveform_relpaths:
        problems.append("seismic_config.waveform_relpaths is empty — "
                        "no waveform payload is bound to the manifest")
        receipt["status"] = "UNOBSERVABLE"
        return _report(receipt)
    declared_files = {
        f.get("relpath"): f.get("sha256")
        for f in source_manifest.get("source_files", ())
        if isinstance(f, Mapping)}
    missing_wave = sorted(set(cfg.waveform_relpaths) -
                         set(declared_files))
    missing_resp = sorted(set(cfg.response_relpaths) -
                          set(declared_files))
    if missing_wave:
        problems.append(
            f"waveform_relpaths {missing_wave} are not declared "
            "source_files members — the manifest must name every "
            "waveform file")
    if missing_resp:
        problems.append(
            f"response_relpaths {missing_resp} are not declared "
            "source_files members — StationXML evidence must be "
            "manifest-bound")
    if missing_wave or missing_resp:
        receipt["status"] = "UNOBSERVABLE"
        return _report(receipt)
    # Response evidence must also be named on each observability
    # record — the record's response_relpath/sha256 must resolve to a
    # declared manifest member.  When require_response is declared
    # every station must bind response evidence; when it is not, an
    # unbound record is admissible but a BOUND one must still match
    # the manifest exactly.
    for rec in records:
        bound = bool(rec.response_relpath or rec.response_sha256)
        if cfg.require_response or bound:
            if rec.response_relpath not in declared_files or \
                    declared_files.get(rec.response_relpath) != \
                    rec.response_sha256:
                problems.append(
                    f"station {rec.station_id}: response_relpath/"
                    "response_sha256 does not match a declared "
                    "source_files member")
                receipt["status"] = "UNOBSERVABLE"
                return _report(receipt)
    # SEISMIC-04: read under the resolved root (the containment
    # helper's contract) AND pin every read to the manifest's
    # declared sha256 — a post-verification root swap or byte
    # substitution between verify_source_evidence and this read
    # can never slip different bytes into the bound digest.
    root = Path(str(source_manifest["evidence_root"])) \
        .resolve()
    raw_payload = b""
    for rel in sorted(cfg.waveform_relpaths):
        try:
            payload_bytes = read_evidence_file(
                root, rel, label="waveform payload")
        except ValueError as exc:
            problems.append(f"waveform payload {rel!r}: {exc}")
            receipt["status"] = "UNOBSERVABLE"
            return _report(receipt)
        if sha256_bytes(payload_bytes) != declared_files[rel]:
            problems.append(
                f"waveform payload {rel!r}: bytes do not match "
                "the manifest's declared sha256 — the verified "
                "file was substituted between verification and "
                "read")
            receipt["status"] = "UNOBSERVABLE"
            return _report(receipt)
        raw_payload += payload_bytes
    receipt["raw_waveform_digest"] = sha256_bytes(raw_payload)

    # ---- feature-frame floor ------------------------------------------
    # The band columns are generated from the DECLARED config bands —
    # the vocabulary is the naming rule, not a single band set.
    catalog_in = sorted(set(fcols) & set(CATALOG_CONTEXT_COLUMNS))
    if catalog_in:
        problems.append(
            f"catalog columns {catalog_in} may never enter the "
            "waveform predictor set — catalog context is a separate "
            "channel")
        return _report(receipt)
    allowed_features = set(SEISMIC_NONBAND_FEATURES) | \
        set(band_feature_names(cfg.bands))
    unknown = sorted(set(fcols) - allowed_features)
    if unknown:
        problems.append(
            f"feature columns {unknown} are outside the declared "
            "seismic feature vocabulary")
        return _report(receipt)
    allow = set(source_manifest.get("feature_allowlist") or ())
    outside_allow = sorted(set(fcols) - allow)
    if outside_allow:
        problems.append(
            f"feature columns {outside_allow} are outside the "
            "manifest's feature_allowlist")
        return _report(receipt)
    required_cols = set(SEISMIC_IDENTITY_COLUMNS) | set(fcols)
    if cfg.catalog_ablation:
        unknown_cat = sorted(set(cfg.catalog_cols) -
                             set(CATALOG_CONTEXT_COLUMNS))
        if unknown_cat:
            problems.append(
                f"catalog_cols {unknown_cat} are outside the "
                "declared catalog vocabulary")
            return _report(receipt)
        required_cols |= set(cfg.catalog_cols)
    missing_cols = sorted(required_cols - set(feature_frame.columns))
    if missing_cols:
        problems.append(f"feature_frame lacks columns {missing_cols}")
        return _report(receipt)

    frame_stations = set(feature_frame["station_id"].astype(str))
    if not frame_stations:
        problems.append("feature_frame carries no station rows")
        return _report(receipt)
    if not frame_stations <= stations_declared:
        problems.append(
            f"frame stations {sorted(frame_stations - stations_declared)} "
            "have no observability record — undeclared stations "
            "cannot produce features")
        return _report(receipt)
    # unit_id <-> station_id bijection: regime identity is (unit_id,
    # date); a station sharing a unit_id would silently merge rows.
    su = feature_frame[["station_id", "unit_id"]].astype(str) \
        .drop_duplicates()
    if su["station_id"].nunique() != su["unit_id"].nunique() or \
            len(su) != su["station_id"].nunique():
        problems.append("station_id and unit_id must be a bijection "
                        "— merged identities corrupt the regime "
                        "row-key contract")
        return _report(receipt)
    if "seis_coherence" in fcols and len(frame_stations) < 2:
        problems.append(
            "seis_coherence is declared with fewer than two stations "
            "— cross-station evidence cannot be simulated")
        return _report(receipt)
    if "seis_coherence" not in feature_frame.columns and \
            "seis_coherence" in fcols:
        problems.append("seis_coherence declared but absent from "
                        "the frame — unavailable coherence is "
                        "dropped, never zero-filled")
        return _report(receipt)

    # Window-level semantic digest — binds the waveform-derived
    # feature surface; catalog context and labels never enter it.
    win_rows = feature_frame.to_dict("records")
    ident_problems = _validate_window_identities(
        win_rows, fcols, expected_window_seconds=cfg.window_seconds)
    if ident_problems:
        problems.extend(f"feature_frame: {p}"
                        for p in ident_problems[:20])
        return _report(receipt)
    receipt["semantic_feature_digest"] = semantic_feature_digest(
        win_rows, fcols)
    if cfg.catalog_ablation:
        receipt["catalog_context_digest"] = _digest(
            [{c: r.get(c) for c in sorted(cfg.catalog_cols)}
             for r in sorted(
                 win_rows,
                 key=lambda r: (str(r["station_id"]),
                                str(r["window_start"])))])

    # ---- daily aggregation + partition --------------------------------
    daily = aggregate_daily(win_rows, fcols)
    if not daily:
        problems.append("no qualified window rows — the semantic "
                        "frame is empty after coverage filtering")
        receipt["status"] = "UNOBSERVABLE"
        return _report(receipt)
    regime_frame = pd.DataFrame(daily)
    # Deterministic holdout partition (station × temporal half).
    # The regime contract requires group_col to be a unit-level
    # partition — every unit_id must map to exactly ONE group — AND
    # every non-mask row must sit in a heldout group.  Both holdout
    # axes therefore bind into identity: the group IS the
    # station×half cell, and the observation unit is the
    # station×half composite — a station's early epoch and late
    # epoch are two declared units.  Station holdout = all of a
    # station's cells in heldout_groups; time holdout = trained
    # stations' late cells in heldout_groups.
    cells, _medians = _station_holdout_cells(daily)
    regime_frame["holdout_cell"] = pd.Series(
        [cells[i] for i in range(len(daily))], dtype=str)
    regime_frame[cfg.unit_col] = pd.Series(
        [f"{r['unit_id']}|{cells[i].rsplit('|', 1)[-1]}"
         for i, r in enumerate(daily)], dtype=str)

    held = set(str(s) for s in cfg.heldout_stations)
    unknown_held = sorted(held - frame_stations)
    if unknown_held:
        problems.append(
            f"heldout_stations {unknown_held} are not in the frame")
        return _report(receipt)
    stations_sorted = sorted(frame_stations)
    if cfg.require_station_holdout and not held:
        # SEISMIC-05: a station holdout must be DECLARED — inferring
        # the held-out station from sort order would let the frame
        # silently choose its own evaluation split.
        problems.append(
            "require_station_holdout is declared but "
            "heldout_stations is empty — the held-out station must "
            "be explicit, never inferred from sort order")
        return _report(receipt)
    trained = [s for s in stations_sorted if s not in held]
    underpowered_reasons: list[str] = []
    if len(trained) < cfg.min_trained_stations:
        underpowered_reasons.append(
            f"trained stations {len(trained)} below declared "
            f"minimum {cfg.min_trained_stations}")
    rows_by_station = regime_frame.groupby("station_id").size()
    short = sorted(s for s in trained
                   if int(rows_by_station.get(s, 0)) <
                   cfg.min_rows_per_station)
    if short:
        underpowered_reasons.append(
            f"stations {short} carry fewer than "
            f"{cfg.min_rows_per_station} qualified daily rows")

    # The declared fit surface: early-half rows of trained stations.
    # Everything else — trained stations' late halves and the whole
    # held-out station — is a locked test row in a heldout group.
    train_cells = {f"{s}|early" for s in trained}
    held_cells = ({f"{s}|late" for s in trained} |
                  {f"{s}|early" for s in held} |
                  {f"{s}|late" for s in held})
    expected_mask = regime_frame["holdout_cell"].isin(
        train_cells).to_numpy()
    caller_mask = np.asarray(train_mask)
    if caller_mask.shape != expected_mask.shape or \
            caller_mask.dtype != bool:
        problems.append("train_mask must be a boolean array matching "
                        "the frame length")
        return _report(receipt)
    if not np.array_equal(caller_mask, expected_mask):
        problems.append(
            "train_mask does not equal the declared holdout-cell "
            "partition — the mask is bound to the declared "
            "partition, never caller-shaped")
        return _report(receipt)
    # Time holdout: every trained station must have a held-out late
    # tail inside the frame — otherwise no temporal holdout exists.
    tailless = sorted(
        s for s in trained
        if not (regime_frame["holdout_cell"] ==
                f"{s}|late").any())
    if tailless:
        problems.append(
            f"trained stations {tailless} have no held-out late "
            "tail — the declared time holdout is not satisfiable")
        return _report(receipt)

    if underpowered_reasons:
        problems.extend(underpowered_reasons)
        receipt["status"] = "UNDERPOWERED_DESCRIPTIVE_ONLY"
        return _report(receipt)

    # ---- FMX value-level audit (observation_metadata class) ----------
    win_lo = min(str(r["window_start"]) for r in win_rows)
    win_hi = max(str(r["window_end"]) for r in win_rows)
    audits = []
    lineage = (str(source_manifest.get("source_id", "")),
               str(source_manifest.get("lineage", "")),
               "seismic_sidecar_waveform_window")
    for col in fcols:
        audits.append(ColumnAudit(
            column_name=col,
            declared_field_class="observation_metadata",
            source_lineage=lineage,
            availability_semantics=(
                "coverage-qualified one-minute windows aggregated "
                "to daily grain; unobservable windows are absent, "
                "never zero-filled"),
            unit=SEISMIC_FEATURE_UNITS.get(
                col, "1 — fraction of window power in the declared "
                "band" if col.startswith("seis_band_")
                else "declared"),
            value_domain="finite float; see feature definition",
            temporal_window=(win_lo, win_hi),
            missingness_policy=cfg.missingness_policy))
    verdicts = audit_matrix(
        {c: regime_frame[c].tolist() for c in fcols},
        audits,
        cutoff_iso=None,
        preprocessing_provenance={c: "train_only" for c in fcols})
    rejected = [v for v in verdicts if v.verdict == "reject"]
    if rejected:
        for v in rejected:
            problems.append(
                f"FMX audit rejected {v.column_name}: "
                + "; ".join(v.reasons))
        return _report(receipt)

    # ---- regime delegation --------------------------------------------
    regime_config = RegimeRunConfig(
        seeds=tuple(cfg.seeds),
        k_candidates=tuple(cfg.k_candidates),
        n_bootstrap=int(cfg.n_bootstrap),
        n_null_replicates=int(cfg.n_null_replicates),
        null_alpha=float(cfg.null_alpha),
        season_col="season",
        group_col="holdout_cell",
        era_col=None,
        era_waiver_reason=(
            "seismic sidecar v0: a single-station-era deployment "
            "carries no era partition — the axis is waived"),
        effort_col="seis_window_count",
        missingness_policy=cfg.missingness_policy,
        unit_col=cfg.unit_col,
        date_col=cfg.date_col,
        train_groups=tuple(sorted(train_cells)),
        heldout_groups=tuple(sorted(held_cells)),
        cadence="1D",
        bootstrap_block_len=int(cfg.bootstrap_block_len),
        source_manifest=dict(source_manifest),
        mode="RETROSPECTIVE_REGIME",
        retrospective_data_class=SEISMIC_WAVEFORM_RETROSPECTIVE)
    try:
        artifact = run_regimes(regime_frame, fcols,
                               expected_mask, regime_config)
    except Exception as exc:  # engine output is untrusted input
        problems.append(f"run_regimes raised "
                        f"{type(exc).__name__}: {exc}")
        return _report(receipt)
    if not isinstance(artifact, Mapping):
        problems.append("regime engine returned a non-mapping "
                        "artifact")
        return _report(receipt)
    if artifact.get("status") == "RUN_ERROR":
        problems.append(
            f"regime run returned RUN_ERROR: "
            f"{artifact.get('reason', 'no reason recorded')}")
        return _report(receipt)
    artifact_status = artifact.get("status")
    try:
        frozen = freeze_regime_artifact(dict(artifact))
    except (TypeError, ValueError, AttributeError) as exc:
        problems.append(f"freeze rejected the artifact: {exc}")
        return _report(receipt)
    receipt["regime_artifact_digest"] = \
        frozen.get("regime_artifact_digest", "")

    if artifact_status == "DESCRIPTIVE_REGIME_ONLY":
        receipt["status"] = "DESCRIPTIVE_REGIME_ONLY"
    elif artifact_status in ("CANDIDATE_ONLY",
                             "UNSUPERVISED_STRUCTURE_NOT_STABLE"):
        problems.append(
            f"regime artifact is {artifact_status} — not "
            "descriptive-stable")
        receipt["status"] = "CANDIDATE_ONLY"
    else:
        problems.append(
            f"artifact status {artifact_status!r} is not a "
            "PoC-admissible status")
    return _report(receipt)
