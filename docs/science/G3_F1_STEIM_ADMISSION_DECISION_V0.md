# G3-F1 — STEIM admission seam decision (v0)

**Status:** `DECISION_RECORDED` — the seam defect is fixed; **STEIM
admission remains closed**. This document changes no source status, no
pilot outcome, and no authority flag.
**Lane:** implementation lane (CLINE-IMPL), 2026-09-19.
**Scope:** `nepal/seismic_sidecar/io.py` only. No protected path, no
dependency lock, and no `requirements.txt` change.

## 1. Finding — the seam was closed by accident, not by contract

`decode_records` resolved the optional decoder, **discarded its
traces**, and then appended the decoder's problem *unconditionally*:

```python
_, problem = _decode_obspy(data)
problems.append(f"{trace_id}: STEIM encoding - {problem}")
```

`_decode_obspy` returns `(traces, None)` on success, so a **perfectly
successful decode** was reported as the literal string

```
4W.DP01.00.DPZ: STEIM encoding - None
```

Two defects follow. First, the message is a **fabricated failure
reason** — it reports a decoder fault that did not occur ("— None").
Second, the record's behaviour was independent of decoder availability:
installing the dependency could never change the outcome, so the code
implied a configurable seam that did not exist.

## 2. Independent qualification (read-only, isolated environment)

Environment: a **separate** virtual environment
`/tmp/p5-obspy-qual/venv` — `obspy==1.5.1`, Python **3.14.7**,
`numpy 2.5.3`. The project `.venv` was never modified; `obspy` remains
absent from it, and `requirements.txt` is unchanged.

Synthetic int32 waveforms (staircase-free 500 Hz `DPZ` trace) were
written to real miniSEED bytes by obspy and read back:

| Encoding | Bytes | Samples | Round-trip byte-identical | Parser encoding code |
|---|---|---|---|---|
| STEIM1 | 65,536 | 60,000 | **True** | `10` |
| STEIM2 | 61,440 | 60,000 | **True** | `11` |

Conclusion: **the decoder is viable**, the record-header parser already
identifies STEIM1/STEIM2 correctly, and the rejection was purely the
seam's fabricated reason. A separate review lane reached the same
toolchain result; this record reproduces it independently rather than
adopting it on assertion.

## 3. Decision

**Option (a) is retained: v0 admission stays closed, fail-closed, with
the reason corrected.** The decoder is now exercised *only* to report
the true cause, and its output is still discarded.

| Condition | Reported reason | Traces |
|---|---|---|
| decoder absent | the installed-environment message naming `obspy` | none |
| decoder parses the payload | explicit contract closure ("admission is closed by contract pending a ratified G3-F1 decision") | none |
| decoder faults | the decoder's own error text, verbatim | none |
| undeclared non-STEIM encoding | unchanged "not a declared decodable encoding" | none |

Rationale for keeping the seam closed: admitting STEIM would let an
**unpinned** third-party binary decoder produce the sample values that
every downstream energy feature is computed from. That is a provenance
change to the evidence chain, not a convenience change, and it is not
something an implementation lane may introduce on its own.

## 4. Preconditions for a future opening (option (b)) — all required

1. An owner-ratified amendment to this record that names the decoder
   identity (distribution, exact version, wheel tag) and the admission
   scope.
2. The decoder pinned in the dependency lock, with the lock digest
   rebound — noting that the manifest binds `requirements_sha256`, so a
   lock change forces a manifest rebind.
3. Representative-byte qualification reproduced **inside the project
   environment** against STEIM1 *and* STEIM2, including a decoder-absent
   negative control.
4. An explicit, default-off configuration flag: absent the flag the
   behaviour in section 3 is byte-identical to today.
5. Regression coverage proving that without the ratified amendment and
   the flag, STEIM bytes still reject and produce no traces.

Until every precondition holds, STEIM bytes are **inadmissible** and the
seismic source remains metadata-qualified only.

## 5. Verification of this change

- `tests/test_r11_9_steim_seam.py` — 7 focused regressions pinning the
  corrected reasons, the absence of the fabricated `None` text, the
  contract-closure wording, the decoder-fault pass-through, the
  undeclared-encoding separation, and the fail-closed outcome.
- Seismic focused lanes: **215 passed** (208 pre-existing + 7 new), zero
  regression.
- Qualification script re-run **against the fixed code** reports the
  real reason for both encodings while still returning zero traces.
- CI: the new lane is added to the `research-v0-contracts` path
  triggers and executed in the seismic sidecar step; the workflow YAML
  re-parses cleanly with 38 path entries in each trigger list.

## 6. Explicit non-changes

No `requirements.txt` edit; no `obspy` in the project environment; no
change to `SEISMIC_FEATURE_UNITS`, response modes, or any authority
flag (`warning_path_authorized=false`, `production_authorized=false`,
`promotion_eligible=false`); no waveform byte retrieved from any
archive; no source qualification.