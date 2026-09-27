# AGENTS.md — integration/ (Nepal GLOF research-only framework)

## Boundaries (non-negotiable)
- Research only: no forecast, warning, detector, causal, event-risk-odds or operational claims. Every artifact carries all authority flags `false`.
- Seismic input is not used as a Nepal predictor (retrospective sidecar only).
- Evidence is write-once: use `scripts/p5_safe_io.py` (`write_once_json`, `write_once_bytes`, `write_once_sidecar`). Never edit or overwrite a sealed file or its `.sha256`; publish a new version (`_v2`) that declares `supersedes`.
- Diagnostic plans are frozen before computation; anything not in the plan is labelled `POST_HOC`.
- IMERG bulk retrieval stays unauthorized unless a new signed amendment exists.

## Environment
- Python: `integration/.venv/bin/python` (system python lacks xarray). Always pass `-B`.
- This `integration/` directory is a git worktree on branch `codex/full-framework-v1-20260912-144802` of `/Users/sanjayb/nepal-event-anomaly`. Scripts/tests were committed 2026-09-27 by owner decision; `.venv/` stays gitignored. Whether to merge into `main` remains an open owner decision — do not merge or push without approval.

## Evidence roots
- Route-B / ARMC: `/Users/sanjayb/nepal-event-anomaly-evidence/p5-armc-pressure-levels-2026-09-22` (v17-lane, v19-lane, retrieval/)
- Amendment chain: `/Users/sanjayb/nepal-event-anomaly-evidence/p5-glof-2026-09-19/retrieval` (head v40; checker `amendment_chain_check_v10.py`)
- Step-1 Nepal re-analysis: `/Users/sanjayb/nepal-event-anomaly-evidence/p5-nepal-reanalysis-2026-09-26` (plan/, lane-adjm/, results/, regime/, sensitivity/, verification/, lane_receipt_v*.json)
- Option-A prep (held-out cohort, label audits): `/Users/sanjayb/nepal-event-anomaly-evidence/p5-optionA-prep-2026-09-26`

## Tests (Step-1 contract suite, 283 tests, ~8.5 min)
```bash
cd integration
.venv/bin/python -B -m pytest tests/test_armc_routeb_analyze_v2.py tests/test_armc_routeb_analyze.py \
  tests/test_science_v0_regimes.py tests/test_seasonal_lane.py -q
```

## Run: Route-B analyser v2 (full, with v1-compat regression)
```bash
E=/Users/sanjayb/nepal-event-anomaly-evidence; P=$E/p5-armc-pressure-levels-2026-09-22; N=$E/p5-nepal-reanalysis-2026-09-26
.venv/bin/python -B scripts/armc_routeb_analyze_v2.py --mode full \
  --lane-root $P/v17-lane --lane-root $P/v19-lane --lane-root $N/lane-adjm \
  --inventory $P/retrieval/armc_v17_selector_inventory_v1.json \
  --inventory $P/retrieval/armc_v19_selector_inventory_v0.json \
  --inventory $N/lane-adjm/armc_adjm_selector_inventory_v1.json \
  --compat-lane-root $P/v17-lane --compat-lane-root $P/v19-lane \
  --compat-inventory $P/retrieval/armc_v17_selector_inventory_v1.json \
  --compat-inventory $P/retrieval/armc_v19_selector_inventory_v0.json \
  --episode-map $P/retrieval/armc_event_episode_mapping_v1.json \
  --decision $P/retrieval/armc_event_adjudication_decision_v1.json \
  --hmaglofdb $E/p5-glof-2026-09-19/glof-events/HMAGLOFDB.csv \
  --plan $N/plan/nepal_diagnostic_reanalysis_plan_v1.json \
  --v34 $P/retrieval/armc_routeb_result_v34.json --out <NEW_PATH>.json
```
`--mode coverage` produces a coverage matrix only. `--out` must be a new path (write-once).

## Run: seasonal regime redesign (Step 1.9)
`scripts/run_seasonal_regime_redesign_v1.py --frame6 ... --frame6-provenance ... --frame12 ... --plan ... --out-root <new dir>`

## Retrieval (Earthmover ERA5, anonymous, snapshot ZFKDHBCTBVHVXM3BQFV0)
`scripts/armc_retrieve_driver_earthmover_adjm_v1.py --inventory <inv> --var <v> --out-root <lane> --cap-bytes <per-worker cap> --selections <ids> --record-tag <tag>`
Never run two workers with overlapping selections for the same variable. Fail-closed on cap, grid or time-axis mismatch.

## Verify
```bash
# sidecars in a lane (sidecar = "<hex>  <name>" or "<hex>")
find <lane> -name '*.sha256' ! -path '*/payload-*' | while read f; do t="${f%.sha256}"; \
  [ "$(awk '{print $1}' "$f")" = "$(shasum -a 256 "$t" | awk '{print $1}')" ] || echo "FAIL $t"; done
# amendment chain (expect exit 0, head_clean/inventory/contract/register/bindings all true, 0 unexpected)
# All four --artifact-root entries are required: v40 binds armc_routeb_analyze.py (integration/scripts)
# and v37-v39 bind the IMERG canary granule; omitting a root yields head_clean=false.
E=/Users/sanjayb/nepal-event-anomaly-evidence
cd $E/p5-glof-2026-09-19/retrieval && <integration>/.venv/bin/python -B amendment_chain_check_v10.py --root . \
  --artifact-root $E/p5-armc-pressure-levels-2026-09-22/retrieval \
  --artifact-root $E/p5-glof-2026-09-19/imerg-canary-ep13 \
  --artifact-root $E/p5-glof-2026-09-19/imerg-canary-ep13/payloads \
  --artifact-root <integration>/scripts
```
Independent verifiers (no imports from the code under test) live under `$N/verification/`. Deterministic replay: re-run to a scratch path and compare the JSON content digest excluding `generated_utc` and `run_provenance.argv`.

## Lanes added 2026-09-26/27 (all sealed; see evidence-root `project_master_receipt_v1.json`)
- HMA held-out: `$E/p5-hma-heldout-2026-09-26` — 28 events/23 lakes, 357 payloads, NULL on primaries (C1,E3); E10 descriptive p=0.10; warm descriptive E6 +0.53.
  Run: `scripts/armc_routeb_analyze_hma_v1.py --mode full --lane-root <HMA lane> --inventory .../armc_hma_selector_inventory_v1.json --episode-map .../hma_episode_map_v1.json --decision .../hma_adjudication_decision_v1.json --hmaglofdb <csv> --protocol .../p5-hma-heldout-protocol-v1.json --nepal-results <nepal reanalysis json> --out <new>.json`
- DOY diagnostic: `$E/p5-hma-diagnostic-2026-09-26/doy_matched_diag_v1.py <out.json>` — POST_HOC ±15d day-of-year-matched reference+null; reuses v2 loaders, HMA lane read-only.
- Tien Shan thermal (D2, both-nulls rule): `$E/p5-tienshan-thermal-2026-09-27` — 26 units (Merzbacher=1), 48 t2m payloads, NULL both nulls (+0.20/+0.21).
  Run: `scripts/armc_routeb_analyze_tienshan_v1.py` (E6-only path replicating sealed v2 math — tp/sf contract inapplicable; see results `constants.adaptation`).
- Writeup: `$E/p5-writeup-2026-09-26` — `p4_manuscript_draft_v3.md` + `p4_number_trace_v3.json` (111 traces) + `transport_summary_v1.json` (POST_HOC descriptive) + `audit_note_overlap_reconciliation_v1.md` + `methods_note_sidedness_v2.md`; manifests v1→v3 supersession-chained.
