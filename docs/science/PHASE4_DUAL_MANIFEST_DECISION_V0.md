# Phase 4 — Dual-manifest contract decision (v0)

**Status:** `DECISION_PROPOSED` — the defect is proven in code; the
resolution requires a ratified amendment to an exact-key contract in the
hardened descriptive runner. **No runner code was changed by this
lane.**
**Lane:** implementation lane (CLINE-IMPL), 2026-09-19.
**Scope:** contract analysis + `RunEvidenceManifestV0` shape. Protected
paths (`nepal/framework_v1/**`, `pinned/**`, `data/**`,
`preregistration.md`) are untouched.

## 1. The defect, proven in code

`run_glof_descriptive_poc` (`nepal/science_v0/glof_poc.py`) requires
the event package's manifest digest to **equal the digest of the single
manifest declared on the regime config**:

```python
cfg_manifest = getattr(regime_config, "source_manifest", None)
cfg_digest = _digest(dict(cfg_manifest))
if cfg_digest != event_package["source_manifest_digest"]:
    -> RUN_ERROR
```

and `run_regimes` applies the REG-11 preflight to that same single
object (`nepal/science_v0/regimes.py`: exact seven keys, non-fixture
marker, `feature_allowlist` enforced against the requested feature
columns, `verify_source_evidence` on its bytes).

The event package carries exactly one `source_manifest_digest`, and
`_EVENT_PACKAGE_KEYS` is an **exact-set** contract — an event package
with an extra key is rejected as carrying undeclared keys.

Now consider the real Phase-2..3 shape the P5 record authorizes:

| Role | Byte-bound source |
|---|---|
| event labels | HMAGLOFDB v1.3.0 (`icimod_hmaglofdb_v1_3_0`) |
| opportunity frame | ICIMOD 2015 lake inventory (`icimod_pdgl_2015`) |
| features | ERA5-Land multi-basin (`reanalysis-era5-land`) |
| sidecar | per-source evidence sidecars |

These are **four distinct byte-bound sources with four distinct source
IDs**. The runner demands one manifest, and `regimes.py` demands that
the same manifest declare the *feature* allowlist. Satisfying the gate
therefore requires pointing the event digest and the feature declaration
at **one** manifest — i.e. merging source IDs, which the Phase-4
instruction forbids and which would destroy per-source provenance.

**Consequence:** with real Phase-2 bytes, the honest path returns
`RUN_ERROR` by construction. This is not a data problem; it is a
contract gap. It is the Phase-4 blocker and it must be resolved before
any regime fitting.

## 2. Options considered

- **R2 — no runner change (rejected).** Keep one manifest and merge the
  event, opportunity and feature sources under a single `source_id`.
  Rejected: it silently merges source identities and voids the
  per-source binding that R11.1/R11.2 provenance established.
- **R1 — typed role wrapper (recommended).** Add a typed
  `RunEvidenceManifestV0` that carries **separate** role digests, and
  let the runner verify each role against its own evidence instead of
  collapsing them into one equality test.

## 3. Recommended shape — `RunEvidenceManifestV0`

A typed wrapper, not a merged manifest:

```
schema                 "RUN_EVIDENCE_MANIFEST_V0"
event_manifest         7-key non-fixture manifest   (event labels)
opportunity_manifest   7-key non-fixture manifest   (Option A frame)
feature_manifest       7-key non-fixture manifest   (reanalysis features)
sidecar_manifest       7-key non-fixture manifest   (per-source sidecars)
source_ids             {role -> source_id}          (must be distinct)
digests                {role -> sha256_canonical(manifest)}
role_digests_digest    sha256_canonical(role -> digest)
run_evidence_digest    sha256_canonical(everything above)
```

Rules: every role is verified by the **existing**
`verify_source_evidence` floor — no new hashing policy; the four
`source_ids` must be distinct unless a role is explicitly declared
not-applicable; and the wrapper never replaces a role manifest, it only
binds them.

## 4. Why this needs ratification rather than a silent edit

Consuming the wrapper requires the runner to accept a role section
inside the event package. `_EVENT_PACKAGE_KEYS` is an exact-set
contract in a **hardened** validator, so adding a key is a contract
amendment, not a bug fix. Under the standing rule — never reopen a
hardened validator without a live failure exposing a defect — this
decision is recorded and the *failing* contract is pinned in tests
rather than implemented on the implementation lane's own authority.

## 5. Enforcement while the decision is open

`tests/test_r11_9_dual_manifest_contract.py` pins the intended
semantics with `xfail(strict=True)`. The current gap is therefore
documented in the suite itself, CI stays honest (the suite is green and
the gap is visible), and the moment the amendment lands the tests turn
`XPASS` and **fail the build**, forcing the markers to be removed.
That is the TDD red state without shipping a permanently broken suite.

## 6. Explicit non-changes

No edit to `glof_poc.py`, `regimes.py`, `records.py`, `gates.py`,
`_hashing.py`, or any protected path; no new validator; no schema
relaxation; no source ID merged; all authority flags remain false.