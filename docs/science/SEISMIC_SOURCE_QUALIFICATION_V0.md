# SEISMIC_SOURCE_QUALIFICATION_V0 — GLM3 read-only metadata qualification

Scope: metadata-only qualification of FDSN seismic sources for the
Nepal post-initiation detection sidecar.  **No waveform or StationXML
payloads were retrieved; no P5 authorization is inferred; no source
is EVIDENCE_VERIFIED.**  Every verdict below is `PAYLOAD-GATED` until
byte-bound intake plus two independent intake reviews and
adjudication.

Query surface (read-only):
`service.earthscope.org/fdsnws/station/1/query` (station + channel
levels), bounded to `27.0–29.0 N / 84.0–86.5 E`, queried 2026-09-19.

## Network inventory (bounded corridor)

| Network | Stations | Channels / rates | Epoch | Notes |
|---|---|---|---|---|
| `4W` | **321** | `DP1/DP2/DPZ` @ **500 Hz** (rotated horizontals) | 2022-10-31 → 2023-05-09 (per-station windows ~2–8 weeks) | Dense nodal deployment — the only source that trivially satisfies the ≥3-trained-stations + station-holdout floor |
| `XF` | 80 | broadband transect | 2002–2005 | Historical array along the corridor; era mismatch with modern event inventories |
| `XQ` | 45 | `BH/EH/HH/LH/SH/VM` mixed bands @ 20–500 Hz | 2015-06 → 2016-05 | Mixed per-station rates — a coherent channel set must be selected per station under the consistent-rate policy |
| `NQ` | 3 in-corridor (`KATNP`, `KNSET`, `KTNP2`); wider network beyond | `HN*` @ 200 Hz | 2011 → | Nepal national network (Kathmandu-centric); open-ended |
| `NK` | 1 (`KKN`) | `BHE/BHN/BHZ` @ 50 Hz | 2016-05 → | Single station — cannot satisfy multi-station floors alone |
| `EM`, `SY`, `YL` | 11 | mixed | various | EM 7-day 2001 deployment; SY synthetic; YL legacy |

## Candidate assessment

| Candidate | Gate | Verdict | Reason |
|---|---|---|---|
| `4W` subarray | station count ≥ 3 trained + 1 held | **PASS** (metadata) | 321 stations |
| `4W` component layout | rotated `DP1/DP2/DPZ` | **PASS** | `ROTATED_3C` is admissible; component-averaged energy is orientation-agnostic |
| `4W` sample rate | Nyquist vs declared bands (≤20 Hz) | **PASS** | 500 Hz |
| `4W` per-station continuity | coverage inside 2022-11→2023-05 | **PAYLOAD-GATED** | per-station spans vary; actual sample coverage unknown until bytes |
| `4W` StationXML response | channel-level response metadata | **PAYLOAD-GATED** | channel metadata exists; response stage completeness unverified until bytes |
| `4W` licence/redistribution | research reuse | **OWNER-GATED** | FDSN archive terms need owner review in the P5 record |
| `XQ` channel coherence | consistent rate + component set | **FAIL (complexity)** | mixed 20–500 Hz bands per station; selection overhead high for a first PoC |
| `NK.KKN` | station-count floor | **FAIL** | single station |
| `XF` transect | era alignment | **PAYLOAD-GATED** | 2002–2005 only — pre-dates the event inventory era |

## Bounded recommendation for P5

**`4W` network, stations within the declared target corridor,
window 2022-11-01 → 2023-05-09, channels `DP1/DP2/DPZ`, RAW_COUNTS
mode.**  Rationale: the only candidate that satisfies the
multi-station regime floors from metadata alone.  Retain `NQ`/`NK`
as adjunct context sources (not predictor stations).

Outstanding before `EVIDENCE_VERIFIED`:

1. P5 record: exact station list, window, licence interpretation,
   evidence root, retrieval limits, two reviewers + adjudicator.
2. `dataselect` availability probe (metadata, post-P5) to confirm
   payload existence per station/window.
3. Byte-bound intake + StationXML content validation per
   `nepal/seismic_sidecar/io.py`.
4. miniSEED encoding reality-check: `DP` channels are typically
   STEIM-compressed — the optional `obspy` decoder seam must be
   resolved (S17) or the source cannot decode natively.

Posture unchanged: `DESIGN_DRAFT_COMPLETE`,
`NO_QUALIFYING_PILOT_SOURCE`, `WARNING_PATH_AUTHORIZED: NO`.
Seismic input is not a Nepal predictor.
