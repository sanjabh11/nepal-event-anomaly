"""Round-9 promotion-closure tests — every reopened audit finding
(R9-P01..R9-P12, plus the R9-D01 docs surface covered by the
register/ledger edits) must fail closed at EVERY applicable boundary.

Four boundaries are exercised for every payload mutation:

* ``freeze`` — ``science_v0.regimes.freeze_regime_artifact`` on the
  pre-freeze artifact (``frozen``/``freeze_digest`` stripped, all
  remaining bound digests honestly rehashed).
* ``adapter`` — ``experiment_v0.adapters.regime_assignment_from_artifact``
  on the fully frozen payload.
* ``audit`` — ``experiment_v0.audit.audit_producer_payload``; an empty
  Finding list is acceptance, anything else is rejection.
* ``association`` — ``experiment_v0.association.run_association`` with
  the mutated payload passed as ``producer_payload`` (the PROV-04
  verified-binding lane).  When the mutated payload still adapts, the
  freshly-adapted artifact is used — so the binding is tested on its
  own checks, not on a trivially-mismatched freeze_digest.  When the
  adapter already rejected, the canonical artifact is used and the
  binding rejection is still asserted (weaker evidence, noted).

Method — the honest-forger envelope.  Every mutation is applied to a
deep copy of a complete, internally consistent NEW-floor payload and
then *fully* rehashed: section digests (config, preprocessing,
stability, fit_partition, run_manifest, forecast_feature_payload,
unit_basin_map, null_model), ``assignment_digest``,
``regime_artifact_digest`` (payload minus
{regime_artifact_digest, freeze_digest, frozen}), and ``freeze_digest``
(payload minus {freeze_digest, frozen}).  A mutation that is only
rejected because its envelope is stale proves nothing — each case
below must fail on the finding's own semantics.

Canonical payload (``_canonical_payload``) — built to the Round-9
contract in the coordinated spec:

* real small ``input_values`` (deterministic binary-exact floats)
  whose decoded float64 C-order bytes hash to ``input_bytes_digest``
  and whose six-decimal semantic normalization hashes to
  ``feature_matrix_digest``;
* real row keys ``f"{unit}|{date}"`` — ``preprocessing.row_keys_digest``
  and ``fit_partition.train_row_keys_digest`` are the sorted-key
  digests the producer emits;
* strict ``{"fixture": True}`` source_manifest (mirrored into
  ``config.source_manifest``);
* a serialized ``RunManifestV0`` (``record_type`` present,
  ``input_digests`` containing ``input_bytes_digest``,
  ``output_digests`` containing ``assignment_digest``,
  ``environment_digest`` equal to the payload's, ``seed`` equal to
  ``seeds_declared[0]``);
* ``unit_basin_map`` + ``unit_basin_map_digest``;
* audit-grade semantics: ``seed_coverage`` keys exactly
  ``seeds_declared``, ``required_gates`` exactly
  ``REQUIRED_REGIME_GATE_NAMES`` and all-True under
  ``DESCRIPTIVE_REGIME_ONLY``, and ``nulls`` carrying both declared
  families (``shuffled``, ``season_matched``) with recomputed
  ``family_digest``/``null_model_digest`` bindings;
* a PSD model (weights sum 1, symmetric positive-definite
  covariances) and a semantically valid ``config`` consistent with
  the artifact's ``mode``/``seeds``/``source_manifest``.

SPECULATIVE MARKERS — the Round-9 code lanes (workers A/B/C) had not
landed when this matrix was written.  Places where the asserted API
is contractual but not yet visible in the tree are marked
``# SPECULATIVE``:

* ``RegimeAssignmentArtifact.unit_basin_map`` /
  ``unit_basin_map_digest`` (P11 relies on it);
* ``validate_producer_payload(..., verify_source_bytes=...)`` and the
  helpers ``fixture_flag`` / ``row_key`` / ``sorted_row_key_digest`` /
  ``semantic_feature_matrix_digest`` / ``canonical_unit_basin_pairs``
  (a guarded parity test covers these — local mirrors below encode the
  documented semantics so the matrix runs either way);
* the exact problem-string wording at each boundary — evidence is
  matched on category-level regexes, not verbatim strings.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import re
from datetime import date as _date, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np
import pytest

from nepal.research_v0._hashing import sha256_canonical
from nepal.research_v0.gates import REQUIRED_REGIME_GATE_NAMES
from nepal.science_v0.regimes import freeze_regime_artifact
from nepal.experiment_v0.adapters import regime_assignment_from_artifact
from nepal.experiment_v0.association import run_association
from nepal.experiment_v0.audit import audit_producer_payload

# Association-geometry fixtures (same synthetic universe as the B1
# lane — six units across three basins/eval regions).
from tests.test_experiment_v0_b1_association import (
    REGION_BASINS, UNIT_BASINS, make_controls, make_events,
    make_holdout, make_opportunities)

# SPECULATIVE — Round-9 shared-validator helpers.  Imported only for
# the parity test; the canonical payload is built with the local
# mirrors below so this file imports cleanly before the code lanes
# land (and the matrix then demonstrates exactly which findings the
# landed floor still misses).
try:  # pragma: no cover - contract pending
    from nepal.research_v0.producer_validation import (
        canonical_unit_basin_pairs, fixture_flag, row_key,
        semantic_feature_matrix_digest, sorted_row_key_digest,
        validate_producer_payload)
    _R9_HELPERS = True
except ImportError:  # pragma: no cover
    _R9_HELPERS = False


# ---------------------------------------------------------------------
# Local mirrors of the producer's canonical constructions
# ---------------------------------------------------------------------

def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _row_key(unit: Any, day: Any) -> str:
    """Producer row key — ``f"{unit}|{date}"`` (regimes.py binds
    ``sorted(f"{u}|{d}" ...)`` into both row-key digests)."""
    return f"{unit}|{day}"


def _sorted_row_key_digest(keys) -> str:
    return sha256_canonical(sorted(keys))


def _semantic_fmx_digest(input_values) -> str:
    """Producer semantic feature-matrix digest — sha256_canonical over
    the input_values normalized to six decimals (``_fm_digest`` in
    regimes.py)."""
    return sha256_canonical(
        [[round(v, 6) if isinstance(v, float) else v for v in row]
         for row in input_values])


def _canonical_ubm_pairs(ubm) -> list:
    """Mirror of ``producer_validation.canonical_unit_basin_pairs``:
    string-normalized len-2 pairs, deduplicated, sorted."""
    pairs = set()
    for e in ubm or ():
        if isinstance(e, (list, tuple)) and len(e) == 2:
            pairs.add((str(e[0]), str(e[1])))
    return [list(p) for p in sorted(pairs)]


def _ubm_digest(ubm) -> str:
    return sha256_canonical(_canonical_ubm_pairs(ubm))


def _decode_input_values(vals) -> np.ndarray:
    """Mirror of the producer's input_values decode (None=NaN,
    'Infinity'/'-Infinity' tokens, else float)."""
    out = []
    for row in vals:
        dec = []
        for v in row:
            if v is None:
                dec.append(np.nan)
            elif v == "Infinity":
                dec.append(np.inf)
            elif v == "-Infinity":
                dec.append(-np.inf)
            else:
                dec.append(float(v))
        out.append(dec)
    return np.asarray(out, dtype=np.float64)


def _input_bytes_digest(vals) -> str:
    arr = np.ascontiguousarray(_decode_input_values(vals),
                               dtype=np.float64)
    return hashlib.sha256(arr.tobytes()).hexdigest()


# ---------------------------------------------------------------------
# Canonical NEW-floor payload
# ---------------------------------------------------------------------

_SEEDS = [11, 23, 42]
_FEATURE_COLS = ["synth_f1", "synth_f2"]
_FIT_GROUPS = sorted(set(UNIT_BASINS.values()))
_HELDOUT_GROUPS = sorted(REGION_BASINS)
_BASE_DATE = _date(2020, 5, 25)
_ASSIGN_END = _date(2020, 12, 15)
_ENV_DIGEST = _sha_text("r9-synth-environment")
_K = 3


def _dates_between(a: _date, b: _date) -> list[_date]:
    out, d = [], a
    while d <= b:
        out.append(d)
        d += timedelta(days=1)
    return out


def _assignment_rows() -> list[list]:
    """Deterministic (unit, date, int-regime) sidecar over the B1
    geometry — hash-driven base regimes, a planted regime on the
    canonical event grid, and a sparse rare regime."""
    rows: dict[tuple[str, str], int] = {}
    all_dates = _dates_between(_BASE_DATE, _ASSIGN_END)
    for unit in sorted(UNIT_BASINS):
        for d in all_dates:
            doy = d.timetuple().tm_yday
            if doy % 37 == 0:
                rows[(unit, d.isoformat())] = 2          # planted
            elif doy % 53 == 0:
                rows[(unit, d.isoformat())] = 1          # rare
            else:
                rows[(unit, d.isoformat())] = int(
                    _sha_text(f"base|{unit}|{d.isoformat()}")[:8],
                    16) % 3
    basin_units: dict[str, list[str]] = {}
    for u, b in UNIT_BASINS.items():
        basin_units.setdefault(b, []).append(u)
    from datetime import datetime
    for ev in make_events(21):
        s = datetime.strptime(ev.event_time_start,
                              "%Y-%m-%dT%H:%M:%SZ").date()
        e = datetime.strptime(ev.event_time_end,
                              "%Y-%m-%dT%H:%M:%SZ").date()
        for d in _dates_between(s, e):
            for u in basin_units[ev.basin_id]:
                rows[(u, d.isoformat())] = 2
    return sorted([u, d, r] for (u, d), r in rows.items())


def _family_digest(payload: Mapping, fam: str, rec: Mapping) -> str:
    """The exact material audit._producer_null_findings recomputes."""
    declared = payload.get("seeds_declared")
    if not isinstance(declared, (list, tuple)):
        declared = payload.get("seeds")
    return sha256_canonical({
        "family": fam,
        "seed_cycle": list(declared)
        if isinstance(declared, (list, tuple)) else declared,
        "n_replicates": rec["n_replicates"],
        "statistic": rec["statistic"],
        "p_value": rec["p_value"],
        "observed": rec.get("observed"),
        "alpha": rec.get("alpha"),
        "n_succeeded": rec.get("n_succeeded"),
        "n_failed": rec.get("n_failed"),
        "status": rec.get("status"),
        "reason": rec.get("reason"),
        "selection": rec.get("selection"),
        "null_stat_min": rec.get("null_stat_min"),
        "null_stat_max": rec.get("null_stat_max"),
        "null_k_distribution": rec.get("null_k_distribution", {}),
        "replicates": rec.get("replicates", [])})


def _null_family(payload: Mapping, fam: str, stat: float) -> dict:
    rec = {"statistic": "silhouette",
           "observed": 0.42,
           "n_replicates": 50,
           "n_succeeded": 50,
           "n_failed": 0,
           "p_value": 0.02,
           "alpha": 0.05,
           "status": "PASS",
           "reason": None,
           "selection": "bic_sweep_declared_candidates",
           "null_stat_min": 0.005,
           "null_stat_max": 0.18,
           "null_k_distribution": {"2": 28, "3": 22},
           "replicates": [
               {"i": 0, "gen_seed": _SEEDS[0] + 1000003,
                "fit_seed": _SEEDS[0], "k": 2, "stat": stat,
                "ok": True,
                "input_digest": _sha_text(f"{fam}-rep0-input")},
               {"i": 1, "gen_seed": _SEEDS[0] + 2000006,
                "fit_seed": _SEEDS[1], "k": 3, "stat": stat - 0.01,
                "ok": True,
                "input_digest": _sha_text(f"{fam}-rep1-input")}]}
    rec["family_digest"] = _family_digest(payload, fam, rec)
    return rec


def _canonical_payload(mode: str = "RETROSPECTIVE_REGIME") -> dict:
    """A complete NEW-floor producer payload — see module docstring."""
    rows = _assignment_rows()
    n_rows = len(rows)
    input_values = [
        [float((i * 13 + c) % 89) * 0.125 for c in
         range(len(_FEATURE_COLS))]
        for i in range(n_rows)]
    ibd = _input_bytes_digest(input_values)
    fmx = _semantic_fmx_digest(input_values)
    row_keys = [_row_key(u, d) for u, d, _ in rows]
    row_keys_digest = _sorted_row_key_digest(row_keys)
    ubm = [list(p) for p in _canonical_ubm_pairs(UNIT_BASINS.items())]
    forecast = mode == "FORECAST_REGIME"
    source_manifest = {"fixture": True}
    # Mirrors the serialized RegimeRunConfig the producer emits —
    # every field the shared floor cross-binds is present.
    config = {
        "seeds": list(_SEEDS), "k_candidates": [1, 2, 3],
        "n_bootstrap": 200, "n_null_replicates": 50,
        "null_alpha": 0.05, "season_col": "season",
        "group_col": "basin_group", "era_col": "era",
        "era_boundaries": [], "elevation_col": None,
        "era_drift_max": 0.5, "missingness_policy": "listwise",
        "max_missingness": 0.25, "effort_col": None,
        "fold_seed_policy": "all", "unit_col": "unit_id",
        "date_col": "date", "label_blinding": True,
        "fitted_on": "TRAIN_ONLY",
        "train_groups": list(_FIT_GROUPS),
        "heldout_groups": list(_HELDOUT_GROUPS),
        "cadence": "1D", "bootstrap_block_len": 7,
        "gap_policy": "calendar",
        "effort_waiver_reason": "", "era_waiver_reason": "",
        "effort_split": "median", "elev_ablation_ari_max": 0.8,
        "source_manifest": dict(source_manifest),
        "mode": mode,
        "forecast_vintage_digests": (
            [_sha_text("r9-vintage-0")] if forecast else []),
        "forecast_feature_set": (["synth_f1"] if forecast else []),
    }
    stability = {
        "seed_ari_min": 0.91, "seed_ari_max": 0.97,
        "modal_k_frequency": 1.0, "k_instability": False,
        "n_bootstrap": 200, "fold_seed_policy": "all",
        "seed_coverage": {str(s): "converged" for s in _SEEDS},
        "required_gates": {
            g: True for g in sorted(REQUIRED_REGIME_GATE_NAMES)}}
    payload: dict[str, Any] = {
        "mode": mode,
        "data_class": ("ARCHIVED_OPERATIONAL" if forecast
                       else "REANALYSIS"),
        "fitted_on": "TRAIN_ONLY",
        "label_blinding": True,
        "status": "DESCRIPTIVE_REGIME_ONLY",
        "terminal": True,
        "associable": True,
        "seeds": list(_SEEDS),
        "seeds_declared": list(_SEEDS),
        "seed_coverage": {str(s): "converged" for s in _SEEDS},
        "k": _K,
        "per_seed_best_k": {str(s): _K for s in _SEEDS},
        "modal_k_frequency": 1.0,
        "occupancy": [0.5, 0.3, 0.2],
        "assignments": rows,
        "feature_cols": list(_FEATURE_COLS),
        "feature_matrix_digest": fmx,
        "input_values": input_values,
        "input_bytes_digest": ibd,
        "input_schema": {
            "feature_cols": list(_FEATURE_COLS),
            "n_rows": n_rows,
            "dtypes": {c: "float64" for c in _FEATURE_COLS},
            "shape": [n_rows, len(_FEATURE_COLS)]},
        "model": {
            "weights": [0.5, 0.3, 0.2],
            "means": [[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]],
            "covariances": [[[0.25, 0.0], [0.0, 0.25]],
                            [[0.25, 0.0], [0.0, 0.25]],
                            [[0.25, 0.0], [0.0, 0.25]]]},
        "config": config,
        "config_digest": sha256_canonical(config),
        "fit_groups": list(_FIT_GROUPS),
        "heldout_groups_declared": list(_HELDOUT_GROUPS),
        "unit_basin_map": ubm,
        "unit_basin_map_digest": _ubm_digest(ubm),
        "n_train_rows": n_rows,
        "n_rows": n_rows,
        "train_mask_digest": _sha_text("r9-train-mask"),
        "stability": stability,
        "preprocessing": {
            "imputer_strategy": "median",
            "imputer_statistics": [0.0, 0.0],
            "scaler_mean": [0.0, 0.0],
            "scaler_var": [1.0, 1.0],
            "feature_order": list(_FEATURE_COLS),
            "row_keys_digest": row_keys_digest,
            "train_mask_membership_digest":
                _sha_text("r9-mask-membership")},
        "source_manifest": dict(source_manifest),
        "environment_digest": _ENV_DIGEST,
        "missingness_applied": {"policy": "listwise",
                                "train_rows_total": n_rows,
                                "train_rows_fitted": n_rows,
                                "train_rows_dropped": 0},
        "k_selection_digest": _sha_text("r9-k-selection"),
        "disclaimer": "synthetic fixture — interface evidence only",
    }
    payload["nulls"] = {
        "statistic": "silhouette",
        "observed": 0.42,
        "alpha": 0.05,
        "n_replicates": 50,
        "season_era_stratified": False,
        "k1_bic": [10.5, 11.5, 12.5],
        "shuffled": _null_family(payload, "shuffled", 0.11),
        "season_matched": _null_family(payload, "season_matched",
                                       0.13)}
    payload["fit_partition"] = {
        "record_type": "fit_partition/v0",
        "train_groups": list(_FIT_GROUPS),
        "heldout_groups": list(_HELDOUT_GROUPS),
        "n_train_rows": n_rows,
        "n_rows": n_rows,
        "train_row_keys_digest": row_keys_digest,
        "cutoff_iso": _ASSIGN_END.isoformat(),
        "feature_matrix_digest": fmx,
        "feature_cols": list(_FEATURE_COLS)}
    payload["run_manifest"] = {
        "record_type": "RunManifestV0",
        "run_id": "r9-synth-run-001",
        "worker_id": "synthetic-fixture",
        "created_at": "2020-12-15T00:00:00Z",
        "environment_digest": _ENV_DIGEST,
        "seed": _SEEDS[0],
        "input_digests": [ibd],
        "output_digests": [
            sha256_canonical(payload["assignments"])],
        "checkpoint_policy": "atomic_publish_or_quarantine",
        "status": "COMPLETED"}
    if forecast:
        payload["forecast_vintage_digests"] = list(
            config["forecast_vintage_digests"])
        payload["forecast_feature_set"] = list(
            config["forecast_feature_set"])
        payload["forecast_feature_payload"] = {
            "record_type": "forecast_feature_payload/v0",
            "forecast_feature_set": list(
                config["forecast_feature_set"]),
            "forecast_vintage_digests": list(
                config["forecast_vintage_digests"]),
            "feature_matrix_digest": fmx,
            "row_count": n_rows,
            "row_keys_digest": row_keys_digest}
    else:
        payload["forecast_vintage_digests"] = []
        payload["forecast_feature_set"] = []
    return _rehash(payload)


# ---------------------------------------------------------------------
# The honest-forger envelope rehash
# ---------------------------------------------------------------------

def _rehash(payload: dict, skip: set[str] = frozenset()) -> dict:
    """Recompute every bound digest over the mutated payload — the
    strongest honest rehash a forger can produce.  ``skip`` names flat
    digest fields left deliberately stale (the carried-digest-is-wrong
    mutations)."""
    sections = (
        ("config", "config_digest"),
        ("preprocessing", "preprocessing_digest"),
        ("stability", "stability_report_digest"),
        ("fit_partition", "fit_partition_digest"),
        ("run_manifest", "run_manifest_digest"),
        ("forecast_feature_payload",
         "forecast_feature_payload_digest"),
        ("unit_basin_map", "unit_basin_map_digest"))
    for section, field in sections:
        if section in payload and field not in skip:
            payload[field] = sha256_canonical(payload[section])
    nulls = payload.get("nulls")
    if isinstance(nulls, Mapping):
        fams = {str(f): r for f, r in nulls.items()
                if isinstance(r, Mapping) and "family_digest" in r}
        for fam, rec in fams.items():
            rec["family_digest"] = _family_digest(payload, fam, rec)
        if "null_model_digest" not in skip:
            payload["null_model_digest"] = sha256_canonical({
                "k1_bic": list(nulls.get("k1_bic") or []),
                "null_families": {
                    f: r["family_digest"] for f, r in fams.items()}})
    payload["assignment_digest"] = sha256_canonical(
        payload["assignments"])
    payload["regime_artifact_digest"] = sha256_canonical(
        {k: v for k, v in payload.items()
         if k not in ("regime_artifact_digest", "freeze_digest",
                      "frozen")})
    payload["freeze_digest"] = sha256_canonical(
        {k: v for k, v in payload.items()
         if k not in ("freeze_digest", "frozen")})
    payload["frozen"] = True
    return payload


def _pre_freeze(payload: Mapping) -> dict:
    """The pre-freeze artifact: the frozen payload minus the two
    fields freeze itself emits.  ``regime_artifact_digest`` was
    computed over exactly this surface."""
    return {k: v for k, v in payload.items()
            if k not in ("freeze_digest", "frozen")}


def _nonfixture_manifest(root: Path, *,
                         source_files: Any = "default",
                         source_digests: Any = None) -> dict:
    """A schema-complete non-fixture source manifest — every required
    key present so the ONLY operative failure is byte verification."""
    m = {"source_id": "synth-r9-source",
         "source_digests": source_digests
         if source_digests is not None else [_sha_text("r9-src-0")],
         "units": sorted(UNIT_BASINS),
         "feature_allowlist": list(_FEATURE_COLS),
         "lineage": "synthetic-lineage-v0",
         "evidence_root": str(root)}
    if source_files != "default":
        m["source_files"] = source_files
    return m


def _bind_manifest(payload: dict, manifest: dict) -> None:
    """Bind a mutated source_manifest into BOTH the payload and its
    serialized config — isolating byte verification from the
    config↔artifact cross-binding."""
    payload["source_manifest"] = manifest
    payload["config"]["source_manifest"] = copy.deepcopy(manifest)


# ---------------------------------------------------------------------
# Association inputs (module scope — built once)
# ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def assoc() -> dict:
    events = make_events(21)
    controls = make_controls()
    return {
        "events": events,
        "controls": controls,
        "unit_basins": dict(UNIT_BASINS),
        "holdout": make_holdout(events),
        "opportunities": make_opportunities(controls),
        "artifact": regime_assignment_from_artifact(
            _canonical_payload(), artifact_id="r9-canonical")}


# ---------------------------------------------------------------------
# Boundary runners — each returns the rejection text for evidence
# ---------------------------------------------------------------------

def _boundary_output(boundary: str, payload: dict,
                     assoc: dict) -> str:
    """Run one boundary against the (mutated, rehashed) payload and
    return its rejection text.  Any acceptance raises/fails the test."""
    if boundary == "freeze":
        with pytest.raises(ValueError) as ei:
            freeze_regime_artifact(_pre_freeze(payload))
        return str(ei.value)
    if boundary == "adapter":
        with pytest.raises(ValueError) as ei:
            regime_assignment_from_artifact(
                payload, artifact_id="r9-probe")
        return str(ei.value)
    if boundary == "audit":
        findings = audit_producer_payload(payload)
        assert findings, \
            "audit_producer_payload accepted a mutated payload"
        return " || ".join(
            f"{f.code} {f.path} {f.detail}" for f in findings)
    if boundary == "association":
        # Strongest form: if the mutated payload still adapts, bind
        # THAT artifact — the association check then stands on its own
        # checks, not on a stale freeze_digest.  Otherwise use the
        # canonical artifact (the binding still must reject).
        art = None
        try:
            art = regime_assignment_from_artifact(
                payload, artifact_id="r9-probe-assoc")
        except ValueError:
            pass
        if art is None:
            art = assoc["artifact"]
        with pytest.raises(ValueError) as ei:
            run_association(
                art, assoc["events"], assoc["controls"],
                assoc["unit_basins"], holdout=assoc["holdout"],
                region_basins=REGION_BASINS,
                opportunities=assoc["opportunities"],
                n_boot=10, producer_payload=payload)
        return str(ei.value)
    raise AssertionError(f"unknown boundary {boundary!r}")


# ---------------------------------------------------------------------
# Mutation registry — (finding, case-id, mutate, evidence-regex, note)
# mutate(payload, tmp_path) -> set[str] of flat digest fields to leave
# stale in _rehash (the carried-digest-is-wrong cases).
# ---------------------------------------------------------------------

_Mut = Callable[[dict, Path], set]


def _m_fixture(value):
    def mut(p: dict, _tmp: Path) -> set:
        _bind_manifest(p, {"fixture": value})
        return set()
    return mut


def _m_nonfixture(source_files: Any = "default", *,
                  files_on_disk: Mapping[str, bytes] | None = None,
                  declared_sha: str = "f" * 64,
                  symlink_case: bool = False):
    def mut(p: dict, tmp: Path) -> set:
        root = tmp / "evidence"
        if source_files == "missing_root":
            root = tmp / "absent-root"     # never created
            m = _nonfixture_manifest(
                root,
                source_files=[{"relpath": "a.bin",
                               "sha256": "0" * 64}])
        else:
            root.mkdir(parents=True, exist_ok=True)
            for name, blob in (files_on_disk or {}).items():
                (root / name).write_bytes(blob)
            if symlink_case:
                link = root / "link.bin"
                try:
                    link.symlink_to("target.bin")
                except OSError as exc:
                    pytest.skip(f"symlink unsupported here: {exc}")
            m = _nonfixture_manifest(
                root, source_files=source_files,
                source_digests=[declared_sha])
        _bind_manifest(p, m)
        return set()
    return mut


def _m_run_manifest(field: str, value: Any = None, *,
                    delete: bool = False):
    def mut(p: dict, _tmp: Path) -> set:
        if delete:
            p["run_manifest"].pop(field, None)
        else:
            p["run_manifest"][field] = value
        return set()
    return mut


def _m_fit_partition_rowkeys(p: dict, _tmp: Path) -> set:
    p["fit_partition"]["train_row_keys_digest"] = \
        _sorted_row_key_digest(["forged-unit|1900-01-01"])
    return set()


def _m_ffp(field: str, value: Any):
    def mut(p: dict, _tmp: Path) -> set:
        p["forecast_feature_payload"][field] = (
            value(p) if callable(value) else value)
        return set()
    return mut


def _m_config(field: str, value: Any):
    def mut(p: dict, _tmp: Path) -> set:
        p["config"][field] = value
        return set()
    return mut


def _m_feature_matrix(p: dict, _tmp: Path) -> set:
    """Carried feature_matrix_digest replaced by the semantic digest
    of DIFFERENT values — every typed copy updated consistently."""
    forged = _semantic_fmx_digest(
        [[v + 1.0 for v in row] for row in p["input_values"]])
    p["feature_matrix_digest"] = forged
    p["fit_partition"]["feature_matrix_digest"] = forged
    if "forecast_feature_payload" in p:
        p["forecast_feature_payload"]["feature_matrix_digest"] = forged
    return set()


def _m_covariance(cov):
    def mut(p: dict, _tmp: Path) -> set:
        p["model"]["covariances"][0] = cov
        return set()
    return mut


def _m_ubm(transform):
    def mut(p: dict, _tmp: Path) -> set:
        p["unit_basin_map"] = transform(
            copy.deepcopy(p["unit_basin_map"]))
        return set()
    return mut


def _m_ubm_digest_stale(p: dict, _tmp: Path) -> set:
    p["unit_basin_map_digest"] = "0" * 64
    return {"unit_basin_map_digest"}


def _m_seed_coverage(p: dict, _tmp: Path) -> set:
    p["seed_coverage"].pop(str(_SEEDS[-1]))
    return set()


def _m_gate_delete(p: dict, _tmp: Path) -> set:
    p["stability"]["required_gates"].pop("effort")
    return set()


def _m_gate_string(p: dict, _tmp: Path) -> set:
    p["stability"]["required_gates"]["loro"] = "PASS"
    return set()


def _m_gate_false(p: dict, _tmp: Path) -> set:
    p["stability"]["required_gates"]["loro"] = False
    return set()


def _m_null_family(p: dict, _tmp: Path) -> set:
    p["nulls"].pop("season_matched")
    return set()


class _Case:
    def __init__(self, finding: str, case_id: str, mut: _Mut,
                 evidence: str, note: str, forecast: bool = False):
        self.finding = finding
        self.case_id = case_id
        self.mut = mut
        self.evidence = re.compile(evidence, re.IGNORECASE)
        self.note = note
        self.forecast = forecast

    def __repr__(self):
        return f"<Case {self.finding}/{self.case_id}>"


MUTATION_CASES: list[_Case] = [
    # ---- P01: truthy non-bool fixture markers -----------------------
    _Case("P01", "fixture-string-true", _m_fixture("true"),
          r"fixture|source_manifest|evidence",
          "no-false-negative: {'fixture': 'true'} must be treated as "
          "non-fixture (strict bool) — under R8 truthiness it bypassed "
          "every non-fixture requirement AND byte verification"),
    _Case("P01", "fixture-int-one", _m_fixture(1),
          r"fixture|source_manifest|evidence",
          "no-false-negative: {'fixture': 1} — int 1 is not True"),
    _Case("P01", "fixture-string-yes", _m_fixture("yes"),
          r"fixture|source_manifest|evidence",
          "no-false-negative: {'fixture': 'yes'} — any non-bool "
          "marker is non-fixture"),

    # ---- P02: non-fixture manifests with fabricated evidence --------
    _Case("P02", "nonexistent-root",
          _m_nonfixture("missing_root"),
          r"evidence_root|not a directory|source_manifest|evidence",
          "no-false-negative: fake 64-hex digests + a root that does "
          "not exist — byte verification must reach every boundary"),
    _Case("P02", "missing-source_files",
          _m_nonfixture(None),
          r"source_files|source_manifest|evidence",
          "no-false-negative: schema-complete manifest without "
          "source_files — declaration absent, not just unverifiable"),
    _Case("P02", "files-absent-on-disk",
          _m_nonfixture([{"relpath": "absent.bin",
                          "sha256": "0" * 64}]),
          r"source_files|not a regular file|evidence",
          "no-false-negative: root exists, declared files do not"),
    _Case("P02", "digest-mismatch-real-files",
          _m_nonfixture(
              [{"relpath": "real.bin", "sha256": "f" * 64}],
              files_on_disk={"real.bin": b"real source bytes"}),
          r"sha256|digest|source_files|evidence",
          "no-false-negative: real files whose bytes do not match "
          "declared digests — a carried digest is verified never "
          "trusted"),

    # ---- P03: inside-root symlink to inside-root file ---------------
    _Case("P03", "inside-root-symlink",
          _m_nonfixture(
              [{"relpath": "link.bin",
                "sha256": hashlib.sha256(
                    b"target bytes").hexdigest()}],
              files_on_disk={"target.bin": b"target bytes"},
              declared_sha=hashlib.sha256(
                  b"target bytes").hexdigest(),
              symlink_case=True),
          r"symlink|source_files|link",
          "no-false-negative: the declared sha256 is CORRECT for the "
          "target's bytes — only a pre-resolution symlink refusal "
          "rejects this"),

    # ---- P04: run_manifest forgery ----------------------------------
    _Case("P04", "record_type-forged",
          _m_run_manifest("record_type", "ForgedManifest"),
          r"record_type|run_manifest|RunManifest",
          "no-false-negative: run_manifest_digest is honestly "
          "rehashed — only record-type strictness rejects"),
    _Case("P04", "record_type-absent",
          _m_run_manifest("record_type", delete=True),
          r"record_type|run_manifest|RunManifest",
          "no-false-negative: absent record_type is not an implicit "
          "pass"),
    _Case("P04", "extra-field",
          _m_run_manifest("forged_field", 1),
          r"run_manifest|record|field|unexpected|RunManifest",
          "no-false-negative: unknown fields reject under the exact "
          "RunManifestV0 key set"),
    _Case("P04", "seed-wrong",
          _m_run_manifest("seed", 99),
          r"seed|run_manifest",
          "no-false-negative: run_manifest.seed must equal "
          "seeds_declared[0]"),
    _Case("P04", "input_digests-missing-ibd",
          _m_run_manifest("input_digests", ["0" * 64]),
          r"input_digest|run_manifest",
          "no-false-negative: input_digests must contain the "
          "payload's input_bytes_digest"),
    _Case("P04", "output_digests-missing-ad",
          _m_run_manifest("output_digests", ["0" * 64]),
          r"output_digest|run_manifest|assignment",
          "no-false-negative: output_digests must contain the "
          "payload's assignment_digest"),
    _Case("P04", "environment-digest-mismatch",
          _m_run_manifest("environment_digest", _sha_text("other-env")),
          r"environment|run_manifest",
          "no-false-negative: run_manifest.environment_digest must "
          "equal the artifact's environment_digest"),

    # ---- P05: typed fit-partition row keys --------------------------
    _Case("P05", "train_row_keys-digest-forged",
          _m_fit_partition_rowkeys,
          r"train_row_keys|fit_partition|row_keys",
          "no-false-negative: fit_partition_digest AND the envelope "
          "are rehashed — only semantic row-key recomputation from "
          "assignments+unit_basin_map rejects"),

    # ---- P06: forecast feature payload row binding ------------------
    _Case("P06", "ffp-row_keys-forged",
          _m_ffp("row_keys_digest", "1" * 64),
          r"row_keys|forecast",
          "no-false-negative: a well-formed 64-hex WRONG row-key "
          "digest — only semantic recomputation rejects",
          forecast=True),
    _Case("P06", "ffp-row_count-off-by-one",
          _m_ffp("row_count", lambda p: p["n_rows"] + 1),
          r"row_count|forecast",
          "no-false-negative: row_count disagrees with the artifact's "
          "n_rows — the typed copy cannot drift from the bound frame",
          forecast=True),

    # ---- P07: config <-> artifact cross-binding ---------------------
    _Case("P07", "config-mode-flip",
          _m_config("mode", "FORECAST_REGIME"),
          r"config|mode",
          "no-false-negative: config_digest honestly recomputed — "
          "only the config.mode vs artifact.mode cross-bind rejects"),
    _Case("P07", "config-seeds-changed",
          _m_config("seeds", [7, 8, 9]),
          r"config|seed",
          "no-false-negative: config.seeds must agree with the "
          "artifact's declared seeds"),
    _Case("P07", "config-source_manifest-differs",
          _m_config("source_manifest",
                    {"fixture": True, "forged": "extra"}),
          r"config|source_manifest",
          "no-false-negative: config.source_manifest must equal "
          "payload.source_manifest"),

    # ---- P08: semantic feature-matrix digest ------------------------
    _Case("P08", "feature_matrix_digest-forged",
          _m_feature_matrix,
          r"feature_matrix|feature matrix|fmx",
          "no-false-negative: every typed copy of the digest is "
          "updated AND the envelope rehashed — only recomputation "
          "from input_values rejects"),

    # ---- P09: pathological covariances ------------------------------
    _Case("P09", "negative-diagonal",
          _m_covariance([[-0.25, 0.0], [0.0, 0.25]]),
          r"covariance|semidefinite|positive|model|PSD",
          "no-false-negative: symmetric with a negative diagonal — "
          "PSD eigenvalue check must run in the shared floor, not "
          "only in the auditor"),
    _Case("P09", "zero-covariance",
          _m_covariance([[0.0, 0.0], [0.0, 0.0]]),
          r"covariance|singular|semidefinite|positive|model",
          "no-false-negative: the zero matrix IS positive-semidefinite "
          "under a -1e-9 tolerance — a singular covariance is still "
          "pathological"),
    _Case("P09", "rank-deficient",
          _m_covariance([[1.0, 1.0], [1.0, 1.0]]),
          r"covariance|singular|semidefinite|positive|model",
          "no-false-negative: rank-1 [[1,1],[1,1]] has eigenvalue 0 — "
          "singular-but-PSD must not certify"),

    # ---- P10: unit->basin partition ---------------------------------
    _Case("P10", "unit-none",
          _m_ubm(lambda m: [[None, m[0][1]]] + m[1:]),
          r"unit_basin|unit|partition",
          "no-false-negative: a (None, basin) pair is well-formed "
          "length-2 — the unit must be a real string id"),
    _Case("P10", "unit-missing-coverage",
          _m_ubm(lambda m: m[:-1]),
          r"unit_basin|coverage|unit",
          "no-false-negative: one assignment unit unmapped — the "
          "partition must cover the assignment universe"),
    _Case("P10", "unit-duplicated",
          _m_ubm(lambda m: m + [list(m[0])]),
          r"duplicate|unit_basin|unit",
          "no-false-negative: duplicated unit — the partition is not "
          "well-defined"),
    _Case("P10", "ubm-digest-stale",
          _m_ubm_digest_stale,
          r"unit_basin_map_digest|unit_basin|digest",
          "no-false-negative: the carried map digest is verified "
          "against the map, never trusted"),

    # ---- P12: audit-grade seed/gate/null/status semantics -----------
    _Case("P12", "seed_coverage-missing-seed",
          _m_seed_coverage,
          r"seed_coverage|seed",
          "no-false-negative: a declared seed unaccounted for — "
          "coverage keys must equal seeds_declared exactly"),
    _Case("P12", "required_gates-missing-gate",
          _m_gate_delete,
          r"required_gates|gate",
          "no-false-negative: an absent gate is not a closed gate — "
          "the map must carry exactly the declared universe"),
    _Case("P12", "gate-value-string-PASS",
          _m_gate_string,
          r"gate|boolean|required_gates",
          "no-false-negative: the string 'PASS' is not True — truthy "
          "non-bool gate values cannot certify a terminal status"),
    _Case("P12", "descriptive-over-open-gate",
          _m_gate_false,
          r"gate|required_gates|DESCRIPTIVE",
          "no-false-negative: DESCRIPTIVE_REGIME_ONLY over a False "
          "gate — status cannot outrun its own gate evidence"),
    _Case("P12", "nulls-missing-family",
          _m_null_family,
          r"null|season_matched|family",
          "no-false-negative: a required null family absent — "
          "null_model_digest is honestly rehashed over the reduced "
          "set"),
]

BOUNDARIES = ("freeze", "adapter", "audit", "association")


def _mutated_payload(case: _Case, tmp_path: Path) -> dict:
    payload = _canonical_payload(
        mode="FORECAST_REGIME" if case.forecast
        else "RETROSPECTIVE_REGIME")
    skip = case.mut(payload, tmp_path) or set()
    return _rehash(payload, skip=skip)


# ---------------------------------------------------------------------
# Positive controls — the unmutated payloads pass every boundary
# ---------------------------------------------------------------------

class TestPositiveControls:
    def test_canonical_payload_freezes(self):
        frozen = freeze_regime_artifact(
            _pre_freeze(_canonical_payload()))
        assert frozen["frozen"] is True
        # the B1 envelope rule: freeze_digest covers the payload
        # minus {freeze_digest, frozen}
        assert frozen["freeze_digest"] == sha256_canonical(
            {k: v for k, v in frozen.items()
             if k not in ("freeze_digest", "frozen")})

    def test_canonical_forecast_payload_freezes(self):
        frozen = freeze_regime_artifact(
            _pre_freeze(_canonical_payload("FORECAST_REGIME")))
        assert frozen["frozen"] is True

    def test_canonical_payload_adapts_and_carries_ubm(self):
        artifact = regime_assignment_from_artifact(
            _canonical_payload(), artifact_id="r9-canonical")
        # SPECULATIVE — the Round-9 adapter must carry the bound
        # unit→basin partition onto the contract artifact.
        assert dict(artifact.unit_basin_map) == dict(UNIT_BASINS)

    def test_canonical_payload_audits_clean(self):
        assert audit_producer_payload(_canonical_payload()) == []

    def test_canonical_payload_binds_in_association(self, assoc):
        report = run_association(
            assoc["artifact"], assoc["events"], assoc["controls"],
            assoc["unit_basins"], holdout=assoc["holdout"],
            region_basins=REGION_BASINS,
            opportunities=assoc["opportunities"],
            n_boot=10, producer_payload=_canonical_payload())
        assert report is not None
        assert report.binding == "verified_producer_payload"


# ---------------------------------------------------------------------
# The mutation matrix — every finding fails closed at every boundary
# ---------------------------------------------------------------------

class TestR9MutationMatrix:
    @pytest.mark.parametrize("case", MUTATION_CASES,
                             ids=lambda c: f"{c.finding}-{c.case_id}")
    @pytest.mark.parametrize("boundary", BOUNDARIES)
    def test_finding_fails_closed(self, case: _Case, boundary: str,
                                  tmp_path: Path, assoc: dict):
        payload = _mutated_payload(case, tmp_path)
        out = _boundary_output(boundary, payload, assoc)
        # Failure evidence: the rejection text names the finding's
        # category — a bare digest-chain rejection would not prove
        # the fix (the envelope was honestly rehashed).
        if not case.evidence.search(out):
            # The association boundary may legitimately reject on the
            # binding layer when the adapter already refused the
            # payload upstream (freeze_digest mismatch) — still
            # fail-closed, weaker evidence, recorded here.
            assert boundary == "association" and \
                "producer_payload" in out, \
                f"{case.finding}/{case.case_id}@{boundary}: " \
                f"rejection does not name the category: {out[:500]}"


# ---------------------------------------------------------------------
# P11 — unit->basin binding through association (not a payload
# mutation — a caller-side binding violation)
# ---------------------------------------------------------------------

class TestP11UnitBasinAssociationBinding:
    def test_caller_unit_basins_must_byte_equal_artifact_map(
            self, assoc):
        """One unit remapped to another DECLARED basin — every other
        binding check still passes, so only the unit→basin equality
        gate can reject."""
        artifact = assoc["artifact"]
        # SPECULATIVE — Round-9 artifact carries unit_basin_map; the
        # caller's map must equal it byte-for-byte.
        caller = dict(getattr(artifact, "unit_basin_map", UNIT_BASINS))
        assert caller.get("unit-koshi-0") == "koshi"
        caller["unit-koshi-0"] = "gandaki"   # still an eval basin
        with pytest.raises(ValueError,
                           match="unit|basin|map") as ei:
            run_association(
                artifact, assoc["events"], assoc["controls"],
                caller, holdout=assoc["holdout"],
                region_basins=REGION_BASINS,
                opportunities=assoc["opportunities"],
                n_boot=10,
                producer_payload=_canonical_payload())
        assert "unit" in str(ei.value).lower()

    def test_producer_payload_map_mismatch_rejected(self, assoc):
        """A producer payload whose unit_basin_map remaps a unit —
        honestly rehashed — must not bind to the canonical artifact."""
        payload = _canonical_payload()
        for entry in payload["unit_basin_map"]:
            if entry[0] == "unit-koshi-0":
                entry[1] = "gandaki"     # still inside declared groups
        _rehash(payload)
        with pytest.raises(ValueError) as ei:
            run_association(
                assoc["artifact"], assoc["events"],
                assoc["controls"], assoc["unit_basins"],
                holdout=assoc["holdout"],
                region_basins=REGION_BASINS,
                opportunities=assoc["opportunities"],
                n_boot=10, producer_payload=payload)
        assert re.search(r"unit|basin|map|producer_payload",
                         str(ei.value), re.IGNORECASE)


# ---------------------------------------------------------------------
# Contract-helper parity (SPECULATIVE — guarded until the lanes land)
# ---------------------------------------------------------------------

class TestContractHelperParity:
    def test_helper_semantics_match_producer(self):
        if not _R9_HELPERS:
            pytest.skip("Round-9 producer_validation helpers not "
                        "landed yet")
        is_fx, probs = fixture_flag({"fixture": True})
        assert is_fx is True and probs == []
        for bad in ("true", 1, "yes"):
            is_fx, probs = fixture_flag({"fixture": bad})
            assert is_fx is False and probs, bad
        is_fx, probs = fixture_flag({"fixture": False})
        assert is_fx is False and probs == []
        is_fx, probs = fixture_flag({})
        assert is_fx is False and probs == []
        assert row_key("unit-a", "2020-01-02") == "unit-a|2020-01-02"
        keys = ["b|2020-01-02", "a|2020-01-01"]
        assert sorted_row_key_digest(keys) == \
            _sorted_row_key_digest(keys)
        vals = [[0.125, 1.0], [2.5, 3.125]]
        assert semantic_feature_matrix_digest(vals) == \
            _semantic_fmx_digest(vals)
        pairs = canonical_unit_basin_pairs(
            [("b", "g2"), ("a", "g1")])
        assert sha256_canonical(pairs) == _ubm_digest(
            [("b", "g2"), ("a", "g1")])

    def test_validator_accepts_canonical_payload(self):
        if not _R9_HELPERS:
            pytest.skip("Round-9 producer_validation not landed yet")
        assert validate_producer_payload(_canonical_payload()) == []
        assert validate_producer_payload(
            _canonical_payload("FORECAST_REGIME")) == []


# ---------------------------------------------------------------------
# Matrix accounting — prove the census the report claims
# ---------------------------------------------------------------------

class TestMatrixAccounting:
    def test_every_finding_has_cases(self):
        covered = {c.finding for c in MUTATION_CASES}
        expected = {f"P{i:02d}" for i in range(1, 13)} - {"P11"}
        assert covered == expected, (
            f"findings without mutation cases: "
            f"{sorted(expected - covered)}")

    def test_case_count_per_finding(self):
        counts: dict[str, int] = {}
        for c in MUTATION_CASES:
            counts[c.finding] = counts.get(c.finding, 0) + 1
        # minimum mutations per finding per the audit spec
        floors = {"P01": 3, "P02": 3, "P03": 1, "P04": 5, "P05": 1,
                  "P06": 2, "P07": 3, "P08": 1, "P09": 2, "P10": 4,
                  "P12": 5}
        for f, n in floors.items():
            assert counts.get(f, 0) >= n, \
                f"{f}: {counts.get(f, 0)} mutations < spec floor {n}"

    def test_every_case_uses_all_boundaries(self):
        # the matrix parametrizes every case over every boundary —
        # this is structural documentation of the coverage claim
        assert len(BOUNDARIES) == 4
        assert all(c.finding and c.case_id and c.mut
                   for c in MUTATION_CASES)


# ---------------------------------------------------------------------
# R9-V residuals — the independent adversary's matrix-missed holes.
# Each test builds the strongest honest-forger variant (full rehash)
# and asserts the NEW floor or the association binding rejects on the
# finding's own semantics.  Honest controls re-prove no regression.
# ---------------------------------------------------------------------

class TestR9AdversarialResiduals:

    def test_v1_verified_binding_requires_adapter_admissibility(
            self, assoc):
        """dataclasses.replace'd artifact + self-consistent
        CANDIDATE_ONLY payload: every digest is honest, the floor
        passes it (an honest demotion is a legal artifact), and the
        binding must STILL refuse the 'verified' claim — the payload
        is one the canonical adapter would never emit."""
        payload = _canonical_payload()
        payload["status"] = "CANDIDATE_ONLY"
        payload["terminal"] = False
        payload["associable"] = False
        payload["stability"]["required_gates"][
            "season_matched_null"] = False
        _rehash(payload)
        assert validate_producer_payload(payload) == []
        forged = dataclasses.replace(
            assoc["artifact"],
            regime_digest=payload["freeze_digest"],
            producer_payload_digest=payload["freeze_digest"])
        with pytest.raises(ValueError,
                           match="associable|status|data_class"):
            run_association(
                forged, assoc["events"], assoc["controls"],
                assoc["unit_basins"], holdout=assoc["holdout"],
                region_basins=REGION_BASINS,
                opportunities=assoc["opportunities"], n_boot=10,
                producer_payload=payload)

    def test_v1_honest_payload_still_binds_verified(self, assoc):
        rep = run_association(
            assoc["artifact"], assoc["events"], assoc["controls"],
            assoc["unit_basins"], holdout=assoc["holdout"],
            region_basins=REGION_BASINS,
            opportunities=assoc["opportunities"], n_boot=10,
            producer_payload=_canonical_payload())
        assert rep.binding == "verified_producer_payload"

    def test_v2_fit_heldout_group_overlap_rejected(self):
        payload = _canonical_payload()
        straddled = [payload["fit_groups"][0]]
        payload["heldout_groups_declared"] = straddled
        payload["fit_partition"]["heldout_groups"] = straddled
        payload["config"]["heldout_groups"] = straddled
        _rehash(payload)
        assert any("overlap" in p for p in
                   validate_producer_payload(payload))

    def test_v3_duplicate_assignment_rows_rejected(self):
        payload = _canonical_payload()
        row = list(payload["assignments"][0])
        payload["assignments"].append(row)
        n = len(payload["assignments"])
        payload["n_rows"] = n
        payload["n_train_rows"] = n
        payload["input_values"].append(
            list(payload["input_values"][0]))
        payload["input_bytes_digest"] = _input_bytes_digest(
            payload["input_values"])
        payload["feature_matrix_digest"] = _semantic_fmx_digest(
            payload["input_values"])
        payload["input_schema"]["n_rows"] = n
        payload["input_schema"]["shape"] = [
            n, len(payload["feature_cols"])]
        keys = [_row_key(u, d) for u, d, _ in payload["assignments"]]
        payload["preprocessing"]["row_keys_digest"] = \
            _sorted_row_key_digest(keys)
        grp = dict(payload["unit_basin_map"])
        tk = [_row_key(u, d) for u, d, _ in payload["assignments"]
              if grp.get(u) in payload["fit_partition"]["train_groups"]]
        fp = payload["fit_partition"]
        fp["train_row_keys_digest"] = _sorted_row_key_digest(tk)
        fp["n_rows"] = n
        fp["n_train_rows"] = len(tk)
        fp["feature_matrix_digest"] = payload["feature_matrix_digest"]
        payload["run_manifest"]["input_digests"] = [
            payload["input_bytes_digest"]]
        payload["missingness_applied"]["train_rows_total"] = n
        payload["missingness_applied"]["train_rows_fitted"] = n
        _rehash(payload)
        assert any("duplicate" in p for p in
                   validate_producer_payload(payload))

    def test_v4_input_bytes_digest_material_bound(self):
        """Every digest consistent except the byte-domain claim —
        only decoding input_values to float64 and rehashing catches
        a fabricated input_bytes_digest."""
        payload = _canonical_payload()
        payload["input_bytes_digest"] = "f" * 64
        payload["run_manifest"]["input_digests"] = ["f" * 64]
        _rehash(payload)
        assert any("input_bytes_digest" in p for p in
                   validate_producer_payload(payload))

    def test_v5_flat_forecast_fields_under_retro_rejected(self):
        for field, value in (
                ("forecast_vintage_digests", ["a" * 64]),
                ("forecast_feature_set", ["synth_f1"])):
            payload = _canonical_payload()
            payload[field] = list(value)
            payload["config"][field] = list(value)
            _rehash(payload)
            assert any("forecast" in p for p in
                       validate_producer_payload(payload)), field

    @pytest.mark.parametrize("field,value", [
        ("k", None), ("k", "3"), ("k", True),
        ("seeds", None), ("seeds", []),
        ("seeds_declared", [11, 11, 42]),
        ("occupancy", None), ("occupancy", [-0.5, 1.0, 0.5]),
        ("feature_cols", None), ("feature_cols", []),
        ("fitted_on", "ALL_DATA"),
        ("associable", "yes"), ("terminal", "yes"),
        ("status", "FABRICATED"),
        ("modal_k_frequency", None),
    ])
    def test_v6_scalar_type_floors(self, field, value):
        payload = _canonical_payload()
        payload[field] = value
        if field in ("seeds", "seeds_declared"):
            payload["config"]["seeds"] = list(value) \
                if isinstance(value, list) else value
        _rehash(payload)
        assert validate_producer_payload(payload), field

    def test_v7_assignment_row_calendar_and_label_bounds(self):
        for mutate, token in (
                (lambda p: p["assignments"][0].__setitem__(
                    1, "2020-13-99"), "calendar|date"),
                (lambda p: p["assignments"][0].__setitem__(
                    0, "   "), "unit_id"),
                (lambda p: p["assignments"][0].__setitem__(
                    2, 99), "regime_id")):
            payload = _canonical_payload()
            mutate(payload)
            keys = [_row_key(u, d)
                    for u, d, _ in payload["assignments"]]
            payload["preprocessing"]["row_keys_digest"] = \
                _sorted_row_key_digest(keys)
            grp = dict(payload["unit_basin_map"])
            fp = payload["fit_partition"]
            tk = [_row_key(u, d) for u, d, _ in payload["assignments"]
                  if grp.get(u) in fp["train_groups"]]
            fp["train_row_keys_digest"] = \
                _sorted_row_key_digest(tk)
            _rehash(payload)
            out = validate_producer_payload(payload)
            assert out, token

    def test_v8_bound_digests_are_required(self):
        for field in ("environment_digest", "run_manifest_digest"):
            payload = _canonical_payload()
            _rehash(payload)
            payload.pop(field)
            payload["regime_artifact_digest"] = sha256_canonical(
                {k: v for k, v in payload.items()
                 if k not in ("regime_artifact_digest",
                              "freeze_digest", "frozen")})
            payload["freeze_digest"] = sha256_canonical(
                {k: v for k, v in payload.items()
                 if k not in ("freeze_digest", "frozen")})
            assert any(field in p or "PROVENANCE" in p
                       for p in validate_producer_payload(
                           payload)), field

    def test_v9_model_width_bound_to_feature_cols(self):
        payload = _canonical_payload()
        payload["model"]["means"] = [
            [1.0, 2.0, 3.0]] * len(payload["model"]["means"])
        payload["model"]["covariances"] = [
            [[0.25, 0.0, 0.0], [0.0, 0.25, 0.0], [0.0, 0.0, 0.25]]
        ] * len(payload["model"]["covariances"])
        _rehash(payload)
        assert any("feature_cols" in p or "dimension" in p
                   for p in validate_producer_payload(payload))

    def test_v10_missingness_accounting_must_close(self):
        payload = _canonical_payload()
        payload["missingness_applied"]["train_rows_dropped"] = 5
        _rehash(payload)
        assert any("missingness" in p for p in
                   validate_producer_payload(payload))

    def test_v11_per_seed_best_k_inside_candidates(self):
        payload = _canonical_payload()
        payload["per_seed_best_k"][
            str(payload["seeds_declared"][0])] = 99
        _rehash(payload)
        assert any("per_seed_best_k" in p for p in
                   validate_producer_payload(payload))

    def test_v11_typed_sections_reject_extra_fields(self):
        payload = _canonical_payload()
        payload["fit_partition"]["forged_field"] = 1
        _rehash(payload)
        assert any("fit_partition" in p for p in
                   validate_producer_payload(payload))

    def test_v11_cutoff_iso_calendar_valid(self):
        payload = _canonical_payload()
        payload["fit_partition"]["cutoff_iso"] = "2020-13-99"
        _rehash(payload)
        assert any("cutoff_iso" in p for p in
                   validate_producer_payload(payload))

    def test_v11_null_replicate_accounting(self):
        """Executed replicates cannot exceed the declared count —
        a skipped family legitimately reports 0+0, so the bound
        is >, not !=."""
        payload = _canonical_payload()
        fam = payload["nulls"]["shuffled"]
        fam["n_succeeded"] = fam["n_replicates"] + 5
        _rehash(payload)
        assert any("n_replicates" in p for p in
                   validate_producer_payload(payload))

    def test_v12_unhashable_input_value_is_problem_not_crash(self):
        payload = _canonical_payload()
        payload["input_values"][0][0] = {"forged": 1}
        _rehash(payload)
        out = validate_producer_payload(payload)  # must not raise
        assert any("undecodable" in p or "SCHEMA" in p
                   for p in out)
