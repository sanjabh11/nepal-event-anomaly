# P5 Operative-Anchor Note (V0) — additive to the executed P5 record

**Status:** `RECORDED` — additive note only; the owner-executed
`P5_ACQUISITION_AUTHORIZATION_RECORD_V0.md` owner sections are
frozen and untouched.
**Binding evidence:**
`<evidence root>/retrieval/anchor_derivation_record.json`
(`state: FROZEN-3-BASINS-FINAL`, `operative_anchors.set: hma_derived`).

## What changed and under what authority

1. The PDGL-interim anchors (koshi 27.97405/87.114312, gandaki
   28.386633/85.203473, karnali 30.124061/81.530622) are **superseded**
   by the uniform-rule HMA anchors derived from the amended
   `zenodo.17948783` inventory attributed via RDS 7952 L2 polygons —
   per the owner-directed anchor amendment recorded in the P5 record
   (P5-B fallback row) and the "uniformly re-derives all five basin
   anchors" clause.
2. **Operative anchors (frozen 2026-09-19T06:28:54Z):**
   `koshi 28.072090/87.043138 (1,436 lakes)`,
   `gandaki 28.633171/84.722626 (301)`,
   `karnali 29.771839/82.198603 (767)`.
3. **3-basin is FINAL, not interim:** mahakali/bagmati have no
   glacial-lake population in either inventory and no RDS 7952 L2
   polygons (`mahakali_bagmati_finding`); no anchor is derivable
   under any lake-based rule. The PDGL-anchor ERA5 zip bytes are
   retained in evidence as superseded interim retrieval.
4. The PDGL 2015 inventory remains the **opportunity frame**
   unchanged (47 lakes; three-basin coverage).

Downstream effect: every feature-side `source_manifest` lineage and
retrieval record binds these anchors by the digest of the anchor
record; the P3 label package and holdout groups use the same
operative basin set (see `P3_BASIN_ASSIGNMENT_RULE_V0.md`).
