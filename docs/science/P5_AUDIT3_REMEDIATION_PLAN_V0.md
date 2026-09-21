# P5 audit-3 remediation record — release integrity v2

> Status (2026-09-21, repair run): **implementation complete; full-suite
> and replay revalidation executed; final rebind and release-closure
> regeneration in progress; scientific acquisition owner-gated.**
> Fresh evidence this run: full local suite
> `3826 passed / 6 skipped / 1 failed / 57 warnings` on **3833
> collected** in 1159.86 s - the single failure is the pre-rebind
> collection-census drift gate (`test_r11_5_release_closure::
> test_collection_count_matches_manifest`, recorded 3794 vs live 3833
> from +39 new governed tests), which resolves at this rebind.
> Daily and seasonal artifact-integrity replays re-executed to **new
> v4 report paths** (`REPLAY_OK`, scope `artifact_integrity_replay`,
> 0 failures; frozen v3 reports untouched). The owner-disposition
> superseding record `p5_d_owner_disposition_v2.json` now exists with
> `approved_by: null` and `approval_status: PENDING_OWNER_APPROVAL`;
> v1 preserved byte-identical. Index v2 and closure v2 remain frozen;
> index v3 and closure v3 are generated at new paths after the rebind.
> Scientific estimands and historical evidence are unchanged.

## Scope lock

This amendment repairs proof boundaries, evidence coverage, replay
terminology, and publication safety. It does not acquire seismic or
pressure-level data, admit ObsPy to the governed project environment, alter
the daily or seasonal gates, change any basin mapping, or change authority
flags. The daily v0 receipt/artifact history and seasonal v0/v1 roots remain
immutable; the v1 daily artifact remains a declared reconstruction rather
than a byte-identical reproduction of the original v0 run.

Baseline recorded before implementation:

- repository HEAD `3e943319b712540f83cd70b929fb52a1874d983d`;
- clean tree and 151-file manifest;
- local suite `3714 passed / 6 skipped / 0 failed / 57 warnings` on 3720
  collected;
- daily and seasonal scientific verdicts remain honest negatives;
- Option 3 remains `BLOCKED` with no waveform acquisition.

## Ordered controls

1. Freeze the baseline and bind this amendment before new output is created.
2. Exhaustively inventory the daily, seismic logical, seasonal-v0, and
   seasonal-v1 evidence surfaces. Every payload requires a verified sidecar;
   the v2 index must fail closed on an unlisted or multiply assigned byte.
3. Keep daily and seasonal replay reports read-only by default and label them
   `artifact_integrity_replay` unless the regime engine is independently
   refit and its digest chain agrees.
4. Require new run/index/report paths and exclusive atomic publication;
   historical paths are never overwritten.
5. Rebind the repository manifest once, after code, tests, and documentation
   are complete. Generate the release index and detached closure only after
   replay and suite checks are final.

## Parallel ownership

Agent 1 owns only the one-station contract and its tests. Agent 2 owns only
the evidence-index v2 generator/validator and their tests. The coordinator
owns replay modes, safe writers, evidence sidecar materialization, release
records, documentation, manifest rebind, and final closure. The two-agent
layout is expected to improve wall time by approximately 1.3–1.6x; manifest
rebind, evidence generation, replay, and final validation remain serialized.

## Exit conditions

The internal release-integrity pass is closed only when focused adversarial
tests, the full local suite, both integrity replays, exhaustive index v2
validation, claim scans, manifest verification, and tree cleanliness agree.
The closure must state exact suite warnings/skips, replay proof scope,
owner-gated items, and all-false authority flags. This closure is not remote
CI, external preservation, publication, customer acceptance, or operational
authority.
