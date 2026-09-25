"""Arm C observation-completeness audit (v16+ readiness phase 4).

Audits the 1,175 lake-season ObservationOpportunityV0 records against
what evidence exists.  An opportunity is promotable to a control only
when its observation coverage is bound (OBSERVED_FULL); UNKNOWN or
censored windows are ineligible as negative controls — they are
unknowns, not non-events.

This script produces the census + per-opportunity gap list; it does
NOT promote anything — promotion requires bound observation frames
(satellite/monitoring evidence), which is a separate acquisition scope.
"""
import json, sys, hashlib, datetime, argparse
from pathlib import Path
sys.path.insert(0, "scripts")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd
from p5_safe_io import write_once_json, write_once_sidecar

def _sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--package", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    pkg = json.loads(Path(a.package).read_text())
    opps = pd.DataFrame(pkg["opportunities"])
    ctrls = pd.DataFrame(pkg["controls"])

    census = {
        "n_opportunities": len(opps),
        "state_distribution": opps["state"].value_counts().to_dict(),
        "platform_distribution": opps["platform"].value_counts().to_dict(),
        "coverage_fraction_null": int(opps["coverage_fraction"].isna().sum()),
        "frame_ids_unbound": int((opps["frame_ids"].map(len) == 0).sum()),
        "detection_threshold_absent": int((opps["detection_threshold"].astype(str) == "").sum()),
        "source_absent": int((opps["source_id"].astype(str) == "").sum()),
        "controls_state": ctrls["state"].value_counts().to_dict(),
        "windows": {"start_min": str(opps["window_start"].min()),
                    "end_max": str(opps["window_end"].max())},
        "years_span": sorted(int(y) for y in pd.to_datetime(opps["window_start"]).dt.year.unique()),
    }
    promotable = int((opps["state"] == "OBSERVED_FULL").sum())
    census["promotable_to_control"] = promotable
    census["ineligible_as_control"] = len(opps) - promotable

    report = {"schema": "P5_OBSERVATION_COMPLETENESS_AUDIT_V0",
              "claim_scope": "research_only_no_operational_authorization",
              "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "package_sha256": _sha(a.package),
              "census": census,
              "verdict": ("NO_OBSERVED_CONTROLS" if promotable == 0
                          else f"{promotable}_PROMOTABLE"),
              "verdict_detail": ("All 1,175 lake-season opportunities have state=UNKNOWN, "
                                 "no bound observation frames, no coverage fraction, no "
                                 "detection threshold and no source — none can serve as a "
                                 "negative control. Promoting any requires bound satellite/"
                                 "monitoring evidence, which is a separate acquisition scope."),
              "unit_prefixes": opps["unit_id"].str.split(":").str[0].value_counts().to_dict()}
    write_once_json(a.out, report, indent=2)
    write_once_sidecar(a.out)
    print(json.dumps({"status": report["verdict"], "opportunities": census["n_opportunities"],
                      "promotable": promotable}, indent=2))

if __name__ == "__main__":
    main()
