# P5 extension status index — 2026-09-20 (current)

The authoritative P5 state record. `FRAMEWORK_V1_CURRENT_STATUS.md`
and `STATUS_SCOPE_RECONCILIATION_NOTE_20260915.md` describe the broader
legacy framework and are **not** P5 evidence. Release-bound counts are
recorded in `docs/science/ARTIFACT_MANIFEST_V0.json` — this document
carries evidence state only.

## Lane states

| Lane | State | Evidence |
|---|---|---|
| P5-A2 daily | `UNSUPERVISED_STRUCTURE_NOT_STABLE` artifact — honest negative; v1 receipt+artifact are byte-bound (amendment v5 lineage repair); v0 receipt is HISTORICAL (receipt-bound only) | `p5-glof-2026-09-19/retrieval/p5_glof_descriptive_receipt_v1.json` + `p5_glof_regime_artifact_v1.json` |
| Seasonal v0 | SUPERSEDED semantics — immutable, never patched | `p5-seasonal-jja-2026-09-20/` |
| **Seasonal v1 (current)** | `UNSUPERVISED_STRUCTURE_NOT_STABLE` — second honest negative; same three open gates under corrected semantics | `p5-seasonal-v1-2026-09-20/run/seasonal_lane_receipt_v0.json` |
| Option 3 seismic | **BLOCKED** at preflight — zero in-window events, no bytes, storage marginal; formal disposition recorded; one-station contract hardened (24h window bound, BLOCKED binds no byte evidence, strict event anchors, bounded errors) | `p5_d_preflight_reconciliation_v0.json` + `p5_d_owner_disposition_v1.json` + `nepal/seismic_sidecar/one_station_contract.py` |
| Arm C pressure levels | DEFERRED — separate owner amendment required | — |
| Event association | BLOCKED — labels `UNADJUDICATED`, controls censored (by design) | — |

## P5-A2 artifact lineage (amendment v5)

The original P5-A2 run bound only the artifact **digest** into the
v0 receipt — the artifact bytes were never persisted. Amendment v5
(`retrieval/p5_amendment_v5_artifact_lineage.json`) records the gap
honestly: the v0 receipt is preserved as historical, and a declared
v1 config re-executed the frozen protocol solely to serialize the
artifact. The v1 artifact carries a different digest (the engine and
config surface evolved) — the honest check is the scientific verdict,
which reproduced: `UNSUPERVISED_STRUCTURE_NOT_STABLE`. Daily replay
now emits `artifact_integrity_replay` only when the persisted artifact's
envelope and freeze digests recompute and the receipt binds those
bytes. This is not an independent model refit; a receipt-bound digest alone
is never a model replay.

## Seasonal v1 result detail

- Failed gates: `modal_k_unanimous` (3/3/4 across seeds), `season_matched_null` (p=0.66 ≥ 0.05), `shuffled_null` (7/50 replicate failures → incomplete envelope, fails closed)
- `gate_observations` distinguishes `PASS`/`FAIL`/`SKIPPED`/`NOT_APPLICABLE` from binding — LORO is `SKIPPED`/non-binding (diagnostic), season_refits/elevation/effort are `NOT_APPLICABLE`
- Negative control refused before fitting (declaration) and rejected statistically (independent tests)
- Replay: `REPLAY_OK` via `scripts/replay_seasonal_p5.py` (deterministic frame rebuild, envelope+freeze digests, floor, bindings, authority)

## Cross-root index

`p5-glof-2026-09-19/retrieval/p5_evidence_index_v4.json` is the exhaustive
release index. It binds every non-sidecar payload in the declared physical
and logical partitions, requires a live sidecar, and records explicit
exclusions — including typed `planned` slots for outputs published after
index generation (`CLOSURE_PENDING` pre-publication semantics). The v1
index is preserved as a historical 31-file listed set; its `INDEX_OK`
status did not prove root-wide coverage; v2/v3 are superseded exhaustive
indexes. The v2-schema validator also checks final-verification closure,
duplicate physical assignment, and manifest/head consistency. The index
and detached release closure are published by exclusive-create writers;
the closure (`p5_release_closure_v4.json`) is additionally verified
post-publication by `scripts/validate_release_closure.py`, which proves
the recorded path resolves to the recorded SHA and that suite counts are
bound to the machine-generated `p5_suite_receipt_v1.json`.

Historical index: `p5-glof-2026-09-19/retrieval/p5_evidence_index_v1.json` binds the
three physical roots (daily, seasonal v0 immutable, seasonal v1
current) plus the seismic preflight as a logical surface inside the
daily root — relative paths under logical `root_id`s, per-file
digests + sidecars, provenance fields, and a `supersedes` pointer to
the immutable v0 index. `scripts/validate_evidence_index.py` verifies
it fail-closed (30-test suite).

## Owner-gated items (not executable without approval)

1. Option 3 execution — needs a documented in-window event or a new non-event estimand, storage re-measure, decoder admission, FDSN intake module (never in the local byte reader), dedicated one-station runner
2. ObsPy project admission — isolated qualification only; needs a dependency amendment with pin/hash/license
3. Arm C — separate CDS/feature amendment
4. Publication / external verification — deferred

## Audit-3 release-integrity disposition

The codeable audit-3 findings are repaired by the release-integrity v2
amendment: exhaustive inventory replaces the curated v1 list; all currently
discovered payloads carry validated sidecars; replay scope is explicitly
integrity-only; daily/seasonal/replay/index writers are refuse-existing and
atomic; logical relative paths are used for new receipt/report pointers; and
the seismic contract requires the complete ten-digest scientific chain with
strict falsey-input and event-timing validation. The detached closure binds
the live index digest, manifest/head, suite counts, replay modes, warnings,
skips, owner gates, and all-false authority flags.

`approved_by` remains null in the Option 3 disposition. That record is a
recommended default, not owner approval. Option 3, ObsPy admission, Arm C,
publication, remote CI, and external preservation remain separate gates.

## Warnings disclosure

Fresh suite carries ~57 xarray/netCDF4 deprecation warnings
(upstream, monitored) and 1 sklearn ConvergenceWarning in a
degenerate synthetic fixture (expected). No warning-free claim is
made.
