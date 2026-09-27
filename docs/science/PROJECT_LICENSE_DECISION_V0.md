# Project License Decision — v0

**Status:** LICENSE_DECISION_RECORD — recorded 2026-09-27.
**Boundary:** this record documents a decision; it authorizes nothing
new (no acquisition, no redistribution of third-party bytes, no
scientific claim).

## Decision

The repository's own work — source code, tests, protocol and decision
documents, schema surfaces, and derived summaries authored by this
project — is released under the **MIT License** (`LICENSE` at the
repository root).

## Why MIT

- The repository is public and intended for review and reuse of its
  governance machinery; an explicit permissive license replaces the
  default-copyright posture of a license-less public repository.
- MIT is the minimal-friction choice compatible with the repository's
  stated open-distribution intent (`OPEN_DISTRIBUTION_NOTE_V0.md`).
- The owner retains the right to supersede this decision in a later
  versioned record; a license change applies prospectively only.

## Explicit exclusions (binding)

The MIT grant **does not** extend to third-party material merely present
in or referenced by the repository:

- `data/dem_n28e085.tif` — Copernicus DEM GLO-30 tile; governed by
  Copernicus terms (see `TRACKED_DATA_PROVENANCE_V0.md`).
- `data/temp/era5_land_2001_06.nc` — raw CDS retrieval bytes for
  ERA5-Land; governed by the Copernicus license.
- Any external evidence payloads bound by digest only — they remain
  under their own licenses and access terms (see
  `OPEN_DISTRIBUTION_NOTE_V0.md` and `INDIA_INVENTORY_REGISTRY_V1.json`).

Project-generated ledgers and logs (`data/download_ledger.json`,
`data/era5_download_log.txt`, `data/nisar_catalog_ledger.json`) are
project work covered by the MIT license; they contain no third-party
payload bytes.

## Standing rule

No new third-party payload bytes may be committed until cleared through
the per-artifact review recorded in `TRACKED_DATA_PROVENANCE_V0.md`.
