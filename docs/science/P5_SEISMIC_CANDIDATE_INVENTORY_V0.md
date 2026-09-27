# P5 seismic candidate inventory V0

**Status: candidate inventory only — not authorization, not acquisition, and
not evidence verification.** This note records bounded source and event
selection inputs for a future Option 3 decision. It does not retrieve
StationXML, MiniSEED, catalog rows, or waveform bytes. It does not admit
ObsPy, select a final station, or change the `NOT_REQUESTED` seismic state.

Prepared 2026-09-22. The network table below transcribes the earlier
repository metadata qualification in
`docs/science/SEISMIC_SOURCE_QUALIFICATION_V0.md`; it is not a fresh metadata
probe. The official service links are retained as future probe surfaces only.

## Current decision boundary

The active Option 3 posture is a one-station, retrospective observability
sidecar. A one-station result can only support the allowed observability and
candidate-anomaly statuses. It cannot become a detector, locator, predictor,
warning, production, or operational claim. The earlier qualification note
recommended the `4W` network for a different multi-station floor. That
recommendation must not be silently substituted into the current one-station
estimand: a future amendment must name one exact station and explain which
station-count assumptions are no longer applicable.

## Candidate waveform sources

| Candidate | Prior metadata indication | Current disposition | What must still be proved |
|---|---|---|---|
| `4W` dense nodal deployment | 321 stations; `DP1/DP2/DPZ`; 500 Hz; station epochs in 2022–2023 | Candidate network only; not a one-station selection | Exact station, event/window overlap, channel response, licence, byte availability, and MiniSEED encoding |
| `XF` historical transect | 80 stations; 2002–2005 | Candidate network only; era alignment is unresolved | Exact station epoch and alignment with the chosen event/window |
| `XQ` mixed deployment | 45 stations; mixed `BH/EH/HH/LH/SH/VM` bands and rates | Lower-priority candidate; channel coherence is unresolved | One exact station/channel set with a consistent rate and complete response |
| `NQ` Nepal national network | Three stations in the prior corridor note (`KATNP`, `KNSET`, `KTNP2`) plus wider network | Candidate network only; no station selected | Open-data terms, exact station epoch, response, and event-window coverage |
| `NK.KKN` | One station; `BHE/BHN/BHZ`; 50 Hz; 2016 onward | Possible one-station candidate, but not a multi-station-floor source | Owner must explicitly accept one-station limitations; bytes and response remain unverified |

The prior table's numbers are planning context, not current evidence. In
particular, metadata availability is not waveform availability, and a station
epoch is not proof that every requested sample exists.

## Candidate event and catalog surfaces

No event ID is selected in this note. The existing adjudicated Melamchi
2021-06-14 GLOF remains a possible retrospective anchor in the broader P5
history, but it is not a seismic catalog event and was excluded from the
previously authorized 2023-04-01–2023-05-09 preflight window. Reusing it would
require a new owner amendment with an explicit event/window decision.

The following are candidate catalog surfaces for a future metadata-only event
selection pass:

| Surface | Intended use | Status here |
|---|---|---|
| [USGS FDSN Event service](https://earthquake.usgs.gov/fdsnws/event/1/1) | Query a bounded Nepal/Himalaya region and time interval; retain event ID, origin time, location, magnitude, and catalog provenance | Documentation reviewed; no query executed |
| [ISC Bulletin search](https://www.isc.ac.uk/iscbulletin/search/) | Independent reviewed/unreviewed event cross-check and QuakeML/CSV event metadata | Documentation reviewed; no query executed |
| [EarthScope FDSN Station service](https://service.earthscope.org/fdsnws/station/1/) | Metadata-only station/channel/epoch/response probe; StationXML is the required metadata form for response interpretation | Documentation reviewed; no query executed |
| [EarthScope FDSN web-services overview](https://service.earthscope.org/fdsnws/) | Separate station metadata from dataselect waveform retrieval; keeps future acquisition stages explicit | Documentation reviewed; no query executed |

The USGS service documents ISO-8601 time and geographic query parameters.
The ISC surface distinguishes reviewed from not-yet-reviewed bulletin data.
The EarthScope station service returns StationXML or derivative metadata, while
dataselect is a separate waveform surface. These facts support the staged
selection protocol; they do not qualify a Nepal station or event by
themselves.

## Required future selection record

Before any acquisition, the owner-approved Option 3 record must bind:

1. one event or an explicitly approved non-event estimand;
2. `source_id`, catalog name/version, event ID, origin UTC, location,
   magnitude, and an independent timing source;
3. one exact `NET.STA.LOC.CHA` station/channel set and provider;
4. station epoch, response availability, sample rate, component orientation,
   and open-data/licence interpretation;
5. the bounded window (maximum 24 hours), storage reservation, and evidence
   root;
6. the decoder environment decision and complete execution-digest chain;
7. the allowed terminal-status vocabulary and the observability-only claim
   ceiling.

The metadata probe must stop if the event/window has no station overlap, the
station response is incomplete, storage falls below the reserved margin, or
the owner has not admitted the decoder environment. A successful metadata
probe is still not waveform evidence; byte-bound intake and independent event
timing verification remain later gates.

## Non-authorizations

- No FDSN request was executed while preparing this note.
- No StationXML, MiniSEED, event catalog, or waveform bytes were acquired.
- No station, event, provider, ObsPy environment, or storage exception is
  approved by this document.
- Seismic remains isolated from the GLOF predictor and all authority flags
  remain false.
