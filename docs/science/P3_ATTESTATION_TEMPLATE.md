# P3 Attestation — DRAFT (awaiting attestation)

> Status: `DRAFT` — awaiting attestation. This document is a template
> for the
> designated approver. It is not an approval, not an envelope, and
> authorizes nothing. `human_approved` remains unset until an
> authenticated human completes the checklist below.

> **Status overlay annotation (2026-09-15):** the `P3_PENDING` value
> below is the blank-template state. For the D1/D2 digest pair bound
> here, it is superseded-by-design-attestation: an executed
> owner-directed `design_review_only` attestation was recorded on
> 2026-09-14 in `P3_ATTESTATION_RECORD_V0.md` (`human_approved=true`,
> `approved_at=2026-09-14`). The row is preserved unmodified so this
> file remains a faithful blank form for any future attestation round;
> nothing in this template is itself an approval.

## Binding values (derived from current D1/D2 bytes)

| Field | Value |
|---|---|
| Designated approver | repository owner (designated approver: current user) |
| Role | repository owner / P3 science-design approver |
| Designation date | 2026-09-14 |
| Attestation date | _blank until authenticated human attestation_ |
| Matrix path | `docs/science/HAZARD_EVENT_INVENTORY_DECISION_MATRIX_V0.md` |
| `matrix_sha256` | `5848668ea01bdab4d2e29554b6cb7bc666a30db46aca2b9f78e40adec76f6d04` |
| Policy path | `docs/science/INFORMATION_CUTOFF_TARGET_POLICY_V0.md` |
| `policy_sha256` | `df856eb1e4e2e264e725d662dfbe9862660e965a8ac8c459f99c5d910dc1861e` |
| Scope | `design_review_only` |
| Pilot rule | `FIRST_PASSING_ALL_GATES_ELSE_NO_QUALIFYING` |
| Status | `P3_PENDING` |
| `human_approved` | _unset in this draft_ |
| `approved_at` | _blank until authenticated attestation_ |
| Current pilot state | `NO_QUALIFYING_PILOT_SOURCE` |
| Authority state | `WARNING_PATH_AUTHORIZED: NO` |

These hashes are current byte-derived values that a future envelope
must carry. No approval envelope exists yet; none is claimed here.

*Annotated 2026-09-15:* the `P3_PENDING` row records this draft's
template state. For the D1/D2 pair it is superseded-by-design-attestation
per `P3_ATTESTATION_RECORD_V0.md` (owner-directed, `design_review_only`,
attested 2026-09-14). The D1/D2 documents' own frozen status text still
reads "pending human approval"; that text is likewise superseded for the
design-review scope by the same record — the frozen bytes are unchanged.

## Attestation string (must match `EXPECTED_ATTESTATION` exactly)

> I reviewed only the design documents identified by their sha256
> digests; this approval authorizes no data intake, no forecast
> execution, no warnings, and no production or authority action.

## Approver checklist (all required before attestation counts)

- [ ] I authenticated as the repository owner outside this Markdown file.
- [ ] I inspected both D1 and D2 at the exact paths listed above.
- [ ] I independently recomputed both SHA-256 values.
- [ ] I verified the values against this draft and any future envelope.
- [ ] I approve only `design_review_only`.
- [ ] I do not authorize downloads, intake, FMX freezing, clustering,
      forecast execution, or operational use.
- [ ] I acknowledge unresolved licenses, archive access, and
      real-data gates.
- [ ] I accept the pilot rule without preselecting a vertical.
- [ ] I accept the engineered dam-breach deferral.
- [ ] I provide the authenticated verification method and an external
      evidence reference without placing secrets in the repository.

## Source-resolution checklist (required order)

1. HiAVAL — exact version, license record, Nepal subset count,
   geography, redistribution rights, reviewer record.
2. HMAGLOFDB — RDS/Zenodo/repository license relationship for the
   exact release; Nepal count; lake-ID joins; recurrence grouping.
3. Sentinel-1 — Zenodo rights, Nepal/China extent split, scene-pair
   coverage, observation opportunities, timing class.
4. Forecast archives — per-provider registration/access,
   issue/init/valid/vintage fields, public-availability delay,
   cycle completeness, license, retrieval evidence.
5. Engineered dam-breach — owner-deferred:
   `DEFERRED_NO_OPEN_TIMED_SOURCE`.

## P5 acquisition-authorization section (required before any byte retrieval)

*Added 2026-09-18 (round-11 audit findings #6/#9).* No payload bytes
may be acquired for any candidate source until the designated owner
executes this section per source. Blank fields are deliberate: no
authorization exists until every field is filled and signed. A
completed P5 section authorizes bounded retrieval only — it is not a
pilot selection, not a qualification, and not an operational approval.

| Field | Value (source-specific scope prepared 2026-09-19; authentication pending) |
|---|---|
| Source + exact version + DOI | `icimod_hmaglofdb_v1_3_0`; v1.3.0; RDS DOI `10.26066/RDS.1973283`; Zenodo mirror `10.5281/zenodo.18257243`; concept DOI `10.5281/zenodo.7271187`; GitHub tag `v1.3.0` = tree `1d975de` |
| Licence scope | CC BY 4.0 governs (RDS declaration; GitHub `LICENSE`; Zenodo `cc-zero` tag superseded, conservatively read as CC BY 4.0). Attribution: cite ICIMOD per RDS terms; licence snapshot saved at retrieval |
| Evidence root | `/Users/sanjayb/nepal-event-anomaly-evidence/p5-glof-2026-09-19/` — fresh, absolute, outside the repository and outside `data/` |
| Storage reserve | abort below 8 GiB free on the evidence-root volume; pre- and post-write checks on every write |
| Retrieval limits | <= 1 GiB total, <= 40 requests, <= 1 concurrent, sequential, dry-run first; session inside `2026-09-19T00:00Z .. 2026-09-20T00:00Z` |
| Stop rules | halt and quarantine on any of: digest mismatch against the published MD5; licence change on either record; scope or version drift; storage floor reached; endpoint divergence; unrecordable linkage keys |
| Owner signature | `OWNER-PENDING` — authenticated owner name, date, and signature line: ______________________ |

**Scope detail.** `P5-A` HMAGLOFDB v1.3.0 event source (107,879-byte
archive, published MD5 `b6af9657ed28d793b058789835dd4ac8`). `P5-B`
optionally-supplied opportunity frame under Option A: ICIMOD 2015
potentially-dangerous-lake inventory, RDS DOI `10.26066/RDS.1971950`,
CC BY 4.0. `P5-C` multi-basin ERA5-Land over five predeclared anchors
(`koshi`, `gandaki`, `karnali`, `mahakali`, `bagmati`), JJA 2001-2025,
the seven existing pre-registered variables. `P5-D` a bounded four-node
`4W` qualification slice only.

The full field-by-field record, the anchor derivation rule, the
read-only evidence annex, and the exhaustive list of remaining
owner-only fields live in
`docs/science/P5_ACQUISITION_AUTHORIZATION_RECORD_V0.md`. That record
is the single authoritative field list; this table is its summary.

### Opportunity-frame policy question (owner decision required)

> May an independently byte-bound lake-inventory source supply the
> opportunity frame (with linkage + uncertainty recorded), or must
> opportunities be source-native?

- [x] Option A — an independently byte-bound lake-inventory source may
      supply the opportunity frame, provided linkage keys and
      uncertainty are recorded.
- [ ] Option B — opportunities must be source-native.

Owner decision: **Option A** (accepted by the owner 2026-09-19; see
`P5_ACQUISITION_AUTHORIZATION_RECORD_V0.md` P5-B). The opportunity
frame must therefore be externally byte-bound with linkage keys
(`GL_ID` / `LakeDB_ID` / `G_ID`) and a linkage-uncertainty ledger;
unlinked inventory lakes are recorded, never silently dropped. Until
the P5 record is signed, the frame is not acquired and remains
`PENDING-OWNER-POLICY`; Option A settles *admissibility*, not
acquisition.
