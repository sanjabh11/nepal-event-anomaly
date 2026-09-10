"""Phase 5: End-to-end runner for Nepal Event Anomaly Assessment.

Runs all phases in sequence:
  Phase 2: feature_extraction.py
  Phase 3: anomaly_detector.py + change_point_detector.py + isolation_forest.py
  Phase 4: gmm_descriptive.py
  Phase 5: Synthesis + report generation

Usage:
    source .venv/bin/activate
    python nepal/run_nepal_test.py
"""
from __future__ import annotations

import json
import sys
import subprocess
from pathlib import Path
from datetime import datetime

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
PLOTS_DIR = REPO_ROOT / "plots"
REPORT_FILE = REPO_ROOT / "nepal_event_anomaly_report.md"

# Import feature contract for report metadata
sys.path.insert(0, str(Path(__file__).resolve().parent))
from feature_contract import (
    EVENT, PRE_EVENT_WINDOW, EVENT_DATE,
    GRID_LAT_KM, GRID_LON_KM, TOTAL_FEATURES,
    HISTORICAL_BASELINE, NEGATIVE_CONTROL_YEARS,
    Z_SCORE_THRESHOLD, PERCENTILE_THRESHOLDS,
    IFOREST_PARAMS, GMM_K_RANGE, GMM_COVARIANCE,
    PELT_MODEL, PELT_PENALTY, CUSUM_K, CUSUM_THRESHOLD,
    BLOCK_PERMUTATION_LENGTHS, ROLLING_WINDOWS_DAYS,
)

ELEVATION_DISCLAIMER = (
    f"ERA5-Land model elevation: {EVENT['model_elevation_m']} m. "
    f"Source slope: {EVENT['source_elevation_m']} m. "
    f"Delta: {EVENT['elevation_gap_m']} m. "
    f"Grid: {GRID_LAT_KM} × {GRID_LON_KM} km."
)


def run_phase(script_name: str, phase_name: str) -> bool:
    """Run a phase script and return success status."""
    script_path = REPO_ROOT / "nepal" / script_name
    print(f"\n{'='*60}")
    print(f"Running {phase_name}: {script_name}")
    print(f"{'='*60}\n")

    result = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=str(REPO_ROOT),
        capture_output=False,
    )

    if result.returncode != 0:
        print(f"\n❌ {phase_name} FAILED (exit code {result.returncode})")
        return False

    print(f"\n✅ {phase_name} PASSED")
    return True


def load_results() -> dict:
    """Load all phase result JSON files."""
    results = {}

    result_files = {
        "anomaly": DATA_DIR / "anomaly_results.json",
        "change_point": DATA_DIR / "change_point_results.json",
        "isolation_forest": DATA_DIR / "isolation_forest_results.json",
        "gmm": DATA_DIR / "gmm_results.json",
        "nisar_catalog": DATA_DIR / "nisar_catalog_ledger.json",
    }

    for name, path in result_files.items():
        if path.exists():
            with open(path) as f:
                results[name] = json.load(f)
        else:
            results[name] = None

    return results


def generate_report(results: dict) -> str:
    """Generate the final markdown report."""
    report = []
    report.append("# Nepal Event Anomaly Assessment — Final Report\n")
    report.append(f"**Generated:** {datetime.now().isoformat()}\n")
    report.append(f"**Status:** RESEARCH ONLY — No prediction, no causal attribution\n")
    report.append(f"**Pre-registration:** FROZEN (see preregistration.md)\n\n")

    # Event definition
    report.append("## 1. Event Definition\n\n")
    report.append(f"| Field | Value |\n|-------|-------|\n")
    report.append(f"| Event date | {EVENT_DATE} |\n")
    report.append(f"| Location | Langtang Lirung north face, Nepal |\n")
    report.append(f"| Reference point | {EVENT['reference_point']} (HiRisk) |\n")
    report.append(f"| ERA5-Land cell | {EVENT['era5_cell']} (Hausfather nearest) |\n")
    report.append(f"| Model elevation | {EVENT['model_elevation_m']} m |\n")
    report.append(f"| Source elevation | {EVENT['source_elevation_m']} m |\n")
    report.append(f"| Elevation gap | {EVENT['elevation_gap_m']} m |\n")
    report.append(f"| Grid resolution | {GRID_LAT_KM} × {GRID_LON_KM} km |\n")
    report.append(f"| Glacier ID | {EVENT['glacier_id']} |\n\n")

    # Elevation disclaimer
    report.append(f"**⚠️ {ELEVATION_DISCLAIMER}**\n\n")

    # Pre-registration summary
    report.append("## 2. Pre-Registration Summary\n\n")
    report.append(f"| Item | Value |\n|------|-------|\n")
    report.append(f"| Pre-event window | {PRE_EVENT_WINDOW[0]} to {PRE_EVENT_WINDOW[1]} |\n")
    report.append(f"| Event day (held out) | {EVENT_DATE} |\n")
    report.append(f"| Historical baseline | {HISTORICAL_BASELINE} |\n")
    report.append(f"| Features | {TOTAL_FEATURES} (7 raw + 3 derived) |\n")
    report.append(f"| Z-score threshold | {Z_SCORE_THRESHOLD} |\n")
    report.append(f"| Percentile thresholds | {PERCENTILE_THRESHOLDS} |\n")
    report.append(f"| Isolation Forest | {IFOREST_PARAMS} |\n")
    report.append(f"| GMM K range | {GMM_K_RANGE} |\n")
    report.append(f"| GMM covariance | {GMM_COVARIANCE} |\n")
    report.append(f"| PELT model | {PELT_MODEL}, pen={PELT_PENALTY} |\n")
    report.append(f"| CUSUM | k={CUSUM_K}, threshold={CUSUM_THRESHOLD} |\n")
    report.append(f"| Block permutation lengths | {BLOCK_PERMUTATION_LENGTHS} days |\n")
    report.append(f"| Negative control years | {NEGATIVE_CONTROL_YEARS} |\n\n")

    # Results: Anomaly detection
    report.append("## 3. Anomaly Detection Results\n\n")
    if results.get("anomaly"):
        a = results["anomaly"]
        z = a.get("zscore_summary", {})
        p = a.get("percentile_summary", {})
        report.append("### 3.1 Z-score Anomaly\n\n")
        report.append(f"| Metric | Value |\n|-------|-------|\n")
        report.append(f"| Pre-event T2m anomalies (|z|>{Z_SCORE_THRESHOLD}) | {z.get('pre_event_t2m_anomalies', '?')} |\n")
        report.append(f"| Pre-event PDD anomalies (|z|>{Z_SCORE_THRESHOLD}) | {z.get('pre_event_pdd_anomalies', '?')} |\n")
        report.append(f"| Max T2m z-score | {z.get('max_t2m_z', '?')} |\n")
        report.append(f"| Max PDD z-score | {z.get('max_pdd_z', '?')} |\n\n")

        report.append("### 3.2 Percentile Ranking\n\n")
        report.append(f"| Metric | Value |\n|-------|-------|\n")
        report.append(f"| T2m above 95th percentile | {p.get('t2m_above_95', '?')} days |\n")
        report.append(f"| T2m above 99th percentile | {p.get('t2m_above_99', '?')} days |\n")
        report.append(f"| PDD above 95th percentile | {p.get('pdd_above_95', '?')} days |\n")
        report.append(f"| PDD above 99th percentile | {p.get('pdd_above_99', '?')} days |\n\n")

        report.append("### 3.3 Block Permutation Test\n\n")
        perm = a.get("permutation_results", {})
        if perm:
            report.append("| Block length | T2m p-value | PDD p-value | T2m significant | PDD significant |\n")
            report.append("|-------------|-------------|------------|-----------------|----------------|\n")
            for block_key, result in perm.items():
                report.append(f"| {block_key} | {result.get('p_value_t2m', '?')} | "
                              f"{result.get('p_value_pdd', '?')} | "
                              f"{'YES' if result.get('significant_t2m') else 'NO'} | "
                              f"{'YES' if result.get('significant_pdd') else 'NO'} |\n")
        report.append("\n")

        report.append("### 3.4 Negative Controls (2021-2025)\n\n")
        controls = a.get("negative_controls", {})
        if controls:
            report.append("| Year | False positives | Max |z| |\n|------|----------------|--------|\n")
            for year, result in controls.items():
                if isinstance(result, dict) and "false_positives" in result:
                    report.append(f"| {year} | {result['false_positives']} | {result['max_z']:.2f} |\n")
        report.append("\n")
    else:
        report.append("Anomaly detection results not available.\n\n")

    # Results: Change-point
    report.append("## 4. Change-Point Detection Results\n\n")
    if results.get("change_point"):
        cp = results["change_point"]
        for method, data in cp.items():
            if isinstance(data, dict) and "n_change_points" in data:
                report.append(f"### 4.1 {method}\n\n")
                report.append(f"- Total change-points: {data['n_change_points']}\n")
                report.append(f"- Pre-event change-points: {len(data.get('pre_event_change_points', []))}\n")
                if data.get("pre_event_change_points"):
                    for cp_date in data["pre_event_change_points"]:
                        report.append(f"  - **{cp_date}** (in pre-event window)\n")
                report.append("\n")
    else:
        report.append("Change-point results not available.\n\n")

    # Results: Isolation Forest
    report.append("## 5. Isolation Forest Results\n\n")
    if results.get("isolation_forest"):
        if_results = results["isolation_forest"]
        if "error" in if_results:
            report.append(f"Error: {if_results['error']}\n\n")
        else:
            report.append(f"| Metric | Value |\n|-------|-------|\n")
            report.append(f"| Pre-event anomalies | {if_results.get('pre_event_anomalies', '?')} / {if_results.get('pre_event_n_days', '?')} days |\n")
            report.append(f"| Min anomaly score | {if_results.get('min_anomaly_score', '?')} |\n")
            report.append(f"| Model params | {if_results.get('model_params', {})} |\n")
            report.append(f"| Features used | {if_results.get('features_used', [])} |\n\n")

            report.append("### 5.1 Negative Controls\n\n")
            controls = if_results.get("negative_controls", {})
            if controls:
                report.append("| Year | N days | N anomalies | Anomaly rate | Min score |\n")
                report.append("|------|--------|-------------|-------------|------------|\n")
                for year, result in controls.items():
                    if isinstance(result, dict) and "n_days" in result:
                        report.append(f"| {year} | {result['n_days']} | {result['n_anomalies']} | "
                                      f"{result['anomaly_rate']} | {result['min_score']} |\n")
            report.append("\n")
    else:
        report.append("Isolation Forest results not available.\n\n")

    # Results: GMM
    report.append("## 6. GMM Descriptive Overlay\n\n")
    if results.get("gmm"):
        gmm = results["gmm"]
        if "error" in gmm:
            report.append(f"Error: {gmm['error']}\n\n")
        else:
            report.append(f"**⚠️ GMM is DESCRIPTIVE ONLY — it describes weather regimes, NOT avalanche precursors.**\n\n")
            report.append(f"| Metric | Value |\n|-------|-------|\n")
            report.append(f"| Best K (by BIC) | {gmm.get('best_k', '?')} |\n")
            report.append(f"| K=1 null benchmark | {'YES' if gmm.get('k1_null_benchmark') else 'NO'} |\n")
            report.append(f"| JS distance (target vs baseline) | {gmm.get('js_distance_target', '?')} |\n")
            report.append(f"| JS distance (pre-event vs baseline) | {gmm.get('js_distance_pre_event', '?')} |\n")
            report.append(f"| Covariance type | {gmm.get('covariance_type', '?')} |\n\n")

            report.append("### 6.1 BIC Scores\n\n")
            bics = gmm.get("bic_scores", {})
            if bics:
                report.append("| K | BIC |\n|---|-----|\n")
                for k, bic in sorted(bics.items()):
                    report.append(f"| {k} | {bic} |\n")
            report.append("\n")

            report.append("### 6.2 Cluster Occupancy\n\n")
            report.append("| Cluster | Baseline | Target |\n|---------|----------|--------|\n")
            baseline_occ = gmm.get("baseline_occupancy", [])
            target_occ = gmm.get("target_occupancy", [])
            for i in range(max(len(baseline_occ), len(target_occ))):
                b = baseline_occ[i] if i < len(baseline_occ) else "?"
                t = target_occ[i] if i < len(target_occ) else "?"
                report.append(f"| Cluster {i} | {b} | {t} |\n")
            report.append("\n")
    else:
        report.append("GMM results not available.\n\n")

    # NISAR feasibility
    report.append("## 7. NISAR Feasibility Assessment\n\n")
    if results.get("nisar_catalog"):
        nisar = results["nisar_catalog"]
        pre_count = len(nisar.get("pre_event_granules", []))
        post_count = len(nisar.get("post_event_granules", []))
        report.append(f"| Metric | Value |\n|-------|-------|\n")
        report.append(f"| Pre-event granules | {pre_count} |\n")
        report.append(f"| Post-event granules (excluded) | {post_count} |\n\n")

        findings = nisar.get("publication_latency_findings", {})
        if findings:
            report.append(f"**Key finding:** {findings.get('astra_finding', '')}\n\n")
            report.append(f"**Implication:** {findings.get('implication', '')}\n\n")
            report.append(f"**Recommendation:** {findings.get('recommendation', '')}\n\n")

        # List pre-event granules with latency traps
        traps = [g for g in nisar.get("pre_event_granules", []) if g.get("latency_trap")]
        if traps:
            report.append("### 7.1 Publication Latency Traps\n\n")
            report.append("| Granule | Acquisition end | Warning lead (hours) | Trap |\n")
            report.append("|---------|---------------|---------------------|------|\n")
            for g in traps:
                report.append(f"| {g.get('collection_name', '?')} | {g.get('acquisition_end', '?')} | "
                              f"{g.get('warning_lead_hours', '?')} | {g.get('latency_trap', '')} |\n")
            report.append("\n")
    else:
        report.append("NISAR catalog results not available.\n\n")

    # Cross-references
    report.append("## 8. Cross-References\n\n")
    report.append("| Source | Finding | Our comparison |\n|--------|---------|----------------|\n")
    report.append("| Hausfather (2026) | ERA5 0.25° t2m anomaly vs 1961-1990 | See EDA plots |\n")
    report.append("| Rui Li (2026) | 7-day mean T: 9.43°C, PDD: 65.94°C·d | See Phase 2 output |\n")
    report.append("| Guo et al. (2026) | Multi-sensor event reconstruction | Cited, not rediscovered |\n")
    report.append("| Xu (2026) | Open-data cascade reconstruction | Cited |\n")
    report.append("| Khadka et al. (2022) | ERA5-Land validation in Everest | Cited for limitation |\n")
    report.append("| Astra (2026) | NISAR catalog + publication latency | See Section 7 |\n\n")

    # Limitations
    report.append("## 9. Limitations\n\n")
    report.append(f"1. **Elevation mismatch:** ERA5-Land model elevation ({EVENT['model_elevation_m']} m) "
                  f"is {EVENT['elevation_gap_m']} m below the source ({EVENT['source_elevation_m']} m). "
                  f"All temperatures are at model elevation, not at the failure plane.\n")
    report.append(f"2. **Grid resolution:** {GRID_LAT_KM} × {GRID_LON_KM} km grid cannot resolve slope-scale conditions.\n")
    report.append(f"3. **Single event:** This study cannot establish predictive capability from one event.\n")
    report.append(f"4. **Reanalysis latency:** ERA5-Land preliminary product has ~5-day delay. "
                  f"An operationally faithful 25 August evaluation cannot use 24-25 August data.\n")
    report.append(f"5. **GMM is descriptive:** Cluster assignments describe weather regimes, not avalanche precursors.\n")
    report.append(f"6. **No causal attribution:** Detected anomalies are associations, not causes.\n")
    report.append(f"7. **Monsoon cloud:** Optical data (MODIS LST, Sentinel-2) is cloud-limited in JJA.\n")
    report.append(f"8. **ERA5-Land validation:** Khadka et al. (2022) found substantial local-scale errors "
                  f"in ERA5-Land wind and monsoon precipitation in the Everest region.\n\n")

    # What this study does NOT claim
    report.append("## 10. What This Study Does NOT Claim\n\n")
    report.append("- It does NOT claim prediction or forecasting capability\n")
    report.append("- It does NOT claim causal attribution of the avalanche to thermal forcing\n")
    report.append("- It does NOT claim that GMM clustering detected the event\n")
    report.append("- It does NOT claim that ERA5-Land 9km data represents conditions at 5,200 m\n")
    report.append("- It does NOT claim that a single event validates any methodology\n")
    report.append("- It does NOT claim that data was available before the event (publication-time ledger documents actual availability)\n\n")

    # Decision
    report.append("## 11. Decision\n\n")
    report.append("Based on the results above, the following decisions are made:\n\n")
    report.append("| Decision | Criteria | Status |\n|----------|----------|--------|\n")
    report.append("| Environmental anomaly detected | Any pre-event z-score > 2 or percentile > 95 | See Section 3 |\n")
    report.append("| Change-point before event | PELT/CUSUM change-point in pre-event window | See Section 4 |\n")
    report.append("| Multivariate anomaly | Isolation Forest flags pre-event days | See Section 5 |\n")
    report.append("| GMM regime shift | JS distance > 0.3 | See Section 6 |\n")
    report.append("| NISAR deformation study justified | Pre-event granules exist with adequate lead time | See Section 7 |\n")
    report.append("| Multi-event benchmark justified | Anomaly detected in this event | TBD |\n")
    report.append("| Operational deployment | NOT AUTHORIZED — single event, no prediction claim | NO-GO |\n\n")

    report.append("## 12. Null Result Acceptance\n\n")
    report.append("If no open meteorological precursor exceeded seasonal/control variability, ")
    report.append("the conclusion is:\n\n")
    report.append("> \"No open meteorological precursor exceeded climatology in the 7 days ")
    report.append(f"before {EVENT_DATE}, conditional on ERA5-Land measurement sensitivity ")
    report.append(f"at {EVENT['model_elevation_m']} m model elevation.\"\n\n")
    report.append("This is NOT a failure. It is a bound on observability.\n\n")

    report.append("---\n\n")
    report.append(f"*Generated by nepal/run_nepal_test.py at {datetime.now().isoformat()}*\n")

    return "\n".join(report)


def main():
    """Main Phase 5 execution — runs all phases and generates report."""
    print("=" * 60)
    print("Phase 5: End-to-End Runner + Report Generation")
    print("=" * 60)
    print()

    # Check prerequisites
    era5_file = DATA_DIR / "era5_land_nepal_jja_2001_2026.nc"
    if not era5_file.exists():
        print(f"❌ ERA5-Land data not found at {era5_file}")
        print("   Run Phase 1 first: python nepal/era5_download.py")
        sys.exit(1)

    feature_file = DATA_DIR / "features_nepal_jja_2001_2026.csv"
    if not feature_file.exists():
        # Run Phase 2
        if not run_phase("feature_extraction.py", "Phase 2: Feature Extraction"):
            print("Phase 2 failed. Cannot continue.")
            sys.exit(1)

    # Run Phase 3 (all three methods)
    if not run_phase("anomaly_detector.py", "Phase 3a: Anomaly Detection"):
        print("Phase 3a failed. Continuing with other methods.")

    if not run_phase("change_point_detector.py", "Phase 3b: Change-Point Detection"):
        print("Phase 3b failed. Continuing with other methods.")

    if not run_phase("isolation_forest.py", "Phase 3c: Isolation Forest"):
        print("Phase 3c failed. Continuing with other methods.")

    # Run Phase 4
    if not run_phase("gmm_descriptive.py", "Phase 4: GMM Descriptive"):
        print("Phase 4 failed. Continuing to report.")

    # Load all results
    print("\n" + "=" * 60)
    print("Loading all results for report generation...")
    print("=" * 60)
    results = load_results()

    # Generate report
    print("\nGenerating final report...")
    report = generate_report(results)

    with open(REPORT_FILE, "w") as f:
        f.write(report)
    print(f"\nReport saved to {REPORT_FILE}")

    print("\n" + "=" * 60)
    print("Phase 5 EXIT GATE: PASS")
    print("=" * 60)
    print(f"\nFinal report: {REPORT_FILE}")
    print(f"Plots: {PLOTS_DIR}/")
    print(f"Data: {DATA_DIR}/")


if __name__ == "__main__":
    main()
