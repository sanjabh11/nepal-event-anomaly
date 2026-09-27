# India Phase-0 GLOF Feasibility Protocol v0

Status: implementation contract only; no acquisition authorization.

## Objective

Determine whether the Indian Himalaya supports either:

1. a defensible event-case weather analysis based on independent,
   day-dated, mechanism-adjudicated GLOF episodes; or
2. a separate lake-year/structural-susceptibility study with explicit
   observation completeness and verified controls.

Mapped lakes, catalog rows, independent episodes, monitored lakes, and
verified non-event lake-years are separate denominators. They must never be
added together or substituted for one another.

## Phase-0 inputs and outputs

The local-only machinery consists of:

- `scripts/india_event_crosswalk.py`, producing
  `INDIA_EVENT_CROSSWALK_V0`;
- `scripts/india_lake_frame.py`, producing `INDIA_LAKE_FRAME_V0`; and
- `scripts/india_feasibility_report.py`, producing
  `INDIA_FEASIBILITY_REPORT_V0`.

Each output binds its input bytes with SHA-256 and publishes through
exclusive-create JSON plus a sidecar. Existing source rows remain intact.
Every output carries an explicit all-false authority object covering bulk
acquisition, weather, satellite, seismic, forecast, warning, detector, odds,
causal, and operational authority.

## Event crosswalk rules

- Every India catalog row is retained.
- Rows begin as `UNREVIEWED` and `AWAITING_ADJUDICATION`.
- No lake alias, recurrence, cascade, mechanism, independence, or eligibility
  is inferred by the crosswalk.
- Placeholder lake IDs are reported but are not canonical identities.
- A future adjudication must be append-only and cite primary evidence. An
  event can count as an independent exact-day episode only when its successor
  record supplies a candidate episode ID, a resolved source or canonical lake
  identity, reviewer identity, `location_confirmed=true`, a typed mechanism
  plus `mechanism_certainty` of `CONFIRMED` or `PROBABLE`, evidence citations,
  and `independence_status=INDEPENDENT`. A catalog mechanism string alone is
  not adjudication.

## Lake-frame rules

- A mapped lake is not an event and is not a control by default.
- `UNKNOWN` observation status cannot become `VERIFIED_NON_EVENT` because a
  catalog contains no event.
- A verified non-event requires full observation completeness, an explicit
  at-risk interval, and evidence references.
- Full observation rows may report an explicit, unique `observed_years` list;
  only those years contribute to the observable-lake-year denominator.
- Cross-source lake identities remain unreconciled until explicitly matched.

## Gates

- No ERA5, ERA5-Land, IMERG, CHIRPS, satellite bulk archive, DEM bulk archive,
  or seismic waveform retrieval is authorized by Phase 0.
- Fewer than 10 independent eligible exact-day episodes closes the event-
  weather route after adjudication is complete.
- Ten to nineteen episodes permit descriptive-only consideration, not a
  replication or risk claim.
- Twenty or more episodes require a preregistered precision simulation before
  any weather canary.
- A 150-lake frame is insufficient without reliable observation histories and
  explicit control eligibility.

All authority flags remain false. The protocol does not authorize forecast,
warning, detector, operational, causal, or event-risk claims.
