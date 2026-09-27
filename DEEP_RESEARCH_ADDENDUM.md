# Deep-Research Addendum: Multi-Event Validation & Forecasting Feasibility

> **Purpose:** This addendum extends the Nepal Event Cross-Validation Addendum with
> (1) ecc-advisor-verified conclusions from the completed single-event analysis,
> (2) a pre-registered multi-event validation protocol, and
> (3) a structured next-step research prompt for external deep-research engines.
>
> **Status:** Post-hoc addendum to a FROZEN pre-registration. All additions are labeled as post-hoc.
> **Date:** 2026-09-10
> **Advisor:** Grok 4.6 via ecc-advisor CLI (call #4, 2026-09-10)

---

## 1. Ecc-Advisor Verified Conclusions

### 1.1 What We Found (Single-Event, Langtang 2026)

| Analysis | Result | Significance | Advisor Verdict |
|----------|--------|-------------|-----------------|
| Z-score (T2m) | Max \|z\|=1.97 | Below threshold of 2.0 | Confirmed null |
| Z-score (PDD) | Max \|z\|=1.98 | Below threshold of 2.0 | Confirmed null |
| All-years ranking | 2026 ranks #6 of 24 years (79th pctile) | 5 years had higher z-scores | **Strengthened null** |
| Block permutation | T2m p=0.057-0.08, PDD p=0.082-0.117 | Not significant at p<0.05 | Confirmed null |
| GMM regime shift | JS=0.6168 (87.7th percentile) | 12.3% FPR at event threshold | **Reject as forecasting tool** |
| Isolation Forest | 0 anomalies in 7 days | Underpowered for 7-day window | **Drop — underpowered** |
| CUSUM PDD | Change-point Aug 24 | 13 total change-points in JJA | High FPR, not actionable |
| EVT return period | T2m RP=1.3y, PDD RP=1.8y | Not extreme | **Confirmed not extreme** |
| Cumulative PDD (14d) | z=1.93, 100th pctile | Highest in 25 years but z<2 | Borderline, not significant |
| Cumulative PDD (21d) | z=1.95, 96th pctile | Borderline | Not significant |
| Negative controls | 2023 had \|z\|=2.12 (higher than 2026) | Event indistinguishable from non-event years | **Valid argument against thermal-only** |

### 1.2 Advisor-Verified Conclusion

> **This event's pre-failure thermal state is not separable from ERA5-Land daily
> monsoon variability at this grid cell.** The 899m elevation gap (~6K bias) is
> a fundamental limitation for this grid. Simple lapse-rate extrapolation is not
> reliable in Himalayan complex terrain (inversions, glacier surfaces ERA5-Land
> does not resolve). Treat uncorrected T2m as a lower-elevation proxy only.

### 1.3 What the Advisor Said NOT to Claim

- Do NOT overclaim "thermal-only ERA5-Land daily data is insufficient for forecasting"
- Do NOT claim the single-event null proves the data class is insufficient
- DO claim: "this event's pre-failure thermal state is not separable from ERA5-Land daily monsoon variability at this cell"
- DO proceed to multi-event validation with a pre-registered protocol

### 1.4 Advisor-Recommended Additional Analyses (Applied)

| Fix | What We Did | Result |
|-----|------------|--------|
| Rank 2026 vs ALL years | Ranked 24 JJA years by max \|z\| | 2026 is #6 of 24 (79th pctile) — 5 years had higher z |
| Cumulative PDD 2-6 weeks | Computed 14/21/30/42-day cumulative PDD | Max z=1.95 at 21 days — still not significant |
| EVT/return periods | Fitted GEV to annual maxima | T2m RP=1.3y, PDD RP=1.8y — not extreme |
| Drop Isolation Forest | Documented as underpowered for 7-day windows | Removed from primary analysis |
| Neighborhood cells | Deferred to multi-event phase | Requires re-download with larger domain |

---

## 2. Multi-Event Validation Protocol (Pre-Registered)

### 2.1 Database

- **Source:** Bashkova & Rupper (2026), Global database of glacier failures (1900-2025)
- **DOI:** 10.5281/zenodo.19477908
- **Total events:** 502 (228 glaciers, 11 RGI regions)
- **HMA events (RGI 13/14/15):** 112 total, 57 post-2000
- **JJA events (ERA5 era):** 14 with parseable dates

### 2.2 Pre-Registered Protocol

For each of the 14 JJA HMA events:

1. **Download:** ERA5-Land daily (CDS API) for the event cell ±0.2°, JJA of (event_year - 25) through event_year
2. **Feature extraction:** Same as Langtang (t2m, d2m, u10, v10, tp → wind_speed, wind_dir, RH, PDD)
3. **Z-score:** Same-calendar-day ±3 day window, baseline = all years before event year
4. **Percentile:** 95th and 99th ranking
5. **Cumulative PDD:** 7, 14, 21, 30, 42-day windows
6. **EVT:** GEV return period for T2m and PDD
7. **GMM:** Fit on baseline, compute JS distance for pre-event 7-day window
8. **No post-hoc method shopping:** If a method is not in this protocol, it is not added

### 2.3 Pre-Registered Thresholds

- **Z-score:** \|z\| > 2.0 (pre-registered)
- **Percentile:** > 95th (pre-registered)
- **Block permutation:** p < 0.05 (pre-registered)
- **GMM JS distance:** > 0.6168 (event threshold, with known 12.3% FPR)
- **EVT return period:** > 5 years (advisor-recommended)
- **Cumulative PDD:** \|z\| > 2.0 (pre-registered)

### 2.4 Pre-Registered Success Criteria (Updated per Advisor Call #5)

**Advisor feedback:** TPR>70% / FPR<20% on n=14 is underpowered and ill-posed (FPR needs a defined non-event set). 10/14 has a CI ~42–88%. Pre-register procedure, report CIs — not a binary gate.

**Updated criteria:**
- Report TPR with Wilson 95% confidence intervals (not a binary gate)
- Report FPR with a defined non-event set (matched JJA windows from non-event years)
- Report ROC AUC with bootstrap confidence intervals
- Pre-register procedure (bias correction, locked event list, non-event sampling, LOO/pooled threshold, climatology/PDD baseline)
- **If TPR < 50% with CI upper bound < 70%** → thermal-only ERA5-Land daily is insufficient
- **If TPR > 70% with CI lower bound > 50%** → thermal signal is detectable, proceed to Phase 2

### 2.5 Pre-Registered Failure Criteria

If the majority of events show null results (like Langtang):
- Conclude: "ERA5-Land daily thermal data alone is insufficient for forecasting HMA glacier failures"
- Pivot to: deformation + thermal integration (Sentinel-1 InSAR + ERA5-Land)
- Document: "A null result across multiple events is a valid scientific finding — it bounds the observability of thermal precursors in ERA5-Land daily data"

---

## 3. Deep-Research Prompt for External Engines (Narrowed per Advisor)

The following prompt has been narrowed to 3 questions per advisor feedback
(lapse-rate correction, event taxonomy, precursor deformation coverage):

```markdown
# Deep-Research Request: Himalayan Glacier Failure Forecasting Feasibility

## Context

We conducted a retrospective anomaly assessment of the 26 August 2026 Langtang Lirung
ice-rock avalanche (Nepal) using ERA5-Land daily reanalysis data. Our analysis found
that the pre-event thermal conditions were NOT separable from normal monsoon variability:

- Z-score: max |z|=1.97 (below threshold of 2.0)
- 2026 ranks #6 of 24 JJA years by max |z| (79th percentile)
- EVT return period: T2m=1.3 years, PDD=1.8 years (not extreme)
- GMM regime shift: JS=0.6168 (87.7th percentile, 12.3% false positive rate)
- Negative control (2023) had higher z-scores than the event year
- The 899m elevation gap between ERA5-Land model elevation (4322m) and source
  elevation (5221m) creates ~5.8K bias (source zone was ~2.1°C, near freezing)
- PDD at model elevation was inflated 3.7x compared to lapse-corrected source

This was cross-verified by an independent advisor model (Grok 4.6), which confirmed
the null result and recommended multi-event validation with bias correction first.

## Database Available

The Bashkova-Rupper Global Glacier Failure Database (2026) contains:
- 502 documented glacier failure events (1900-2025)
- 112 in High Mountain Asia (RGI regions 13/14/15)
- 57 post-2000 events (ERA5 era)
- 14 JJA events with parseable dates, locked with failure-mode labels:
  - 5 glacier detachments, 4 ice avalanches, 4 rock-ice avalanches, 1 lake impact
  - 10/14 triggered by meltwater, 1 by precipitation, 1 by surge, 2 unknown

## Three Questions for Deep-Research Engines

### Q1: Lapse-Rate Correction for ERA5-Land in HMA

Our ERA5-Land grid cell is at 4322m model elevation, but the failure source zone
is at 5221m — an 899m gap creating ~5.8K bias with standard lapse rate (-6.5 K/km).

- What is the state of the art for lapse-rate correction in HMA complex terrain?
- How do Khadka et al. (2022) and the 2025 ERA5-Land bias correction study
  (DOI: 10.1016/j.ejrh.2025.103079) handle this elevation mismatch?
- Is a single lapse rate adequate, or do we need spatially/temporally varying rates?
- What AWS datasets are available for HMA validation (HI-AWS, Pyramid Station,
  EVK2CNR, PROMICE)?
- Can we use Copernicus DEM GLO-30 to compute a more accurate local lapse rate?
- What is the uncertainty range for lapse rates in monsoon Himalayan conditions?

### Q2: Taxonomy of the 14 JMA Events

Our 14 JJA events span 4 failure modes (detachment, ice avalanche, rock-ice
avalanche, lake impact) and 4 triggers (meltwater, precipitation, surge, unknown).

- Are these failure modes physically distinct processes or a continuum?
- Should we stratify the multi-event validation by failure mode?
- Is the meltwater trigger (10/14 events) a thermal signal we should look for?
- How do the Aru-1/Aru-2 (2016) glacier detachments differ from the Shuraki
  Kapali ice avalanches? Do they require different precursor analysis?
- Are there events in our list that should be excluded (e.g., earthquake-triggered)?
- What is the reporting bias in this database? Are we missing events in
  poorly monitored regions (Tibet, Bhutan, Pakistan)?

### Q3: Precursor Deformation Coverage

The literature shows Sentinel-1 InSAR detected slope deformation before
Chamoli 2021 and Blatten 2025.

- What is the Sentinel-1 coverage (temporal and spatial) for our 14 event sites?
- Which events have pre-event InSAR data available in the ASF DAAC?
- What is the typical InSAR coherence in HMA glacier zones (snow-covered,
  steep terrain)?
- Can we use the NISAR L-band data (launched 2024) for better coherence?
- What open-source InSAR processing tools exist (SNAP, ISCE, MintPy) and
  what is their learning curve?
- Is there a published InSAR deformation precursor for ANY of our 14 events?
- What is the minimum detectable deformation rate with Sentinel-1 in HMA?

## What We Need From You

For each question:
- **CONFIRMED** (you agree, with evidence) or **CHALLENGED** (you disagree, with counter-evidence)
- **Specific datasets** with DOI, resolution, latency, and access method
- **Specific methods** with references to peer-reviewed implementations
- **Blind spots** we have not identified
- **GO/NO-GO recommendation** for multi-event validation with bias-corrected ERA5-Land daily
```

---

## 4. Phase-Wise Execution Plan (Updated per Advisor Call #5)

### Phase 0: Bias Correction & Event Homogeneity (Days 1-2) — ADVISOR ADDED

**Objective:** Fix the 899m/~6K elevation bias and audit event homogeneity BEFORE any multi-event validation.

| Step | Task | Exit Gate |
|------|------|-----------|
| 0.1 | Quantify ERA5-Land elevation bias (899m gap, lapse-rate uncertainty) | **DONE** — 5.8K bias, source ~2.1°C, PDD inflated 3.7x |
| 0.2 | Lock 14-event list with failure-mode labels (avalanche/GLOF/surge/detachment) | **DONE** — 14 events locked, 5 detachments, 4 ice avalanches, 4 rock-ice, 1 lake |
| 0.3 | Apply lapse-rate correction to Langtang data (sensitivity: -4 to -8 K/km) | Re-analyze with corrected T2m |
| 0.4 | Audit event homogeneity: exclude earthquake-triggered events from thermal analysis | Clean event list documented |
| 0.5 | **EXIT GATE:** Bias correction applied, event list locked, failure modes labeled | Decision recorded |

### Phase 1: Multi-Event Validation (Days 3-5)

**Objective:** Determine if bias-corrected ERA5-Land daily thermal signals generalize beyond Langtang 2026.

| Step | Task | Exit Gate |
|------|------|-----------|
| 1.1 | Download ERA5-Land daily for 14 JJA HMA events (CDS API, monthly requests) | All 14 events downloaded |
| 1.2 | Apply lapse-rate correction using Copernicus DEM GLO-30 for each event | Corrected T2m computed |
| 1.3 | Run z-score, percentile, cumulative PDD, EVT for each event (bias-corrected) | All 14 events analyzed |
| 1.4 | Compute TPR, FPR with confidence intervals (Wilson 95% CI for n=14) | TPR/FPR with CIs |
| 1.5 | Define non-event set: sample matched JJA windows from non-event years | Non-event baseline established |
| 1.6 | **EXIT GATE:** Report TPR/FPR with CIs, NOT a binary gate (advisor: n=14 is underpowered for binary gate) | Results documented |

### Phase 2: Enhanced Thermal Analysis (Days 6-9)

**Objective:** Test advisor-recommended enhancements that may recover signal.

| Step | Task | Exit Gate |
|------|------|-----------|
| 2.1 | Neighborhood cells analysis (3x3 grid around event) | Spatial pattern documented |
| 2.2 | ERA5-Land bias correction (LR/GAM vs AWS data) | Bias-corrected T2m computed |
| 2.3 | Re-run multi-event validation with bias-corrected data | TPR/FPR with correction |
| 2.4 | Add MODIS LST as alternative thermal source | MODIS TPR/FPR computed |
| 2.5 | **EXIT GATE:** Does bias correction or MODIS change the conclusion? | Decision recorded |

### Phase 3: Deformation Integration (Days 10-16)

**Objective:** Test whether deformation + thermal outperforms thermal-only.

| Step | Task | Exit Gate |
|------|------|-----------|
| 3.1 | Download Sentinel-1 InSAR for Langtang region (2016-2026) | InSAR time series computed |
| 3.2 | Compute slope deformation velocity for source area | Deformation time series |
| 3.3 | Integrate deformation + thermal: "slope moving AND thermal anomaly" | Combined signal defined |
| 3.4 | Test combined signal on Chamoli 2021 (documented deformation precursor) | Combined TPR/FPR |
| 3.5 | **EXIT GATE:** Does deformation + thermal improve TPR/FPR? | Decision recorded |

### Phase 4: Forecasting Architecture (Days 17-23)

**Objective:** Design a credible multi-sensor early warning architecture.

| Step | Task | Exit Gate |
|------|------|-----------|
| 4.1 | Define minimum sensor suite (thermal + deformation + ?) | Sensor list documented |
| 4.2 | Compute achievable lead time for each sensor | Lead time table |
| 4.3 | Define false alarm rate tolerance (community survey literature) | FPR tolerance defined |
| 4.4 | Design warning dissemination protocol | Protocol documented |
| 4.5 | **EXIT GATE:** Is the architecture credible? GO/NO-GO | Decision recorded |

### Phase 5: Prospective Feasibility (Days 24-30)

**Objective:** Determine if the system could work in real-time.

| Step | Task | Exit Gate |
|------|------|-----------|
| 5.1 | Test ERA5-Land-T (preliminary, 5-day latency) access | Real-time data available |
| 5.2 | Simulate real-time operation: "What would we have known on Aug 24?" | Lead time computed |
| 5.3 | Test Sentinel-1 latency (6-12 day revisit) | Deformation latency documented |
| 5.4 | **EXIT GATE:** Is prospective operation feasible? GO/NO-GO | Decision recorded |

---

## 5. 360-Degree Risk Assessment

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|-----------|
| CDS API rate limits prevent multi-event download | HIGH | Delays Phase 1 | Use EDH Zarr (if key available) or download one month at a time |
| 14 JJA events is too few for statistical power | MEDIUM | Weak conclusions | Document as pilot; extend to all 57 post-2000 events if feasible |
| ERA5-Land bias masks true signal | HIGH | False null | Apply bias correction in Phase 2 |
| Deformation data unavailable for all events | MEDIUM | Cannot complete Phase 3 | Focus on events with Sentinel-1 coverage |
| Community false alarm tolerance is lower than our FPR | HIGH | System unusable | Survey literature; document honestly |
| Single-event null is overgeneralized | MEDIUM | Premature abandonment | Advisor explicitly warned against this; proceed to multi-event |
| Post-hoc method shopping inflates TPR | HIGH | False positive results | Pre-registered protocol; no methods added after validation starts |
| ERA5-Land 9km cannot resolve slope-scale processes | CERTAIN | Fundamental limitation | Document; cannot be fixed with open data |
| Seasonal confounding (JJA vs winter events) | MEDIUM | Mixed results | Stratify by season in multi-event analysis |
| Reporting bias in glacier failure database | HIGH | Sample not representative | Document; focus on well-monitored regions |

---

## 6. Next Steps After Deep-Research Outcomes

After external engines respond to the deep-research prompt:

1. **Cross-validate** their responses against our findings (CONFIRMED/CHALLENGED)
2. **Integrate** their recommended datasets and methods into the protocol
3. **Execute** Phase 1 (multi-event validation) with the refined protocol
4. **Report** results honestly, including null results
5. **Decide** GO/NO-GO for Phase 2-5 based on Phase 1 results

**If Phase 1 shows TPR<50% and FPR>30%:**
- Declare: "ERA5-Land daily thermal data alone is insufficient for HMA glacier failure forecasting"
- Pivot to: deformation + thermal integration (Phase 3)
- Document: "This is a valid scientific finding — it bounds thermal observability"

**If Phase 1 shows TPR>70% and FPR<20%:**
- Proceed to Phase 2 (enhanced thermal analysis)
- Document: "Thermal signals are detectable but require bias correction and spatial enhancement"
- Begin Phase 4 (forecasting architecture) design

---

## 7. Advisor Call Log

| Call | Date | Context | Verdict |
|------|------|---------|---------|
| #4 | 2026-09-10 | Scientific conclusion verification | CONFIRMED null with refinements: rank vs all years, cumulative PDD, EVT, drop IF, neighborhood cells |
| #5 | 2026-09-10 | Deep-research addendum review | CONFIRMED plan with critical additions: Phase 0 (bias correction + event taxonomy), CIs not binary gate, narrow prompt to 3 questions |
| #6 | TBD | Multi-event protocol review | Pending |
| #7 | TBD | Phase 1 results review | Pending |

### Advisor Call #5 Key Quotes

> "Missing Phase 0: fix the 899 m/~6 K bias and audit event homogeneity. Do not run multi-event validation with that confounder in place."

> "TPR>70% / FPR<20% on n=14 is underpowered and ill-posed (FPR needs a defined non-event set). 10/14 has a CI ~42–88%. Pre-register procedure, report CIs—not a binary gate."

> "Narrow to three [questions]: lapse-rate correction, taxonomy of the 14, precursor deformation coverage. Architecture later."

> "Do first: Correct/quantify the elevation bias, then lock the 14-event list and failure-mode labels. Only then compute any TPR/FPR."
