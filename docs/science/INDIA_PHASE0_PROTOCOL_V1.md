# India Phase-0 GLOF Feasibility Protocol v1

**Status:** implementation contract only; no acquisition authorization.
**Supersedes (semantics):** `INDIA_PHASE0_PROTOCOL_V0.md` — the v0 document
remains unchanged as a historical record; v1 records the hardened
decision machinery without altering any v0 bytes.

## Objective

Unchanged from v0: determine whether the Indian Himalaya supports either

1. a defensible event-case weather analysis based on independent,
   day-dated, mechanism-adjudicated GLOF episodes; or
2. a separate lake-year/structural-susceptibility study with explicit
   observation completeness and verified controls.

Mapped lakes, catalog rows, adjudicated episodes, independent episode
clusters, canonical lakes, monitored lakes, and verified non-event
lake-years are separate denominators. They must never be added together
or substituted for one another.

## Phase-0 inputs and outputs

The local-only machinery consists of:

- `docs/science/INDIA_INVENTORY_METADATA_V0.json` (retained unchanged)
  and its successor `docs/science/INDIA_INVENTORY_REGISTRY_V1.json`,
  pinned source metadata with versions, access terms, file formats,
  and payload-digest slots — no row-level ingestion yet;
- `docs/science/INDIA_EVIDENCE_REGISTER_V0.json`, the resolvable-evidence
  surface (see "Evidence" below);
- `scripts/india_event_crosswalk.py`, producing `INDIA_EVENT_CROSSWALK_V0`;
- `scripts/india_lake_frame.py`, producing `INDIA_LAKE_FRAME_V0`;
- `scripts/india_event_adjudication.py`, producing the append-only
  `INDIA_EVENT_ADJUDICATION_V0` intake/decision record; and
- `scripts/india_feasibility_report.py`, producing
  `INDIA_FEASIBILITY_REPORT_V0`.

Each output binds its input bytes with SHA-256 and publishes through
exclusive-create JSON plus a `.sha256` sidecar. Every output carries an
explicit all-false authority object.

## Decision-machinery hardening (v1)

- **Input verification.** The feasibility report fails closed unless
  every input (crosswalk, lake frame, adjudication, evidence register)
  passes its own validator *and* matches its `.sha256` sidecar.
  Input-shape checks alone are not accepted.
- **Denominators.** Episode denominators count *distinct*
  `candidate_episode_id` values, not rows; lake denominators count
  *canonical* lakes, and any unreconciled lake identity blocks the lake
  screen (`IDENTITY_RECONCILE_REQUIRED`) rather than inflating the count.
  Records sharing a candidate episode id must agree on
  `independence_status`; the adjudication validator rejects inconsistent
  reuse.
- **Evidence.** A citation string is attribution only. Eligibility and
  control counting require at least one `evidence:<id>` citation that
  resolves to a `BYTES_VERIFIED` register record (pinned locator +
  SHA-256) whose declared temporal coverage contains the claimed
  interval. `METADATA_VERIFIED` records prove a source's terms exist;
  they never satisfy evidence checks. Reviewer ids are attribution
  strings, not authenticated identities or cryptographic signoff.
  Eligible-but-unverified rows are counted and gated separately
  (`UNVERIFIED_EVIDENCE_PRESENT` / `VERIFY_EVENT_EVIDENCE`).
- **Territory.** Numeric coordinates are never a territory
  classification. Event records carry `territory_status` in
  `{IN_COUNTRY, OUTSIDE, UNCERTAIN, UNASSESSED}` assigned at
  adjudication against a declared boundary source, version, and CRS
  (the adjudication document's `geography` block); eligibility requires
  `IN_COUNTRY`. Lake-frame records carry the same taxonomy.
- **Identity.** Lake identities carry `identity_status` in
  `{UNRECONCILED, RECONCILED, UNRESOLVED_CONFLICT}`; a `RECONCILED`
  record must name a `canonical_lake_id`, and canonical ids may merge
  multiple source rows (aliases, recurrences, splits/merges are resolved
  at reconciliation, never silently).
- **Observation completeness.** `FULL` completeness is a coverage claim,
  not a label: it requires an explicit `observed_years` list, and when an
  at-risk interval is declared the list must contain every year of that
  interval. A monitored-portfolio membership is not a verified non-event
  history. Verified controls additionally require resolvable
  `BYTES_VERIFIED` evidence covering the at-risk interval.
- **Screens remain heuristics.** The 150-lake and 10/20-episode bands are
  workflow screens, not power guarantees; the 150-lake value is never
  equivalent to 150 positive events. A preregistered, cluster-aware
  precision simulation remains a separate required input before any
  event-weather canary.

## Event crosswalk rules (unchanged in v1)

- Every India catalog row is retained.
- Rows begin as `UNREVIEWED` and `AWAITING_ADJUDICATION`; territory is
  `UNASSESSED`.
- No lake alias, recurrence, cascade, mechanism, independence, or
  eligibility is inferred by the crosswalk.
- Placeholder lake IDs are reported but are not canonical identities.
- A future adjudication must be append-only and cite resolvable primary
  evidence. An event can count as an independent exact-day episode only
  when its successor record supplies a candidate episode ID, a resolved
  source or canonical lake identity, reviewer attribution,
  `location_confirmed=true`, `territory_status=IN_COUNTRY`, a typed
  mechanism plus `mechanism_certainty` of `CONFIRMED` or `PROBABLE`,
  register-resolvable evidence citations, and
  `independence_status=INDEPENDENT`. A catalog mechanism string alone is
  not adjudication.

## Gates

- No ERA5, ERA5-Land, IMERG, CHIRPS, satellite bulk archive, DEM bulk
  archive, or seismic waveform retrieval is authorized by Phase 0.
- Unreviewed rows keep the event screen at `ADJUDICATION_INCOMPLETE`.
- Verified-evidence gaps keep `next_gate` at `VERIFY_EVENT_EVIDENCE`
  before simulation is considered.
- Fewer than 10 verified independent eligible exact-day episodes closes
  the event-weather route after adjudication completes.
- Ten to nineteen permit descriptive-only consideration.
- Twenty or more require a preregistered precision simulation before any
  weather canary.
- The lake screen requires resolved canonical identities before any
  size threshold is consulted.

All authority flags remain false. The protocol does not authorize
forecast, detector, operational, causal, or event-risk claims.
