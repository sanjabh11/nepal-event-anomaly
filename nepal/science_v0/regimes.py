"""Multi-region retrospective regime runner (REGIME_PROTOCOL_V0.md).

Descriptive-only machinery: GMM over reanalysis-class feature matrices
across >=3 geographic groups, with K=1 null mandatory, >=3 seeds,
train-only preprocessing, stability axes, and two null families.

Terminal statuses are limited to the protocol vocabulary:
    DESCRIPTIVE_REGIME_ONLY / UNSUPERVISED_STRUCTURE_NOT_STABLE /
    CANDIDATE_ONLY / RUN_ERROR

Round-3 hardening implemented here:

* REG-C01 — the temporal-block bootstrap REFITS preprocessing + GMM
  inside every contiguous-date replicate and emits 95% percentile
  intervals for mixture weights, per-component-per-feature means,
  occupancy, and mean max-posterior, with explicit replicate-failure
  accounting (>10% failures fails the axis).
* REG-C02 — every cross-refit comparison (LORO, season, elevation,
  bootstrap, missingness) aligns component IDs to the reference
  modal-K model via deterministic minimum-cost assignment
  (exhaustive permutation search, lexicographic component-index
  tie-break); label-invariant ARI is emitted per fold.
* REG-C03 — every declared seed is accounted for in
  ``seed_coverage``; a missing/failed seed demotes the run to
  UNSUPERVISED_STRUCTURE_NOT_STABLE; fold refits honour the declared
  ``fold_seed_policy``.
* REG-C04 — null families compare the SAME predeclared statistic
  (silhouette) on observed vs >=50 empirical null replicates;
  p = fraction of null statistics >= observed; the declared
  ``null_alpha`` thresholds the envelope; any nonconverged/missing
  null replicate fails the gate.
* REG-C05 — missingness executes exactly the declared
  ``missingness_policy`` (listwise | bounded_impute | stratified);
  an optional declared ``effort_col`` is evaluated; unavailable axes
  are NOT_APPLICABLE with an explicit reason — never a silent pass.
* REG-C06 — era diagnostics run only over DECLARED
  ``era_boundaries``; an era column without declared boundaries fails
  the axis with an explicit reason.  The elevation axis additionally
  records whether regime assignment is marginally explained by
  elevation alone (per-band occupancy JS + note).
* REG-C07 — pathological feature columns (sparse finite counts,
  low group coverage, non-finite after imputation, constant) are
  rejected BEFORE fitting with RUN_ERROR.
* PROV-C03 — ``freeze_regime_artifact`` re-derives
  assignment_digest, config_digest, input_bytes_digest, and
  regime_artifact_digest at freeze time and enforces gate/status
  consistency; a direct freeze call cannot bypass the producer
  audit.

Round-6 hardening implemented here:

* PROV-01 — freeze rejects truthy non-bool ``required_gates``
  values; every gate value must be a real boolean.
* REG-02 — ``_iso_date_ok`` is calendar-valid (strptime), not just
  shape-valid: ``2020-13-99`` rejects.
* REG-03 — every stability refit (missingness, effort, era) is
  intersected with the policy-selected fit surface; dropped rows
  can never re-enter a refit.
* REG-04 — declared ``effort_split="first10"`` fails closed on a
  numeric effort column; the token executes as named or not at all.
* REG-05a — each null replicate binds ``input_digest`` (sha256 of
  the generated float64 matrix) inside the hashed ``family_digest``
  material.
* FCST-01 — declared ``mode`` is a RegimeMode value;
  FORECAST_REGIME requires bound ``forecast_vintage_digests`` and
  ``forecast_feature_set`` and emits data_class
  ARCHIVED_OPERATIONAL; RETROSPECTIVE_REGIME emits REANALYSIS and
  must carry no forecast fields — enforced at config validation and
  again at freeze.
* PROV-03 — non-fixture source manifests are byte-verified
  (``verify_source_evidence``): every ``source_files`` entry must
  resolve inside ``evidence_root`` and hash to its declared digest,
  and the verified multiset must equal ``source_digests``.

Round-9 hardening implemented here:

* R9-P01 — the source-manifest ``fixture`` marker is a strict
  boolean via the shared ``fixture_flag`` floor; a truthy non-bool
  (e.g. the string ``"true"``) is RUN_ERROR, never a synthetic
  bypass.
* R9-P02 — ``freeze_regime_artifact`` re-verifies non-fixture source
  evidence defense-in-depth after the shared validator.
* R9-P04 — the run manifest is deserialized through the typed
  ``records.deserialize_record`` boundary (exact ``RunManifestV0``
  record_type + field-set) and bound to input_bytes_digest,
  assignment_digest, environment_digest, and seeds_declared[0].
* R9 — feature-matrix and row-key digests are emitted through the
  shared canonical helpers (``semantic_feature_matrix_digest``,
  ``row_key``/``sorted_row_key_digest``), ``unit_basin_map_digest``
  is bound inside the envelope, and the serialized config's
  forecast fields/source_manifest are normalized to the artifact
  copies.

Round-10 hardening implemented here:

* R10-P04 — the unit/group/season (and declared era) carrier
  columns are preflighted for missing and whitespace-only values
  BEFORE any mask/fit access: ``.astype(str)`` would silently turn
  NaN into the literal 'nan' and None into 'None', so a malformed
  frame could inject phantom labels into unit_basin_map, the
  assignment sidecar, and the null strata.
* R10 — ``RegimeRunConfig.forecast_vintages`` binds the serialized
  ForecastVintageV0-shaped evidence records behind
  ``forecast_vintage_digests``: FORECAST_REGIME emits them verbatim
  as the artifact-level ``forecast_vintages`` section inside the
  ``regime_artifact_digest`` envelope (an associable forecast
  artifact carries its vintage records, not only their digests);
  the serialized config keeps exactly the declared 33-field
  contract — the records are evidence, never configuration.
* R10-P07 parity — the non-fixture ``source_manifest`` preflight
  mirrors the shared floor's exact typed contract (declared keys
  only, non-empty-string ``source_id``/``lineage``/
  ``evidence_root``, non-empty string sequences for ``units``/
  ``feature_allowlist``, 64-hex ``source_digests``/
  ``source_files[].sha256``, no duplicate relpaths) so the run can
  never emit an artifact freeze would reject on manifest shape.

This module never reads event labels, never emits forecast or
precursor language, and never tunes on locked test basins.
"""

from __future__ import annotations

import bisect
import copy
import dataclasses
import hashlib
import itertools
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Sequence

import numpy as np
import pandas as pd
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer

from nepal.research_v0._hashing import (
    sha256_canonical, verify_source_evidence)
from nepal.research_v0.gates import REQUIRED_REGIME_GATE_NAMES
from nepal.research_v0.policy import ForecastDataClass, RegimeMode
from nepal.research_v0.producer_validation import (
    canonical_unit_basin_pairs, fixture_flag, row_key,
    semantic_feature_matrix_digest, sorted_row_key_digest,
    validate_producer_payload)
from nepal.research_v0.records import (
    RETROSPECTIVE_REGIME_DATA_CLASSES,
    SEISMIC_WAVEFORM_RETROSPECTIVE_DATA_CLASS, RunManifestV0,
    deserialize_record)

STATUSES = frozenset({
    "DESCRIPTIVE_REGIME_ONLY",
    "UNSUPERVISED_STRUCTURE_NOT_STABLE",
    "CANDIDATE_ONLY",
    "RUN_ERROR",
})

K_CANDIDATES = (1, 2, 3, 4, 5)
MIN_SEEDS = 3
MIN_GEO_GROUPS = 3
MIN_BOOTSTRAP = 200
MIN_NULL_REPLICATES = 50
MIN_COL_FINITE = 50
LORO_JS_MAX = 0.35
LORO_ARI_MIN = 0.6
BOOTSTRAP_MAX_FAILURE_RATE = 0.10
MISSINGNESS_POLICIES = ("listwise", "bounded_impute", "stratified")
FOLD_SEED_POLICIES = ("all", "first")
NULL_STATISTIC = "silhouette"


def _sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _digest(obj) -> str:
    """Strict canonical digest — no default=str fallback, so two
    distinct non-JSON-native objects can never collide in the digest
    domain (DIG-01)."""
    return sha256_canonical(obj)


def _finite_or_token(v):
    """Canonical-JSON-safe scalar: finite floats stay floats;
    non-finite values become explicit string tokens so a failed fit
    (bic=inf) can never crash a bound digest."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    if np.isnan(f):
        return "NaN"
    if np.isinf(f):
        return "Infinity" if f > 0 else "-Infinity"
    return f


# ------------------------------------------------- input byte binding
#
# The raw feature matrix is bound as exact float64 C-order bytes.
# Because sha256_canonical rejects non-finite floats, the artifact
# carries an explicitly encoded copy (``input_values``) in which NaN
# is None and +-inf are string tokens; ``_decode_input_values``
# reconstructs the exact byte domain at freeze time (PROV-C03).

def _encode_input_values(arr: np.ndarray) -> list:
    out = []
    for row in np.asarray(arr, dtype=np.float64):
        enc = []
        for v in row:
            if np.isnan(v):
                enc.append(None)
            elif np.isinf(v):
                enc.append("Infinity" if v > 0 else "-Infinity")
            else:
                enc.append(float(v))
        out.append(enc)
    return out


def _decode_input_values(vals) -> np.ndarray:
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


# ------------------------------------------------------- preprocessing

@dataclass
class TrainOnlyPreprocessor:
    """Imputer + scaler fit on TRAIN rows only.

    fit() must be called with train-group rows exclusively; any
    transform of validation/test rows reuses the frozen parameters.
    """
    fitted_rows: int = 0
    _imputer: SimpleImputer = field(
        default_factory=lambda: SimpleImputer(strategy="median"))
    _scaler: StandardScaler = field(default_factory=StandardScaler)

    def fit(self, train_df: pd.DataFrame):
        self._imputer.fit(train_df)
        self._scaler.fit(self._imputer.transform(train_df))
        self.fitted_rows = len(train_df)
        return self

    def transform(self, df: pd.DataFrame) -> np.ndarray:
        if not self.fitted_rows:
            raise RuntimeError("preprocessor not fitted")
        return self._scaler.transform(self._imputer.transform(df))

    def digest(self) -> str:
        return _digest({"mean": self._scaler.mean_.tolist(),
                        "var": self._scaler.var_.tolist(),
                        "fitted_rows": self.fitted_rows})


# ------------------------------------------------------------- fitting

def _fit_gmm(X: np.ndarray, k: int, seed: int) -> dict:
    try:
        g = GaussianMixture(n_components=k, covariance_type="full",
                            random_state=int(seed), max_iter=500,
                            n_init=1)
        g.fit(X)
        return {"k": k, "seed": int(seed), "bic": float(g.bic(X)),
                "aic": float(g.aic(X)),
                "converged": bool(g.converged_), "model": g}
    except Exception:
        return {"k": k, "seed": int(seed), "bic": np.inf,
                "aic": np.inf, "converged": False, "model": None}


def _ari(a: np.ndarray, b: np.ndarray) -> float:
    """Adjusted Rand Index — label-invariant partition agreement."""
    from sklearn.metrics import adjusted_rand_score
    return float(adjusted_rand_score(a, b))


def _silhouette(X: np.ndarray, labels: np.ndarray):
    """Predeclared null statistic (REG-C04). Returns None when the
    statistic is undefined (single cluster / degenerate labels)."""
    from sklearn.metrics import silhouette_score
    labels = np.asarray(labels)
    if len(labels) < 3 or len(set(labels.tolist())) < 2:
        return None
    try:
        return float(silhouette_score(X, labels))
    except Exception:
        return None


def _js_divergence(p: np.ndarray, q: np.ndarray) -> float:
    p = np.asarray(p, float); q = np.asarray(q, float)
    p = p / p.sum(); q = q / q.sum()
    m = 0.5 * (p + q)
    def kl(a, b):
        mask = a > 0
        return float(np.sum(a[mask] * np.log2(a[mask] / b[mask])))
    return 0.5 * kl(p, m) + 0.5 * kl(q, m)


# ------------------------------------------- component label alignment

def _means_in_ref_space(means_scaled: np.ndarray,
                        src_prep: TrainOnlyPreprocessor,
                        ref_prep: TrainOnlyPreprocessor) -> np.ndarray:
    """Map component means fitted in ``src_prep``'s standardized space
    into ``ref_prep``'s standardized space via the shared raw space.

    StandardScaler maps raw x -> (x - mean) / scale, so a mean fitted
    in src space un-scales through src parameters and re-standardizes
    through ref parameters.  Imputation does not shift a fitted
    component mean; it only precedes the affine map.
    """
    s = np.asarray(src_prep._scaler.scale_, float)
    m = np.asarray(src_prep._scaler.mean_, float)
    rs = np.asarray(ref_prep._scaler.scale_, float)
    rm = np.asarray(ref_prep._scaler.mean_, float)
    raw = np.asarray(means_scaled, float) * s + m
    return (raw - rm) / rs


def _align_to_reference(ref_means: np.ndarray,
                        other_means: np.ndarray) -> tuple:
    """Deterministic minimum-cost assignment of reference component i
    to other component ``perm[i]`` on standardized mean distance
    (Hungarian equivalent; REG-C02).

    K <= 5 in this protocol, so the assignment is computed by
    exhaustive permutation search ordered by (total cost,
    lexicographic component index) — the component-index tie-break is
    exact, never solver- or set-order dependent.
    """
    ref = np.asarray(ref_means, float)
    oth = np.asarray(other_means, float)
    k = ref.shape[0]
    cost = np.linalg.norm(ref[:, None, :] - oth[None, :, :], axis=2)
    if k <= 8:
        best = min(
            itertools.permutations(range(k)),
            key=lambda p: (float(sum(cost[i, p[i]]
                                       for i in range(k))), p))
        return tuple(int(i) for i in best)
    # defensive fallback (unreachable under k_candidates <= 5)
    from scipy.optimize import linear_sum_assignment
    rows, cols = linear_sum_assignment(cost)
    perm = [0] * k
    for r, c in zip(rows.tolist(), cols.tolist()):
        perm[int(r)] = int(c)
    return tuple(perm)


def _aligned(values: np.ndarray, perm: tuple) -> np.ndarray:
    """Reorder a per-component vector/matrix into reference order."""
    return np.asarray([values[perm[i]] for i in range(len(perm))])


def _refit_against_reference(sub_df: pd.DataFrame,
                             ref_prep: TrainOnlyPreprocessor,
                             ref_model,
                             ref_occupancy: np.ndarray,
                             ref_labels_sub: np.ndarray,
                             modal_k: int,
                             decl_seeds: list,
                             fold_seed_policy: str) -> dict:
    """Refit preprocessing + GMM on ``sub_df`` under the declared seed
    policy, align components to the reference model, and return a fold
    record.  ``js`` is the aligned-occupancy JS against the reference
    occupancy; ``ari`` is the label-invariant partition agreement on
    the shared rows.  js = worst (max) and ari = worst (min) across
    the executed seeds — a lucky seed can never hide a divergence.
    """
    rec = {"status": None, "js": None, "ari": None, "reason": None,
           "seeds_declared": [int(s) for s in
                              (decl_seeds if fold_seed_policy == "all"
                               else decl_seeds[:1])],
           "seed_failures": [], "per_seed": {}}
    try:
        p_sub = TrainOnlyPreprocessor().fit(sub_df)
        X_sub = p_sub.transform(sub_df)
        if not np.isfinite(X_sub).all():
            rec["status"] = "FAIL"
            rec["reason"] = ("non-finite values remain after "
                             "refit preprocessing")
            return rec
    except Exception as exc:
        rec["status"] = "FAIL"
        rec["reason"] = f"refit preprocessing failed: {exc}"
        return rec
    for sd in rec["seeds_declared"]:
        f = _fit_gmm(X_sub, modal_k, sd)
        if not f["converged"]:
            rec["seed_failures"].append(int(sd))
            continue
        oth_ref = _means_in_ref_space(f["model"].means_, p_sub,
                                      ref_prep)
        perm = _align_to_reference(ref_model.means_, oth_ref)
        labels_sub = f["model"].predict(X_sub)
        occ = np.bincount(labels_sub,
                          minlength=modal_k) / len(labels_sub)
        occ_al = _aligned(occ, perm)
        js = float(_js_divergence(ref_occupancy, occ_al))
        ari = float(_ari(ref_labels_sub, labels_sub))
        rec["per_seed"][str(int(sd))] = {"js": js, "ari": ari}
    if not rec["per_seed"]:
        rec["status"] = "NONCONVERGED"
        rec["reason"] = "every declared fold seed failed to converge"
        return rec
    # REG-03: under the "all" policy every declared seed must
    # converge — a failed seed demotes the fold outright.
    if fold_seed_policy == "all" and rec["seed_failures"]:
        rec["status"] = "NONCONVERGED"
        rec["reason"] = (f"declared seed(s) {rec['seed_failures']} "
                         "failed to converge on the fold — partial "
                         "seed coverage cannot pass a required axis")
        return rec
    rec["js"] = float(max(v["js"] for v in rec["per_seed"].values()))
    rec["ari"] = float(min(v["ari"] for v in rec["per_seed"].values()))
    rec["status"] = "PASS" if rec["js"] < LORO_JS_MAX else "FAIL"
    if rec["status"] == "FAIL":
        rec["reason"] = (f"aligned occupancy JS {rec['js']:.4f} "
                         f">= {LORO_JS_MAX}")
    return rec


# ------------------------------------------------------------ nulls

def shuffled_null(X: np.ndarray, seed: int) -> np.ndarray:
    """Within-row feature shuffling destroys cross-feature dependence
    while preserving marginals."""
    rng = np.random.default_rng(seed)
    out = X.copy()
    for j in range(out.shape[1]):
        rng.shuffle(out[:, j])
    return out


def season_matched_null(df: pd.DataFrame, feature_cols: list[str],
                        season_col: str, seed: int,
                        era_col: str | None = None) -> np.ndarray:
    """Synthetic samples from same seasonal marginals: resample each
    feature independently within season strata — and within
    (season, era) joint strata when ``era_col`` is bound (REG-04)."""
    rng = np.random.default_rng(seed)
    out = np.empty((len(df), len(feature_cols)))
    if era_col is not None and era_col in df.columns:
        strata = (df[season_col].astype(str) + "|"
                  + df[era_col].astype(str)).to_numpy()
    else:
        strata = df[season_col].to_numpy()
    for si, s in enumerate(pd.unique(strata)):
        idx = np.where(strata == s)[0]
        for j, c in enumerate(feature_cols):
            pool = df[c].to_numpy()[idx]
            pool = pool[np.isfinite(pool)]
            out[idx, j] = rng.choice(pool, size=len(idx)) \
                if len(pool) else np.nan
    return out


def _null_envelope(observed_stat, generator, modal_k: int,
                   decl_seeds: list, n_replicates: int,
                   alpha: float,
                   k_candidates: tuple = K_CANDIDATES) -> dict:
    """Empirical null envelope for the SAME predeclared statistic
    (REG-C04): ``generator(i, gen_seed)`` returns the i-th null
    design matrix (already in fit space) or None on failure; each
    replicate is refit under a cycled declared seed and scored.
    p = fraction of null statistics >= observed.  Any
    nonconverged/missing replicate fails the axis."""
    rec = {"statistic": NULL_STATISTIC,
           "observed": observed_stat,
           "n_replicates": int(n_replicates),
           "n_succeeded": 0, "n_failed": 0,
           "p_value": None, "alpha": float(alpha),
           "status": "FAIL", "reason": None,
           "selection": "bic_sweep_declared_candidates",
           "null_k_distribution": {}}
    if observed_stat is None:
        rec["reason"] = ("observed statistic undefined (K=1 or "
                         "degenerate partition) — the null envelope "
                         "cannot be exceeded")
        return rec
    stats = []
    rec["replicates"] = []
    for i in range(int(n_replicates)):
        gen_seed = int(decl_seeds[0]) + 1000003 * (i + 1)
        fit_seed = int(decl_seeds[i % len(decl_seeds)])
        rep = {"i": i, "gen_seed": gen_seed, "fit_seed": fit_seed,
               "k": None, "stat": None, "ok": False,
               "input_digest": None}
        rec["replicates"].append(rep)
        try:
            X_n = generator(i, gen_seed)
        except Exception:
            X_n = None
        if X_n is None or not np.isfinite(X_n).all():
            rec["n_failed"] += 1
            continue
        # REG-05a: the exact generated input surface is bound per
        # replicate — the family digest hashes this record, so an
        # altered null input can never replay under the same digest.
        rep["input_digest"] = _sha_bytes(
            np.ascontiguousarray(X_n, dtype=np.float64).tobytes())
        # REG-05 selection consistency: the null replicate replays
        # the declared K sweep and takes the BIC-best converged fit —
        # the null is not privileged with the observed modal K.
        k_fits = [_fit_gmm(X_n, k, fit_seed) for k in k_candidates]
        conv = [f for f in k_fits if f["converged"]]
        if not conv:
            rec["n_failed"] += 1
            continue
        f = min(conv, key=lambda f: f["bic"])
        rec["null_k_distribution"][str(f["k"])] =             rec["null_k_distribution"].get(str(f["k"]), 0) + 1
        rep["k"] = int(f["k"])
        s = _silhouette(X_n, f["model"].predict(X_n))
        if s is None:
            rec["n_failed"] += 1
            continue
        rep["stat"] = float(s)
        rep["ok"] = True
        stats.append(s)
    rec["n_succeeded"] = len(stats)
    if rec["n_failed"]:
        rec["reason"] = (f"{rec['n_failed']} null replicates failed "
                         "or did not converge — the envelope is "
                         "incomplete")
        return rec
    p = float(np.mean([1.0 if s >= observed_stat else 0.0
                       for s in stats]))
    rec["p_value"] = p
    rec["null_stat_min"] = float(min(stats))
    rec["null_stat_max"] = float(max(stats))
    if p < alpha:
        rec["status"] = "PASS"
    else:
        rec["reason"] = (f"null-envelope p {p:.4f} >= alpha "
                         f"{alpha}")
    return rec


# -------------------------------------------------------------- runner

_ISO_DATE_RE = None  # compiled lazily


def _iso_date_ok(s: str) -> bool:
    """Calendar-valid canonical YYYY-MM-DD check — the shape regex
    admits `2020-13-99`, so calendar validity is verified by
    strptime (same pattern as research_v0.gates._valid_calendar_date).
    """
    global _ISO_DATE_RE
    if _ISO_DATE_RE is None:
        _ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    if not _ISO_DATE_RE.match(str(s)):
        return False
    try:
        datetime.strptime(str(s), "%Y-%m-%d")
        return True
    except ValueError:
        return False


@dataclass(frozen=True)
class RegimeRunConfig:
    seeds: tuple = (42, 7, 2024)
    k_candidates: tuple = K_CANDIDATES
    n_bootstrap: int = MIN_BOOTSTRAP
    n_null_replicates: int = MIN_NULL_REPLICATES
    null_alpha: float = 0.05
    season_col: str = "season"
    group_col: str = "basin_group"
    era_col: str | None = "era"
    era_boundaries: tuple = ()
    elevation_col: str | None = None
    era_drift_max: float = 0.5
    missingness_policy: str = "listwise"
    max_missingness: float = 0.25
    effort_col: str | None = None
    fold_seed_policy: str = "all"
    unit_col: str = "unit_id"
    date_col: str = "date"
    label_blinding: bool = True
    fitted_on: str = "TRAIN_ONLY"
    # Explicit holdout membership: the declared train groups the fit
    # may see and the declared held-out groups it may never fit on.
    # Both must be non-empty and disjoint; the mask must honour them.
    train_groups: tuple = ()
    heldout_groups: tuple = ()
    # Holdout axis (P5-A2 temporal amendment): "geographic" keeps the
    # classic LORO lock (heldout_groups required, disjoint).
    # "temporal" replaces it with owner-declared train/embargo/holdout
    # date intervals — heldout_groups must then be EMPTY (a run
    # cannot declare two lock axes), all intervals must be ISO-date
    # (start, end) pairs ordered train < embargo < holdout, and every
    # held-out row must fall inside embargo∪holdout.  Temporal mode
    # reports temporal extrapolation only — geographic transfer stays
    # unevaluated.
    holdout_axis: str = "geographic"
    temporal_train_interval: tuple = ()
    temporal_embargo_interval: tuple = ()
    temporal_holdout_interval: tuple = ()
    # REG-01 calendar-aware bootstrap: cadence + declared block
    # length + gap policy are bound configuration, never derived.
    cadence: str = "1D"
    bootstrap_block_len: int = 0   # MUST be declared >0 (REG-02)
    gap_policy: str = "calendar"   # blocks are calendar ranges
    # REG-08/09 waivers: an undeclared axis FAILS the gate unless the
    # waiver reason is explicitly bound in configuration.
    effort_waiver_reason: str = ""
    era_waiver_reason: str = ""
    effort_split: str = "median"   # declared effort stratification
    # REG-10 elevation ablation threshold
    elev_ablation_ari_max: float = 0.8
    # REG-11 input manifest: {"source_id", "source_digests",
    # "units", "feature_allowlist", "lineage", "evidence_root",
    # "source_files"} or {"fixture": True}.
    source_manifest: object = None
    # FCST-01: the run mode is declared, never derived.
    # FORECAST_REGIME must bind archive-vintage evidence
    # (forecast_vintage_digests, all 64-hex) and a declared
    # forecast_feature_set contained in the feature columns; a
    # RETROSPECTIVE run must carry neither.
    mode: str = "RETROSPECTIVE_REGIME"
    forecast_vintage_digests: tuple = ()
    forecast_feature_set: tuple = ()
    # R10: the serialized ForecastVintageV0-shaped evidence records
    # behind ``forecast_vintage_digests`` — a FORECAST_REGIME run may
    # bind the actual vintage records (not only their digests) so the
    # artifact carries the evidence the shared floor requires of an
    # associable forecast artifact.  Artifact-level evidence, never
    # serialized into the bound config.
    forecast_vintages: tuple = ()
    # SEISMIC-01: the declared retrospective data class bound into the
    # emitted artifact's ``data_class`` when mode is
    # RETROSPECTIVE_REGIME.  ``REANALYSIS`` remains the default — the
    # weather/GLOF contract is unchanged.  ``SEISMIC_WAVEFORM_
    # RETROSPECTIVE`` is admitted for the seismic sidecar's
    # retrospective artifacts only: it requires a non-fixture,
    # byte-bound source_manifest, can never appear on a FORECAST_REGIME
    # config, and marks the emitted artifact non-associable.
    retrospective_data_class: str = "REANALYSIS"


def _modal_k(ks: list[int]) -> int:
    """Deterministic modal K: on a frequency tie the SMALLEST K wins —
    declared tie-break, never set-order dependent."""
    counts = {k: ks.count(k) for k in set(ks)}
    top = max(counts.values())
    return min(k for k, c in counts.items() if c == top)


def run_regimes(df: pd.DataFrame, feature_cols: list[str],
                train_mask: np.ndarray, config: RegimeRunConfig
                ) -> dict:
    """Execute the protocol on a feature frame.

    df must carry: feature_cols + group_col + season_col (+era_col).
    train_mask marks rows eligible for preprocessing/fitting — locked
    test rows are transformed but never fitted on.
    Returns a RegimeArtifactV0-shaped dict plus a terminal status.
    """
    # ---- REG-01: complete configuration preflight BEFORE any
    # dataframe access — a malformed configuration must fail closed,
    # never partially execute.
    if not isinstance(feature_cols, (list, tuple)) or \
            not feature_cols or not all(
                isinstance(c, str) and c.strip() for c in feature_cols):
        return {"status": "RUN_ERROR",
                "reason": "feature_cols must be a non-empty list of "
                          "column names"}
    missing = [c for c in feature_cols if c not in df.columns]
    if missing:
        return {"status": "RUN_ERROR",
                "reason": f"missing feature columns: {missing}"}
    for col in (config.group_col, config.season_col,
                config.unit_col, config.date_col):
        if not isinstance(col, str) or col not in df.columns:
            return {"status": "RUN_ERROR",
                    "reason": f"required column {col!r} missing from "
                              "the frame — preflight rejects before "
                              "any mask or fit access"}
    if config.elevation_col is not None and \
            config.elevation_col not in df.columns:
        return {"status": "RUN_ERROR",
                "reason": f"declared elevation column "
                          f"{config.elevation_col!r} missing"}
    # REG-10: elevation is unit-bound numeric only — an arbitrary
    # string column can never act as an elevation band.
    if config.elevation_col is not None and \
            not pd.api.types.is_numeric_dtype(
                df[config.elevation_col]):
        return {"status": "RUN_ERROR",
                "reason": f"declared elevation column "
                          f"{config.elevation_col!r} must be numeric "
                          "(metres) — arbitrary labels cannot act "
                          "as elevation bands"}
    if config.effort_col is not None and \
            config.effort_col not in df.columns:
        return {"status": "RUN_ERROR",
                "reason": f"declared effort column "
                          f"{config.effort_col!r} missing"}
    if config.era_col is not None and \
            config.era_col not in df.columns:
        return {"status": "RUN_ERROR",
                "reason": f"declared era column {config.era_col!r} "
                          "missing"}
    if config.era_col is None and config.era_boundaries:
        return {"status": "RUN_ERROR",
                "reason": "era_boundaries declared but era_col is "
                          "None — the drift axis has no era carrier"}
    # R10-P04: the partition carriers must be real values BEFORE any
    # mask/fit access — ``.astype(str)`` would silently turn NaN into
    # the literal 'nan' and None into 'None', so a missing or blank
    # unit/group/season would enter unit_basin_map, the assignment
    # sidecar, and the null-strata as a phantom label.  Reject any
    # missing or whitespace-only value in the declared columns.
    _value_cols = ((config.unit_col, "unit"),
                   (config.group_col, "group"),
                   (config.season_col, "season")) + \
        (((config.era_col, "era"),)
         if config.era_col is not None else ())
    for _vc, _vlabel in _value_cols:
        _series = df[_vc]
        _bad = _series.isna() | ~_series.map(
            lambda v: bool(str(v).strip()))
        if bool(_bad.any()):
            _n = int(_bad.sum())
            return {"status": "RUN_ERROR",
                    "reason": f"{_vlabel} column {_vc!r} carries "
                              f"{_n} missing or blank value(s) — "
                              "null/'nan'/'None' labels cannot enter "
                              "the unit->group partition, the "
                              "assignment sidecar, or the null "
                              "strata"}
    if not config.label_blinding or config.fitted_on != "TRAIN_ONLY":
        return {"status": "RUN_ERROR",
                "reason": "label_blinding and TRAIN_ONLY are mandatory"}
    # K candidates: a declared subset of {1..5} that MUST contain the
    # K=1 null; non-int or out-of-range values never silently pass.
    if not isinstance(config.k_candidates, (list, tuple)) or \
            not config.k_candidates:
        return {"status": "RUN_ERROR",
                "reason": "k_candidates must be a non-empty declared "
                          "set"}
    if any(isinstance(k, bool) or not isinstance(k, int) or
           k not in K_CANDIDATES for k in config.k_candidates):
        return {"status": "RUN_ERROR",
                "reason": f"k_candidates must be ints within "
                          f"{K_CANDIDATES}; got "
                          f"{list(config.k_candidates)!r}"}
    if 1 not in config.k_candidates:
        return {"status": "RUN_ERROR",
                "reason": "the K=1 null candidate is mandatory and "
                          "must appear in k_candidates"}
    # Seeds: distinct, non-negative integers.
    if len(set(config.seeds)) < MIN_SEEDS:
        return {"status": "RUN_ERROR",
                "reason": f"need >= {MIN_SEEDS} distinct seeds"}
    if any(isinstance(sd, bool) or not isinstance(sd, int)
           or sd < 0 for sd in config.seeds):
        return {"status": "RUN_ERROR",
                "reason": "seeds must be distinct non-negative ints — "
                          "a non-int or negative seed is silently "
                          "disenfranchised from the modal-K vote "
                          "after int() coercion in the fit"}
    if isinstance(config.n_bootstrap, bool) or \
            not isinstance(config.n_bootstrap, int) or \
            config.n_bootstrap < MIN_BOOTSTRAP:
        return {"status": "RUN_ERROR",
                "reason": f"n_bootstrap must be an int >= "
                          f"{MIN_BOOTSTRAP} — the temporal-block "
                          "bootstrap is a required stability axis"}
    # REG-01: cadence must be a parseable pandas offset and
    # gap_policy a declared policy — both enter the bound config
    # digest, so an undeclared or malformed value can never produce
    # a terminal artifact.
    try:
        pd.tseries.frequencies.to_offset(config.cadence)
    except (TypeError, ValueError) as exc:
        return {"status": "RUN_ERROR",
                "reason": f"cadence {config.cadence!r} is not a "
                          f"parseable offset: {exc}"}
    if config.gap_policy != "calendar":
        return {"status": "RUN_ERROR",
                "reason": f"gap_policy {config.gap_policy!r} "
                          "unsupported — only 'calendar' (blocks are "
                          "calendar ranges that cannot bridge missing "
                          "dates) is admitted"}
    if isinstance(config.bootstrap_block_len, bool) or \
            not isinstance(config.bootstrap_block_len, int) or \
            config.bootstrap_block_len <= 0:
        return {"status": "RUN_ERROR",
                "reason": "bootstrap_block_len must be a positive "
                          "preregistered int — a derived or omitted "
                          "block length silently binds the temporal "
                          "null to the observed grid"}
    _ES_POLICIES = ("median", "tercile", "first10")
    _es = str(config.effort_split)
    if not (_es in _ES_POLICIES or
            (_es.startswith("quantile:") and
             _es.split(":", 1)[1].replace(".", "", 1).isdigit()
             and 0.0 < float(_es.split(":", 1)[1]) < 1.0)):
        return {"status": "RUN_ERROR",
                "reason": f"effort_split {config.effort_split!r} "
                          "unsupported — declare 'median', 'tercile', "
                          "'first10', or 'quantile:<q in (0,1)>'"}
    # FCST-01: mode is a declared RegimeMode value.
    # FORECAST_REGIME requires bound archive-vintage digests and a
    # forecast feature set contained in the declared feature columns;
    # RETROSPECTIVE_REGIME must never silently carry forecast fields.
    if config.mode not in {m.value for m in RegimeMode}:
        return {"status": "RUN_ERROR",
                "reason": f"mode {config.mode!r} is not a declared "
                          "RegimeMode value"}
    if config.mode == RegimeMode.FORECAST_REGIME.value:
        fvd = config.forecast_vintage_digests
        if not isinstance(fvd, (list, tuple)) or not fvd or \
                any(not isinstance(d, str) or
                    not re.fullmatch(r"[0-9a-f]{64}", d)
                    for d in fvd):
            return {"status": "RUN_ERROR",
                    "reason": "FORECAST_REGIME requires non-empty "
                              "forecast_vintage_digests — every "
                              "vintage digest must be 64-hex sha256"}
        ffs = config.forecast_feature_set
        if not isinstance(ffs, (list, tuple)) or not ffs or \
                any(not isinstance(c, str) or not c.strip()
                    for c in ffs):
            return {"status": "RUN_ERROR",
                    "reason": "FORECAST_REGIME requires a non-empty "
                              "forecast_feature_set"}
        outside = sorted(set(ffs) - set(feature_cols))
        if outside:
            return {"status": "RUN_ERROR",
                    "reason": f"forecast_feature_set members "
                              f"{outside} are outside the declared "
                              "feature columns"}
        # R10: declared forecast_vintages are serialized
        # ForecastVintageV0-shaped records — the run only needs the
        # mapping shape (deep validation is the shared floor's job);
        # the artifact carries them verbatim as top-level evidence.
        if config.forecast_vintages:
            if not isinstance(config.forecast_vintages,
                              (list, tuple)) or \
                    any(not isinstance(v, Mapping)
                        for v in config.forecast_vintages):
                return {"status": "RUN_ERROR",
                        "reason": "forecast_vintages entries must be "
                                  "serialized ForecastVintageV0 "
                                  "mappings — the run binds records, "
                                  "not opaque blobs"}
    elif config.forecast_vintage_digests or \
            config.forecast_feature_set or \
            config.forecast_vintages:
        return {"status": "RUN_ERROR",
                "reason": "RETROSPECTIVE_REGIME must not carry "
                          "forecast_vintage_digests or "
                          "forecast_feature_set — forecast evidence "
                          "cannot silently transfer into a "
                          "retrospective artifact"}
    # REG-04: 'first10' is an ordinal/categorical stratification — a
    # numeric effort column must declare an explicit numeric split;
    # the declared token executes as named or not at all.
    if config.effort_col is not None and _es == "first10" and \
            pd.api.types.is_numeric_dtype(df[config.effort_col]):
        return {"status": "RUN_ERROR",
                "reason": "effort_split 'first10' cannot execute on "
                          f"numeric effort column "
                          f"{config.effort_col!r} — declare 'median', "
                          "'tercile', or 'quantile:<q>' for numeric "
                          "effort"}
    if isinstance(config.n_null_replicates, bool) or \
            not isinstance(config.n_null_replicates, int) or \
            config.n_null_replicates < MIN_NULL_REPLICATES:
        return {"status": "RUN_ERROR",
                "reason": f"n_null_replicates must be an int >= "
                          f"{MIN_NULL_REPLICATES} — the null envelope "
                          "is a required evidence gate"}
    if isinstance(config.null_alpha, bool) or \
            not isinstance(config.null_alpha, (int, float)) or \
            not 0.0 < float(config.null_alpha) < 1.0:
        return {"status": "RUN_ERROR",
                "reason": "null_alpha must be a declared probability "
                          "in (0, 1)"}
    if config.missingness_policy not in MISSINGNESS_POLICIES:
        return {"status": "RUN_ERROR",
                "reason": f"missingness_policy must be one of "
                          f"{MISSINGNESS_POLICIES}; got "
                          f"{config.missingness_policy!r}"}
    if isinstance(config.max_missingness, bool) or \
            not isinstance(config.max_missingness, (int, float)) or \
            not 0.0 <= float(config.max_missingness) <= 1.0:
        return {"status": "RUN_ERROR",
                "reason": "max_missingness must be a declared "
                          "fraction in [0, 1]"}
    if config.fold_seed_policy not in FOLD_SEED_POLICIES:
        return {"status": "RUN_ERROR",
                "reason": f"fold_seed_policy must be one of "
                          f"{FOLD_SEED_POLICIES}; got "
                          f"{config.fold_seed_policy!r}"}
    # REG-11: a minimal input manifest is mandatory — either an
    # explicit fixture declaration or a fully-bound real-source
    # record (source digests, units, feature allowlist, lineage).
    # An ungoverned frame can never produce a terminal artifact.
    sm = config.source_manifest
    if not isinstance(sm, Mapping) or not sm:
        return {"status": "RUN_ERROR",
                "reason": "source_manifest is required — declare "
                          "{'fixture': true} for synthetic frames or "
                          "a bound {source_id, source_digests, units, "
                          "feature_allowlist, lineage} record for "
                          "real inputs"}
    # R9-P01: the fixture marker is a strict boolean via the shared
    # fixture_flag() floor — a truthy non-bool (e.g. the string
    # "true") is malformed, never a synthetic bypass.
    _is_fixture, _fixture_problems = fixture_flag(sm)
    if _fixture_problems:
        return {"status": "RUN_ERROR",
                "reason": "source_manifest fixture marker invalid: "
                          f"{_fixture_problems} — declare a boolean "
                          "{'fixture': true|false}"}
    if not _is_fixture:
        missing_sm = [k for k in ("source_id", "source_digests",
                                  "units", "feature_allowlist",
                                  "lineage", "evidence_root")
                      if not sm.get(k)]
        if missing_sm:
            return {"status": "RUN_ERROR",
                    "reason": f"non-fixture source_manifest lacks "
                              f"{missing_sm} — an ungoverned frame "
                              "cannot produce a terminal artifact"}
        # R10-P07 parity: the non-fixture manifest is an exact typed
        # record — the shared floor rejects undeclared keys and
        # wrong-typed fields, so the run must refuse them up front or
        # it would emit an artifact freeze cannot certify.
        _sm_allowed = {"source_id", "source_digests", "units",
                       "feature_allowlist", "lineage",
                       "evidence_root", "fixture", "source_files"}
        _sm_unknown = sorted(set(sm) - _sm_allowed)
        if _sm_unknown:
            return {"status": "RUN_ERROR",
                    "reason": f"non-fixture source_manifest carries "
                              f"undeclared fields {_sm_unknown} — "
                              "the evidence contract admits exactly "
                              f"{sorted(_sm_allowed)}"}
        for _k in ("source_id", "lineage", "evidence_root"):
            _v = sm.get(_k)
            if not isinstance(_v, str) or not _v.strip():
                return {"status": "RUN_ERROR",
                        "reason": f"source_manifest.{_k} must be a "
                                  "non-empty string"}
        for _k in ("units", "feature_allowlist"):
            _v = sm.get(_k)
            if not isinstance(_v, (list, tuple)) or not _v or \
                    any(not isinstance(u, str) or not u.strip()
                        for u in _v):
                return {"status": "RUN_ERROR",
                        "reason": f"source_manifest.{_k} must be a "
                                  "non-empty sequence of non-empty "
                                  "strings"}
        _sds = sm.get("source_digests")
        if not isinstance(_sds, Sequence) or \
                isinstance(_sds, (str, bytes)) or not _sds:
            return {"status": "RUN_ERROR",
                    "reason": "source_manifest.source_digests must "
                              "be a non-empty sequence of 64-hex "
                              "sha256 digests"}
        if any(not isinstance(d, str) or
               not re.fullmatch(r"[0-9a-f]{64}", d)
               for d in _sds):
            return {"status": "RUN_ERROR",
                    "reason": "source_manifest.source_digests "
                              "entries must be 64-hex sha256"}
        _sfiles = sm.get("source_files")
        if not isinstance(_sfiles, (list, tuple)) or not _sfiles:
            return {"status": "RUN_ERROR",
                    "reason": "source_manifest.source_files must be "
                              "a non-empty list of {relpath, sha256} "
                              "bindings"}
        _seen_rel: set = set()
        for _i, _entry in enumerate(_sfiles):
            if not isinstance(_entry, Mapping):
                return {"status": "RUN_ERROR",
                        "reason": f"source_manifest.source_files"
                                  f"[{_i}] must be a {{relpath, "
                                  "sha256}} mapping"}
            _extra = sorted(set(_entry) - {"relpath", "sha256"})
            if _extra:
                return {"status": "RUN_ERROR",
                        "reason": f"source_manifest.source_files"
                                  f"[{_i}] carries undeclared "
                                  f"fields {_extra}"}
            _rel = _entry.get("relpath")
            if not isinstance(_rel, str) or not _rel.strip():
                return {"status": "RUN_ERROR",
                        "reason": f"source_manifest.source_files"
                                  f"[{_i}].relpath must be a "
                                  "non-empty string"}
            if _rel in _seen_rel:
                return {"status": "RUN_ERROR",
                        "reason": f"source_manifest.source_files "
                                  f"relpath {_rel!r} is duplicated "
                                  "— one evidence binding per file"}
            _seen_rel.add(_rel)
            if not isinstance(_entry.get("sha256"), str) or \
                    not re.fullmatch(
                        r"[0-9a-f]{64}", _entry["sha256"]):
                return {"status": "RUN_ERROR",
                        "reason": f"source_manifest.source_files"
                                  f"[{_i}].sha256 must be a 64-hex "
                                  "sha256"}
        # the declared allowlist must cover the requested features —
        # a feature outside the declared allowlist can never enter a
        # governed run
        allow = sm.get("feature_allowlist")
        if isinstance(allow, (list, tuple, set)):
            not_allowed = [c for c in feature_cols
                           if c not in set(allow)]
            if not_allowed:
                return {"status": "RUN_ERROR",
                        "reason": f"feature columns {not_allowed} "
                                  "are outside the declared "
                                  "source_manifest.feature_allowlist"}
        # PROV-03: byte-verify the declared evidence binding — every
        # source_files entry must resolve inside evidence_root and
        # hash to its declared digest, and the verified multiset must
        # equal source_digests.  A manifest that names evidence it
        # cannot produce fails closed before any fit.
        ev_problems = verify_source_evidence(sm)
        if ev_problems:
            return {"status": "RUN_ERROR",
                    "reason": "source_manifest evidence verification "
                              f"failed: {ev_problems}"}

    # SEISMIC-01: retrospective_data_class is a declared
    # RETROSPECTIVE-only class.  REANALYSIS is the default; the
    # seismic waveform class admits only a non-fixture, byte-bound
    # source manifest and can never be carried by a FORECAST_REGIME
    # config (where it would smuggle retrospective provenance into
    # the forecast lane).
    if config.retrospective_data_class not in \
            RETROSPECTIVE_REGIME_DATA_CLASSES:
        return {"status": "RUN_ERROR",
                "reason": f"retrospective_data_class "
                          f"{config.retrospective_data_class!r} is "
                          "not a declared retrospective class "
                          f"{sorted(RETROSPECTIVE_REGIME_DATA_CLASSES)}"}
    if config.mode == RegimeMode.FORECAST_REGIME.value and \
            config.retrospective_data_class != \
            ForecastDataClass.REANALYSIS.value:
        return {"status": "RUN_ERROR",
                "reason": "FORECAST_REGIME must carry the "
                          "REANALYSIS retrospective_data_class "
                          "default — a retrospective data class "
                          "cannot bind forecast provenance"}
    if config.retrospective_data_class == \
            SEISMIC_WAVEFORM_RETROSPECTIVE_DATA_CLASS and _is_fixture:
        return {"status": "RUN_ERROR",
                "reason": "SEISMIC_WAVEFORM_RETROSPECTIVE requires "
                          "a non-fixture, byte-bound source_manifest "
                          "— synthetic frames may not claim seismic "
                          "waveform provenance"}

    # REG-C06: era boundaries are either uniformly ISO dates (a
    # date-defined partition) or uniformly era labels matching the
    # era column; mixing the two declaration styles is malformed.
    if config.era_boundaries:
        if not isinstance(config.era_boundaries, (list, tuple)) or \
                not all(isinstance(x, str) and x.strip()
                        for x in config.era_boundaries):
            return {"status": "RUN_ERROR",
                    "reason": "era_boundaries must be a non-empty "
                              "tuple of ISO dates or era labels"}
        iso_flags = [_iso_date_ok(x) for x in config.era_boundaries]
        if any(iso_flags) and not all(iso_flags):
            return {"status": "RUN_ERROR",
                    "reason": "era_boundaries mixes ISO dates and "
                              "era labels — declare one style"}
        if all(iso_flags) and \
                len(set(config.era_boundaries)) != \
                len(config.era_boundaries):
            return {"status": "RUN_ERROR",
                    "reason": "duplicate era boundary dates declared"}
    # Holdout binding (I-07): the mask is meaningless unless it is
    # tied to a declared holdout axis — geographic (disjoint group
    # membership) or temporal (owner-declared train/embargo/holdout
    # date intervals, P5-A2).  The axis is declared, never inferred.
    train_groups = set(config.train_groups)
    heldout_groups = set(config.heldout_groups)
    holdout_axis = getattr(config, "holdout_axis", "geographic")
    if holdout_axis not in ("geographic", "temporal"):
        return {"status": "RUN_ERROR",
                "reason": f"holdout_axis must be 'geographic' or "
                          f"'temporal'; got {holdout_axis!r}"}
    if holdout_axis == "geographic":
        if not train_groups or not heldout_groups:
            return {"status": "RUN_ERROR",
                    "reason": "train_groups and heldout_groups must "
                              "both be non-empty — an undeclared "
                              "holdout cannot produce a terminal "
                              "regime status"}
        if train_groups & heldout_groups:
            return {"status": "RUN_ERROR",
                    "reason": f"train/heldout groups overlap: "
                              f"{sorted(train_groups & heldout_groups)}"}
    else:
        if heldout_groups:
            return {"status": "RUN_ERROR",
                    "reason": "heldout_groups must be empty when "
                              "holdout_axis='temporal' — a run cannot "
                              "declare two lock axes"}
        if not train_groups:
            return {"status": "RUN_ERROR",
                    "reason": "train_groups must be non-empty for a "
                              "temporal holdout — the fit's basin "
                              "membership is still declared"}
    mask = np.asarray(train_mask)
    if mask.shape[0] != len(df):
        return {"status": "RUN_ERROR",
                "reason": f"train_mask length {mask.shape[0]} != "
                          f"frame rows {len(df)}"}
    if mask.dtype != bool:
        return {"status": "RUN_ERROR",
                "reason": "train_mask must be a boolean array"}
    if not mask.any() or mask.all():
        return {"status": "RUN_ERROR",
                "reason": "train_mask must mark a non-empty train set "
                          "and a non-empty held-out set"}
    mask_groups = set(df.loc[mask, config.group_col])
    held_mask_groups = set(df.loc[~mask, config.group_col])
    if not mask_groups <= train_groups:
        return {"status": "RUN_ERROR",
                "reason": f"train rows contain undeclared groups: "
                          f"{sorted(mask_groups - train_groups)}"}
    if holdout_axis == "geographic":
        if not held_mask_groups <= heldout_groups:
            return {"status": "RUN_ERROR",
                    "reason": f"held-out rows contain undeclared "
                              f"groups: "
                              f"{sorted(held_mask_groups - heldout_groups)}"}
    else:
        # P5-A2 temporal axis: the lock is a declared date interval,
        # not a group — held rows must live in the SAME declared
        # basins (temporal extrapolation, never geographic transfer)
        if not held_mask_groups <= train_groups:
            return {"status": "RUN_ERROR",
                    "reason": f"temporal held-out rows contain "
                              f"undeclared groups: "
                              f"{sorted(held_mask_groups - train_groups)}"}
        import datetime as _dt
        ivals = {}
        for name in ("temporal_train_interval",
                     "temporal_embargo_interval",
                     "temporal_holdout_interval"):
            iv = getattr(config, name, ())
            if not isinstance(iv, (list, tuple)) or len(iv) != 2 or \
                    not all(isinstance(x, str) and _iso_date_ok(x)
                            for x in iv):
                return {"status": "RUN_ERROR",
                        "reason": f"{name} must be a (start, end) "
                                  "pair of ISO dates for a temporal "
                                  "holdout — undeclared intervals "
                                  "cannot bound a lock"}
            ivals[name] = tuple(_dt.date.fromisoformat(x) for x in iv)
        tr, em, ho = (ivals["temporal_train_interval"],
                      ivals["temporal_embargo_interval"],
                      ivals["temporal_holdout_interval"])
        if not (tr[0] <= tr[1] < em[0] <= em[1] < ho[0] <= ho[1]):
            return {"status": "RUN_ERROR",
                    "reason": "temporal intervals must be strictly "
                              "ordered train < embargo < holdout — "
                              "overlap or inversion leaks the lock"}
        dates = pd.to_datetime(df[config.date_col],
                               errors="coerce", utc=True)
        dates = pd.Series(dates.dt.date, index=df.index)
        in_tr_iv = (dates >= tr[0]) & (dates <= tr[1])
        if not (~mask | in_tr_iv).all():
            return {"status": "RUN_ERROR",
                    "reason": "train mask contains rows outside the "
                              "declared temporal_train_interval — "
                              "the mask must honour the amendment"}
        held_dates = dates[~mask]
        in_em = (held_dates >= em[0]) & (held_dates <= em[1])
        in_ho = (held_dates >= ho[0]) & (held_dates <= ho[1])
        if not (in_em | in_ho).all():
            return {"status": "RUN_ERROR",
                    "reason": "held-out rows fall outside the "
                              "declared embargo+holdout intervals — "
                              "undeclared rows cannot sit in the "
                              "lock"}
        n_embargo = int(in_em.sum())
        n_holdout = int(in_ho.sum())
        if n_embargo == 0 or n_holdout == 0:
            return {"status": "RUN_ERROR",
                    "reason": "declared embargo and holdout "
                              "intervals must both contain rows — "
                              "an empty interval is not a lock"}
        _temporal_lock = {
            "train_interval": list(config.temporal_train_interval),
            "embargo_interval": list(config.temporal_embargo_interval),
            "holdout_interval": list(config.temporal_holdout_interval),
            "n_embargo_rows_excluded": n_embargo,
            "n_holdout_rows": n_holdout,
            "claim_scope": "temporal extrapolation only — "
                           "geographic transfer unevaluated"}
    train_mask = mask
    # the multi-region gate applies to the FIT side: held-out groups
    # must not inflate the geographic diversity of the model fit
    if len(mask_groups) < MIN_GEO_GROUPS:
        return {"status": "RUN_ERROR",
                "reason": f"need >= {MIN_GEO_GROUPS} geographic groups "
                          f"in the fit mask; got {len(mask_groups)} — "
                          "a single-cell or single-basin fit may not "
                          "emit a terminal status"}

    # Row identity is explicit, never derived from the frame index:
    # (unit_id, date) pairs must be unique (columns were preflighted).
    bad_dates = ~df[config.date_col].astype(str).map(_iso_date_ok)
    if bad_dates.any():
        return {"status": "RUN_ERROR",
                "reason": f"{int(bad_dates.sum())} rows have "
                          "non-canonical ISO dates"}
    dup = df.duplicated(subset=[config.unit_col, config.date_col])
    if dup.any():
        return {"status": "RUN_ERROR",
                "reason": f"{int(dup.sum())} duplicate (unit_id, date) "
                          "rows rejected"}
    # REG-01: a unit belongs to exactly one geographic group — the
    # unit->basin binding is a partition, so no unit can cross the
    # LORO train/evaluation boundary.
    _ug = df.groupby(df[config.unit_col].astype(str))[
        config.group_col].nunique()
    _multi = sorted(_ug[_ug > 1].index.tolist())
    if _multi:
        return {"status": "RUN_ERROR",
                "reason": "geographic groups must be a unit-level "
                          "partition — unit(s) appearing in multiple "
                          f"groups: {_multi[:8]}"}
    # REG-02: the declared cadence must match the observed within-
    # unit grid — every observed spacing is a positive integer
    # multiple of the declared step and the declared step equals the
    # finest observed spacing.  A 1H declaration over a daily grid
    # (or a 1.5D declaration over daily rows) fails before fitting.
    try:
        _step_td = pd.Timedelta(
            pd.tseries.frequencies.to_offset(config.cadence).nanos)
    except (TypeError, ValueError) as exc:
        return {"status": "RUN_ERROR",
                "reason": f"cadence {config.cadence!r} is not a "
                          "fixed-length offset (grid cannot be "
                          f"verified): {exc}"}
    _diffs = (df.assign(_d=pd.to_datetime(df[config.date_col]))
                .sort_values([config.unit_col, "_d"])
                .groupby(config.unit_col)["_d"].diff().dropna())
    if len(_diffs):
        _min_d = _diffs.min()
        if _step_td != _min_d or (_min_d % _step_td) != pd.Timedelta(0):
            return {"status": "RUN_ERROR",
                    "reason": f"declared cadence {config.cadence!r} "
                              "does not match the observed grid: "
                              f"finest within-unit spacing {_min_d} "
                              "— the declared step must equal the "
                              "finest observed spacing"}
        if bool((_diffs % _step_td != pd.Timedelta(0)).any()):
            return {"status": "RUN_ERROR",
                    "reason": f"rows fall off the declared "
                              f"{config.cadence!r} grid — observed "
                              "within-unit spacings are not integer "
                              "multiples of the cadence"}

    # per-column missingness report (pre-filter)
    missingness = {c: float(df[c].isna().mean()) for c in feature_cols}

    train_df = df.loc[train_mask, feature_cols]
    if len(train_df) < 50:
        return {"status": "RUN_ERROR",
                "reason": "insufficient train rows (<50)"}

    # ---- REG-C07: pathological feature columns are rejected BEFORE
    # any fit — sparse finite counts, low geographic coverage,
    # non-numeric payloads, post-imputation non-finite values, and
    # constant columns can never silently enter the model.
    grp_train = df.loc[train_mask, config.group_col].to_numpy()
    malformed = {}
    for c in feature_cols:
        try:
            vals = train_df[c].to_numpy(dtype=np.float64)
        except (TypeError, ValueError):
            malformed[c] = ["non-numeric dtype — cannot coerce to "
                            "float64"]
            continue
        finite = np.isfinite(vals)
        problems = []
        if int(finite.sum()) < MIN_COL_FINITE:
            problems.append(f"finite train count {int(finite.sum())} "
                            f"< {MIN_COL_FINITE}")
        n_groups_finite = int(
            pd.unique(grp_train[finite]).size)
        if n_groups_finite < MIN_GEO_GROUPS:
            problems.append(f"finite values in {n_groups_finite} "
                            f"train groups < {MIN_GEO_GROUPS}")
        if problems:
            malformed[c] = problems
    if malformed:
        return {"status": "RUN_ERROR",
                "reason": f"malformed feature columns rejected "
                          f"before fitting: {malformed}"}

    # REG-07: the declared missingness policy applies to the PRIMARY
    # fit surface — policy changes alter model inputs, and the applied
    # row selection is bound into the artifact.
    row_miss_frac_primary = train_df.isna().mean(axis=1).to_numpy()
    if config.missingness_policy == "listwise":
        fit_sel = row_miss_frac_primary == 0.0
    elif config.missingness_policy == "bounded_impute":
        fit_sel = row_miss_frac_primary <= float(
            config.max_missingness)
    else:  # stratified: all train rows; bands reported in stability
        fit_sel = np.ones(len(train_df), dtype=bool)
    missingness_applied = {
        "policy": config.missingness_policy,
        "train_rows_total": int(len(train_df)),
        "train_rows_fitted": int(fit_sel.sum()),
        "train_rows_dropped": int((~fit_sel).sum())}
    # REG-03: the policy-selected surface is bound as a frame-level
    # mask so EVERY downstream refit, resample, and null generator
    # draws from exactly the rows the model was fitted on.
    fit_sel_full = np.zeros(len(df), dtype=bool)
    fit_sel_full[np.flatnonzero(train_mask)[fit_sel]] = True
    fit_rows_index = df.index.to_numpy()[fit_sel_full]
    train_df = train_df.loc[fit_sel]
    if len(train_df) < 50:
        return {"status": "RUN_ERROR",
                "reason": f"declared missingness policy "
                          f"{config.missingness_policy!r} leaves "
                          f"{len(train_df)} train rows (<50)"}
    prep = TrainOnlyPreprocessor()
    try:
        prep.fit(train_df)
        X_all = prep.transform(df[feature_cols])
    except Exception as exc:
        return {"status": "RUN_ERROR",
                "reason": f"preprocessing failed: {exc}"}
    if not np.isfinite(X_all).all():
        bad = [feature_cols[i] for i in range(len(feature_cols))
               if not np.isfinite(X_all[:, i]).all()]
        return {"status": "RUN_ERROR",
                "reason": f"non-finite values remain after "
                          f"imputation in columns {bad}"}
    constant = [feature_cols[i] for i in range(len(feature_cols))
                if float(np.std(X_all[train_mask, i])) <= 0.0]
    if constant:
        return {"status": "RUN_ERROR",
                "reason": f"constant feature columns rejected: "
                          f"{constant}"}
    # the model fits the same policy-selected surface the
    # preprocessor saw — never the imputed remainder
    X_train = X_all[train_mask][fit_sel]

    decl_seeds = sorted(set(int(s) for s in config.seeds))

    # raw input byte binding (shared producer schema): exact float64
    # C-order bytes of the declared feature matrix.
    try:
        feat_arr = np.ascontiguousarray(
            df.loc[:, list(feature_cols)].to_numpy(dtype=np.float64))
    except (TypeError, ValueError) as exc:
        return {"status": "RUN_ERROR",
                "reason": f"feature matrix is not coercible to "
                          f"float64 bytes: {exc}"}
    input_bytes = feat_arr.tobytes()
    input_values = _encode_input_values(feat_arr)

    # --- K sweep across seeds; only converged fits eligible ----------
    fits = []
    for seed in decl_seeds:
        for k in config.k_candidates:
            fits.append(_fit_gmm(X_train, k, seed))
    converged = [f for f in fits if f["converged"]]
    if not converged:
        return {"status": "RUN_ERROR",
                "reason": "no converged fit across all seeds"}
    # K=1 null mandatory: must have at least one converged K=1 fit.
    null_fits = [f for f in converged if f["k"] == 1]
    if not null_fits:
        return {"status": "RUN_ERROR",
                "reason": "K=1 null failed to converge on every seed"}

    # REG-C03: every declared seed is accounted for — "converged"
    # only if it produced a converged fit for EVERY declared K.
    declared_ks = set(int(k) for k in config.k_candidates)
    seed_coverage = {}
    for sd in decl_seeds:
        ks_ok = {f["k"] for f in converged if f["seed"] == sd}
        seed_coverage[str(sd)] = ("converged" if declared_ks <= ks_ok
                                  else "failed")
    seed_coverage_complete = all(
        v == "converged" for v in seed_coverage.values()) and \
        set(seed_coverage) == {str(s) for s in decl_seeds}

    # per-seed best K by BIC
    per_seed_best = {}
    for seed in decl_seeds:
        seed_fits = [f for f in converged if f["seed"] == seed]
        if seed_fits:
            per_seed_best[seed] = min(seed_fits,
                                      key=lambda f: f["bic"])["k"]
    if not per_seed_best:
        return {"status": "RUN_ERROR",
                "reason": "no seed produced a converged fit"}
    ks = list(per_seed_best.values())
    modal_k = _modal_k(ks)   # deterministic: smallest K on a tie
    k_freq = ks.count(modal_k) / len(ks)

    best = min([f for f in converged if f["k"] == modal_k],
               key=lambda f: f["bic"])
    model = best["model"]
    labels_train = model.predict(X_train)
    post = model.predict_proba(X_train).max(axis=1)
    occupancy = np.bincount(labels_train,
                            minlength=modal_k) / len(labels_train)

    # --- deterministic assignment sidecar (unit_id, date, regime_id) --
    # Every transformed row receives exactly one assignment; rows are
    # canonically sorted by (unit_id, date) and the digest is bound
    # into the artifact — the frozen artifact can never be emitted
    # without it.
    labels_all = model.predict(X_all)
    assignments = sorted(
        (str(u), str(d), int(r))
        for u, d, r in zip(df[config.unit_col], df[config.date_col],
                           labels_all))
    assignment_digest = _digest(assignments)

    # --- seed stability: pairwise ARI on train assignments -----------
    seed_labels = {}
    for seed, k in per_seed_best.items():
        if k == modal_k:
            f = next(f for f in converged
                     if f["seed"] == seed and f["k"] == modal_k)
            seed_labels[seed] = f["model"].predict(X_train)
    seed_ari = [_ari(a, b) for i, a in enumerate(seed_labels.values())
                for b in list(seed_labels.values())[i + 1:]]

    # --- stability axes --------------------------------------------------
    stability = {
        "seed_ari_min": min(seed_ari) if seed_ari else None,
        "seed_ari_max": max(seed_ari) if seed_ari else None,
        "modal_k_frequency": k_freq,
        "k_instability": k_freq < 1.0,
        "n_bootstrap": config.n_bootstrap,
        "seed_coverage": dict(seed_coverage),
        "fold_seed_policy": config.fold_seed_policy,
        "component_alignment": (
            "minimum-cost assignment on standardized mean distance "
            "to the reference modal-K model; lexicographic "
            "component-index tie-break"),
    }

    # leave-one-region-out refit (basin/geographic axis).
    # REG-02: folds iterate over the declared TRAIN groups only — a
    # locked held-out group can never enter a fold, and can never
    # manufacture a false zero-distance pass.  REG-03: every fold is
    # an explicit state.  REG-C02: fold components are aligned to the
    # reference model before any occupancy comparison, and a
    # label-invariant ARI is emitted per fold.  REG-C03: every fold
    # honours the declared fold_seed_policy.
    loro = {}
    loro_required = sorted(mask_groups)
    for g in loro_required:
        fold = {"status": None, "js": None, "js_vs_reference": None,
                "ari": None, "reason": None,
                "seeds_declared": [int(s) for s in
                                   (decl_seeds
                                    if config.fold_seed_policy == "all"
                                    else decl_seeds[:1])],
                "seed_failures": [], "per_seed": {}}
        m_fit = np.asarray(train_mask) & fit_sel_full & \
            (df[config.group_col].astype(str) != g)
        m_eval = np.asarray(train_mask) & \
            (df[config.group_col].astype(str) == g)
        sub = df.loc[m_fit, feature_cols]
        eval_rows = df.loc[m_eval, feature_cols]
        if len(sub) < 50 or len(eval_rows) == 0:
            fold["status"] = "SKIPPED"
            fold["reason"] = ("insufficient complement or empty "
                              "excluded-group rows")
            loro[g] = fold
            continue
        try:
            p2 = TrainOnlyPreprocessor().fit(sub)
            X2 = p2.transform(sub)
            X_eval = p2.transform(eval_rows)
            if not np.isfinite(X2).all() or \
                    not np.isfinite(X_eval).all():
                raise ValueError("non-finite fold transform")
        except Exception as exc:
            fold["status"] = "FAIL"
            fold["reason"] = f"fold preprocessing failed: {exc}"
            loro[g] = fold
            continue
        ref_labels_sub = model.predict(prep.transform(sub))
        for sd in fold["seeds_declared"]:
            f2 = _fit_gmm(X2, modal_k, sd)
            if not f2["converged"]:
                fold["seed_failures"].append(int(sd))
                continue
            oth_ref = _means_in_ref_space(f2["model"].means_, p2,
                                          prep)
            perm = _align_to_reference(model.means_, oth_ref)
            # genuine out-of-fold evaluation: the excluded group's
            # train rows were never fitted on; their predicted
            # occupancy is contrasted against the fold-fit occupancy,
            # both aligned to the reference component IDs.
            eval_labels = f2["model"].predict(X_eval)
            occ_eval = _aligned(
                np.bincount(eval_labels, minlength=modal_k)
                / len(eval_labels), perm)
            fit_labels = f2["model"].predict(X2)
            occ_fit = _aligned(
                np.bincount(fit_labels, minlength=modal_k)
                / len(fit_labels), perm)
            js = float(_js_divergence(occ_eval, occ_fit))
            js_ref = float(_js_divergence(occupancy, occ_fit))
            ari = float(_ari(ref_labels_sub, fit_labels))
            # decisive out-of-fold agreement: the reference model and
            # the fold model label the SAME excluded-group rows —
            # label-invariant, so component permutation is irrelevant
            ref_eval_labels = model.predict(
                prep.transform(eval_rows))
            ari_eval = float(_ari(ref_eval_labels, eval_labels))
            fold["per_seed"][str(int(sd))] = {
                "js": js, "ari": ari, "ari_eval": ari_eval,
                "js_vs_reference": js_ref}
        if not fold["per_seed"]:
            fold["status"] = "NONCONVERGED"
            fold["reason"] = ("every declared fold seed failed to "
                              "converge")
            loro[g] = fold
            continue
        fold["js"] = float(max(v["js"]
                               for v in fold["per_seed"].values()))
        fold["ari"] = float(min(v["ari"]
                                for v in fold["per_seed"].values()))
        fold["ari_eval"] = float(min(
            v["ari_eval"] for v in fold["per_seed"].values()))
        fold["js_vs_reference"] = float(
            max(v["js_vs_reference"]
                for v in fold["per_seed"].values()))
        fold["n_eval_rows"] = int(len(eval_rows))
        # gate on out-of-fold label agreement (ARI on the excluded
        # group's rows under fold-vs-reference models); occupancy JS
        # remains a diagnostic — a homogeneous excluded group can
        # legitimately diverge in composition without the partition
        # being unstable.
        fold["status"] = ("PASS" if fold["ari_eval"] >= LORO_ARI_MIN
                          else "FAIL")
        if fold["status"] == "FAIL":
            fold["reason"] = (
                f"out-of-fold label agreement ARI "
                f"{fold['ari_eval']:.3f} < {LORO_ARI_MIN} — the "
                "partition does not reproduce on the excluded "
                "group's rows")
        loro[g] = fold
    stability["leave_one_region_out"] = {
        "folds": loro,
        "n_required": len(loro_required),
        "n_pass": sum(1 for f in loro.values()
                      if f["status"] == "PASS"),
        "locked_groups_excluded": sorted(heldout_groups),
        "holdout_axis": holdout_axis,
    }
    loro_pass = (all(f["status"] == "PASS" for f in loro.values())
                 and len(loro) >= MIN_GEO_GROUPS)

    # REG-02: locked held-out groups are PREDICTED by the frozen
    # model (never fitted) — coverage of the locked universe is a
    # reported diagnostic, and any fit access would be a violation.
    locked_cov = {}
    if holdout_axis == "temporal":
        # P5-A2: locked rows are the declared temporal holdout
        # interval within the SAME basins — embargo rows are
        # excluded from evaluation entirely.  Per-basin occupancy
        # contrast reports temporal extrapolation only.
        _locked_eval_groups = sorted(mask_groups)
        _dates = pd.to_datetime(df[config.date_col],
                                errors="coerce", utc=True)
        _in_ho = ((_dates.dt.date >= ho[0]) &
                  (_dates.dt.date <= ho[1])).to_numpy()
        stability["temporal_holdout"] = _temporal_lock
    else:
        _locked_eval_groups = sorted(heldout_groups)
        _in_ho = np.ones(len(df), dtype=bool)
    for g in _locked_eval_groups:
        m_g = np.asarray(~train_mask) & _in_ho & \
            (df[config.group_col].astype(str) == g)
        sub_g = df.loc[m_g, feature_cols]
        rec = {"n_rows": int(len(sub_g)),
               "occupancy_js_vs_reference": None, "status": None}
        if len(sub_g) == 0:
            rec["status"] = "EMPTY"
            rec["reason"] = (
                "declared temporal holdout interval has no rows "
                "for this group" if holdout_axis == "temporal" else
                "declared held-out group has no rows")
        else:
            lab_g = model.predict(prep.transform(sub_g))
            occ_g = np.bincount(lab_g,
                                minlength=modal_k) / len(lab_g)
            js = float(_js_divergence(occupancy, occ_g))
            rec["occupancy_js_vs_reference"] = js
            rec["status"] = "REPORTED"
        locked_cov[g] = rec
    stability["locked_group_coverage"] = locked_cov

    # --- REG-C01: temporal-block bootstrap — every replicate REFITS
    # preprocessing + GMM on its contiguous-date resample (train rows
    # only); fitted parameters are aligned to the reference model and
    # collected into 95% percentile intervals.  Replicate failures are
    # counted explicitly; >10% failures fails the axis.
    # REG-03: the resample pool is the missingness-selected fit
    # surface — rows the policy dropped can never re-enter a
    # replicate.
    train_dates = pd.to_datetime(df.loc[fit_rows_index,
                                      config.date_col])
    unique_days = np.array(sorted(train_dates.dt.normalize()
                                  .unique()))
    n_dates = int(len(unique_days))
    n_train_rows = int(len(fit_rows_index))
    norm_dates = train_dates.dt.normalize()
    train_index = fit_rows_index
    norm_values = norm_dates.to_numpy()

    boot = {"n_replicates": int(config.n_bootstrap),
            "n_succeeded": 0, "n_failures": 0,
            "failure_rate": None,
            "max_failure_rate": BOOTSTRAP_MAX_FAILURE_RATE,
            "procedure": ("contiguous-date block resample; "
                          "preprocessing + GMM refit inside every "
                          "replicate; components aligned to the "
                          "reference model"),
            "status": "FAIL", "reason": None}
    if n_dates < 2:
        boot["reason"] = ("fewer than 2 distinct train dates — no "
                          "contiguous block exists")
        boot["failure_rate"] = 1.0
        stability["temporal_block_bootstrap"] = boot
    else:
        block_len = int(config.bootstrap_block_len)
        rng_bt = np.random.default_rng(int(decl_seeds[0]))
        b_weights, b_means, b_occ, b_post, b_ari = \
            [], [], [], [], []
        failures = 0
        # REG-01: blocks are CALENDAR ranges at the declared cadence —
        # a block spanning missing dates simply contains fewer rows;
        # it never bridges the gap by borrowing a later date.
        day0 = unique_days[0]
        day_idx = {d: i for i, d in enumerate(unique_days)}
        step = pd.tseries.frequencies.to_offset(config.cadence)
        start_pool = np.arange(n_dates)
        for b in range(config.n_bootstrap):
            chosen = []
            while len(chosen) < n_train_rows:
                st = rng_bt.choice(start_pool)
                lo = unique_days[st]
                hi = lo + step * (block_len - 1)
                span = [d for d in unique_days if lo <= d <= hi]
                chosen.extend(
                    train_index[np.isin(norm_values, span)].tolist())
            rows = np.array(chosen[:n_train_rows])
            sub = df.loc[rows, feature_cols]
            try:
                p_b = TrainOnlyPreprocessor().fit(sub)
                X_b = p_b.transform(sub)
                if not np.isfinite(X_b).all():
                    raise ValueError("non-finite replicate transform")
            except Exception:
                failures += 1
                continue
            sd = decl_seeds[b % len(decl_seeds)]
            f_b = _fit_gmm(X_b, modal_k, sd)
            if not f_b["converged"]:
                failures += 1
                continue
            means_ref = _means_in_ref_space(f_b["model"].means_,
                                            p_b, prep)
            perm = _align_to_reference(model.means_, means_ref)
            b_weights.append(
                _aligned(f_b["model"].weights_, perm).tolist())
            b_means.append(
                _aligned(means_ref, perm).tolist())
            lab_b = f_b["model"].predict(X_b)
            occ_b = np.bincount(lab_b,
                                minlength=modal_k) / len(lab_b)
            b_occ.append(_aligned(occ_b, perm).tolist())
            b_post.append(float(
                f_b["model"].predict_proba(X_b).max(axis=1).mean()))
            ref_lab_b = model.predict(prep.transform(sub))
            b_ari.append(_ari(ref_lab_b, lab_b))
        boot["n_succeeded"] = len(b_post)
        boot["n_failures"] = int(failures)
        boot["failure_rate"] = float(failures / config.n_bootstrap)
        boot["block_len_dates"] = int(block_len)
        boot["cadence"] = config.cadence
        boot["gap_policy"] = config.gap_policy
        if b_post:
            bw = np.asarray(b_weights)
            bm = np.asarray(b_means)
            bo = np.asarray(b_occ)
            bp = np.asarray(b_post)
            boot["weight_intervals_95"] = {
                f"w{i}": [float(np.percentile(bw[:, i], 2.5)),
                          float(np.percentile(bw[:, i], 97.5))]
                for i in range(modal_k)}
            boot["means_intervals_95"] = {
                f"comp{i}": {
                    feature_cols[j]: [
                        float(np.percentile(bm[:, i, j], 2.5)),
                        float(np.percentile(bm[:, i, j], 97.5))]
                    for j in range(len(feature_cols))}
                for i in range(modal_k)}
            boot["occupancy_intervals_95"] = {
                f"occ{i}": [float(np.percentile(bo[:, i], 2.5)),
                            float(np.percentile(bo[:, i], 97.5))]
                for i in range(modal_k)}
            boot["mean_max_posterior_interval_95"] = [
                float(np.percentile(bp, 2.5)),
                float(np.percentile(bp, 97.5))]
            boot["ari_min"] = float(min(b_ari))
            boot["ari_mean"] = float(np.mean(b_ari))
            boot["status"] = (
                "PASS" if boot["failure_rate"]
                <= BOOTSTRAP_MAX_FAILURE_RATE else "FAIL")
            if boot["status"] == "FAIL":
                boot["reason"] = (
                    f"replicate failure rate "
                    f"{boot['failure_rate']:.3f} > "
                    f"{BOOTSTRAP_MAX_FAILURE_RATE}")
        else:
            boot["reason"] = ("every bootstrap replicate failed — no "
                              "parameter distribution exists")
        stability["temporal_block_bootstrap"] = boot

    # --- REG-05: season refits — each declared season is refit under
    # the frozen K and declared seed policy; components are aligned to
    # the reference model before the occupancy comparison (REG-C02)
    # and a label-invariant ARI is emitted per fold.
    season_refits = {}
    seasons_seen = [str(x) for x in
                    pd.unique(df.loc[train_mask, config.season_col])]
    for seas in sorted(seasons_seen):
        m_s = np.asarray(train_mask) & fit_sel_full & \
            (df[config.season_col].astype(str) == seas)
        sub = df.loc[m_s, feature_cols]
        rec = {"status": None, "js": None, "ari": None,
               "reason": None,
               "seeds_declared": [int(s) for s in
                                  (decl_seeds
                                   if config.fold_seed_policy == "all"
                                   else decl_seeds[:1])],
               "seed_failures": [], "per_seed": {}}
        if len(sub) < 50:
            rec["status"] = "SKIPPED"
            rec["reason"] = "insufficient in-season rows"
        else:
            rec = _refit_against_reference(
                sub, prep, model, occupancy,
                model.predict(prep.transform(sub)), modal_k,
                decl_seeds, config.fold_seed_policy)
        season_refits[seas] = rec
    if len(seasons_seen) < 2:
        stability["season_refits"] = {
            "status": "NOT_APPLICABLE",
            "reason": "single declared season — the calendar-artifact "
                      "axis has no refit complement",
            "folds": season_refits}
        season_ok = True
    else:
        stability["season_refits"] = {
            "status": "PASS" if all(
                f["status"] == "PASS" for f in season_refits.values())
            else "FAIL",
            "folds": season_refits}
        season_ok = all(f["status"] == "PASS"
                        for f in season_refits.values())

    # --- REG-06a/REG-C06: elevation bands (only when declared +
    # present).  Per-band refits are aligned to the reference model;
    # the axis additionally records whether the regime assignment is
    # marginally explained by elevation alone (diagnostic note — the
    # feature-ablation refit is out of scope by declaration).
    if config.elevation_col is None:
        stability["elevation"] = {
            "status": "NOT_APPLICABLE",
            "reason": "no elevation column declared in the run "
                      "configuration"}
        elev_ok = True
    else:
        elev_refits = {}
        elev_ok = True
        band_js = []
        bands = pd.unique(df.loc[train_mask, config.elevation_col])
        for band in sorted(map(str, bands)):
            m_e = np.asarray(train_mask) & fit_sel_full & \
                (df[config.elevation_col].astype(str) == band)
            sub = df.loc[m_e, feature_cols]
            if len(sub) < 50:
                rec = {"status": "SKIPPED", "js": None, "ari": None,
                       "reason": "insufficient band rows",
                       "seeds_declared": [], "seed_failures": [],
                       "per_seed": {}}
                elev_ok = False
            else:
                rec = _refit_against_reference(
                    sub, prep, model, occupancy,
                    model.predict(prep.transform(sub)), modal_k,
                    decl_seeds, config.fold_seed_policy)
                if rec["status"] == "FAIL":
                    elev_ok = False
                elif rec["status"] == "NONCONVERGED":
                    elev_ok = False
                if rec["js"] is not None:
                    band_js.append(float(rec["js"]))
            elev_refits[band] = rec
        if band_js:
            max_band_js = max(band_js)
            if max_band_js >= 0.05:
                note = ("regime occupancy differs across elevation "
                        f"bands (max per-band occupancy JS "
                        f"{max_band_js:.4f}) — the assignment may be "
                        "marginally explained by elevation alone; "
                        "feature-ablation refit is out of scope by "
                        "declaration")
            else:
                note = ("regime assignment is not marginally "
                        "explained by elevation alone (max per-band "
                        f"occupancy JS {max_band_js:.4f})")
        else:
            max_band_js = None
            note = ("no evaluable elevation band — marginal "
                    "elevation explanation could not be assessed")
        # REG-10: elevation-only ablation — fit a 1-D GMM on the
        # elevation column alone; if it reproduces the partition the
        # regime structure is marginally explained by elevation and
        # cannot be promoted as independent structure.
        try:
            elev_train = df.loc[fit_sel_full,
                                config.elevation_col].to_numpy(
                                    dtype=np.float64).reshape(-1, 1)
            f_el = _fit_gmm(elev_train, modal_k,
                            int(decl_seeds[0]))
            if f_el["converged"]:
                el_lab = f_el["model"].predict(elev_train)
                ref_lab_el = model.predict(prep.transform(
                    df.loc[fit_sel_full, feature_cols]))
                abl_ari = float(_ari(ref_lab_el, el_lab))
            else:
                abl_ari = None
        except Exception:
            abl_ari = None
        if abl_ari is not None and                 abl_ari > float(config.elev_ablation_ari_max):
            elev_ok = False
            note += (" | ELEVATION ABLATION: elevation alone "
                     f"reproduces the partition (ARI {abl_ari:.3f} "
                     f"> {config.elev_ablation_ari_max}) — the "
                     "structure is not independent")
        stability["elevation"] = {
            "status": "PASS" if elev_ok else "FAIL",
            "folds": elev_refits,
            "marginal_band_js_max": max_band_js,
            "elevation_only_ablation_ari": abl_ari,
            "ablation_threshold": float(config.elev_ablation_ari_max),
            "note": note}

    # --- REG-C05: missingness/effort sensitivity — exactly the
    # declared policy executes; unavailable axes are NOT_APPLICABLE
    # with a reason, never a silent pass.
    row_miss_frac = df.loc[train_mask, feature_cols].isna() \
        .mean(axis=1).to_numpy()
    train_rows_index = df.loc[train_mask].index.to_numpy()
    policy = config.missingness_policy
    miss_ax = {"policy": policy, "status": "FAIL", "reason": None}
    if policy in ("listwise", "bounded_impute"):
        if policy == "listwise":
            sel = row_miss_frac == 0.0
            miss_ax["complete_rows"] = int(sel.sum())
        else:
            sel = row_miss_frac <= float(config.max_missingness)
            miss_ax["n_rows_used"] = int(sel.sum())
            miss_ax["max_missingness"] = float(
                config.max_missingness)
        # REG-03: the axis refits only the policy-selected fit
        # surface — intersect the recomputed selection with fit_sel
        # so a policy-dropped row can never re-enter a refit.
        sel = np.asarray(sel, dtype=bool) & \
            np.asarray(fit_sel, dtype=bool)
        sub_idx = train_rows_index[sel]
        sub = df.loc[sub_idx, feature_cols]
        if len(sub) < 50:
            miss_ax["status"] = "FAIL"
            miss_ax["reason"] = (
                f"declared policy {policy!r} leaves {len(sub)} "
                "train rows (<50) — the missingness axis cannot be "
                "evaluated")
        else:
            rec = _refit_against_reference(
                sub, prep, model, occupancy,
                model.predict(prep.transform(sub)), modal_k,
                decl_seeds, config.fold_seed_policy)
            miss_ax.update(rec)
            miss_ax["occupancy_js"] = rec["js"]
    else:  # stratified: per-missingness-band occupancy comparison
        band_defs = {
            "complete": row_miss_frac == 0.0,
            "low_(0,0.25]": (row_miss_frac > 0.0) &
                            (row_miss_frac <= 0.25),
            "mid_(0.25,0.5]": (row_miss_frac > 0.25) &
                              (row_miss_frac <= 0.5),
            "high_>0.5": row_miss_frac > 0.5,
        }
        bands = {}
        for bname, bsel in band_defs.items():
            # REG-03: bands are intersected with the policy-selected
            # fit surface — dropped rows never re-enter a band refit.
            sub_idx = train_rows_index[
                np.asarray(bsel, dtype=bool) &
                np.asarray(fit_sel, dtype=bool)]
            sub = df.loc[sub_idx, feature_cols]
            brec = {"n_rows": int(len(sub)), "status": None,
                    "js": None}
            if len(sub) < 30:
                brec["status"] = "SKIPPED"
                brec["reason"] = "fewer than 30 rows in band"
            else:
                # REG-07: each band REFITS preprocessing + GMM under
                # the declared seed policy — a band comparison that
                # only re-predicts through the primary model cannot
                # detect that the partition itself is missingness-
                # driven.
                brec = _refit_against_reference(
                    sub, prep, model, occupancy,
                    model.predict(prep.transform(sub)),
                    modal_k, decl_seeds, config.fold_seed_policy)
                brec["n_rows"] = int(len(sub))
            bands[bname] = brec
        evaluable = [r for r in bands.values()
                     if r["status"] in ("PASS", "FAIL")]
        miss_ax["bands"] = bands
        if len(evaluable) < 2:
            miss_ax["status"] = "NOT_APPLICABLE"
            miss_ax["reason"] = (
                "fewer than 2 evaluable missingness bands — the "
                "stratified policy has no comparison surface on "
                "this frame")
        else:
            ok = all(r["status"] == "PASS" for r in evaluable)
            miss_ax["status"] = "PASS" if ok else "FAIL"
            if not ok:
                miss_ax["reason"] = (
                    "at least one missingness band diverges from "
                    "the reference occupancy")
    stability["missingness_sensitivity"] = miss_ax
    # a declared-but-unevaluable missingness axis is never a pass
    miss_ok = miss_ax["status"] == "PASS"

    # REG-08: effort axis — undeclared means FAIL unless an explicit
    # waiver reason is bound in configuration; a declared axis uses
    # the declared stratification policy, never a dynamic one.
    if config.effort_col is None:
        if config.effort_waiver_reason.strip():
            stability["effort_sensitivity"] = {
                "status": "NOT_APPLICABLE",
                "reason": f"waived: {config.effort_waiver_reason}"}
            effort_ok = True
        else:
            stability["effort_sensitivity"] = {
                "status": "FAIL",
                "reason": "no effort column declared and no "
                          "effort_waiver_reason bound — the effort "
                          "axis cannot be silently skipped"}
            effort_ok = False
    else:
        ev = df.loc[train_mask, config.effort_col]
        bands = {}
        # REG-08: the effort split is a DECLARED policy recorded in
        # the bound config — "median" is the declared midpoint split
        # (its threshold is recorded), not a silently-dynamic strata.
        _ev_num = ev.to_numpy(dtype=np.float64) \
            if pd.api.types.is_numeric_dtype(ev) else None
        if _ev_num is not None:
            if _es == "tercile":
                q1, q2 = (float(np.nanquantile(_ev_num, q))
                          for q in (1 / 3, 2 / 3))
                band_sel = {"le_t1": _ev_num <= q1,
                            "t1_t2": (_ev_num > q1) & (_ev_num <= q2),
                            "gt_t2": _ev_num > q2}
                strata_policy = {"kind": "numeric_tercile",
                                 "q1": q1, "q2": q2}
            elif _es.startswith("quantile:"):
                q = float(_es.split(":", 1)[1])
                cut = float(np.nanquantile(_ev_num, q))
                band_sel = {f"le_q{q}": _ev_num <= cut,
                            f"gt_q{q}": _ev_num > cut}
                strata_policy = {"kind": "numeric_quantile",
                                 "q": q, "cut": cut}
            else:  # "median" (default) and "first10" on numeric
                med = float(np.nanmedian(_ev_num))
                band_sel = {"le_median": _ev_num <= med,
                            "gt_median": _ev_num > med}
                strata_policy = {"kind": "numeric_median",
                                 "median": med}
        else:
            uniques = sorted(map(str, ev.dropna().unique()))
            band_sel = {u: (ev.astype(str) == u).to_numpy()
                        for u in uniques[:10]}
            strata_policy = {"kind": "categorical",
                             "values": uniques[:10]}
        for bname, bsel in band_sel.items():
            # REG-03: effort bands are intersected with the
            # policy-selected fit surface — a missingness-dropped row
            # can never re-enter a band refit.
            sub_idx = train_rows_index[
                np.asarray(bsel, dtype=bool) &
                np.asarray(fit_sel, dtype=bool)]
            sub = df.loc[sub_idx, feature_cols]
            brec = {"n_rows": int(len(sub)), "status": None,
                    "js": None}
            if len(sub) < 30:
                brec["status"] = "SKIPPED"
                brec["reason"] = "fewer than 30 rows in band"
            else:
                # refit under the declared seed policy — occupancy
                # under the primary model alone cannot detect an
                # effort-driven partition
                brec = _refit_against_reference(
                    sub, prep, model, occupancy,
                    model.predict(prep.transform(sub)),
                    modal_k, decl_seeds, config.fold_seed_policy)
                brec["n_rows"] = int(len(sub))
            bands[str(bname)] = brec
        evaluable = [r for r in bands.values()
                     if r["status"] in ("PASS", "FAIL")]
        if len(evaluable) < 2:
            stability["effort_sensitivity"] = {
                "status": "FAIL",
                "reason": "declared effort axis yields fewer than 2 "
                          "evaluable bands — a declared axis that "
                          "cannot be evaluated is not a pass",
                "strata_policy": strata_policy,
                "bands": bands}
            effort_ok = False
        else:
            ok = all(r["status"] == "PASS" for r in evaluable)
            stability["effort_sensitivity"] = {
                "status": "PASS" if ok else "FAIL",
                "strata_policy": strata_policy,
                "bands": bands}
            effort_ok = ok

    # --- REG-C06: era drift — DECLARED boundaries only.  An era
    # column without declared era_boundaries fails the axis: no
    # dynamically chosen boundary may enter a claimed result.
    drift = {}
    era_ax = {"status": "NOT_APPLICABLE", "reason": None,
              "boundaries": None, "pairs": {}}
    if config.era_col is None:
        if config.era_waiver_reason.strip():
            era_ax["reason"] = (f"waived: "
                                f"{config.era_waiver_reason}")
        else:
            era_ax["status"] = "FAIL"
            era_ax["reason"] = ("no era column declared and no "
                                "era_waiver_reason bound — the era "
                                "axis cannot be silently skipped")
    elif not config.era_boundaries:
        era_ax["status"] = "FAIL"
        era_ax["reason"] = (
            "era column present but era_boundaries undeclared — no "
            "dynamically chosen boundary may enter a claimed result")
    else:
        boundaries = [str(x) for x in config.era_boundaries]
        era_ax["boundaries"] = list(boundaries)
        # REG-03: era assignment is computed over the policy-selected
        # fit surface — X_train is indexed by fit rows only, so a
        # dropped-row era/dates vector cannot index it.
        train_dates_str = df.loc[fit_rows_index,
                                 config.date_col].astype(str)
        if all(_iso_date_ok(x) for x in boundaries):
            bounds = sorted(boundaries)
            seg_labels = (
                [f"<{bounds[0]}"] +
                [f"{bounds[i - 1]}..{bounds[i]}"
                 for i in range(1, len(bounds))] +
                [f">={bounds[-1]}"])
            era_assign = np.array(
                [seg_labels[bisect.bisect_right(bounds, d)]
                 for d in train_dates_str])
        else:
            observed = set(map(str, pd.unique(
                df.loc[train_mask, config.era_col])))
            declared = set(boundaries)
            if declared != observed:
                era_assign = None
                era_ax["status"] = "FAIL"
                era_ax["reason"] = (
                    f"declared era labels {sorted(declared)} do not "
                    f"match observed train eras {sorted(observed)}")
            else:
                era_assign = df.loc[
                    fit_sel_full,
                    config.era_col].astype(str).to_numpy()
        if era_assign is not None:
            eras = sorted(set(era_assign.tolist()))
            if len(eras) < 2:
                era_ax["status"] = "FAIL"
                era_ax["reason"] = (
                    "declared era partition yields fewer than 2 "
                    "populated eras on the train frame")
            else:
                for i in range(len(eras)):
                    for j in range(i + 1, len(eras)):
                        a = X_train[era_assign == eras[i]].mean(
                            axis=0)
                        b = X_train[era_assign == eras[j]].mean(
                            axis=0)
                        drift[f"{eras[i]}|{eras[j]}"] = float(
                            np.abs(a - b).max())
                ok = all(v < config.era_drift_max
                         for v in drift.values())
                era_ax["status"] = "PASS" if ok else "FAIL"
                era_ax["threshold"] = float(config.era_drift_max)
                era_ax["pairs"] = drift
                if not ok:
                    era_ax["reason"] = (
                        "max abs standardized mean shift across a "
                        "declared era pair >= "
                        f"{config.era_drift_max}")
    stability["era_drift"] = era_ax
    era_ok = era_ax["status"] in ("PASS", "NOT_APPLICABLE")
    stability["drift_max_abs_mean_shift"] = drift

    # --- REG-C04: null families — empirical envelope of the SAME
    # predeclared statistic (silhouette) against the observed fit;
    # declared null_alpha thresholds; any failed replicate fails the
    # gate.  The hard-coded 0.02 JS threshold is removed.
    observed_stat = _silhouette(X_train, labels_train) \
        if modal_k >= 2 else None
    # REG-03: null generators draw from the policy-selected
    # surface, not the raw train slice.
    train_sub = df.loc[fit_rows_index]

    def _gen_shuffled(i, gen_seed):
        return shuffled_null(X_train, seed=gen_seed)

    def _gen_season(i, gen_seed):
        raw = season_matched_null(train_sub, feature_cols,
                                  config.season_col, seed=gen_seed,
                                  era_col=config.era_col)
        if not np.isfinite(raw).all():
            return None
        p_n = TrainOnlyPreprocessor().fit(
            pd.DataFrame(raw, columns=list(feature_cols)))
        X_n = p_n.transform(
            pd.DataFrame(raw, columns=list(feature_cols)))
        return X_n if np.isfinite(X_n).all() else None

    null_shuf = _null_envelope(observed_stat, _gen_shuffled,
                               modal_k, decl_seeds,
                               config.n_null_replicates,
                               config.null_alpha,
                               k_candidates=config.k_candidates)
    null_seas = _null_envelope(observed_stat, _gen_season,
                               modal_k, decl_seeds,
                               config.n_null_replicates,
                               config.null_alpha,
                               k_candidates=config.k_candidates)
    # REG-06: every null family, its inputs, seeds, and replicate
    # statistics are bound and hashed — replay can recompute the
    # envelope without trusting a summary.
    for fam, rec in (("shuffled", null_shuf),
                     ("season_matched", null_seas)):
        rec["family_digest"] = _digest(
            {"family": fam, "seed_cycle": decl_seeds,
             "n_replicates": rec["n_replicates"],
             "statistic": rec["statistic"],
             "p_value": rec["p_value"],
             # C06: bind every declared record field into the family
             # digest — a tampered summary cannot replay under the
             # same digest.
             "observed": rec.get("observed"),
             "alpha": rec.get("alpha"),
             "n_succeeded": rec.get("n_succeeded"),
             "n_failed": rec.get("n_failed"),
             "status": rec.get("status"),
             "reason": rec.get("reason"),
             "selection": rec.get("selection"),
             "null_stat_min": rec.get("null_stat_min"),
             "null_stat_max": rec.get("null_stat_max"),
             "null_k_distribution": rec.get(
                 "null_k_distribution", {}),
             "replicates": rec.get("replicates", [])})
    nulls = {"statistic": NULL_STATISTIC,
             "observed": observed_stat,
             "alpha": float(config.null_alpha),
             "n_replicates": int(config.n_null_replicates),
             "season_era_stratified": bool(
                 config.era_col and config.era_col in df.columns),
             # REG-05b: serialize the K=1 BIC list so replay can
             # recompute null_model_digest over exactly the carried
             # material instead of trusting the envelope.
             "k1_bic": [_finite_or_token(f["bic"])
                        for f in null_fits],
             "shuffled": null_shuf,
             "season_matched": null_seas}

    # --- terminal status -------------------------------------------------
    # Every required stability axis must explicitly PASS (or carry a
    # policy-admissible NOT_APPLICABLE) — a recorded-but-ungated
    # metric can never promote a result.
    required_gates = {
        # REG-03: a one-seed fold policy is diagnostic-only — terminal
        # stability requires every declared seed to participate.
        "seed_policy": config.fold_seed_policy == "all",
        "modal_k_unanimous": bool(k_freq == 1.0),
        "seed_ari": bool(seed_ari) and min(seed_ari) > 0.6,
        "seed_coverage": bool(seed_coverage_complete),
        "loro": bool(loro_pass),
        "temporal_bootstrap": bool(boot["status"] == "PASS"),
        "season_refits": bool(season_ok),
        "elevation": bool(elev_ok),
        "missingness": bool(miss_ok),
        "effort": bool(effort_ok),
        "era_drift": bool(era_ok),
        "shuffled_null": bool(null_shuf["status"] == "PASS"),
        "season_matched_null": bool(null_seas["status"] == "PASS"),
    }
    assert set(required_gates) == REQUIRED_REGIME_GATE_NAMES
    stability["required_gates"] = required_gates
    stable = all(required_gates.values())
    structural = ("modal_k_unanimous", "seed_ari", "seed_coverage",
                  "loro")
    if stable:
        status = "DESCRIPTIVE_REGIME_ONLY"
    elif not all(required_gates[g] for g in structural):
        # structural instability — the partition itself does not
        # reproduce under seeds/regions, or a declared seed is
        # unaccounted for
        status = "UNSUPERVISED_STRUCTURE_NOT_STABLE"
    else:
        # structure reproduces but an evidence gate (bootstrap,
        # season/elevation/missingness/effort/era axis, or a null
        # family) failed — candidate structure only, never
        # descriptive-stable
        status = "CANDIDATE_ONLY"

    # C15: bind a real RunManifestV0 — the run's worker identity,
    # environment, seed, and input/output byte digests are typed and
    # validated, not a synthetic digest.  The logical creation time is
    # derived deterministically from the input data window (max date)
    # so identical runs stay byte-identical.
    _env_digest = _digest({
        "python": sys.version.split()[0],
        "numpy": np.__version__})
    _max_date = pd.to_datetime(df[config.date_col]).max()
    # R9-P07: serialize the config once — the artifact's config copy,
    # config_digest, and the run_manifest run_id all bind this exact
    # dict.  Forecast fields are normalized (sorted strings) so the
    # serialized config matches the artifact's top-level copies
    # field-for-field regardless of declaration order.
    _config_dict = dataclasses.asdict(config)
    # R10: forecast_vintages is artifact-level evidence (the records
    # behind forecast_vintage_digests), never part of the bound
    # configuration — the serialized config keeps exactly the
    # declared 33-field contract.
    _config_dict.pop("forecast_vintages", None)
    _config_dict["forecast_vintage_digests"] = sorted(
        str(d) for d in config.forecast_vintage_digests)
    _config_dict["forecast_feature_set"] = sorted(
        str(c) for c in config.forecast_feature_set)
    _run_manifest = RunManifestV0(
        run_id=f"regimes-{_digest(_config_dict)[:16]}"
               f"-{_sha_bytes(input_bytes)[:16]}",
        worker_id="science_v0.regimes.run_regimes",
        created_at=_max_date.strftime("%Y-%m-%dT%H:%M:%SZ"),
        environment_digest=_env_digest,
        seed=int(decl_seeds[0]) if decl_seeds else None,
        input_digests=(_sha_bytes(input_bytes),),
        output_digests=(assignment_digest,),
        checkpoint_policy="atomic_publish_or_quarantine",
        status="COMPLETED")
    _rm_problems = _run_manifest.problems()
    if _rm_problems:
        return {"status": "RUN_ERROR",
                "reason": f"run manifest invalid: {_rm_problems}"}
    _run_manifest_dict = _run_manifest.to_dict()

    # R8-C09: the typed fit-partition binding — a first-class record
    # re-stating the fit surface (train/heldout groups, row counts,
    # the train row-key digest, the fit-window cutoff, the feature
    # matrix) so a downstream boundary cross-checks a single bound
    # structure instead of trusting scattered flat fields.
    # R9 single-source: the semantic feature-matrix digest is the
    # shared canonical 6-decimal normalization — emission and the
    # shared validator run ONE routine.
    _fm_digest = semantic_feature_matrix_digest(input_values)
    _train_dates = pd.to_datetime(
        df.loc[train_mask, config.date_col])
    _fit_partition = {
        "record_type": "fit_partition/v0",
        "train_groups": sorted(mask_groups),
        "heldout_groups": sorted(heldout_groups),
        "n_train_rows": int(mask.sum()),
        "n_rows": int(len(df)),
        "train_row_keys_digest": sorted_row_key_digest(
            [row_key(u, d) for u, d in zip(
                df.loc[train_mask, config.unit_col].astype(str),
                df.loc[train_mask, config.date_col].astype(str))]),
        "cutoff_iso": (_train_dates.max().strftime("%Y-%m-%d")
                       if len(_train_dates) else ""),
        "feature_matrix_digest": _fm_digest,
        "feature_cols": list(feature_cols)}

    # C03: the unit→basin/group map — hoisted so the canonical pair
    # digest binds the SAME emitted value inside the envelope (the
    # shared validator requires unit_basin_map_digest).
    _unit_basin_map = sorted(
        (str(u), str(g)) for u, g in
        df[[config.unit_col, config.group_col]].drop_duplicates()
        .itertuples(index=False))

    # SEISMIC-01: the emitted data_class is the declared retrospective
    # class on a RETROSPECTIVE run (REANALYSIS default, or the
    # seismic waveform class for the sidecar); a FORECAST run always
    # binds ARCHIVED_OPERATIONAL — REANALYSIS and the seismic class
    # are retrospective-only and can never be emitted as forecast
    # evidence.
    _emitted_data_class = (
        config.retrospective_data_class
        if config.mode == RegimeMode.RETROSPECTIVE_REGIME.value
        else ForecastDataClass.ARCHIVED_OPERATIONAL.value)

    artifact = {
        "mode": config.mode,
        # FCST-01: FORECAST_REGIME binds issue-time archive vintages
        # — ARCHIVED_OPERATIONAL is the ForecastDataClass archive
        # class; REANALYSIS is retrospective-only and can never be
        # emitted as forecast evidence.
        "data_class": _emitted_data_class,
        "forecast_vintage_digests": sorted(
            str(d) for d in config.forecast_vintage_digests),
        "forecast_feature_set": sorted(
            str(c) for c in config.forecast_feature_set),
        "fitted_on": "TRAIN_ONLY",
        "label_blinding": True,
        # provenance binding (I-05): feature order, frame digest,
        # configuration, fit/held-out membership, the exact mask,
        # and the exact raw input bytes are all bound before freezing.
        "feature_cols": list(feature_cols),
        # six-decimal semantic digest is a versioned normalization
        # domain; the exact raw input bytes are bound alongside it
        "feature_matrix_digest": _fm_digest,
        # R8-C09: the typed fit-partition record and its digest are
        # bound into the artifact BEFORE regime_artifact_digest is
        # computed — a rehashed partial artifact cannot pass freeze.
        "fit_partition": _fit_partition,
        "fit_partition_digest": _digest(_fit_partition),
        "input_bytes_digest": _sha_bytes(input_bytes),
        "input_values": input_values,
        "input_schema": {
            "feature_cols": list(feature_cols),
            "n_rows": int(len(df)),
            "dtypes": {c: str(df[c].dtype) for c in feature_cols},
            "shape": [int(len(df)), int(len(feature_cols))]},
        "config": _config_dict,
        "config_digest": _digest(_config_dict),
        # R9-P07: the artifact's source_manifest IS the serialized
        # config's copy — config.source_manifest and
        # artifact.source_manifest can never diverge.
        "source_manifest": _config_dict["source_manifest"],
        "environment_digest": _env_digest,
        # C15: a real typed RunManifestV0 serialized into the
        # artifact — its digest recomputes over the full record.
        "run_manifest": _run_manifest_dict,
        "run_manifest_digest": _digest(_run_manifest_dict),
        "fit_groups": sorted(mask_groups),
        "heldout_groups_declared": sorted(heldout_groups),
        # C03: the unit→basin/group map is bound into the artifact —
        # downstream association cannot remap a unit to a different
        # basin than the producer declared.
        "unit_basin_map": _unit_basin_map,
        # R9: the canonical unit→basin pair digest — emitted BEFORE
        # regime_artifact_digest so it is inside the bound envelope.
        "unit_basin_map_digest": sha256_canonical(
            canonical_unit_basin_pairs(_unit_basin_map)),
        "n_train_rows": int(mask.sum()),
        "n_rows": int(len(df)),
        "train_mask_digest": _digest(mask.tolist()),
        "k": modal_k,
        "seeds": decl_seeds,
        "seeds_declared": decl_seeds,
        "seed_coverage": seed_coverage,
        "per_seed_best_k": {str(s): int(k)
                            for s, k in per_seed_best.items()},
        "modal_k_frequency": k_freq,
        "occupancy": occupancy.tolist(),
        "assignments": assignments,
        "assignment_digest": assignment_digest,
        "mean_max_posterior": float(post.mean()),
        "ambiguous_fraction": float((post < 0.7).mean()),
        "missingness": missingness,
        "missingness_applied": missingness_applied,
        # REG-12: preprocessing bound end-to-end — imputer stats,
        # scaler params, feature order, canonical row keys, and the
        # exact policy-selected train membership.
        "preprocessing": {
            "imputer_strategy": "median",
            "imputer_statistics": prep._imputer.statistics_.tolist(),
            "scaler_mean": prep._scaler.mean_.tolist(),
            "scaler_var": prep._scaler.var_.tolist(),
            "feature_order": list(feature_cols),
            "row_keys_digest": sorted_row_key_digest(
                [row_key(u, d) for u, d in zip(
                    df[config.unit_col].astype(str),
                    df[config.date_col].astype(str))]),
            "train_mask_membership_digest": _digest(
                sorted(str(i) for i in
                       df.index[train_mask][
                           np.asarray(fit_sel)].tolist()))},
        "stability": stability,
        "nulls": nulls,
        "model": {"weights": model.weights_.tolist(),
                  "means": model.means_.tolist(),
                  "covariances": model.covariances_.tolist()},
        "preprocessing_digest": None,  # bound below after section
        "k_selection_digest": _digest(
            [{kk: _finite_or_token(f[kk])
              for kk in ("k", "seed", "bic", "aic", "converged")}
             for f in fits]),
        "stability_report_digest": _digest(stability),
        "null_model_digest": _digest({
            "k1_bic": [_finite_or_token(f["bic"])
                       for f in null_fits],
            "null_families": {fam: rec.get("family_digest")
                              for fam, rec in
                              (("shuffled", null_shuf),
                               ("season_matched", null_seas))}}),
        "status": status,
        # REG-13: CANDIDATE_ONLY is a demotion, not a terminal
        # result — only DESCRIPTIVE_REGIME_ONLY may associate.
        "terminal": status in ("DESCRIPTIVE_REGIME_ONLY",
                               "UNSUPERVISED_STRUCTURE_NOT_STABLE"),
        # SEISMIC-01: a seismic-waveform retrospective artifact is
        # terminal-descriptive when stable but NEVER associable —
        # the association and forecast adapters admit REANALYSIS /
        # forecast classes only, and the flag is not free.
        "associable": status == "DESCRIPTIVE_REGIME_ONLY" and
                      _emitted_data_class !=
                      SEISMIC_WAVEFORM_RETROSPECTIVE_DATA_CLASS,
        "disclaimer": "descriptive regime structure only; not an "
                      "event precursor, association, or skill claim",
    }
    # R8-C10: FORECAST_REGIME binds a typed forecast-feature payload
    # — the vintage digests, the declared feature subset, the matrix
    # digest, and the frame's row count/keys in one hashed record —
    # so a bare forecast_feature_set string is no longer the only
    # binding.  RETROSPECTIVE_REGIME never carries it.
    if config.mode == RegimeMode.FORECAST_REGIME.value:
        _ffp = {
            "record_type": "forecast_feature_payload/v0",
            "forecast_feature_set": sorted(
                str(c) for c in config.forecast_feature_set),
            "forecast_vintage_digests": sorted(
                str(d) for d in config.forecast_vintage_digests),
            "feature_matrix_digest": _fm_digest,
            "row_count": int(len(df)),
            "row_keys_digest":
                artifact["preprocessing"]["row_keys_digest"]}
        artifact["forecast_feature_payload"] = _ffp
        artifact["forecast_feature_payload_digest"] = _digest(_ffp)
        # R10: the declared vintage evidence records ride the
        # artifact verbatim (inside regime_artifact_digest) — an
        # associable FORECAST artifact must carry the records its
        # forecast_vintage_digests attest.  Undeclared means the key
        # is simply absent.
        if config.forecast_vintages:
            artifact["forecast_vintages"] = [
                dict(v) for v in config.forecast_vintages]
    artifact["preprocessing_digest"] = _digest(
        artifact["preprocessing"])
    artifact["regime_artifact_digest"] = _digest(
        {k: v for k, v in artifact.items()
         if k != "regime_artifact_digest"})
    return artifact


def _validate_config_semantics(cfg: dict) -> list[str]:
    """C04: semantic revalidation of the serialized producer config.

    Freeze and adapters recompute the config digest but do not
    revalidate semantics — a digest-matching config with invalid
    cadence, block length, K universe, or seeds would pass.  This
    function re-runs the key semantic checks on the serialized dict
    so any downstream boundary catches them.
    """
    problems: list[str] = []
    cadence = cfg.get("cadence")
    if not isinstance(cadence, str) or not cadence.strip():
        problems.append("cadence must be a non-empty string")
    else:
        try:
            pd.Timedelta(
                pd.tseries.frequencies.to_offset(cadence).nanos)
        except (TypeError, ValueError, AttributeError):
            problems.append(f"cadence {cadence!r} is not a "
                            "fixed-length offset")
    if cfg.get("gap_policy") != "calendar":
        problems.append(f"gap_policy must be 'calendar'; got "
                        f"{cfg.get('gap_policy')!r}")
    block_len = cfg.get("bootstrap_block_len")
    if isinstance(block_len, bool) or \
            not isinstance(block_len, int) or block_len <= 0:
        problems.append(f"bootstrap_block_len must be a positive "
                        f"int; got {block_len!r}")
    ks = cfg.get("k_candidates")
    if not isinstance(ks, (list, tuple)) or not ks:
        problems.append("k_candidates must be non-empty")
    else:
        if any(isinstance(k, bool) or not isinstance(k, int) or
               k not in K_CANDIDATES for k in ks):
            problems.append(f"k_candidates must be ints within "
                            f"{K_CANDIDATES}")
        if 1 not in ks:
            problems.append("K=1 null candidate must appear in "
                            "k_candidates")
    seeds = cfg.get("seeds")
    if not isinstance(seeds, (list, tuple)) or \
            len(set(seeds)) < MIN_SEEDS:
        problems.append(f"need >= {MIN_SEEDS} distinct seeds")
    elif any(isinstance(sd, bool) or not isinstance(sd, int) or
             sd < 0 for sd in seeds):
        problems.append("seeds must be non-negative ints")
    eras = cfg.get("era_boundaries") or []
    if not isinstance(eras, (list, tuple)):
        problems.append("era_boundaries must be a list of "
                        "YYYY-MM-DD dates or era labels")
    else:
        # era boundaries are either uniformly ISO dates or uniformly
        # era labels — the producer allows both styles
        iso_flags = [_iso_date_ok(e) for e in eras if
                     isinstance(e, str) and e.strip()]
        if any(not isinstance(e, str) or not e.strip() for e in eras):
            problems.append("era_boundaries must be non-empty "
                            "strings")
    miss = cfg.get("missingness_policy")
    if miss not in MISSINGNESS_POLICIES:
        problems.append(f"missingness_policy {miss!r} not in "
                        f"{MISSINGNESS_POLICIES}")
    effort = cfg.get("effort_split")
    if effort not in ("median", "tercile", "first10") and \
            not (isinstance(effort, str) and
                 effort.startswith("quantile:")):
        problems.append(f"effort_split {effort!r} not a supported "
                        "policy")
    mode = cfg.get("mode") or RegimeMode.RETROSPECTIVE_REGIME.value
    if mode not in RegimeMode._value2member_map_:
        problems.append(f"mode {mode!r} not a RegimeMode value")
    # SEISMIC-01: the serialized retrospective_data_class must be a
    # declared retrospective class; on a FORECAST config only the
    # REANALYSIS default is admissible.
    rdc = cfg.get("retrospective_data_class",
                  ForecastDataClass.REANALYSIS.value)
    if rdc not in RETROSPECTIVE_REGIME_DATA_CLASSES:
        problems.append(
            f"retrospective_data_class {rdc!r} not in "
            f"{sorted(RETROSPECTIVE_REGIME_DATA_CLASSES)}")
    elif mode == RegimeMode.FORECAST_REGIME.value and \
            rdc != ForecastDataClass.REANALYSIS.value:
        problems.append(
            "FORECAST_REGIME config must carry the REANALYSIS "
            "retrospective_data_class default")
    return problems


def freeze_regime_artifact(artifact: dict) -> dict:
    """Freeze assignments BEFORE any label association. The frozen
    dict is immutable input for downstream phases — labels are opened
    only after this call returns.

    PROV-C03: freeze is an audit, not a stamp.  Every bound digest is
    recomputed at freeze time — assignment_digest, config_digest,
    input_bytes_digest, and the regime_artifact_digest — and the
    declared terminal status is checked against the recorded
    required_gates.  A direct freeze call on a hand-assembled or
    mutated artifact fails closed; it cannot bypass the producer
    audit.

    R8-C01: the shared provenance floor
    (``research_v0.producer_validation.validate_producer_payload``)
    runs BEFORE any digest recomputation — the same validator the
    association adapter and the experiment auditor run — so a
    rehashed partial artifact (missing ``model``,
    ``source_manifest``, the typed ``fit_partition`` binding, …)
    fails closed here exactly as it does downstream.
    """
    if not isinstance(artifact, Mapping):
        raise ValueError("cannot freeze: artifact is not a mapping")
    # The two fields only freeze itself emits (frozen, freeze_digest)
    # are supplied to the probe so the canonical schema floor applies
    # to the pre-freeze surface; a caller's own values, when present,
    # are validated as carried.
    _probe = dict(artifact)
    _probe.setdefault("frozen", True)
    if "freeze_digest" not in _probe:
        try:
            _probe["freeze_digest"] = _digest(
                {k: v for k, v in artifact.items()
                 if k != "freeze_digest"})
        except (TypeError, ValueError):
            _probe["freeze_digest"] = "uncomputable"
    _shared = validate_producer_payload(_probe)
    if _shared:
        raise ValueError("cannot freeze: shared producer validation "
                         "failed: " + "; ".join(_shared))
    # R9-P02: defense-in-depth — the shared validator byte-verifies
    # non-fixture source manifests, but freeze re-verifies locally so
    # a hand-assembled non-fixture artifact can never freeze on
    # unverified bytes even if the shared floor changes.
    _sm = artifact.get("source_manifest")
    if isinstance(_sm, Mapping):
        _is_fixture, _fixture_problems = fixture_flag(_sm)
        if _fixture_problems:
            raise ValueError(
                "cannot freeze: source_manifest fixture marker "
                "invalid: " + "; ".join(_fixture_problems))
        if not _is_fixture:
            _ev_problems = verify_source_evidence(_sm)
            if _ev_problems:
                raise ValueError(
                    "cannot freeze: source_manifest evidence "
                    "verification failed: " + "; ".join(_ev_problems))
    status = artifact.get("status")
    if status == "RUN_ERROR":
        raise ValueError("cannot freeze a RUN_ERROR artifact")
    if status not in STATUSES:
        raise ValueError(f"cannot freeze an artifact without a "
                         f"terminal status in {sorted(STATUSES)}")
    if not artifact.get("assignments"):
        raise ValueError("cannot freeze an artifact without the "
                         "assignment sidecar")
    # --- recompute bound digests -------------------------------------
    if artifact.get("assignment_digest") != _digest(
            artifact["assignments"]):
        raise ValueError("assignment_digest mismatch — the bound "
                         "assignment sidecar was altered")
    cfg = artifact.get("config")
    if cfg is None:
        raise ValueError("cannot freeze: the producer config payload "
                         "is missing, so config_digest cannot be "
                         "recomputed — a direct freeze call cannot "
                         "bypass the producer audit")
    if artifact.get("config_digest") != _digest(cfg):
        raise ValueError("config_digest mismatch — the bound "
                         "configuration was altered")
    vals = artifact.get("input_values")
    if vals is None:
        raise ValueError("cannot freeze: input_values missing, so "
                         "input_bytes_digest cannot be recomputed")
    try:
        arr = np.ascontiguousarray(_decode_input_values(vals),
                                   dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"input_values cannot be decoded to the "
                         f"raw feature byte domain: {exc}") from None
    schema = artifact.get("input_schema") or {}
    if schema.get("shape") and \
            list(arr.shape) != list(schema["shape"]):
        raise ValueError("input_values shape is inconsistent with "
                         "the bound input_schema")
    if artifact.get("input_bytes_digest") != _sha_bytes(
            arr.tobytes()):
        raise ValueError("input_bytes_digest mismatch — the bound "
                         "raw feature bytes were altered")
    pre = artifact.get("preprocessing")
    if not isinstance(pre, dict) or not pre.get("row_keys_digest"):
        raise ValueError("cannot freeze: preprocessing binding "
                         "missing — the train surface is "
                         "unrecoverable")
    if artifact.get("preprocessing_digest") != _digest(pre):
        raise ValueError("preprocessing_digest mismatch — the bound "
                         "preprocessing surface was altered")
    recomputed = _digest(
        {k: v for k, v in artifact.items()
         if k != "regime_artifact_digest"})
    if artifact.get("regime_artifact_digest") != recomputed:
        raise ValueError("regime_artifact_digest mismatch — the "
                         "artifact payload was mutated after "
                         "production")
    # --- C03: unit→basin map binding ------------------------------------
    ubm = artifact.get("unit_basin_map")
    if not isinstance(ubm, (list, tuple)) or not ubm:
        raise ValueError("cannot freeze: unit_basin_map missing or "
                         "empty — the producer did not bind the "
                         "unit→basin partition")
    _ubm_groups = {g for _, g in ubm}
    _declared_groups = set(artifact.get("fit_groups") or []) | \
        set(artifact.get("heldout_groups_declared") or [])
    if not _ubm_groups <= _declared_groups:
        raise ValueError(
            f"unit_basin_map references groups not declared in "
            f"fit_groups/heldout_groups_declared: "
            f"{sorted(_ubm_groups - _declared_groups)}")
    _ubm_units = [u for u, _ in ubm]
    if len(_ubm_units) != len(set(_ubm_units)):
        raise ValueError("unit_basin_map has duplicate unit ids — "
                         "the partition is not well-defined")
    # --- C15: run manifest validation -----------------------------------
    rm = artifact.get("run_manifest")
    if not isinstance(rm, dict):
        raise ValueError("cannot freeze: run_manifest missing — the "
                         "typed run provenance record is required")
    # R9-P04: exact typed deserialization — a forged record_type tag
    # or an extra/missing field rejects before any binding check.
    try:
        _rm_rec = deserialize_record(rm)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"cannot freeze: run_manifest is not a "
                         f"well-formed typed record: {exc}") from None
    if type(_rm_rec) is not RunManifestV0:
        raise ValueError("cannot freeze: run_manifest record_type "
                         "must name RunManifestV0 exactly")
    _rm_problems = _rm_rec.problems()
    if _rm_problems:
        raise ValueError(f"cannot freeze: run_manifest invalid: "
                         f"{_rm_problems}")
    # Cross-field binding: the manifest must name THIS artifact's
    # input bytes, assignment output, environment, and declared seed.
    if artifact.get("input_bytes_digest") not in \
            tuple(_rm_rec.input_digests):
        raise ValueError("cannot freeze: run_manifest.input_digests "
                         "does not bind the artifact's "
                         "input_bytes_digest")
    if artifact.get("assignment_digest") not in \
            tuple(_rm_rec.output_digests):
        raise ValueError("cannot freeze: run_manifest.output_digests "
                         "does not bind the artifact's "
                         "assignment_digest")
    if _rm_rec.environment_digest != \
            artifact.get("environment_digest"):
        raise ValueError("cannot freeze: run_manifest."
                         "environment_digest does not match the "
                         "artifact's environment_digest")
    _decl_seeds = artifact.get("seeds_declared") or ()
    if _rm_rec.seed != (_decl_seeds[0] if _decl_seeds else None):
        raise ValueError("cannot freeze: run_manifest.seed does not "
                         "match seeds_declared[0]")
    if artifact.get("run_manifest_digest") != _digest(rm):
        raise ValueError("run_manifest_digest mismatch — the typed "
                         "run manifest was altered after production")
    # --- C04: semantic config revalidation -----------------------------
    cfg_sem = _validate_config_semantics(artifact.get("config") or {})
    if cfg_sem:
        raise ValueError(f"cannot freeze: config semantics invalid "
                         f"after digest recomputation: {cfg_sem}")
    # --- gate/status consistency --------------------------------------
    gates = (artifact.get("stability") or {}).get("required_gates")
    if not isinstance(gates, dict) or not gates:
        raise ValueError("stability.required_gates missing or empty "
                         "— the producer audit cannot run")
    if set(gates) != REQUIRED_REGIME_GATE_NAMES:
        raise ValueError(
            "stability.required_gates must carry exactly the "
            "declared gate universe — missing gates "
            f"{sorted(REQUIRED_REGIME_GATE_NAMES - set(gates))}, "
            f"extra gates {sorted(set(gates) - REQUIRED_REGIME_GATE_NAMES)}")
    # PROV-01: gate values must be real booleans — a truthy non-bool
    # (e.g. the string "PASS") can never certify a terminal status.
    if any(not isinstance(v, bool) for v in gates.values()):
        raise ValueError("stability.required_gates values must be "
                         "booleans — a truthy non-bool gate value "
                         "cannot certify a terminal status")
    if status == "DESCRIPTIVE_REGIME_ONLY" and \
            not all(bool(v) for v in gates.values()):
        raise ValueError("DESCRIPTIVE_REGIME_ONLY requires every "
                         "required_gates value True")
    if status == "CANDIDATE_ONLY" and \
            not any(not bool(v) for v in gates.values()):
        raise ValueError("CANDIDATE_ONLY requires at least one "
                         "recorded failure in required_gates — a "
                         "candidate claim without a recorded failure "
                         "reason is fabricated")
    if status == "UNSUPERVISED_STRUCTURE_NOT_STABLE" and \
            not any(not bool(v) for v in gates.values()):
        raise ValueError("UNSUPERVISED_STRUCTURE_NOT_STABLE without "
                         "any recorded required_gates failure is "
                         "inconsistent")
    # FCST-01: mode ↔ data_class ↔ forecast-field consistency is
    # bound state — a frozen artifact can never claim a mode its
    # evidence fields do not support.
    mode = artifact.get("mode")
    fc_vd = artifact.get("forecast_vintage_digests") or ()
    fc_fs = artifact.get("forecast_feature_set") or ()
    if mode == RegimeMode.FORECAST_REGIME.value:
        if artifact.get("data_class") != \
                ForecastDataClass.ARCHIVED_OPERATIONAL.value:
            raise ValueError(
                "FORECAST_REGIME requires data_class "
                "ARCHIVED_OPERATIONAL — issue-time archive vintages "
                "are the only admissible forecast evidence")
        if not fc_vd or not fc_fs:
            raise ValueError("FORECAST_REGIME requires bound "
                             "forecast_vintage_digests and "
                             "forecast_feature_set")
    elif mode == RegimeMode.RETROSPECTIVE_REGIME.value:
        if artifact.get("data_class") not in \
                RETROSPECTIVE_REGIME_DATA_CLASSES:
            raise ValueError(
                "RETROSPECTIVE_REGIME requires a declared "
                f"retrospective data_class in "
                f"{sorted(RETROSPECTIVE_REGIME_DATA_CLASSES)}")
        if fc_vd or fc_fs:
            raise ValueError("RETROSPECTIVE_REGIME must not carry "
                             "forecast_vintage_digests or "
                             "forecast_feature_set")
        # SEISMIC-01: the seismic waveform class additionally
        # requires a non-fixture, byte-bound source manifest and a
        # hard non-associable flag — a freeze can never certify a
        # seismic artifact for association.
        if artifact.get("data_class") == \
                SEISMIC_WAVEFORM_RETROSPECTIVE_DATA_CLASS:
            if artifact.get("associable") is not False:
                raise ValueError(
                    "a SEISMIC_WAVEFORM_RETROSPECTIVE artifact is "
                    "non-associable — the flag is not free")
            _fx, _fxp = fixture_flag(artifact.get("source_manifest"))
            if _fxp:
                raise ValueError(
                    f"source_manifest fixture marker invalid: "
                    f"{_fxp}")
            if _fx:
                raise ValueError(
                    "SEISMIC_WAVEFORM_RETROSPECTIVE requires a "
                    "non-fixture, byte-bound source_manifest")
    else:
        raise ValueError(f"mode {mode!r} is not a declared "
                         "RegimeMode value")
    # Deep copy (I-06): a shallow dict() leaves nested payloads shared —
    # mutating the source artifact's nested lists/dicts would silently
    # change the "frozen" surface.  Digest verification above makes any
    # post-freeze mutation detectable.
    frozen = copy.deepcopy(artifact)
    frozen["frozen"] = True
    frozen["freeze_digest"] = _digest(
        {k: v for k, v in artifact.items() if k != "freeze_digest"})
    return frozen
