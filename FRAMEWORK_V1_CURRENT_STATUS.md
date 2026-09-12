# Framework v1 current status

This document is the additive current-status reference for the clean-room
framework. It does not rewrite legacy reports, historical phase-loop notes, or
the reconciled data-lane manifest.

## Authorization path

The primary Phase B path is hash-bound and fail-closed:

1. Run the read-only preflight against the authoritative checkout.
2. Bind both the data-contract and framework-contract SHA-256 values.
3. Load the canonical Phase B manifest through `load_verified_b_input_bundle`.
4. Resolve component files only through `B_TARGET_GRID_ARTIFACT_IDS`.
5. Verify typed raster semantics, exact target-grid geometry, the nested
   `observability_by_unit` map, the controls lock, and a self-hashed A gate.
6. Run the bounded B screen. The result is `SCREEN_RANKED`; its independent
   `phase_status` is `B_TO_C_READY` or `B_TO_C_BLOCKED`. The strict path uses
   the contract-bound 300-second default deadline and writes an atomic
   checkpoint beside the requested result unless one is supplied explicitly.

The compatibility loader may still be used for diagnostics, but a pretty-JSON
manifest hash, missing explicit contract hash, arbitrary component path, or
caller-supplied gate boolean cannot authorize a primary run.

## Current proof boundaries

- GHSL built-up surface is a measured non-negative m²-per-cell quantity. It is
  not a binary presence mask unless a separately identified artifact is
  produced and contracted.
- Sentinel-1 observability is metadata compatible-pair footprint coverage in
  `[0,1]`, not SLC coherence, displacement, or InSAR quality.
- Hanging-ice output is a Sentinel-2/SCL and slope support proxy. It is not an
  independently validated hanging-ice detection product.
- WorldPop is loaded context and is inactive in the v1 primary component
  registry unless a new versioned control activates it. Aspect is context and
  does not participate in strict leave-one-layer-out stability.
- C and D remain intentionally `BLOCKED`/`NOT_RUN` in v1. E requires verified
  A and B envelopes; F strict mode requires verified A, B, and E envelopes.
- The preregistration file hash remains a frozen-file check, while the current
  data-source history is explicitly `POST_HOC_DATA_SOURCE_CHANGE`.

## Commands

```text
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m nepal.framework_v1.cli \
  preflight --repo-root /Users/sanjayb/nepal-event-anomaly \
  --handoff-root /Users/sanjayb/nepal-event-anomaly/data/framework_inputs_v1_reconciled

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m nepal.framework_v1.cli \
  contract --verify --repo-root /Users/sanjayb/nepal-event-anomaly

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m nepal.framework_v1.cli \
  pipeline --repo-root /Users/sanjayb/nepal-event-anomaly \
  --expected-root /Users/sanjayb/nepal-event-anomaly \
  --raw /Users/sanjayb/nepal-event-anomaly/data/glacier_failure_db/hma_events_all.json \
  --manifest /Users/sanjayb/nepal-event-anomaly/data/framework_inputs_v1_reconciled/manifest.json \
  --manifest-root /Users/sanjayb/nepal-event-anomaly/data/framework_inputs_v1_reconciled \
  --out /Users/sanjayb/nepal-event-anomaly/data/framework_v1_runs/<run-id> \
  --expected-contract-sha256 <data-contract-sha256> \
  --expected-framework-contract-sha256 <framework-contract-sha256>
```

For strict screening, pass an explicit data hash, the runtime framework hash,
the canonical manifest root, a verified controls lock, and a verified
`A_CATALOG` gate. A blocked B screen may produce a ranked diagnostic envelope,
but it must not be described as scientific validation, warning readiness,
production readiness, or authority approval.

Phase A writes an explicit `holdout_plan.json` alongside the normalized
catalog, adjudication ledger, controls lock, gate, and artifact manifest. The
one-shot pipeline stops at the first unmet gate and writes a deterministic
`pipeline_report.json`; omitted strict E inputs leave E and F blocked.
