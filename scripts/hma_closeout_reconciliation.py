"""Seal the PoC closeout reconciliation artifact.

Binds the frozen NKP cohort numbers, the independent audit's relaxed cohort
estimate, the H1/H2 outcomes, the GCAL non-specificity verdict, and the
authorized claim language — so downstream reports cannot drift past what the
sealed evidence supports.  Read-only over sealed inputs; write-once output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import india_lake_epoch_linkage as linkage  # noqa: E402
from p5_safe_io import write_once_json, write_once_sidecar  # noqa: E402

E = Path("/Users/sanjayb/nepal-event-anomaly-evidence"
         "/india-phase0-source-intake")
V2 = E / "hma-lake-trajectory-poc-v2"
V3 = E / "hma-lake-trajectory-poc-v3"
GCAL = E / "hma-gate-calibration-v0"
AUDIT = E / "hma-posthoc-audit-v0"
SCHEMA = "HMA_POC_CLOSEOUT_RECONCILIATION_V0"


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _bound_json(path: Path) -> dict[str, Any]:
    sidecar = Path(f"{path}.sha256")
    expected = f"{_sha256(path)}  {path.name}\n"
    if not sidecar.is_file() or sidecar.read_text() != expected:
        raise ValueError(f"sidecar verification failed: {path}")
    return json.loads(path.read_text())


def build() -> dict[str, Any]:
    nkp = _bound_json(V3 / "HMA_NKP_RESULT_V0.json")
    traj = _bound_json(V3 / "HMA_LAKE_TRAJECTORIES_V3.json")
    audit = _bound_json(AUDIT / "HMA_INDEPENDENT_AUDIT_V0.json")
    gcal = _bound_json(GCAL / "HMA_GATE_CALIBRATION_V0.json")
    disp = _bound_json(E / "HMA_ERROR_FIELD_DISPOSITION_V0.json")
    census = _bound_json(E / "HMA_EVENT_LINKABILITY_CENSUS_V0.json")
    from collections import Counter
    dist = Counter(len(r["epochs_observed"])
                   for r in traj["trajectories"])
    audit_nested = (audit["checks"]["exploratory_audit_reproduction"]
                    ["checks"]["type_counts_relaxed_nested08_cohort"])
    return {
        "schema": SCHEMA, "version": 0,
        "purpose": ("Single bound record of the post-V2 audit/NKP lane: "
                    "cohort reconciliation, hypothesis outcomes, claim "
                    "ceiling, and corrected interpretation language."),
        "cohort_reconciliation": {
            "frozen_nkp_complete_paths": dist[5],
            "relaxed_audit_nested08_reference": 5087,
            "difference_note": ("The frozen NKP cohort (5,047) applies the "
                                "owner-approved dual-channel rule "
                                "containment>=0.8 AND implied area ratio "
                                "<=4.0; the audit's 5,087 used nested>=0.8 "
                                "without the area-ratio cap. The 40-path "
                                "difference is the cap, not a discrepancy."),
            "nkp_path_length_distribution":
                {str(k): dist[k] for k in sorted(dist)},
            "paths_with_containment_only_edge": sum(
                1 for r in traj["trajectories"]
                if r.get("containment_only_edge_ids")),
            "audit_nested08_type_counts":
                audit_nested.get("observed"),
        },
        "hypothesis_outcomes": {
            "h1": nkp["h1"],
            "h2": nkp["h2"],
            "h3": nkp["h3"],
            "status": nkp["status"],
            "gcal_verdict": gcal["verdict"],
        },
        "containment_rule": {
            "sealed_parameters": {"containment_min": 0.8,
                                  "area_ratio_max": 4.0,
                                  "iou_min": 0.8, "from_frac_min": 0.8},
            "sensitivity_cap_2_0": "declared future sensitivity only; "
                                   "not retuned post-result",
        },
        "claim_ceiling": {
            "authorized": [
                "The raw stability gate is non-specific under the declared "
                "dependence-preserving null families (pass rates "
                "0.83-1.00); it is not a validated structure detector.",
                "The V2 result cannot be interpreted as calibrated "
                "evidence of absent structure; it is a negative under a "
                "non-specific gate.",
                "A positive-control contrast (source Type labels) was "
                "recovered in candidate paths: stratified AUC 0.758 "
                "[0.730, 0.790]; this validates label-recoverability of "
                "the cohort, not physical lake identity or GLOF behavior.",
                "No within-candidate-path growth persistence was detected "
                "under the frozen complete-path design (1 of 3 "
                "non-overlapping interval pairs).",
                "At most 111 catalog records could ever link to >=3-epoch "
                "candidate paths and 23 satisfy all conditions; the event "
                "association branch is closed for this dataset.",
            ],
            "prohibited": [
                "claiming natural lake clusters, typologies, or GLOF "
                "precursors (H3 never ran)",
                "claiming the data are 'less structured than Gaussian' — "
                "the gate's failure mode is non-specificity, not a data "
                "comparison",
                "claiming precise Type-I calibration from 6 replicates; "
                "the decisive reading is unbounded non-specificity, not a "
                "point estimate",
                "treating N3/N4 nulls as universally structureless — they "
                "are dependence/nuisance nulls",
                "calling H1 independent instrument validation — it is "
                "positive-control label recovery on candidate paths",
                "generalizing H2 beyond the complete-case, candidate-link "
                "cohort subject to linkage uncertainty, cadence, and "
                "measurement error",
                "treating Error as independent uncertainty — it is a "
                "perimeter-derived proxy (2000: m^2-scale, ~96.3% of rows "
                "on the recovered 10.308*Perimeter formula; residual "
                "off-formula rows documented in the disposition)",
                "Asia-wide generalization — the source is the Greater "
                "Himalaya inventory",
                "treating catalog-unlisted lakes as verified non-events",
                "calling a future Hi-MAG run 'replication of H2' — the "
                "H2 sequence failed here; any Hi-MAG lane is a separate "
                "H1-only estimand requiring its own intake decision",
            ],
        },
        "input_artifacts": {
            "trajectories_v3": _sha256(V3 / "HMA_LAKE_TRAJECTORIES_V3.json"),
            "nkp_result": _sha256(V3 / "HMA_NKP_RESULT_V0.json"),
            "frozen_nkp_plan": _sha256(V3 / "FROZEN_NKP_PLAN_V0.json"),
            "linkage_review_v1": _sha256(
                E / "INDIA_LAKE_LINKAGE_REVIEW_V1.json"),
            "gcal_result": _sha256(GCAL / "HMA_GATE_CALIBRATION_V0.json"),
            "independent_audit": _sha256(
                AUDIT / "HMA_INDEPENDENT_AUDIT_V0.json"),
            "error_disposition": _sha256(
                E / "HMA_ERROR_FIELD_DISPOSITION_V0.json"),
            "linkability_census": _sha256(
                E / "HMA_EVENT_LINKABILITY_CENSUS_V0.json"),
            "proof_binding_correction": _sha256(
                V2 / "HMA_PROOF_BINDING_CORRECTION_V0.json"),
        },
        "dispositions": {
            "event_association": "CLOSED_FOR_THIS_DATASET",
            "h3_structure": ("DORMANT; requires a new preregistered "
                             "clusterability test with a matched null "
                             "envelope and independent validation"),
            "hi_mag": ("DEFERRED; if reopened, a separate H1-only estimand "
                       "with its own owner intake decision"),
            "error_field_disposition": disp.get("disposition"),
            "census_joint_ceiling": census.get("ceiling", {})
                or census.get("headline", {}).get("joint_all_conditions"),
        },
        "authority": dict(linkage.AUTHORITY_FLAGS),
        "event_association_branch": "CLOSED",
    }


def _publish_or_match(path: Path, document: dict[str, Any]) -> str:
    if path.exists():
        digest = _sha256(path)
        if Path(f"{path}.sha256").read_text() != f"{digest}  {path.name}\n":
            raise ValueError(f"sidecar verification failed: {path}")
        if json.loads(path.read_text()) != document:
            raise FileExistsError(
                f"write-once artifact exists with different content: {path}")
        return digest
    write_once_json(path, document)
    return write_once_sidecar(path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=("seal", "verify"))
    args = ap.parse_args()
    try:
        doc = build()
        if args.cmd == "verify":
            sealed = _bound_json(E / "hma-lake-trajectory-poc-v3"
                                 / f"{SCHEMA}.json")
            ok = sealed == doc
            print("RECONCILIATION_VERIFY_OK" if ok
                  else "RECONCILIATION_VERIFY_MISMATCH")
            return 0 if ok else 1
        sha = _publish_or_match(
            E / "hma-lake-trajectory-poc-v3" / f"{SCHEMA}.json", doc)
        print(json.dumps({"status": "RECONCILIATION_SEALED",
                          "sha256": sha}, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) \
            as exc:
        print(f"RECONCILIATION_BLOCKED: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
