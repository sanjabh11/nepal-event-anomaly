"""HMA held-out Route-B run — executes the SEALED v2 analyser once under the
frozen p5-hma-heldout-protocol-v1.json.

armc_routeb_analyze_v2.py is hash-bound in the Step-1 code snapshot and is NOT
modified. This wrapper is the declared adaptation layer:

  * the pre-registered secondary E10_tp_m10_m1 (10-day tp sum, days -10..-1;
    Mohanty et al. 2026, Nat. Hazards 122:144) is added to v2's module-level
    EXPOSURES map IN MEMORY before the single analyse() pass — v2 file bytes
    are untouched and the emitted constants record the augmented set
  * v2 has no v34 lineage for this cohort, so no compatibility regression is
    requested
  * the frozen decision rule is evaluated mechanically from the emitted
    aggregates and recorded under protocol_verdict
  * Nepal+HMA pooling is emitted as descriptive_only per the protocol

Claim ceiling: held-out inference limited to the declared primary decision
rule; everything else descriptive.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import armc_routeb_analyze as V1  # noqa: E402
import armc_routeb_analyze_v2 as V2  # noqa: E402
from p5_safe_io import write_once_json, write_once_sidecar  # noqa: E402

E10 = {"E10_tp_m10_m1": ("tp", "sum", -10, -1)}
AUTHORITY = dict(V1.AUTHORITY_CEILING, confirmatory_claim_authorized=False)


def _sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _gate(entry: dict, *, stat: str, boot_key: str, mc_key: str) -> dict:
    """Triple criterion: mean > 0 AND 95% cluster CI excludes 0 AND p_upper<0.05."""
    mean = entry.get(stat)
    ci = (entry.get(boot_key) or {}).get("ci95")
    p = (entry.get(mc_key) or {}).get("p_upper")
    out = {"mean": mean, "ci95": ci, "mc_p_upper": p,
           "passes_mean": mean is not None and mean > 0,
           "passes_ci": bool(ci) and ci[0] > 0,
           "passes_mc": p is not None and p < 0.05}
    out["verdict"] = "PASS" if all((out["passes_mean"], out["passes_ci"], out["passes_mc"])) else "NULL"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("coverage", "full"), required=True)
    ap.add_argument("--lane-root", action="append", required=True)
    ap.add_argument("--inventory", action="append", required=True)
    ap.add_argument("--episode-map", required=True)
    ap.add_argument("--decision", required=True)
    ap.add_argument("--hmaglofdb", required=True)
    ap.add_argument("--protocol", required=True)
    ap.add_argument("--nepal-results", required=True,
                    help="sealed Step-1 diagnostic doc; descriptive pooling only")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    V2.EXPOSURES = {**V2.EXPOSURES, **E10}   # in-memory only; v2 file unchanged
    res, units = V2.analyse(a.lane_root, a.inventory, a.episode_map, a.decision,
                            a.hmaglofdb, a.mode)

    doc = {"schema": "P5_HMA_HELDOUT_COVERAGE_V1" if a.mode == "coverage"
                     else "P5_HMA_HELDOUT_REANALYSIS_V1",
           "protocol_sha256": _sha(a.protocol),
           "claim_scope": "held-out inference for the declared primary decision rule only; "
                          "all other emitted quantities descriptive; Nepal+HMA pooling descriptive",
           "authority": AUTHORITY,
           "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "run_provenance": {"code_file_hma": _sha(__file__),
                              "code_file_v2": _sha(V2.__file__),
                              "code_file_v1": _sha(V1.__file__),
                              "argv": sys.argv, "python": sys.version.split()[0]},
           "inputs_sha256": {"inventories": {Path(p).name: _sha(p) for p in a.inventory},
                             "episode_map": _sha(a.episode_map), "decision": _sha(a.decision),
                             "hmaglofdb": _sha(a.hmaglofdb), "protocol": _sha(a.protocol),
                             "nepal_results": _sha(a.nepal_results)},
           "lanes": a.lane_root,
           "constants": {"MIN_REF": V2.MIN_REF, "WASH_DAYS": V2.WASH_DAYS,
                         "R_MC": V2.R_MC, "B_BOOT": V2.B_BOOT,
                         "SEED_MC": V2.SEED_MC, "SEED_BOOT": V2.SEED_BOOT,
                         "SEA_LAGS": list(V2.SEA_LAGS),
                         "EXPOSURES": {k: list(v) for k, v in V2.EXPOSURES.items()},
                         "exposure_extension": "E10_tp_m10_m1 added in memory per protocol; "
                                               "v2 file bytes unchanged"},
           "units": units, **res}
    doc["aggregates_all_units"] = doc.pop("aggregates_all26", None)

    if a.mode == "full":
        agg = res["aggregates_primary"]
        primary = {
            "C1_trig_minus_early": _gate(agg.get("C1_trig_minus_early", {}),
                                         stat="mean_delta_pct",
                                         boot_key="boot_mean_delta",
                                         mc_key="mc_null_mean_delta"),
            "E3_tp_v1": _gate(agg.get("E3_tp_v1", {}),
                              stat="mean_z", boot_key="boot_mean_z",
                              mc_key="mc_null_mean_z"),
        }
        secondary = {"E10_tp_m10_m1": _gate(agg.get("E10_tp_m10_m1", {}),
                                            stat="mean_z", boot_key="boot_mean_z",
                                            mc_key="mc_null_mean_z")}
        doc["protocol_verdict"] = {
            "rule": "primary positive iff EACH of C1,E3: mean>0 AND 95% lake-cluster CI excludes 0 "
                    "AND one-sided MC p_upper<0.05; E10 reported descriptively",
            "primary": primary,
            "primary_outcome": "POSITIVE" if all(v["verdict"] == "PASS"
                                                 for v in primary.values()) else "NULL",
            "secondary": secondary,
        }
        # descriptive-only Nepal+HMA pooling over per-unit values
        nep = json.loads(Path(a.nepal_results).read_text())
        nep_units = [u for u in nep.get("units", []) if not u.get("gorkha_window")]
        combined = {}
        for key, extract in (
            ("C1_trig_minus_early", lambda u: u.get("C1_trig_minus_early")),
            ("E3_tp_v1_mean_pct", lambda u: (u.get("E3_tp_v1") or {}).get("pct")),
            ("E1_tp_trig0_mean_pct", lambda u: (u.get("E1_tp_trig0") or {}).get("pct")),
        ):
            hv = [extract(u) for u in units]
            nv = [extract(u) for u in nep_units]
            vals = [x for x in hv + nv if x is not None]
            combined[key] = {"n": len(vals), "n_hma": sum(x is not None for x in hv),
                             "n_nepal": sum(x is not None for x in nv),
                             "mean": float(np.mean(vals)) if vals else None,
                             "claim": "descriptive_only"}
        doc["nepal_hma_combined_descriptive"] = combined

    out = Path(a.out)
    write_once_json(out, doc, indent=2)
    write_once_sidecar(out)
    print(json.dumps({"mode": a.mode, "out": str(out),
                      "verdict": (doc.get("protocol_verdict") or {}).get("primary_outcome"),
                      "units": len(units)}, default=str))


if __name__ == "__main__":
    main()
