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
