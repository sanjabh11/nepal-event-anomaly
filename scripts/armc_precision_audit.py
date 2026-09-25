"""Arm C case-crossover Monte-Carlo precision audit (readiness phase 6).

Implements the design-based feasibility protocol recommended in the
four-engine synthesis (Gemini derivation):  precision in a
time-stratified case-crossover depends on within-stratum exposure
variance and matched controls, NOT on an arbitrary minimum event count.

Design (predeclared):
  stratum i   = one provisional eligible event (in anchor box,
                day-precision, day covered by JJA daily frame)
  controls    = same basin_group, same calendar month, all frame years,
                excluding +-7d washout around the event date and any
                other candidate-event intervals
  exposure    = standardized candidate feature (predeclared list)
  beta grid   = ln(OR) for OR in {1.10, 1.25, 1.35, 1.50, 2.00}
  B           = 2000 replicates per beta per exposure
  criteria    = A: exp(W_lnOR(beta=0)) <= 3.0
                B: power(beta=ln(1.35)) >= 0.80 at alpha=0.05

This script simulates OUTCOMES under hypothesized betas — it never fits
real event outcomes.  It is a feasibility audit, not an analysis.
"""
import json, sys, hashlib, argparse, datetime
from pathlib import Path
sys.path.insert(0, "scripts")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from p5_safe_io import write_once_json, write_once_sidecar

BOXES = {"gandaki": (28.25, 29.0, 84.5, 85.0),
         "karnali": (29.5, 30.0, 81.75, 82.5),
         "koshi":   (27.75, 28.25, 86.75, 87.25)}
EXPOSURES = ["tcwv_mean", "cape_mean", "theta500_minus_thetasfc", "w500_mean"]
OR_GRID = [1.10, 1.25, 1.35, 1.50, 2.00]
B = 2000
WASHOUT_DAYS = 7
ALPHA = 0.05
RNG = np.random.default_rng(20260924)

def _sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def mc_precision(strata, beta, B=B):
    """Vectorized MC: simulate case index via Gumbel-max, refit scalar-beta
    conditional logit for all B replicates simultaneously via Newton."""
    xs_list = [np.concatenate([[st["case_x"]], st["ctrl_x"]]) for st in strata]
    # Simulate case indices for all replicates at once
    case_vals = np.zeros(B)
    for xs in xs_list:
        logits = beta * xs
        g = -np.log(-np.log(RNG.uniform(size=(B, len(xs)))))
        case_idx = np.argmax(logits[None, :] + g, axis=1)
        case_vals += xs[case_idx]
    # Newton for scalar beta across all replicates
    bh = np.zeros(B)
    for _ in range(30):
        score = np.full(B, 0.0); info = np.full(B, 0.0)
        for xs in xs_list:
            z = bh[:, None] * xs[None, :]
            z -= z.max(axis=1, keepdims=True)
            ex = np.exp(z); w = ex / ex.sum(axis=1, keepdims=True)
            score += -(w * xs[None, :]).sum(axis=1)
            info += (w * xs[None, :]**2).sum(axis=1) - (w * xs[None, :]).sum(axis=1)**2
        score += case_vals
        step = score / np.maximum(info, 1e-10)
        bh += np.clip(step, -5, 5)
        if np.abs(step).max() < 1e-8:
            break
    # SE per replicate at convergence
    info = np.full(B, 0.0)
    for xs in xs_list:
        z = bh[:, None] * xs[None, :]
        z -= z.max(axis=1, keepdims=True)
        ex = np.exp(z); w = ex / ex.sum(axis=1, keepdims=True)
        info += (w * xs[None, :]**2).sum(axis=1) - (w * xs[None, :]).sum(axis=1)**2
    se = 1.0 / np.sqrt(np.maximum(info, 1e-10))
    return {"mean_se": float(np.mean(se)), "ci_width_or": float(np.exp(3.92 * np.mean(se))),
            "power": float(np.mean(np.abs(bh / se) > 1.96)),
            "rel_bias": float(abs(np.mean(bh) - beta) / max(beta, 1e-9))}

def build_strata(els, frame, exposure):
    """Return list of strata: each = (case_x, control_xs, meta)."""
    all_event_days = set()
    for _, e in els.iterrows():
        ts = pd.Timestamp(e["event_time_start"])
        te = pd.Timestamp(e["event_time_end"])
        for d in pd.date_range(ts.tz_localize(None) - pd.Timedelta(days=WASHOUT_DAYS),
                               te.tz_localize(None) + pd.Timedelta(days=WASHOUT_DAYS)):
            all_event_days.add(d.normalize())
    strata = []
    for _, e in els.iterrows():
        b = BOXES[e["basin_group"]]
        if not (b[0] <= e["latitude"] <= b[1] and b[2] <= e["longitude"] <= b[3]):
            continue
        if e["event_time_precision"] != "day":
            continue
        d = pd.Timestamp(e["event_time_start"]).tz_localize(None)
        ev = frame[(frame["basin_group"] == e["basin_group"]) & (frame["date"] == d)]
        if len(ev) == 0:
            continue
        ctrl = frame[(frame["basin_group"] == e["basin_group"]) &
                     (frame["date"].dt.month == d.month) &
                     (~frame["date"].isin(all_event_days)) &
                     (frame["date"] != d)]
        strata.append({"event_id": e["event_id"], "date": str(d.date()),
                       "basin": e["basin_group"], "n_controls": len(ctrl),
                       "case_x": float(ev[exposure].iloc[0]),
                       "ctrl_x": ctrl[exposure].to_numpy()})
    return strata

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--package", required=True)
    ap.add_argument("--frame", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    els = pd.DataFrame(json.loads(Path(a.package).read_text())["event_labels"])
    frame = pd.read_csv(a.frame)
    frame["date"] = pd.to_datetime(frame["date"])
    results = {}
    for exp in EXPOSURES:
        strata = build_strata(els, frame, exp)
        # standardize exposure on the control pool
        pool = np.concatenate([st["ctrl_x"] for st in strata])
        mu, sd = pool.mean(), pool.std()
        for st in strata:
            st["case_x"] = (st["case_x"] - mu) / sd
            st["ctrl_x"] = (st["ctrl_x"] - mu) / sd
        s2 = [float(np.var(np.concatenate([[st["case_x"]], st["ctrl_x"]]), ddof=1)) for st in strata]
        per_or = {}
        for orr in OR_GRID:
            per_or[str(orr)] = mc_precision(strata, np.log(orr))
        results[exp] = {"n_strata": len(strata),
                        "strata": [{"event_id": st["event_id"], "date": st["date"],
                                    "basin": st["basin"], "n_controls": st["n_controls"]}
                                   for st in strata],
                        "within_stratum_var": s2,
                        "fisher_info_approx": float(np.sum(s2)),
                        "per_odds_ratio": per_or}
    rep = {"schema": "P5_CASE_CROSSOVER_PRECISION_AUDIT_V0",
           "claim_scope": "research_only_no_operational_authorization",
           "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "package_sha256": _sha(a.package), "frame_sha256": _sha(a.frame),
           "design": {"strata_rule": "in-box + day-precision + day-covered-by-JJA-frame",
                      "controls": "same basin, same month, all frame years, +-7d washout",
                      "B": B, "or_grid": OR_GRID,
                      "criterion_A": "exp(W_lnOR at beta=0) <= 3.0",
                      "criterion_B": "power at OR=1.35 >= 0.80"},
           "provisional_cohort_note": ("strata are UNADJUDICATED labels; this audit assesses "
                                       "whether the design COULD be estimable if adjudication "
                                       "confirms them — it does not analyze real outcomes"),
           "results": results}
    verdicts = {}
    for exp, r in results.items():
        a_ok = r["per_odds_ratio"]["1.1"]["ci_width_or"] <= 3.0
        b_ok = r["per_odds_ratio"]["1.35"]["power"] >= 0.80
        verdicts[exp] = {"criterion_A": bool(a_ok), "criterion_B": bool(b_ok),
                         "estimable": bool(a_ok and b_ok)}
    rep["verdicts"] = verdicts
    rep["overall"] = ("ESTIMABLE" if any(v["estimable"] for v in verdicts.values())
                      else "NOT_ESTIMABLE_ON_PROVISIONAL_COHORT")
    write_once_json(a.out, rep, indent=2)
    write_once_sidecar(a.out)
    print(json.dumps({"overall": rep["overall"],
                      "verdicts": {k: v["estimable"] for k, v in verdicts.items()}}, indent=2))

if __name__ == "__main__":
    main()
