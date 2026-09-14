"""Phase 4: GMM descriptive overlay + Jensen-Shannon distance.

Fits GMM K=1..5 (BIC selection) on 2001-2025 JJA daily vector.
Assigns Aug 2026 days to clusters. Uses Jensen-Shannon distance
for cluster occupancy comparison.

GMM is DESCRIPTIVE ONLY — it describes weather regimes, NOT avalanche
precursors. K=1 (null benchmark) is included per Astra's recommendation.

Usage:
    source .venv/bin/activate
    python nepal/gmm_descriptive.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from sklearn.mixture import GaussianMixture
from scipy.spatial.distance import jensenshannon

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from feature_contract import (
    EVENT, PRE_EVENT_WINDOW, EVENT_DATE, JJA_2026,
    GMM_K_RANGE, GMM_COVARIANCE, JJA_MONTHS,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = REPO_ROOT / "data"
# data/ is a frozen contract surface — this module never reads
# features from it and never writes under it.  All I/O is under one
# shared run root:
#   <run_root>/features/features_nepal_jja_2001_2026.csv   (extractor)
#   <run_root>/features/feature_units.json                 (extractor)
#   <run_root>/features/run_metadata.json                  (extractor)
#   <run_root>/gmm/{gmm_results.json,bundle.json,plots/}   (this file)
DEFAULT_RUN_ROOT = REPO_ROOT / "research_runs" / "gmm_confirmation"
RUN_DIR = DEFAULT_RUN_ROOT / "gmm"
GMM_PLOT_DIR = RUN_DIR / "plots"
RESULTS_FILE = RUN_DIR / "gmm_results.json"
BUNDLE_FILE = RUN_DIR / "bundle.json"

FEATURE_FILENAME = "features_nepal_jja_2001_2026.csv"
UNITS_FILENAME = "feature_units.json"
RUN_METADATA_FILENAME = "run_metadata.json"

# P5-02/P5-11 — frozen contract surfaces that must never receive run
# outputs.  A run root inside any of these is rejected.
FROZEN_SURFACES = (
    DATA_DIR,
    REPO_ROOT / "pinned",
    REPO_ROOT / "nepal" / "framework_v1",
    REPO_ROOT / "preregistration.md",
)


def _assert_safe_root(path: Path) -> Path:
    """P5-11 — reject any path inside a frozen contract surface.

    Applied to the run root in main() and to every run_dir handed to
    write_bundle()/run_gmm_descriptive() so no call path can write
    outputs under data/, pinned/, nepal/framework_v1/, or
    preregistration.md."""
    resolved = Path(path).expanduser().resolve()
    for frozen in FROZEN_SURFACES:
        f = frozen.resolve()
        if resolved == f or f in resolved.parents:
            raise ValueError(
                f"run path {resolved} is inside frozen surface {f}; "
                "refusing to use it.")
    return resolved


def _resolve_run_root(run_root: str | None) -> Path:
    """P5-02 — resolve --run-root (repo-root-relative when not
    absolute) and enforce the frozen-surface guard."""
    root = Path(run_root).expanduser() if run_root else DEFAULT_RUN_ROOT
    if not root.is_absolute():
        root = REPO_ROOT / root
    return _assert_safe_root(root)

# Declared independent seeds (CFM-06) — three fits per K; K selection
# is reported as a frequency, not a single lucky fit.
GMM_SEEDS = (42, 7, 2024)
# Posterior membership below this is reported as ambiguous (CFM-09).
AMBIGUITY_THRESHOLD = 0.7
# Bootstrap parameters for JS-distance uncertainty (CFM-08).
JS_BOOTSTRAP_BLOCKS = 200
JS_BLOCK_DAYS = 7

ELEVATION_DISCLAIMER = (
    f"ERA5-Land model elevation: {EVENT['model_elevation_m']} m. "
    f"Source slope: {EVENT['source_elevation_m']} m. "
    f"Delta: {EVENT['elevation_gap_m']} m."
)

# GAP FIX: Previously only 4 features. Now uses ALL available daily features
# from the Phase 2 output (up to 10-D + thermal indices).
# Pre-registered 10 distinct quantities + thermal indices for regime context.
GMM_FEATURES = [
    "t2m_daily", "d2m_daily", "tp_daily", "sf_daily", "sd_daily",
    "wind_speed_daily", "wind_dir_sin", "wind_dir_cos", "rh_daily",
    "pdd_7day", "pdd_daily", "freezing_height_m",
]


def fit_gmm_range(data: np.ndarray, k_range: tuple = GMM_K_RANGE,
                  covariance: str = GMM_COVARIANCE,
                  seeds: tuple = GMM_SEEDS) -> dict:
    """Fit GMM for each K in k_range under every declared seed.

    CFM-06: K is selected by BIC *per seed*; the modal selection is
    reported with its frequency — a single-seed best-K is not a
    stability statement.
    """
    results = {
        "seeds": list(seeds),
        "bic_scores": {},          # seed -> {k: bic}
        "aic_scores": {},
        "converged": {},           # seed -> {k: bool}
        "models": {},              # seed -> {k: model}
        "best_k_per_seed": {},
        "modal_k": None,
        "modal_k_frequency": None,
        "k_instability": None,
        "best_model": None,
        "best_seed": None,
    }

    for seed in seeds:
        results["bic_scores"][seed] = {}
        results["aic_scores"][seed] = {}
        results["converged"][seed] = {}
        results["models"][seed] = {}
        for k in k_range:
            gmm = GaussianMixture(
                n_components=k, covariance_type=covariance,
                random_state=seed,
                max_iter=200, n_init=(1 if k == 1 else 3),
            )
            try:
                gmm.fit(data)
                bic = gmm.bic(data)
                aic = gmm.aic(data)
                results["bic_scores"][seed][k] = round(float(bic), 2)
                results["aic_scores"][seed][k] = round(float(aic), 2)
                results["converged"][seed][k] = bool(gmm.converged_)
                results["models"][seed][k] = gmm
                print(f"  seed={seed} K={k}: BIC={bic:.2f} "
                      f"AIC={aic:.2f} converged={gmm.converged_}")
            except Exception as e:
                print(f"  seed={seed} K={k}: FAILED ({e})")
                results["bic_scores"][seed][k] = None
                results["aic_scores"][seed][k] = None
                results["converged"][seed][k] = False
        # CFM-04 (audit): only converged fits are eligible for BIC
        # selection — a non-converged model must not win best_k.
        valid = {k: v for k, v in results["bic_scores"][seed].items()
                 if v is not None
                 and results["converged"][seed].get(k, False)}
        if valid:
            results["best_k_per_seed"][seed] = min(
                valid, key=valid.get)
        # A seed with no converged fit contributes no best_k.

    if results["best_k_per_seed"]:
        from collections import Counter
        counts = Counter(results["best_k_per_seed"].values())
        modal_k, freq = counts.most_common(1)[0]
        modal_freq = freq / len(results["best_k_per_seed"])
        results["modal_k"] = modal_k
        results["modal_k_frequency"] = modal_freq
        # CFM-07 — modal-K tie policy: frequency < 1.0 means the
        # seeds disagree; record the instability explicitly.
        if modal_freq < 1.0:
            results["k_instability"] = {
                "modal_k": modal_k,
                "frequency": modal_freq,
                "best_k_per_seed": dict(results["best_k_per_seed"]),
            }
        else:
            results["k_instability"] = None
        # Representative model: the seed whose BIC-selected K equals
        # the modal K, with the lowest BIC.  best_k_per_seed only
        # contains converged fits, so the representative model is
        # guaranteed converged.
        candidates = [(results["bic_scores"][s][modal_k], s)
                      for s, k in results["best_k_per_seed"].items()
                      if k == modal_k]
        best_seed = min(candidates)[1]
        results["best_seed"] = best_seed
        results["best_model"] = \
            results["models"][best_seed][modal_k]
        print(f"\n  Modal K: {modal_k} "
              f"(frequency {results['modal_k_frequency']:.2f} across "
              f"{len(seeds)} seeds; representative seed {best_seed})")

    return results


def compute_cluster_occupancy(model: GaussianMixture, data: np.ndarray) -> np.ndarray:
    """Compute cluster occupancy (fraction of days in each cluster)."""
    labels = model.predict(data)
    n_clusters = model.n_components
    occupancy = np.zeros(n_clusters)
    for i in range(n_clusters):
        occupancy[i] = np.mean(labels == i)
    return occupancy


def compute_js_distance(p: np.ndarray, q: np.ndarray) -> float:
    """Compute Jensen-Shannon distance between two distributions.

    JS distance is bounded [0, 1] and avoids the infinite-KL problem
    when distributions have zero-support regions.
    """
    # Normalize to probability distributions
    p = p / np.sum(p) if np.sum(p) > 0 else p
    q = q / np.sum(q) if np.sum(q) > 0 else q

    # scipy.spatial.distance.jensenshannon returns the JS distance (sqrt of JS divergence)
    return float(jensenshannon(p, q))


def js_uncertainty(baseline: np.ndarray, target: np.ndarray,
                   model: GaussianMixture,
                   n_blocks: int = JS_BOOTSTRAP_BLOCKS,
                   block_days: int = JS_BLOCK_DAYS,
                   rng_seed: int = 42) -> dict:
    """CFM-08 — block-bootstrap CI + within-baseline null for the
    occupancy JS distance.

    Bootstrap: resample baseline day-blocks, recompute occupancy,
    compare to the fixed target occupancy.  Null: split baseline into
    two random halves per replicate and take the max JS — the scale of
    disagreement expected between two in-distribution samples.
    """
    rng = np.random.default_rng(rng_seed)
    base_occ = compute_cluster_occupancy(model, baseline)
    tgt_occ = compute_cluster_occupancy(model, target)
    observed = compute_js_distance(base_occ, tgt_occ)
    n = len(baseline)
    # CFM-06 (audit): blocks_per_rep is the number of day-blocks drawn
    # within each replicate; n_blocks stays the replicate count.
    blocks_per_rep = max(1, n // block_days)
    boot = []
    for _ in range(n_blocks):
        starts = rng.integers(0, n - block_days + 1,
                              size=blocks_per_rep)
        sample = np.concatenate(
            [baseline[s:s + block_days] for s in starts])
        occ = compute_cluster_occupancy(model, sample)
        boot.append(compute_js_distance(occ, tgt_occ))
    null = []
    for _ in range(n_blocks):
        idx = rng.permutation(n)
        half = n // 2
        occ_a = compute_cluster_occupancy(model, baseline[idx[:half]])
        occ_b = compute_cluster_occupancy(model, baseline[idx[half:]])
        null.append(compute_js_distance(occ_a, occ_b))
    boot = np.asarray(boot)
    null = np.asarray(null)
    return {
        "observed": round(float(observed), 4),
        "ci95": [round(float(np.quantile(boot, 0.025)), 4),
                 round(float(np.quantile(boot, 0.975)), 4)],
        "null_p95": round(float(np.quantile(null, 0.95)), 4),
        "exceeds_null": bool(observed > np.quantile(null, 0.95)),
        "n_replicates": int(n_blocks),
        "blocks_per_replicate": int(blocks_per_rep),
    }


# P5-06 — embedded default units, used only when the extractor's
# feature_units.json sidecar is absent (units_source records which).
EMBEDDED_FEATURE_UNITS = {
    "t2m_daily": "K", "d2m_daily": "K", "tp_daily": "m",
    "sf_daily": "m SWE", "sd_daily": "m SWE",
    "wind_speed_daily": "m/s", "wind_dir_sin": "unitless",
    "wind_dir_cos": "unitless", "rh_daily": "fraction",
    "pdd_7day": "degC·d", "pdd_daily": "degC·d",
    "freezing_height_m": "m",
}


def _cell_pair(cell) -> list[float] | None:
    """Normalize a cell spec — [lat, lon] sequence or
    {latitude, longitude} dict — to a [lat, lon] list; None if the
    value is absent or unparseable."""
    if cell is None:
        return None
    if isinstance(cell, dict):
        lat = cell.get("latitude", cell.get("lat"))
        lon = cell.get("longitude", cell.get("lon"))
        if lat is None or lon is None:
            return None
        return [float(lat), float(lon)]
    try:
        seq = list(cell)
    except TypeError:
        return None
    if len(seq) != 2:
        return None
    try:
        return [float(seq[0]), float(seq[1])]
    except (TypeError, ValueError):
        return None


def load_run_provenance(features_dir: Path) -> dict:
    """P5-05 — load the extractor's run_metadata.json for provenance.

    Returns selected_cell (the actual extraction cell), requested_cell,
    and model_elevation_m.  The frozen contract values are the fallback
    when the sidecar is absent so the run stays reproducible; the
    caller records both and flags any mismatch."""
    meta_file = features_dir / RUN_METADATA_FILENAME
    meta: dict = {}
    if meta_file.is_file():
        try:
            loaded = json.loads(meta_file.read_text())
            if isinstance(loaded, dict):
                meta = loaded
        except (json.JSONDecodeError, OSError) as e:
            print(f"WARNING: could not parse {meta_file}: {e}")
    contract_cell = [float(v) for v in EVENT["era5_cell"]]
    selected = _cell_pair(meta.get("selected_cell"))
    requested = _cell_pair(meta.get("requested_cell"))
    elev = meta.get("model_elevation_m")
    try:
        elev = float(elev) if elev is not None else None
    except (TypeError, ValueError):
        elev = None
    return {
        "selected_cell": selected if selected is not None
                         else contract_cell,
        "requested_cell": requested if requested is not None
                          else contract_cell,
        "model_elevation_m": (elev if elev is not None
                              else float(EVENT["model_elevation_m"])),
        "contract_cell": contract_cell,
        "source": ("run_metadata.json" if meta
                   else "contract_fallback"),
    }


def load_feature_units(features_dir: Path) -> dict | None:
    """P5-06 — load the feature_units.json sidecar if present; the
    recorded feature_units must come from it, not the embedded
    defaults, whenever it exists."""
    units_file = features_dir / UNITS_FILENAME
    if not units_file.is_file():
        return None
    try:
        units = json.loads(units_file.read_text())
    except (json.JSONDecodeError, OSError) as e:
        print(f"WARNING: could not parse {units_file}: {e}")
        return None
    return units if isinstance(units, dict) else None


def run_gmm_descriptive(daily_df: pd.DataFrame,
                        target_year: int = 2026,
                        provenance: dict | None = None,
                        feature_units: dict | None = None,
                        run_dir: Path | None = None) -> dict:
    """Run the bounded GMM descriptive confirmation.

    CFM-02: single-cell, single-period — inference is limited to the
    selected grid cell and period; labelled
    EXPLORATORY_DESCRIPTIVE_SINGLE_CELL.  CFM-03: all declared
    features are required — no silent column reduction.  CFM-04:
    scaling is fit on the baseline only.  Event dates never touch
    fitting or K selection (CFM-12).
    """
    # P5-11 — frozen-surface guard on this call path too.
    if run_dir is not None:
        _assert_safe_root(run_dir)

    status_meta = {
        "status": "EXPLORATORY_DESCRIPTIVE_SINGLE_CELL",
        "scope": ("single ERA5-Land grid cell, single pre-event "
                  "period — implementation confirmation only, not "
                  "validation or generalization"),
    }

    # P5-05 — extraction provenance (selected/requested cell, model
    # elevation).  Contract values are the fallback when the run-root
    # run_metadata.json sidecar is absent.
    prov = provenance or {}
    contract_cell = [float(v) for v in EVENT["era5_cell"]]
    actual_cell = prov.get("selected_cell") or contract_cell
    requested_cell = prov.get("requested_cell") or contract_cell
    model_elevation_m = prov.get("model_elevation_m",
                                 EVENT["model_elevation_m"])

    # CFM-03 — declared feature set is required.
    missing_cols = [c for c in GMM_FEATURES if c not in daily_df.columns]
    if missing_cols:
        return {**status_meta,
                "error": f"required features missing: {missing_cols}"}

    # P5-09 (defense in depth) — preflight hard-fails on anything
    # outside the exact JJA universe; here we still restrict to JJA
    # months before the baseline/target split so a direct call can
    # never leak non-JJA rows into the fit.
    n_input = len(daily_df)
    daily_df = daily_df[daily_df.index.month.isin(JJA_MONTHS)]
    non_jja_dropped = n_input - len(daily_df)

    feature_df = daily_df[GMM_FEATURES]
    n_raw = len(feature_df)
    # CFM-03 (audit): per-column NaN counts must be computed on the
    # PRE-filter frame — after dropna() they would always be zero.
    per_column_na = {c: int(feature_df[c].isna().sum())
                     for c in GMM_FEATURES}
    feature_df = feature_df.dropna()
    missingness = {
        "input_rows": int(n_raw),
        "non_jja_rows_dropped": int(non_jja_dropped),
        "rows_after_dropna": int(len(feature_df)),
        "rows_dropped": int(n_raw - len(feature_df)),
        "per_column_na": per_column_na,
    }

    baseline = feature_df[
        feature_df.index.year.isin(range(2001, target_year))]
    target = feature_df[feature_df.index.year == target_year]
    target_pre = target[target.index < pd.Timestamp(EVENT_DATE)]

    if len(baseline) < 100:
        return {**status_meta, "missingness": missingness,
                "error": f"Insufficient baseline: {len(baseline)} rows"}
    if len(target_pre) < 5:
        return {**status_meta, "missingness": missingness,
                "error": f"Insufficient pre-event target: "
                         f"{len(target_pre)} rows"}

    print(f"Baseline: {len(baseline)} rows, "
          f"{len(baseline.columns)} features")
    print(f"Target (pre-event): {len(target_pre)} rows")
    print(f"Features: {list(GMM_FEATURES)}")
    print(f"Missingness: {missingness['rows_dropped']} rows dropped")

    # CFM-04 — baseline-fitted scaling so mixed-unit features are
    # jointly comparable; the scaler never sees target rows.
    from sklearn.preprocessing import StandardScaler
    scaler = StandardScaler()
    baseline_scaled = scaler.fit_transform(baseline.values)
    target_scaled = scaler.transform(target_pre.values)
    # P5-06 — recorded feature units come from the extractor's
    # feature_units.json sidecar when it exists; the embedded defaults
    # are the fallback.  units_source says which was used.
    if feature_units:
        recorded_units = dict(feature_units)
        units_source = "sidecar"
        units_missing = [c for c in GMM_FEATURES
                         if c not in recorded_units]
    else:
        recorded_units = dict(EMBEDDED_FEATURE_UNITS)
        units_source = "embedded_defaults"
        units_missing = []
    scaling_record = {
        "policy": "baseline-fitted StandardScaler",
        "feature_units": recorded_units,
        "units_source": units_source,
        "units_missing_features": units_missing,
    }

    print(f"\nFitting GMM K={list(GMM_K_RANGE)} with "
          f"{GMM_COVARIANCE} covariance, seeds {list(GMM_SEEDS)}...")
    gmm_results = fit_gmm_range(baseline_scaled)

    if gmm_results["best_model"] is None:
        return {**status_meta, "missingness": missingness,
                "scaling": scaling_record,
                "error": "All GMM fits failed"}

    best_k = gmm_results["modal_k"]
    best_model = gmm_results["best_model"]

    # Retrospective overlay — event dates did NOT influence fitting
    # or K selection (CFM-12).
    print(f"\nRetrospective overlay: assigning Aug {target_year} days "
          f"to clusters (K={best_k}, seed {gmm_results['best_seed']})...")
    target_labels = best_model.predict(target_scaled)
    target_proba = best_model.predict_proba(target_scaled)
    max_proba = target_proba.max(axis=1)

    baseline_occupancy = compute_cluster_occupancy(
        best_model, baseline_scaled)
    target_occupancy = compute_cluster_occupancy(
        best_model, target_scaled)
    js = js_uncertainty(baseline_scaled, target_scaled, best_model)

    print(f"Baseline occupancy: {baseline_occupancy}")
    print(f"Target occupancy: {target_occupancy}")
    print(f"JS distance: {js['observed']:.4f} "
          f"(95% CI {js['ci95']}, null p95 {js['null_p95']}, "
          f"exceeds null: {js['exceeds_null']})")

    pre_event = target_pre[
        (target_pre.index >= PRE_EVENT_WINDOW[0]) &
        (target_pre.index <= PRE_EVENT_WINDOW[1])]
    if len(pre_event) > 0:
        pre_scaled = scaler.transform(pre_event.values)
        pre_event_labels = best_model.predict(pre_scaled)
        pre_event_occupancy = compute_cluster_occupancy(
            best_model, pre_scaled)
        pre_event_js = compute_js_distance(baseline_occupancy,
                                           pre_event_occupancy)
        print(f"\nPre-event window ({PRE_EVENT_WINDOW[0]} to "
              f"{PRE_EVENT_WINDOW[1]}):")
        print(f"  Occupancy: {pre_event_occupancy}")
        print(f"  JS distance: {pre_event_js:.4f}")
    else:
        pre_event_labels = None
        pre_event_occupancy = None
        pre_event_js = None

    results = {
        **status_meta,
        "best_k": best_k,
        "modal_k_frequency": gmm_results["modal_k_frequency"],
        # CFM-07 — k_instability is populated only when seeds
        # disagree (modal_k_frequency < 1.0); None when unanimous.
        "k_instability": (
            {**gmm_results["k_instability"],
             "best_k_per_seed": {
                 str(s): k for s, k in
                 gmm_results["k_instability"]
                 ["best_k_per_seed"].items()}}
            if gmm_results.get("k_instability") is not None
            else None),
        "k_selection_note": (
            "modal K by BIC across declared seeds; frequency < 1.0 "
            "means unstable K — treat as candidate only"),
        "best_k_per_seed": {str(s): k for s, k in
                            gmm_results["best_k_per_seed"].items()},
        "seeds": list(GMM_SEEDS),
        "bic_scores": {str(s): {str(k): v for k, v in d.items()}
                       for s, d in gmm_results["bic_scores"].items()},
        "aic_scores": {str(s): {str(k): v for k, v in d.items()}
                       for s, d in gmm_results["aic_scores"].items()},
        "converged": {str(s): {str(k): v for k, v in d.items()}
                      for s, d in gmm_results["converged"].items()},
        "covariance_type": GMM_COVARIANCE,
        "features_used": list(GMM_FEATURES),
        "missingness": missingness,
        "scaling": scaling_record,
        # CFM audit: reference only — the units dict lives in
        # scaling.feature_units; do not duplicate it here.
        "units": "see scaling.feature_units",
        # P5-05 — extraction provenance from the run-root
        # run_metadata.json when present; the frozen contract cell is
        # the fallback.  Both are recorded so a mismatch is visible
        # rather than silently assumed away.
        "selected_cell": actual_cell,
        "actual_cell": actual_cell,  # compat alias for bundle/audit
        "requested_cell": requested_cell,
        "contract_cell": contract_cell,
        "cell_mismatch": bool(list(actual_cell) != contract_cell),
        "model_elevation_m": model_elevation_m,
        "provenance_source": prov.get("source", "contract_fallback"),
        "baseline_occupancy": [round(float(o), 4)
                               for o in baseline_occupancy],
        "target_occupancy": [round(float(o), 4)
                             for o in target_occupancy],
        "js_distance": js,
        "posterior_confidence": {
            "mean_max_proba": round(float(max_proba.mean()), 4),
            "median_max_proba": round(float(np.median(max_proba)), 4),
            "ambiguous_fraction": round(
                float((max_proba < AMBIGUITY_THRESHOLD).mean()), 4),
            "ambiguity_threshold": AMBIGUITY_THRESHOLD,
        },
        "pre_event_occupancy": [round(float(o), 4)
                                for o in pre_event_occupancy]
        if pre_event_occupancy is not None else None,
        "js_distance_pre_event": round(pre_event_js, 4)
        if pre_event_js is not None else None,
        "pre_event_cluster_assignments": [
            {"date": str(d), "cluster": int(l)}
            for d, l in zip(pre_event.index, pre_event_labels)
        ] if pre_event_labels is not None else [],
        "event_overlay_note": (
            "retrospective overlay — event dates did not influence "
            "feature selection, scaling, fitting, or K selection"),
        "disclaimer": "GMM is DESCRIPTIVE ONLY. It describes weather "
                      "regimes, NOT avalanche precursors.",
        "k1_null_benchmark": best_k == 1,
    }

    return results


def write_bundle(results: dict, input_files: list[Path],
                 feature_file: Path, run_dir: Path) -> dict:
    """CFM-11 — research-only result bundle: every input and output
    digest recorded so another worker can replay the run."""
    import hashlib

    # P5-11 — a bundle must never be written under a frozen surface.
    run_dir = _assert_safe_root(run_dir)
    results_file = run_dir / "gmm_results.json"
    bundle_file = run_dir / "bundle.json"
    units_file = feature_file.parent / UNITS_FILENAME

    def _sha(p: Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()

    # CFM-08 — UTC timestamp (local-time run ids are not replayable).
    run_id = (f"gmm_confirmation_"
              f"{pd.Timestamp.now(tz='UTC'):%Y%m%dT%H%M%S}Z")
    # P5-05 — requested/selected cell come from the results (bound
    # from run_metadata.json when present); contract value is the
    # fallback.  Mismatches against either are flagged.
    contract_cell = [float(v) for v in EVENT["era5_cell"]]
    requested_cell = (results.get("requested_cell")
                      or contract_cell)
    actual_cell = (results.get("selected_cell")
                   or results.get("actual_cell"))
    if actual_cell is None:
        cell_note = (f"requested cell {requested_cell}; actual "
                     "extraction cell not recorded in results")
    elif list(actual_cell) == list(requested_cell):
        cell_note = ("requested cell matches actual extraction "
                     f"cell {list(actual_cell)}")
    else:
        cell_note = (f"MISMATCH: requested cell {requested_cell} "
                     f"but features were extracted at "
                     f"{list(actual_cell)}")

    bundle = {
        "run_id": run_id,
        "run_root": str(run_dir.parent.resolve()),
        "run_dir": str(run_dir.resolve()),
        "requested_cell": requested_cell,
        "selected_cell": actual_cell,
        "actual_cell": actual_cell,
        "contract_cell": contract_cell,
        "cell_mismatch_vs_requested": bool(
            actual_cell is not None
            and list(actual_cell) != list(requested_cell)),
        "cell_mismatch_vs_contract": bool(
            actual_cell is not None
            and list(actual_cell) != contract_cell),
        "model_elevation_m": results.get(
            "model_elevation_m", EVENT["model_elevation_m"]),
        "provenance_source": results.get("provenance_source",
                                         "contract_fallback"),
        "note": cell_note,
        "status": results.get("status", "EXPLORATORY_DESCRIPTIVE_"
                                       "SINGLE_CELL"),
        "input_digests": {p.name: _sha(p) for p in input_files
                          if p.is_file()},
        "feature_digest": _sha(feature_file)
        if feature_file.is_file() else None,
        # P5-05 — the units sidecar is hashed into the bundle so the
        # recorded feature_units are auditable against the file that
        # produced them.
        "feature_units_digest": _sha(units_file)
        if units_file.is_file() else None,
        "configuration": {
            "features": list(GMM_FEATURES), "k_range":
            list(GMM_K_RANGE), "covariance": GMM_COVARIANCE,
            "seeds": list(GMM_SEEDS),
            "ambiguity_threshold": AMBIGUITY_THRESHOLD,
            "js_bootstrap_blocks": JS_BOOTSTRAP_BLOCKS,
            "js_block_days": JS_BLOCK_DAYS,
        },
        "results_digest": _sha(results_file)
        if results_file.is_file() else None,
        "environment": {
            "python": sys.version.split()[0],
            "sklearn": __import__("sklearn").__version__,
            "numpy": np.__version__,
            "pandas": pd.__version__,
        },
        "error": results.get("error"),
        "descriptive_only": True,
        "no_event_labels_in_fit": True,
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    with open(bundle_file, "w") as f:
        json.dump(bundle, f, indent=2, default=str)
    return bundle


def generate_gmm_plots(daily_df: pd.DataFrame, gmm_results: dict, output_dir: Path):
    """Generate GMM descriptive plots."""
    output_dir.mkdir(parents=True, exist_ok=True)

    if "error" in gmm_results:
        return

    # Plot 1: BIC scores per seed (CFM-06 — multi-seed K selection)
    fig, ax = plt.subplots(figsize=(10, 6))
    for seed, scores in gmm_results["bic_scores"].items():
        ks = sorted([k for k, v in scores.items() if v is not None])
        ax.plot(ks, [scores[k] for k in ks], "o-",
                label=f"seed {seed}", alpha=0.8)
    ax.axvline(x=gmm_results["best_k"], color="r", linestyle="--",
               label=f"Modal K={gmm_results['best_k']}")
    ax.set_xlabel("Number of components (K)")
    ax.set_ylabel("BIC (lower is better)")
    ax.set_title("GMM BIC Scores — K=1..5 per seed "
                 "(K=1 is null benchmark)")
    ax.legend()
    fig.text(0.5, 0.01, ELEVATION_DISCLAIMER, ha="center", fontsize=8,
             style="italic")
    plt.tight_layout()
    fig.savefig(output_dir / "10_gmm_bic_scores.png", dpi=150)
    plt.close()

    # Plot 2: Cluster occupancy comparison
    fig, ax = plt.subplots(figsize=(10, 6))
    k = gmm_results["best_k"]
    x = range(k)
    width = 0.35
    baseline_occ = gmm_results["baseline_occupancy"]
    target_occ = gmm_results["target_occupancy"]
    ax.bar([i - width/2 for i in x], baseline_occ, width, label="2001-2025 baseline", alpha=0.7)
    ax.bar([i + width/2 for i in x], target_occ, width, label="2026 pre-event", alpha=0.7)
    ax.set_xlabel("Cluster")
    ax.set_ylabel("Occupancy fraction")
    ax.set_title(f"GMM Cluster Occupancy (K={k}) — DESCRIPTIVE ONLY")
    ax.set_xticks(x)
    ax.set_xticklabels([f"Cluster {i}" for i in x])
    ax.legend()
    js = gmm_results.get("js_distance", {})
    fig.text(0.5, 0.01, f"{ELEVATION_DISCLAIMER}\nJS distance: "
             f"{js.get('observed', float('nan')):.4f} "
             f"(95% CI {js.get('ci95', '?')})",
             ha="center", fontsize=8, style="italic")
    plt.tight_layout()
    fig.savefig(output_dir / "11_gmm_cluster_occupancy.png", dpi=150)
    plt.close()

    print(f"GMM plots saved to {output_dir}/")


def preflight(feature_file: Path, min_baseline_rows: int = 100) -> list[str]:
    """CFM-01/CFM-10 — input completeness gate. Returns problems; any
    problem means the confirmation run does not execute.

    P5-09 — requires the EXACT JJA universe, not mere coverage:
    for each year 2001-2025 exactly the 92 JJA dates Jun 1..Aug 31;
    for 2026 exactly Jun 1..Aug 25 (86 dates, ending before the
    held-out event date).  Duplicates, non-JJA rows, non-finite
    values in required feature columns, and any row on/after the
    event cutoff are all reported."""
    problems: list[str] = []
    if not feature_file.is_file():
        problems.append(f"feature matrix {feature_file} missing")
        return problems
    daily_df = pd.read_csv(feature_file, index_col=0, parse_dates=True)
    if not isinstance(daily_df.index, pd.DatetimeIndex):
        try:
            daily_df.index = pd.to_datetime(daily_df.index)
        except Exception:
            problems.append(
                f"{feature_file}: index is not parseable as dates")
            return problems
    if daily_df.index.hasnans:
        problems.append("index contains unparseable/NaT dates")

    missing = [c for c in GMM_FEATURES if c not in daily_df.columns]
    if missing:
        problems.append(f"required features missing: {missing}")
    if len(daily_df) < min_baseline_rows:
        problems.append(
            f"only {len(daily_df)} rows — the confirmation run "
            "requires the complete baseline, not a partial file")

    # P5-09 — no duplicate dates anywhere in the frame.
    dup_dates = daily_df.index[daily_df.index.duplicated()]
    if len(dup_dates):
        problems.append(
            f"{len(dup_dates)} duplicate date rows "
            f"(e.g. {[str(d.date()) for d in dup_dates[:5]]})")

    # P5-09 — no non-JJA rows anywhere in the frame.
    non_jja = daily_df[~daily_df.index.month.isin(JJA_MONTHS)]
    if len(non_jja):
        problems.append(
            f"{len(non_jja)} non-JJA rows present — the feature "
            "matrix must contain JJA days only (e.g. "
            f"{[str(d.date()) for d in non_jja.index[:5]]})")

    # P5-09 — nothing on/after the held-out event cutoff.
    post_cutoff = daily_df[daily_df.index >= pd.Timestamp(EVENT_DATE)]
    if len(post_cutoff):
        problems.append(
            f"{len(post_cutoff)} rows on/after event cutoff "
            f"{EVENT_DATE} — post-cutoff data must not be present")

    # P5-09 — non-finite values in required feature columns.
    present_cols = [c for c in GMM_FEATURES if c in daily_df.columns]
    if present_cols:
        nonfinite = {}
        for c in present_cols:
            vals = pd.to_numeric(daily_df[c], errors="coerce")
            n_bad = int((~np.isfinite(
                vals.to_numpy(dtype=float))).sum())
            if n_bad:
                nonfinite[c] = n_bad
        if nonfinite:
            problems.append(
                "non-finite values in required feature columns: "
                f"{nonfinite}")

    # P5-09 — exact date sets: observed dates per year must equal the
    # expected JJA calendar, not merely cover it.  Baseline years are
    # the full Jun 1..Aug 31 (92 days); 2026 ends Aug 25 per contract.
    idx = daily_df.index
    for yr in range(2001, 2026):
        expected = pd.date_range(f"{yr}-06-01", f"{yr}-08-31", freq="D")
        observed = idx[idx.year == yr].unique()
        missing_dates = expected.difference(observed)
        extra_dates = observed.difference(expected)
        msgs = []
        if len(missing_dates):
            msgs.append(
                f"missing {len(missing_dates)} of 92 JJA dates "
                f"(e.g. {[str(d.date()) for d in missing_dates[:3]]})")
        if len(extra_dates):
            msgs.append(
                f"{len(extra_dates)} dates outside the expected "
                "Jun1-Aug31 set (e.g. "
                f"{[str(d.date()) for d in extra_dates[:3]]})")
        if msgs:
            problems.append(f"year {yr}: " + "; ".join(msgs))

    # 2026 is partial by contract: Jun (30) + Jul (31) + Aug 1-25
    # (25) = 86 days, ending before the held-out event date.
    expected_2026 = pd.date_range(JJA_2026[0], JJA_2026[1], freq="D")
    observed_2026 = idx[idx.year == 2026].unique()
    missing_2026 = expected_2026.difference(observed_2026)
    extra_2026 = observed_2026.difference(expected_2026)
    msgs_2026 = []
    if len(missing_2026):
        msgs_2026.append(
            f"missing {len(missing_2026)} of {len(expected_2026)} "
            "pre-event JJA dates (e.g. "
            f"{[str(d.date()) for d in missing_2026[:3]]})")
    if len(extra_2026):
        msgs_2026.append(
            f"{len(extra_2026)} dates outside the expected "
            f"{JJA_2026[0]}..{JJA_2026[1]} set (e.g. "
            f"{[str(d.date()) for d in extra_2026[:3]]})")
    if msgs_2026:
        problems.append("year 2026: " + "; ".join(msgs_2026))

    return problems


def main() -> int:
    """Main GMM descriptive confirmation.

    CFM-10 — exit code 0 only on a complete, reproducible run.
    Missing input, failed fits, or non-convergence exit nonzero.

    P5-02 — all I/O is under one shared run root: the extractor's
    features/ directory provides inputs; this stage writes gmm/.
    """
    parser = argparse.ArgumentParser(
        description="Phase 4: GMM descriptive overlay + Jensen-Shannon "
                    "distance (confirmation run).")
    parser.add_argument(
        "--run-root",
        default=None,
        help=("Shared run root containing features/ from the "
              "extractor; gmm/ outputs are written beneath it. "
              "Default: research_runs/gmm_confirmation/ relative to "
              "the repo root. Rejected if inside data/, pinned/, "
              "nepal/framework_v1/, or preregistration.md."),
    )
    args = parser.parse_args()

    try:
        run_root = _resolve_run_root(args.run_root)
    except ValueError as e:
        print(f"RUN ROOT REJECTED: {e}")
        return 1

    print("=" * 60)
    print("GMM Descriptive Confirmation — EXPLORATORY, single cell")
    print("=" * 60)
    print()
    print("GMM is DESCRIPTIVE ONLY — it describes weather regimes, "
          "NOT avalanche precursors or forecasts.")
    print()
    print(f"Run root: {run_root}")

    features_dir = run_root / "features"
    feature_file = features_dir / FEATURE_FILENAME
    run_dir = _assert_safe_root(run_root / "gmm")
    plot_dir = run_dir / "plots"
    results_file = run_dir / "gmm_results.json"
    bundle_file = run_dir / "bundle.json"

    problems = preflight(feature_file)
    if problems:
        print("PREFLIGHT FAILED — no run executed:")
        for p in problems:
            print(f"  - {p}")
        return 1

    print(f"Loading features from {feature_file}...")
    daily_df = pd.read_csv(feature_file, index_col=0, parse_dates=True)
    print(f"Loaded {len(daily_df)} rows\n")

    # P5-05/P5-06 — provenance and units sidecars from the shared
    # features/ directory.
    provenance = load_run_provenance(features_dir)
    feature_units = load_feature_units(features_dir)

    gmm_results = run_gmm_descriptive(
        daily_df, target_year=2026, provenance=provenance,
        feature_units=feature_units, run_dir=run_dir)

    # Bundle inputs: the features/ artifacts this stage consumed,
    # plus the merged ERA5 input under the run root if present.
    input_files = sorted(p for p in features_dir.glob("*")
                         if p.is_file())
    era5_merged = run_root / "era5" / "merged"
    if era5_merged.is_dir():
        input_files += sorted(era5_merged.glob("*.nc"))

    run_dir.mkdir(parents=True, exist_ok=True)
    if "error" in gmm_results:
        with open(results_file, "w") as f:
            json.dump(gmm_results, f, indent=2, default=str)
        write_bundle(gmm_results, input_files, feature_file, run_dir)
        print(f"RUN FAILED: {gmm_results['error']}")
        print("Results/bundle recorded under the run root as "
              "quarantined output.")
        return 1

    print("\nGenerating GMM plots...")
    generate_gmm_plots(daily_df, gmm_results, plot_dir)

    with open(results_file, "w") as f:
        json.dump(gmm_results, f, indent=2, default=str)
    print(f"\nResults saved to {results_file}")

    bundle = write_bundle(gmm_results, input_files, feature_file,
                          run_dir)
    print(f"Run bundle saved to {bundle_file} "
          f"(run_id={bundle['run_id']})")

    js = gmm_results["js_distance"]
    print("\n" + "=" * 60)
    print("GMM Descriptive Summary")
    print("=" * 60)
    print(f"Modal K (BIC, {len(GMM_SEEDS)} seeds): "
          f"{gmm_results['best_k']} "
          f"(frequency {gmm_results['modal_k_frequency']:.2f})")
    print(f"K=1 null benchmark: {'YES' if gmm_results['k1_null_benchmark'] else 'NO'}")
    print(f"JS distance: {js['observed']} (95% CI {js['ci95']}, "
          f"null p95 {js['null_p95']}, exceeds null: "
          f"{js['exceeds_null']})")
    pc = gmm_results["posterior_confidence"]
    print(f"Posterior confidence: mean {pc['mean_max_proba']}, "
          f"ambiguous {pc['ambiguous_fraction']:.1%}")
    if gmm_results.get("js_distance_pre_event") is not None:
        print(f"JS distance (pre-event): "
              f"{gmm_results['js_distance_pre_event']}")
    print(f"\nStatus: {gmm_results['status']}")
    print(f"DISCLAIMER: {gmm_results['disclaimer']}")

    print("\n" + "=" * 60)
    print("GMM Descriptive EXIT GATE: PASS "
          "(complete reproducible run)")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
