# Nepal Event Anomaly Assessment — Final Report

**Generated:** 2026-09-10T15:26:18.239696

**Status:** RESEARCH ONLY — No prediction, no causal attribution

**Pre-registration:** FROZEN (see preregistration.md)


## 1. Event Definition


| Field | Value |
|-------|-------|

| Event date | 2026-08-26 |

| Location | Langtang Lirung north face, Nepal |

| Reference point | (28.288708, 85.528159) (HiRisk) |

| ERA5-Land cell | (28.25, 85.5) (Hausfather nearest) |

| Model elevation | 4322 m |

| Source elevation | 5221 m |

| Elevation gap | 899 m |

| Grid resolution | 11.1 × 9.8 km |

| Glacier ID | RGI2000-v7.0-G-15-05732 |


**⚠️ ERA5-Land model elevation: 4322 m. Source slope: 5221 m. Delta: 899 m. Grid: 11.1 × 9.8 km.**


## 2. Pre-Registration Summary


| Item | Value |
|------|-------|

| Pre-event window | 2026-08-19 to 2026-08-25 |

| Event day (held out) | 2026-08-26 |

| Historical baseline | ('2001-01-01', '2025-12-31') |

| Features | 10 (7 raw + 3 derived) |

| Z-score threshold | 2.0 |

| Percentile thresholds | (95, 99) |

| Isolation Forest | {'n_estimators': 200, 'contamination': 0.01, 'random_state': 42} |

| GMM K range | (1, 2, 3, 4, 5) |

| GMM covariance | diag |

| PELT model | rbf, pen=10 |

| CUSUM | k=1.0, threshold=5.0 |

| Block permutation lengths | (7, 14, 30) days |

| Negative control years | (2021, 2022, 2023, 2024, 2025) |


## 3. Anomaly Detection Results


### 3.1 Z-score Anomaly


| Metric | Value |
|-------|-------|

| Pre-event T2m anomalies (|z|>2.0) | 0 |

| Pre-event PDD anomalies (|z|>2.0) | 0 |

| Max T2m z-score | 1.97 |

| Max PDD z-score | 1.98 |


### 3.2 Percentile Ranking


| Metric | Value |
|-------|-------|

| T2m above 95th percentile | 3 days |

| T2m above 99th percentile | 1 days |

| PDD above 95th percentile | 3 days |

| PDD above 99th percentile | 0 days |


### 3.3 Block Permutation Test


| Block length | T2m p-value | PDD p-value | T2m significant | PDD significant |

|-------------|-------------|------------|-----------------|----------------|

| block_7d | 0.08 | 0.113 | YES | YES |

| block_14d | 0.057 | 0.082 | YES | YES |

| block_30d | 0.081 | 0.117 | YES | YES |



### 3.4 Negative Controls (2021-2025)


| Year | False positives | Max |z| |
|------|----------------|--------|

| 2021 | 0 | 1.90 |

| 2022 | 0 | 1.88 |

| 2023 | 2 | 2.12 |

| 2024 | 0 | 1.15 |

| 2025 | 0 | 1.28 |



## 4. Change-Point Detection Results


### 4.1 pelt_t2m


- Total change-points: 2

- Pre-event change-points: 0



### 4.1 pelt_pdd


- Total change-points: 2

- Pre-event change-points: 0



### 4.1 cusum_t2m


- Total change-points: 4

- Pre-event change-points: 0



### 4.1 cusum_pdd


- Total change-points: 13

- Pre-event change-points: 1

  - **2026-08-24 00:00:00** (in pre-event window)



## 5. Isolation Forest Results


| Metric | Value |
|-------|-------|

| Pre-event anomalies | 0 / 7 days |

| Min anomaly score | 0.2155 |

| Model params | {'n_estimators': 200, 'contamination': 0.01, 'random_state': 42} |

| Features used | ['t2m_daily', 'd2m_daily', 'tp_daily', 'sf_daily', 'wind_speed_daily', 'wind_dir_sin', 'wind_dir_cos', 'rh_daily', 'pdd_7day', 'pdd_daily', 'freezing_height_m'] |


### 5.1 Negative Controls


| Year | N days | N anomalies | Anomaly rate | Min score |

|------|--------|-------------|-------------|------------|

| 2021 | 92 | 0 | 0.0 | 0.0409 |

| 2022 | 92 | 0 | 0.0 | 0.0755 |

| 2023 | 92 | 4 | 0.0435 | -0.069 |

| 2024 | 92 | 0 | 0.0 | 0.0527 |

| 2025 | 92 | 8 | 0.087 | -0.0379 |



## 6. GMM Descriptive Overlay


**⚠️ GMM is DESCRIPTIVE ONLY — it describes weather regimes, NOT avalanche precursors.**


| Metric | Value |
|-------|-------|

| Best K (by BIC) | 5 |

| K=1 null benchmark | NO |

| JS distance (target vs baseline) | 0.3085 |

| JS distance (pre-event vs baseline) | 0.6168 |

| Covariance type | diag |


### 6.1 BIC Scores


| K | BIC |
|---|-----|

| 1 | 80717.96 |

| 2 | 62092.54 |

| 3 | 57712.12 |

| 4 | 53138.02 |

| 5 | 50642.09 |



### 6.2 Cluster Occupancy


| Cluster | Baseline | Target |
|---------|----------|--------|

| Cluster 0 | 0.2339 | 0.1395 |

| Cluster 1 | 0.1865 | 0.0581 |

| Cluster 2 | 0.1178 | 0.2209 |

| Cluster 3 | 0.25 | 0.5465 |

| Cluster 4 | 0.2117 | 0.0349 |



## 7. NISAR Feasibility Assessment


| Metric | Value |
|-------|-------|

| Pre-event granules | 28 |

| Post-event granules (excluded) | 4 |


**Key finding:** The 26 Jul → 19 Aug GUNW pair was inserted into catalog on 25 Aug — only ~26 hours before event


**Implication:** Acquisition date alone overstates warning time. Must use publication time for any lead-time claim.


**Recommendation:** Record both acquisition and publication timestamps. Any warning assessment must use publication cutoff.


### 7.1 Publication Latency Traps


| Granule | Acquisition end | Warning lead (hours) | Trap |

|---------|---------------|---------------------|------|

| nisar_gunw_provisional | 2026-08-19T23:39:53.999Z | 25.7 | WARNING: publication less than 3 days before event |

| nisar_goff_provisional | 2026-08-19T23:39:53.999Z | 36.5 | WARNING: publication less than 3 days before event |



## 8. Cross-References


| Source | Finding | Our comparison |
|--------|---------|----------------|

| Hausfather (2026) | ERA5 0.25° t2m anomaly vs 1961-1990 | See EDA plots |

| Rui Li (2026) | 7-day mean T: 9.43°C, PDD: 65.94°C·d | See Phase 2 output |

| Guo et al. (2026) | Multi-sensor event reconstruction | Cited, not rediscovered |

| Xu (2026) | Open-data cascade reconstruction | Cited |

| Khadka et al. (2022) | ERA5-Land validation in Everest | Cited for limitation |

| Astra (2026) | NISAR catalog + publication latency | See Section 7 |


## 9. Limitations


1. **Elevation mismatch:** ERA5-Land model elevation (4322 m) is 899 m below the source (5221 m). All temperatures are at model elevation, not at the failure plane.

2. **Grid resolution:** 11.1 × 9.8 km grid cannot resolve slope-scale conditions.

3. **Single event:** This study cannot establish predictive capability from one event.

4. **Reanalysis latency:** ERA5-Land preliminary product has ~5-day delay. An operationally faithful 25 August evaluation cannot use 24-25 August data.

5. **GMM is descriptive:** Cluster assignments describe weather regimes, not avalanche precursors.

6. **No causal attribution:** Detected anomalies are associations, not causes.

7. **Monsoon cloud:** Optical data (MODIS LST, Sentinel-2) is cloud-limited in JJA.

8. **ERA5-Land validation:** Khadka et al. (2022) found substantial local-scale errors in ERA5-Land wind and monsoon precipitation in the Everest region.


## 10. What This Study Does NOT Claim


- It does NOT claim prediction or forecasting capability

- It does NOT claim causal attribution of the avalanche to thermal forcing

- It does NOT claim that GMM clustering detected the event

- It does NOT claim that ERA5-Land 9km data represents conditions at 5,200 m

- It does NOT claim that a single event validates any methodology

- It does NOT claim that data was available before the event (publication-time ledger documents actual availability)


## 11. Decision


Based on the results above, the following decisions are made:


| Decision | Criteria | Status |
|----------|----------|--------|

| Environmental anomaly detected | Any pre-event z-score > 2 or percentile > 95 | See Section 3 |

| Change-point before event | PELT/CUSUM change-point in pre-event window | See Section 4 |

| Multivariate anomaly | Isolation Forest flags pre-event days | See Section 5 |

| GMM regime shift | JS distance > 0.3 | See Section 6 |

| NISAR deformation study justified | Pre-event granules exist with adequate lead time | See Section 7 |

| Multi-event benchmark justified | Anomaly detected in this event | TBD |

| Operational deployment | NOT AUTHORIZED — single event, no prediction claim | NO-GO |


## 12. Null Result Acceptance


If no open meteorological precursor exceeded seasonal/control variability, 
the conclusion is:


> "No open meteorological precursor exceeded climatology in the 7 days 
before 2026-08-26, conditional on ERA5-Land measurement sensitivity 
at 4322 m model elevation."


This is NOT a failure. It is a bound on observability.


---


*Generated by nepal/run_nepal_test.py at 2026-09-10T15:26:18.239932*
