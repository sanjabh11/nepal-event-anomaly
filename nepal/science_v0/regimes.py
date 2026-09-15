"""Multi-region retrospective regime runner (REGIME_PROTOCOL_V0.md).

Descriptive-only machinery: GMM over reanalysis-class feature matrices
across >=3 geographic groups, with K=1 null mandatory, >=3 seeds,
train-only preprocessing, stability axes, and two null families.

Terminal statuses are limited to the protocol vocabulary:
    DESCRIPTIVE_REGIME_ONLY / UNSUPERVISED_STRUCTURE_NOT_STABLE /
    CANDIDATE_ONLY / RUN_ERROR

This module never reads event labels, never emits forecast or
precursor language, and never tunes on locked test basins.
"""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.mixture import GaussianMixture
from sklearn.preprocessing import StandardScaler
from sklearn.impute import SimpleImputer

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


def _sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _digest(obj) -> str:
    return _sha_bytes(json.dumps(obj, sort_keys=True,
                               default=str).encode())


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
    """Adjusted Rand Index (no sklearn.metrics dependency quirk)."""
    from sklearn.metrics import adjusted_rand_score
    return float(adjusted_rand_score(a, b))


def _js_divergence(p: np.ndarray, q: np.ndarray) -> float:
    p = np.asarray(p, float); q = np.asarray(q, float)
    p = p / p.sum(); q = q / q.sum()
    m = 0.5 * (p + q)
    def kl(a, b):
        mask = a > 0
        return float(np.sum(a[mask] * np.log2(a[mask] / b[mask])))
    return 0.5 * kl(p, m) + 0.5 * kl(q, m)


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
                        season_col: str, seed: int) -> np.ndarray:
    """Synthetic samples from same seasonal marginals: resample each
    feature independently within season strata."""
    rng = np.random.default_rng(seed)
    out = np.empty((len(df), len(feature_cols)))
    seasons = df[season_col].to_numpy()
    for si, s in enumerate(pd.unique(seasons)):
        idx = np.where(seasons == s)[0]
        for j, c in enumerate(feature_cols):
            pool = df[c].to_numpy()[idx]
            pool = pool[np.isfinite(pool)]
            out[idx, j] = rng.choice(pool, size=len(idx)) \
                if len(pool) else np.nan
    return out


# -------------------------------------------------------------- runner

_ISO_DATE_RE = None  # compiled lazily


def _iso_date_ok(s: str) -> bool:
    import re
    global _ISO_DATE_RE
    if _ISO_DATE_RE is None:
        _ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    return bool(_ISO_DATE_RE.match(str(s)))


@dataclass(frozen=True)
class RegimeRunConfig:
    seeds: tuple = (42, 7, 2024)
    k_candidates: tuple = K_CANDIDATES
    n_bootstrap: int = MIN_BOOTSTRAP
    season_col: str = "season"
    group_col: str = "basin_group"
    era_col: str | None = "era"
    unit_col: str = "unit_id"
    date_col: str = "date"
    label_blinding: bool = True
    fitted_on: str = "TRAIN_ONLY"
    # Explicit holdout membership: the declared train groups the fit
    # may see and the declared held-out groups it may never fit on.
    # Both must be non-empty and disjoint; the mask must honour them.
    train_groups: tuple = ()
    heldout_groups: tuple = ()


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
    missing = [c for c in feature_cols if c not in df.columns]
    if missing:
        return {"status": "RUN_ERROR",
                "reason": f"missing feature columns: {missing}"}
    if not config.label_blinding or config.fitted_on != "TRAIN_ONLY":
        return {"status": "RUN_ERROR",
                "reason": "label_blinding and TRAIN_ONLY are mandatory"}
    if len(set(config.seeds)) < MIN_SEEDS:
        return {"status": "RUN_ERROR",
                "reason": f"need >= {MIN_SEEDS} distinct seeds"}
    if any(isinstance(sd, bool) or not isinstance(sd, int)
           for sd in config.seeds):
        return {"status": "RUN_ERROR",
                "reason": "seeds must be ints — a non-int seed is "
                          "silently disenfranchised from the modal-K "
                          "vote after int() coercion in the fit"}
    # Holdout binding (I-07): the mask is meaningless unless it is
    # tied to declared, disjoint train/held-out group membership.
    train_groups = set(config.train_groups)
    heldout_groups = set(config.heldout_groups)
    if not train_groups or not heldout_groups:
        return {"status": "RUN_ERROR",
                "reason": "train_groups and heldout_groups must both "
                          "be non-empty — an undeclared holdout cannot "
                          "produce a terminal regime status"}
    if train_groups & heldout_groups:
        return {"status": "RUN_ERROR",
                "reason": f"train/heldout groups overlap: "
                          f"{sorted(train_groups & heldout_groups)}"}
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
    if not held_mask_groups <= heldout_groups:
        return {"status": "RUN_ERROR",
                "reason": f"held-out rows contain undeclared groups: "
                          f"{sorted(held_mask_groups - heldout_groups)}"}
    train_mask = mask
    if config.group_col not in df.columns or             config.season_col not in df.columns:
        return {"status": "RUN_ERROR",
                "reason": "group/season columns missing from the "
                          "frame"}
    groups = df[config.group_col].unique()
    # the multi-region gate applies to the FIT side: held-out groups
    # must not inflate the geographic diversity of the model fit
    if len(mask_groups) < MIN_GEO_GROUPS:
        return {"status": "RUN_ERROR",
                "reason": f"need >= {MIN_GEO_GROUPS} geographic groups "
                          f"in the fit mask; got {len(mask_groups)} — "
                          "a single-cell or single-basin fit may not "
                          "emit a terminal status"}

    # Row identity is explicit, never derived from the frame index:
    # unit_id + canonical ISO date columns must exist, and each
    # (unit_id, date) pair must be unique.
    for col in (config.unit_col, config.date_col):
        if col not in df.columns:
            return {"status": "RUN_ERROR",
                    "reason": f"required identity column {col!r} "
                              "missing — identity is never inferred"}
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

    # per-column missingness report (pre-filter)
    missingness = {c: float(df[c].isna().mean()) for c in feature_cols}

    prep = TrainOnlyPreprocessor()
    train_df = df.loc[train_mask, feature_cols]
    if len(train_df) < 50:
        return {"status": "RUN_ERROR",
                "reason": "insufficient train rows (<50)"}
    prep.fit(train_df)
    X_all = prep.transform(df[feature_cols])
    X_train = X_all[np.asarray(train_mask)]

    # --- K sweep across seeds; only converged fits eligible ----------
    fits = []
    for seed in config.seeds:
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

    # per-seed best K by BIC
    per_seed_best = {}
    for seed in config.seeds:
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

    # --- null families -------------------------------------------------
    X_shuf = shuffled_null(X_train, seed=int(config.seeds[0]))
    shuf_fit = _fit_gmm(X_shuf, modal_k, int(config.seeds[0]))
    if shuf_fit["converged"]:
        shuf_occ = np.bincount(shuf_fit["model"].predict(X_shuf),
                               minlength=modal_k) / len(X_shuf)
        js_shuf = _js_divergence(occupancy, shuf_occ)
    else:
        js_shuf = 0.0
    # Regime structure must diverge from the shuffled-null occupancy;
    # a collapsed/uniform null fit yields js near the degenerate floor.
    exceeds_shuffled = shuf_fit["converged"] and js_shuf > 0.02

    X_seas = season_matched_null(df.loc[train_mask], feature_cols,
                                 config.season_col,
                                 seed=int(config.seeds[0]))
    seas_ok = np.isfinite(X_seas).all()
    js_seas = None
    if seas_ok:
        seas_fit = _fit_gmm(X_seas, modal_k, int(config.seeds[0]))
        if seas_fit["converged"]:
            seas_occ = np.bincount(
                seas_fit["model"].predict(X_seas),
                minlength=modal_k) / len(X_seas)
            js_seas = _js_divergence(occupancy, seas_occ)

    # --- stability axes --------------------------------------------------
    stability = {
        "seed_ari_min": min(seed_ari) if seed_ari else None,
        "seed_ari_max": max(seed_ari) if seed_ari else None,
        "modal_k_frequency": k_freq,
        "k_instability": k_freq < 1.0,
        "n_bootstrap": config.n_bootstrap,
    }

    # leave-one-region-out refit (basin/geographic axis)
    loro = {}
    stable_loro = True
    for g in [str(g) for g in groups]:
        m = np.asarray(train_mask) & \
            (df[config.group_col].astype(str) != g)
        sub = df.loc[m, feature_cols]
        if len(sub) < 50:
            loro[g] = "skipped_insufficient"
            continue
        p2 = TrainOnlyPreprocessor().fit(sub)
        X2 = p2.transform(sub)
        f2 = _fit_gmm(X2, modal_k, int(config.seeds[0]))
        if not f2["converged"]:
            loro[g] = "nonconverged"
            stable_loro = False
            continue
        l2 = f2["model"].predict(X2)
        # compare structure: occupancy JSD vs full fit
        occ2 = np.bincount(l2, minlength=modal_k) / len(l2)
        loro[g] = _js_divergence(occupancy, occ2)
    stability["leave_one_region_out"] = loro

    # drift: era-pair diagnostics (means per era)
    drift = {}
    if config.era_col and config.era_col in df.columns:
        eras = pd.unique(df[config.era_col])
        for i in range(len(eras)):
            for j in range(i + 1, len(eras)):
                a = df.loc[train_mask &
                           (df[config.era_col] == eras[i]),
                           feature_cols].mean()
                b = df.loc[train_mask &
                           (df[config.era_col] == eras[j]),
                           feature_cols].mean()
                drift[f"{eras[i]}|{eras[j]}"] = float(
                    np.abs(a - b).max())
    stability["drift_max_abs_mean_shift"] = drift

    # --- terminal status -------------------------------------------------
    loros_numeric = [v for v in loro.values()
                     if isinstance(v, float)]
    loro_ok = all(v < 0.35 for v in loros_numeric) if \
        loros_numeric else False
    stable = (k_freq == 1.0 and
              (min(seed_ari) if seed_ari else 0.0) > 0.6 and
              loro_ok and exceeds_shuffled)
    if stable:
        status = "DESCRIPTIVE_REGIME_ONLY"
    elif k_freq < 1.0 or (seed_ari and min(seed_ari) <= 0.6) \
            or not loro_ok:
        status = "UNSUPERVISED_STRUCTURE_NOT_STABLE"
    else:
        status = "CANDIDATE_ONLY"

    artifact = {
        "mode": "RETROSPECTIVE_REGIME",
        "data_class": "REANALYSIS",
        "fitted_on": "TRAIN_ONLY",
        "label_blinding": True,
        # provenance binding (I-05): feature order, frame digest,
        # configuration, fit/held-out membership, and the exact mask
        # are all bound into the artifact before freezing.
        "feature_cols": list(feature_cols),
        "feature_matrix_digest": _digest(
            df[feature_cols].round(6).to_numpy().tolist()),
        "config_digest": _digest(dataclasses.asdict(config)),
        "fit_groups": sorted(mask_groups),
        "heldout_groups_declared": sorted(heldout_groups),
        "n_train_rows": int(mask.sum()),
        "n_rows": int(len(df)),
        "train_mask_digest": _digest(mask.tolist()),
        "k": modal_k,
        "seeds": sorted(set(int(s) for s in config.seeds)),
        "per_seed_best_k": {str(s): int(k)
                            for s, k in per_seed_best.items()},
        "modal_k_frequency": k_freq,
        "occupancy": occupancy.tolist(),
        "assignments": assignments,
        "assignment_digest": assignment_digest,
        "mean_max_posterior": float(post.mean()),
        "ambiguous_fraction": float((post < 0.7).mean()),
        "missingness": missingness,
        "stability": stability,
        "nulls": {"shuffled_js": js_shuf,
                  "season_matched_js": js_seas},
        "preprocessing_digest": prep.digest(),
        "k_selection_digest": _digest(
            [{kk: f[kk] for kk in ("k", "seed", "bic", "converged")}
             for f in fits]),
        "stability_report_digest": _digest(stability),
        "null_model_digest": _digest({"k1_bic": [f["bic"]
                                                for f in null_fits]}),
        "status": status,
        "disclaimer": "descriptive regime structure only; not an "
                      "event precursor, association, or skill claim",
    }
    artifact["regime_artifact_digest"] = _digest(
        {k: v for k, v in artifact.items()
         if k != "regime_artifact_digest"})
    return artifact


def freeze_regime_artifact(artifact: dict) -> dict:
    """Freeze assignments BEFORE any label association. The frozen
    dict is immutable input for downstream phases — labels are opened
    only after this call returns."""
    if artifact.get("status") == "RUN_ERROR":
        raise ValueError("cannot freeze a RUN_ERROR artifact")
    if not artifact.get("assignments"):
        raise ValueError("cannot freeze an artifact without the "
                         "assignment sidecar")
    # Deep copy (I-06): a shallow dict() leaves nested payloads shared —
    # mutating the source artifact's nested lists/dicts would silently
    # change the "frozen" surface.  Digest verification at the adapter
    # boundary additionally makes any post-freeze mutation detectable.
    frozen = copy.deepcopy(artifact)
    frozen["frozen"] = True
    frozen["freeze_digest"] = _digest(
        {k: v for k, v in artifact.items() if k != "freeze_digest"})
    return frozen
