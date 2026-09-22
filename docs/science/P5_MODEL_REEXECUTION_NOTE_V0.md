# P5 model re-execution interpretive note V0

## Purpose and scope

The model-re-execution proof answers a narrow question:

> Given the persisted, byte-verified input frames and declared configurations,
> does a fresh invocation of the declared regime engine reproduce the frozen
> artifact semantics and terminal result?

It is a reproducibility proof for the governed local run. It is not a new
scientific estimand, a positive regime result, an external replication, or an
operational authorization.

## Bound result

The append-only report
`p5-glof-2026-09-19/retrieval/p5_model_reexecution_report_v2.json` has schema
`P5_MODEL_REEXECUTION_PROOF_V0`, proof scope `model_reexecution`, and terminal
status `MODEL_REEXECUTED`.

| Lane | Lane verdict | Input fidelity | What matched |
|---|---|---|---|
| Daily | `REPRODUCED` | `exact` | Configuration, environment, freeze, semantic artifact, train-mask, required-gate map, terminal status, and authority-false result |
| Seasonal | `REPRODUCED` | `roundtrip_drift` | The same semantic and gate-level fields after authoritative bound `input_values` were spliced into the reconstructed frame |

Both lanes report zero semantic differences, empty problem lists, equal
recorded/recomputed digests, and all authority flags false. The scientific
terminal result remains the earlier honest negative:
`UNSUPERVISED_STRUCTURE_NOT_STABLE`; the proof does not promote it.

## Seasonal CSV precision disclosure

The first strict re-execution report is preserved as
`p5_model_reexecution_report_v1.json` with
`MODEL_REEXECUTION_UNAVAILABLE`. It correctly exposed that reading the
persisted seasonal CSV through the ordinary pandas path changed 43 frame cells
by at most `1.052e-15` relative difference. This is a serialization/float64
round-trip issue, not evidence that the scientific artifact changed.

The repaired re-execution path therefore:

1. verifies the persisted frame and sidecar;
2. detects the bounded round-trip drift;
3. restores the declared `input_values` matrix as the authoritative model
   input;
4. reruns the engine and compares the resulting semantic artifact and gate
   record;
5. records `input_fidelity: roundtrip_drift` rather than hiding the defect.

This makes the seasonal verdict digest-exact at the declared semantic layer
while preserving the byte-level limitation. Future runs should prefer a
serialization format or canonical numeric encoding that does not require
this splice; any such change requires a new amendment and release chain.

## What `MODEL_REEXECUTED` proves

- The two declared negative results are deterministic under the bound input
  bytes, declared configurations, and recorded environment digest.
- The comparison included terminal status, configuration/freeze digests,
  semantic artifact digest, required-gate equality, train-mask binding, and
  all-false authority flags.
- The v1 failure mode remains auditable rather than being overwritten by the
  repaired proof.

## What it does not prove

- It does not prove that the ERA5 or event data are externally correct,
  complete, or independently collected.
- It does not provide a second host, independent implementation, remote CI,
  public preservation, or third-party reproduction.
- It does not increase the seasonal sample size, resolve K instability, or
  change the `DESCRIPTIVE_REGIME_ONLY` ceiling.
- It does not authorize Arm C retrieval, seismic acquisition, ObsPy
  admission, association, publication, warning, forecasting, or operations.
- It does not make the daily and seasonal negatives evidence that hazards do
  not exist; it only makes the governed computation reproducible.

## Required interpretation in later releases

Release reports must keep these proof classes separate:

| Proof class | Allowed wording |
|---|---|
| Artifact integrity replay | Persisted envelope, freeze, receipt, sidecar, and binding checks passed |
| Model re-execution | Declared engine reran on bound inputs and reproduced the recorded semantics |
| External reproduction | A separately provisioned host or independent implementation reran the protocol |
| Scientific qualification | All declared gates and claim ceilings support the stated descriptive result |
| Operational authority | Not present in this framework; all authority flags remain false |

The current report reaches the second row for both lanes. It must not be
described using the third, fourth, or fifth row.
