# Open Distribution Note — v1

**Status:** `OWNER_RATIFIED` 2026-10-01 — supersedes v0 for the deposit
scope recorded below; all other v0 provisions unchanged.
**Supersedes:** `OPEN_DISTRIBUTION_NOTE_V0.md` (extends scope; does not
weaken any third-party exclusion).
**Scope:** adds a specific authorization — deposit of the project's own
authored evidence artifacts to Zenodo for the TC LESSONS Report
(egusphere-2026-5996). Nothing else changes.

## Clarification of v0 scope

`OPEN_DISTRIBUTION_NOTE_V0.md` §1–2 binds two things: (a) what is
publishable *from this repository*, and (b) third-party payload bytes
(GRIB/NetCDF retrievals, gated catalogues), which may never be redistributed.
The Zenodo deposit contains **only project-authored JSON artifacts**
(frozen plans, linkage decision records, candidate-trajectory lanes,
test-sequence results, gate-calibration outputs, audit, census) and the
project's own code/tests. No third-party payload bytes are included; the
Zhang inventory is cited via its own Figshare DOI
(`10.6084/m9.figshare.21708590.v1`), not re-hosted. The deposit is
therefore a *new distribution act by the owner*, outside v0's repository
scope — this note records the authorization explicitly.

## Authorized deposit contents

`/Users/sanjayb/nepal-event-anomaly-evidence/manuscript/zenodo-deposit/payload/`
— 92 files enumerated in `ZENODO_DEPOSIT_MANIFEST.json` with recomputed
SHA-256 digests; `all_sidecars_match: true` at staging. Every sealed
artifact retains its `.sha256` sidecar inside the deposit.

- JSON evidence artifacts (`plans__`, `linkage__`, `v2-lane__`, `v3-lane__`,
  `calibration__`, `audit__`, `census__`): licensed **CC BY 4.0**.
- Python sources (`code__`, `tests__`): **MIT**, per
  `PROJECT_LICENSE_DECISION_V0.md`.
- Zenodo record carries both licences; the record description states the
  split.

## Still excluded (unchanged from v0)

- All third-party payload bytes: ERA5/ERA5-Land/ERA5T-class, GRIB/NetCDF,
  TIGGE/S2S/MARS, Earthdata, request-gated files, ICOLD WRD, HMAGLOFDB CSV.
- The Figshare inventory itself (cite, do not re-host).
- `restricted-backups/`, `_glmdrift-audit/`, and anything under `data/`
  containing retrieved bytes.

## Standing rules (unchanged)

- Write-once evidence discipline: sealed artifacts and sidecars are never
  edited; supersession is by new versioned files only.
- `warning_path_authorized=false`; `production_authorized=false`;
  `promotion_eligible=false` — the deposit makes no forecast, warning,
  detector, causal, event-risk, or operational claim.
- Scientific posture: candidate paths are not verified lake identities;
  event association is DORMANT; descriptive research only.
