# P5 Option 3 Seismic Execution Template — v0

**Status: TEMPLATE_ONLY_NOT_AUTHORIZED**

This blank form is a planning scaffold for a retrospective one-station
observability qualification. It is not an acquisition authorization, a
dependency admission, a source qualification, or a scientific result. No
FDSN request, StationXML retrieval, waveform retrieval, decoder admission,
or feature execution may begin from this file.

The scope ceiling is observability and anomaly triage after event initiation.
This lane must never become a detector, locator, warning, predictor, forecast,
production, or operational claim. It remains isolated from every GLOF
predictor and regime feature frame.

## 1. Owner and amendment identity

| Field | Owner completion |
|---|---|
| Record type | P5_OPTION3_EXECUTION_AMENDMENT_V0 |
| Amendment id | TBD_OWNER |
| Supersedes | TBD_OWNER |
| Created UTC | TBD_OWNER |
| Scientific owner | TBD_OWNER |
| Execution coordinator | TBD_OWNER |
| Approval status | TBD_OWNER |
| Approved by | TBD_OWNER |
| Approval UTC | TBD_OWNER |
| Approval evidence reference | TBD_OWNER |
| Authorization window | TBD_OWNER |

The owner must choose exactly one route before execution:

- a documented in-window event anchor with independent timing evidence; or
- a separately declared non-event observability estimand.

An absent event is a blocked preflight, not a negative scientific result.

## 2. Event or non-event estimand

| Field | Owner completion |
|---|---|
| Route selected | TBD_OWNER |
| Event source id or non-event definition | TBD_OWNER |
| Event UTC, if applicable | TBD_OWNER |
| Event date and timezone, if applicable | TBD_OWNER |
| Timing source and independent verification | TBD_OWNER |
| Timing tolerance | TBD_OWNER |
| Relation to retrieval window | inside / edge / lead / TBD_OWNER |
| Authorized start and end UTC | TBD_OWNER |
| Window duration | no more than 24 hours; exact value TBD_OWNER |
| Anchor digest | TBD_OWNER |

The event UTC, date, relation, and window must be mutually consistent. A
syntactically valid timestamp outside the declared window does not qualify an
event route. The timing evidence and its digest must be bound before feature
interpretation.

## 3. Station and FDSN contract

| Field | Owner completion |
|---|---|
| FDSN provider and service URLs | TBD_OWNER |
| Network and station code | TBD_OWNER |
| Authorized station list | exactly one station; TBD_OWNER |
| Location code | TBD_OWNER |
| Components | three declared components; TBD_OWNER |
| Station epoch overlap | TBD_OWNER |
| StationXML request | TBD_OWNER |
| MiniSEED request | TBD_OWNER |
| Response metadata requirement | required |
| Metadata and payload license interpretation | TBD_OWNER |
| Retrieval limit | one station and at most 24 hours |
| Storage root | TBD_OWNER |
| Storage reserve and cap | TBD_OWNER |

Station selection must be deterministic and the selected station must belong
to the authorized list. A missing, empty, duplicated, or mismatched station
declaration blocks the run. FDSN metadata is required to interpret waveform
bytes; payload bytes without matching StationXML are inadmissible.

## 4. Dependency and decoder decision

| Field | Owner completion |
|---|---|
| Decoder package and exact version | TBD_OWNER |
| Package hash and license | TBD_OWNER |
| Environment digest | TBD_OWNER |
| Project-environment admission | TBD_OWNER |
| Isolated qualification environment | TBD_OWNER |
| Representative STEIM bytes | TBD_OWNER |
| STEIM1/STEIM2 qualification result | TBD_OWNER |
| Decoder failure stop rule | TBD_OWNER |

Installing or admitting ObsPy, or any equivalent decoder, requires a separate
owner dependency decision. A qualification probe in an isolated environment
does not constitute project-environment admission or waveform evidence.

## 5. Byte-bound intake and feature contract

| Field | Owner completion |
|---|---|
| StationXML SHA-256 and sidecar | TBD_OWNER |
| MiniSEED SHA-256 and sidecar | TBD_OWNER |
| Source metadata digest | TBD_OWNER |
| Decoder environment digest | TBD_OWNER |
| Storage receipt digest | TBD_OWNER |
| Fixed windowing contract | TBD_OWNER |
| Sample-rate and gap policy | TBD_OWNER |
| Response and timing checks | TBD_OWNER |
| Prespecified spectral features | TBD_OWNER |
| Feature-contract digest | TBD_OWNER |
| Configuration digest | TBD_OWNER |

The scientific digest chain must bind source metadata, waveform bytes,
StationXML, decoder environment, feature contract, windowing, evaluation,
timing verification, storage receipt, and configuration. False, zero, empty,
or non-string digest values are invalid. Missing or malformed sidecars fail
closed.

## 6. Evaluation and allowed outcomes

| Field | Owner completion |
|---|---|
| Temporal blocking rule | TBD_OWNER |
| Earthquake/noise confusion controls | TBD_OWNER |
| Negative-control construction | TBD_OWNER |
| Independent event-timing check | TBD_OWNER |
| Anomaly decision rule | TBD_OWNER |
| Minimum reproducibility rule | TBD_OWNER |
| Replay mode | artifact_integrity_replay or model_reexecuted; TBD_OWNER |
| Final terminal status | TBD_OWNER |

Only these Option 3 terminal statuses are permitted:

- BLOCKED — preflight or authorization did not qualify; not a scientific
  negative;
- NO_QUALIFIED_SIGNAL — qualified bytes and evaluation completed without a
  reproducible signal;
- CANDIDATE_ANOMALIES_ONLY — reproducible candidate windows without authority
  to generalize;
- OBSERVABILITY_PASS — bounded retrospective observability gates passed.

Every scientific status must retain all authority flags false. No result may
be used as a GLOF predictor or combined with regime features.

## 7. Owner completion and execution gate

- [ ] I selected and documented one event or non-event route.
- [ ] I confirmed the station, components, FDSN provider, and 24-hour limit.
- [ ] I confirmed storage reserve and the exact evidence root.
- [ ] I made the dependency and decoder decision.
- [ ] I prespecified features, controls, timing checks, and terminal statuses.
- [ ] I confirmed the complete digest chain and sidecar policy.
- [ ] I recorded approval identity, decision time, and evidence reference.
- [ ] I understand that this template itself authorizes nothing.

**Owner decision:** TBD_OWNER  
**Decision UTC:** TBD_OWNER  
**Decision record digest:** TBD_OWNER
