# Seismic Event Detection Addendum — v0 (Run B sidecar)

**Status:** `DESIGN_DRAFT_COMPLETE` — architecture and metadata
qualification only.
**Role:** a separate research track for detecting abnormal seismic change
after an event has started. This addendum does not select a pilot, authorize
retrieval, reopen the weather/GLOF preregistration, or change any existing
source decision.
**Posture:** `NO_QUALIFYING_PILOT_SOURCE`; `WARNING_PATH_AUTHORIZED: NO`.

This is a sidecar design. The current Nepal thin-PoC remains a retrospective
ERA5-Land weather/thermal experiment. No seismic predictor, waveform loader,
station adapter, or seismic feature block is part of that implementation.

## 1. Current surfaces and boundary

The current Nepal implementation contains the following seismic-related
surfaces, none of which is a seismic predictor:

| Surface | Role now | Boundary |
|---|---|---|
| `nepal/feature_contract.py` and `nepal/feature_extraction.py` | ERA5-Land temperature, dew point, precipitation, snowfall, snow depth, wind, humidity and thermal features | No seismic columns or waveform input |
| `nepal/research_v0/records.py` | `landslide_coseismic` and `slope_initiation_coseismic` ontology values | Label/trigger vocabulary only |
| `usgs_gorkha_2015_landslides` | Coseismic trigger-window labels and controls | Trigger-specific role; not a continuous waveform source |
| `nepal/framework_v1/extensions.py` | Frozen precursor boundary | Explicitly has no seismic dependency |

The earlier T2A PoC has two different seismic surfaces:

1. `backend/common/seismic_integrator.py` queries the USGS FDSN event catalog
   and parses event metadata: event ID, magnitude, origin time, latitude,
   longitude, depth and place. It applies a risk-amplification post-processor
   to the T2A grid. This is catalog context, not continuous ground motion and
   not a GMM waveform detector.
2. `backend/common/geophone_spectral.py` accepts raw voltage samples and
   computes FFT/PSD-derived features. It is default-disabled and has no live
   station stream, response-metadata binding or production caller in T2A.

The T2A catalog bounding box and hard-coded lag/amplification values are
implementation choices for its northwestern-Himalaya experiment. They are not
validated Nepal/Langtang parameters and must not be copied into this sidecar.

## 2. Scientific objective

The proposed objective is **post-initiation event detection**: identify a
change in seismic behavior near a monitored slope or hazard corridor, then
describe the associated cluster or anomaly. It is not a cause classifier, a
general earthquake-to-landslide predictor, or a calibrated probability model.

The customer recommendation in the T2A meeting record is consistent with
this narrower objective: rare-event cause labels are sparse, while a known
event time and location can support a retrospective signal-detection study.

Published evidence supports detectability but does not establish a universal
detector. A Chamoli study reported continuous seismic signals before one
rock-ice avalanche using proximal stations ([Scientific Reports](https://www.nature.com/articles/s41598-022-07491-y)). A Blatten study used
unsupervised clustering on 20 days of three-component data from one nearby
station, while noting tuning, real-time use and generalization limits
([GRL](https://agupubs.onlinelibrary.wiley.com/doi/10.1029/2025GL121175)).
Feature transfer depends on station position, propagation and signal-to-noise
conditions ([GJI](https://academic.oup.com/gji/article/237/2/1189/7629152)).

Accordingly, seismic evidence must remain a separate channel. A seismic
anomaly is not by itself a community decision; any future downstream alert
would require an independent impact, routing, authority and communication
layer.

## 3. Candidate source classes

The USGS event service supplies event-catalog metadata through a query API
([USGS Event API](https://earthquake.usgs.gov/fdsnws/event/1/1)). Continuous
waveform analysis requires a waveform service and station response metadata,
not merely an event-catalog response ([FDSN dataselect](https://service.earthscope.org/fdsnws/dataselect/docs/1/help/), [FDSN Station](https://service.iris.edu/fdsnws/station/docs/1/help/)).

| Candidate | Evidence class | Permitted research role | Qualification state |
|---|---|---|---|
| USGS Event Catalog | Earthquake event metadata | Context, earthquake-window baseline, nearest-event magnitude/distance/age | `CANDIDATE_ONLY`; not waveform evidence |
| FDSN Nepal/NK and National Seismological Centre stations | Continuous waveform plus StationXML where exposed | Candidate seismic feature source | `CANDIDATE_ONLY`; target-corridor coverage and access unverified |
| Raspberry Shake/FDSN stations | Continuous waveform where the station and response are available | Candidate supplementary station source | `CANDIDATE_ONLY`; station-specific continuity and redistribution terms required |
| Other open FDSN providers | Continuous waveform plus response metadata | Candidate only after station-level review | `CANDIDATE_ONLY` |

Nepal network listings and national resources make a waveform path plausible,
but they do not prove coverage at a selected slope or date ([FDSN Nepal network](https://www.fdsn.org/networks/detail/NK/), [Nepal National Earthquake Monitoring resources](https://seismonepal.gov.np/en/pages/resources-5109)). No source is `EVIDENCE_VERIFIED` from metadata alone, and no waveform payload bytes have been acquired for this sidecar.

## 4. Observability gate

Before any seismic feature is generated, the source must provide an
evidence-bound record for each station and channel:

- network, station, location and channel identifiers;
- distance from the declared target corridor;
- three-component availability and orientation;
- sample rate, timing quality and response metadata (StationXML or equivalent);
- continuous coverage interval, gaps and retrieval latency;
- noise-floor/SNR evidence for the chosen bands;
- access, licence and redistribution terms;
- exact retrieved bytes, source manifest and SHA-256 sidecar.

If the station set, response, continuity or SNR is inadequate, the result is
`UNOBSERVABLE`. Missing seismic coverage must never be silently represented as
zero energy or a negative event.

The owner must choose the target corridor and provide P5 authorization before
any payload retrieval. This document does not authorize network access.

## 5. Proposed sidecar feature design (post-P5 only)

The feature contract below is a design candidate, not an implementation
commitment. It must be frozen only after station sampling, response handling
and model cadence are verified.

### Waveform-derived block

Candidate features per fixed window are:

- RMS/RSAM and STA/LTA or event-rate measures;
- band energies, spectral centroid, median/dominant frequency and entropy;
- kurtosis, crest factor, duration and SNR;
- station coverage and gap fraction;
- optional cross-station coherence/correlation when multiple calibrated
  stations are available.

Raw waveform bytes remain in the evidence root. The normal model frame carries
only versioned semantic features with separate raw-byte and semantic digests.
One-minute internal windows are a starting hypothesis; aggregation to hourly
rows is allowed only after the source cadence and coverage support it.

### Catalog-context block

Catalog context is kept separate from waveform features:

- event count in a declared window;
- nearest event magnitude and distance;
- elapsed time since the catalog origin.

Catalog context must never be described as a precursor signal.

### Evaluation

The first comparison is seismic-only versus weather-only versus late fusion.
The regime engine may be reused through `ColumnAudit`, `audit_matrix`,
`run_regimes`, source manifests and the existing research-only receipt.
Required controls include a K=1 null, a declared K sweep, at least three
seeds, train-only standardization, and time/station/basin holdouts.

Outputs remain descriptive cluster/anomaly artifacts with statuses such as
`DESCRIPTIVE_REGIME_ONLY` or `UNDERPOWERED_DESCRIPTIVE_ONLY`. No forecast,
warning, production or promotion status may be emitted by this sidecar.

## 6. Phased execution gates

### Phase A — documentation and metadata qualification

This addendum may be reviewed without payload retrieval. The current Nepal
feature contract, `gmm_descriptive.py`, `framework_v1`, and
`preregistration.md` remain unchanged.

### Phase B — owner authorization

An authenticated P5 record must name the source/version, evidence root,
storage reserve, retrieval limits, stop rules, reviewer scope and target
corridor. Without it, the sidecar remains metadata-only.

### Phase C — bounded waveform intake

After P5, acquire only approved bytes, bind StationXML and waveform files,
verify paths/symlinks/digests/timestamps, and produce the observability record.
Stop with `UNOBSERVABLE` if any required station or response gate fails.

### Phase D — sidecar benchmark

Run seismic-only, weather-only and late-fusion retrospective comparisons. Keep
the result non-promotable and report event coverage, negative windows, station
gaps, SNR, holdout behavior and all demotions.

## 7. References and implementation boundary

- Customer recommendation: `docs/MVP4/01_customer_review/meeting_minutes/MoM_2026-09-18.md` in the T2A repository.
- T2A catalog comparison: `backend/common/seismic_integrator.py`.
- T2A spectral comparison: `backend/common/geophone_spectral.py`.
- Nepal ontology: `nepal/research_v0/records.py`.
- Nepal frozen exclusion: `nepal/framework_v1/extensions.py`.

This addendum creates no new source qualification, does not change the pilot
outcome, and does not authorize acquisition or downstream alert use.
