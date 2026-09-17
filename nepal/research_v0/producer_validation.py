"""Shared producer-payload provenance validator (Round-8, R7-C01).

One canonical schema floor for a serialized ``science_v0`` regime
artifact — the dict ``run_regimes`` emits and
``freeze_regime_artifact`` certifies.  Before this module existed the
three consumers of that payload enforced divergent rules:
``experiment_v0.audit.audit_producer_payload`` checked the richest
schema, ``freeze_regime_artifact`` recomputed the bound digests but
never required ``model``/``source_manifest`` presence, and the
adapter checked its own subset — so a hand-assembled artifact missing
whole provenance sections could freeze cleanly once its envelope
digests were rehashed.

Every boundary now runs ``validate_producer_payload`` first:
``freeze_regime_artifact`` raises on any problem before digest
recomputation, ``regime_assignment_from_artifact`` raises, and
``audit_producer_payload`` maps each problem string to a Finding —
the same mutation is rejected identically at all three boundaries.

Layering: this module lives in ``research_v0`` — it operates on plain
dicts and imports nothing from ``science_v0`` or ``experiment_v0``.
R9-P02: it MAY perform file I/O — a non-fixture ``source_manifest``
is byte-verified through ``_hashing.verify_source_evidence`` (same
package, no layering violation) so every promotion boundary invokes
the same binding; ``{"fixture": true}`` remains the declared
synthetic bypass, and ``verify_source_bytes=False`` restores the
pure offline surface for callers that must not touch the disk.

Problem strings are tagged ``"<CATEGORY>: <detail>"`` where CATEGORY
is one of ``PAYLOAD_MALFORMED``, ``PROVENANCE_MISSING``,
``SCHEMA_MALFORMED``, ``DIGEST_MALFORMED``, ``DIGEST_MISMATCH``;
``audit_producer_payload`` maps them to ``PRODUCER_<CATEGORY>``
finding codes.  Callers that only need to fail closed raise on any
non-empty return.
"""
from __future__ import annotations

import dataclasses
import hashlib
import math
import re
from datetime import date as _date
from typing import Any, Mapping, Sequence

from ._hashing import (canonical_json, sha256_canonical,
                       verify_source_evidence)
from .gates import REQUIRED_REGIME_GATE_NAMES
from .policy import RegimeMode
from .records import RunManifestV0, deserialize_record

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

#: The canonical producer schema — every field a serialized
#: ``science_v0.run_regimes`` + ``freeze_regime_artifact`` payload
#: must carry.  Missing fields are provenance gaps; a fabricated
#: artifact that omits them cannot support a terminal status.
#: Round-8 adds the typed ``fit_partition`` binding and its digest.
PRODUCER_REQUIRED_FIELDS = (
    "assignments", "assignment_digest",
    "regime_artifact_digest", "freeze_digest", "frozen",
    "label_blinding", "fitted_on", "mode", "status",
    "data_class", "seeds", "seeds_declared", "seed_coverage",
    "feature_cols", "feature_matrix_digest", "input_bytes_digest",
    "input_schema", "model", "config_digest", "fit_groups",
    "heldout_groups_declared", "n_train_rows", "n_rows",
    "train_mask_digest", "k", "per_seed_best_k",
    "modal_k_frequency", "occupancy", "stability", "nulls",
    "preprocessing", "preprocessing_digest", "k_selection_digest",
    "stability_report_digest", "null_model_digest", "disclaimer",
    "source_manifest", "missingness_applied",
    "terminal", "associable",
    "fit_partition", "fit_partition_digest",
    # R9: the raw bound sections and the canonical pair digest are
    # provenance, not decoration — a payload that omits them cannot
    # recompute its own bindings.
    "input_values", "config", "unit_basin_map", "run_manifest",
    "unit_basin_map_digest",
    # R9-V8: the bound digests of the typed record and the run
    # environment are provenance too — a payload that omits them
    # cannot recompute its own bindings at any boundary.
    "environment_digest", "run_manifest_digest")

#: Carried digests are verified, never trusted: each must be a
#: 64-hex sha256 when present (absence is a provenance finding).
PRODUCER_DIGEST_FIELDS = (
    "assignment_digest", "regime_artifact_digest", "freeze_digest",
    "feature_matrix_digest", "input_bytes_digest", "config_digest",
    "train_mask_digest", "preprocessing_digest",
    "k_selection_digest", "stability_report_digest",
    "null_model_digest",
    "environment_digest", "run_manifest_digest",
    "fit_partition_digest", "forecast_feature_payload_digest",
    "unit_basin_map_digest")

#: The typed fit-partition binding (R7-C09) — record type and the
#: sub-keys the freeze/adapter/audit boundaries cross-check against
#: the artifact's declared fit surface.
FIT_PARTITION_RECORD_TYPE = "fit_partition/v0"
FIT_PARTITION_FIELDS = (
    "record_type", "train_groups", "heldout_groups",
    "n_train_rows", "n_rows", "train_row_keys_digest",
    "cutoff_iso", "feature_matrix_digest", "feature_cols")

#: The typed forecast-feature binding (R7-C10) — required under
#: FORECAST_REGIME, forbidden under RETROSPECTIVE_REGIME.
FORECAST_PAYLOAD_RECORD_TYPE = "forecast_feature_payload/v0"
FORECAST_PAYLOAD_FIELDS = (
    "record_type", "forecast_feature_set",
    "forecast_vintage_digests", "feature_matrix_digest",
    "row_count", "row_keys_digest")

#: Source-manifest keys a non-fixture manifest must carry for the
#: schema floor; byte verification of ``source_files`` under
#: ``evidence_root`` runs through ``_hashing.verify_source_evidence``
#: when ``verify_source_bytes`` is on (R9-P02).
_NONFIXTURE_MANIFEST_KEYS = (
    "source_id", "source_digests", "units", "feature_allowlist",
    "lineage", "evidence_root")

#: The declared seed_coverage state vocabulary (REG-C03): a declared
#: seed either converged on every candidate K or it failed — no
#: third state exists.
_SEED_COVERAGE_STATES = frozenset({"converged", "failed"})

#: Encoded non-finite tokens the producer emits inside
#: ``input_values`` (None encodes NaN) — the only string values the
#: feature-matrix binding admits.
_NONFINITE_TOKENS = frozenset({"Infinity", "-Infinity"})

#: The declared null families a producer artifact must serialize.
_NULL_FAMILY_NAMES = ("shuffled", "season_matched")

#: Serialized fields every null-family record must carry for its
#: ``family_digest`` to be a bound claim.
_NULL_FAMILY_REQUIRED = (
    "statistic", "observed", "alpha", "n_replicates", "n_succeeded",
    "n_failed", "status", "selection", "family_digest")


# ---------------------------------------------------------------------
# Shared canonical helpers — single-source routines the producer
# emission path (science_v0.regimes) and every validation boundary
# run identically (R9 shared-validation layer).
# ---------------------------------------------------------------------

def row_key(unit: str, date: str) -> str:
    """The canonical feature-row key ``"<unit>|<date>"`` — the same
    binding the producer hashes into ``preprocessing.row_keys_digest``
    and ``fit_partition.train_row_keys_digest``."""
    return f"{unit}|{date}"


def sorted_row_key_digest(keys) -> str:
    """sha256 over the canonically sorted row-key list — one routine
    for emission and every recompute boundary."""
    return sha256_canonical(sorted(keys))


def semantic_feature_matrix_digest(input_values) -> str:
    """The 6-decimal semantic normalization of the encoded feature
    matrix: ``round(v, 6)`` on floats, everything else verbatim
    (ints, ``None`` for NaN, ``"Infinity"``/``"-Infinity"`` tokens).
    Emission and recompute share this routine so the domain can
    never drift."""
    return sha256_canonical(
        [[round(v, 6) if isinstance(v, float) else v
          for v in row] for row in input_values])


def fixture_flag(manifest) -> tuple[bool, list[str]]:
    """The strict fixture marker (R9-P01): ``{"fixture": true}`` is
    the only synthetic bypass.

    Returns ``(is_fixture, problems)`` — a non-bool marker is
    SCHEMA_MALFORMED (a truthy string/int is never a bypass);
    ``fixture: false`` or an absent marker is simply non-fixture and
    must carry real evidence."""
    if not isinstance(manifest, Mapping):
        return False, ["SCHEMA_MALFORMED: source_manifest must be "
                       "a mapping"]
    if "fixture" in manifest and \
            not isinstance(manifest["fixture"], bool):
        return False, [
            "SCHEMA_MALFORMED: source_manifest.fixture must be a "
            f"boolean — got {manifest['fixture']!r}; a truthy "
            "non-bool marker is not a synthetic bypass"]
    return (manifest["fixture"] is True
            if "fixture" in manifest else False), []


def canonical_unit_basin_pairs(ubm) -> list[list[str]]:
    """Canonical ``[[unit, group], ...]`` pairs for the C03
    unit→basin map: string-normalized, deduplicated, sorted — the
    exact material ``unit_basin_map_digest`` binds."""
    pairs = set()
    for entry in ubm or ():
        if isinstance(entry, (list, tuple)) and len(entry) == 2:
            pairs.add((str(entry[0]), str(entry[1])))
    return [list(p) for p in sorted(pairs)]


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.match(value))


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and \
        isinstance(value, (int, float))


def _seq_of_numbers(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and bool(value) and \
        all(_is_number(v) for v in value)


#: Terminal producer statuses the serialized artifact vocabulary
#: admits (RUN_ERROR is vocabulary-valid but always terminal-
#: rejected by the status check).
_PRODUCER_STATUSES = frozenset({
    "DESCRIPTIVE_REGIME_ONLY", "CANDIDATE_ONLY",
    "UNSUPERVISED_STRUCTURE_NOT_STABLE", "RUN_ERROR"})


def _scalar_floor_problems(
        payload: Mapping[str, Any]) -> list[str]:
    """R9-V6: exact type floors for the scalar/sequence fields —
    presence checks admit wrong types silently, so every required
    field whose consumers were isinstance-guarded now gets an
    unconditional shape check."""
    problems: list[str] = []

    def _int_seq(name, *, distinct=False):
        v = payload.get(name)
        if name not in payload:
            return
        if not isinstance(v, (list, tuple)) or not v or \
                any(isinstance(x, bool) or not isinstance(x, int)
                    for x in v):
            problems.append(
                f"SCHEMA_MALFORMED: {name} must be a non-empty "
                "sequence of integers")
        elif distinct and len(set(v)) != len(v):
            problems.append(
                f"SCHEMA_MALFORMED: {name} must carry distinct "
                "integers — a duplicated declared seed is not a "
                "new run")

    if "k" in payload and (
            isinstance(payload["k"], bool) or
            not isinstance(payload["k"], int) or
            payload["k"] <= 0):
        problems.append(
            "SCHEMA_MALFORMED: k must be a positive integer")
    _int_seq("seeds")
    _int_seq("seeds_declared", distinct=True)
    occ = payload.get("occupancy")
    if "occupancy" in payload and (
            not isinstance(occ, (list, tuple)) or not occ or
            any(not _is_number(v) or float(v) < 0.0 for v in occ)):
        problems.append(
            "SCHEMA_MALFORMED: occupancy must be a non-empty "
            "sequence of non-negative numbers")
    fc = payload.get("feature_cols")
    if "feature_cols" in payload and (
            not isinstance(fc, (list, tuple)) or not fc or
            any(not isinstance(c, str) or not c.strip()
                for c in fc)):
        problems.append(
            "SCHEMA_MALFORMED: feature_cols must be a non-empty "
            "sequence of non-empty strings")
    elif "feature_cols" in payload and \
            len(set(fc)) != len(fc):
        problems.append(
            "SCHEMA_MALFORMED: feature_cols must be unique")
    if "fitted_on" in payload and \
            payload["fitted_on"] != "TRAIN_ONLY":
        problems.append(
            "SCHEMA_MALFORMED: fitted_on must be 'TRAIN_ONLY' — "
            "the producer's declared fit surface is exact")
    for flag in ("associable", "terminal"):
        if flag in payload and not isinstance(payload[flag], bool):
            problems.append(
                f"SCHEMA_MALFORMED: {flag} must be a boolean")
    if "status" in payload and \
            payload["status"] not in _PRODUCER_STATUSES:
        problems.append(
            f"SCHEMA_MALFORMED: status {payload['status']!r} is "
            "not a declared producer status")
    mf = payload.get("modal_k_frequency")
    if "modal_k_frequency" in payload and not _is_number(mf):
        problems.append(
            "SCHEMA_MALFORMED: modal_k_frequency must be a "
            "number in [0, 1]")
    return problems


def _model_problems(payload: Mapping[str, Any]) -> list[str]:
    """Well-formedness of the ``model`` binding — mirrors the
    structural half of ``audit._producer_model_findings`` (exact key
    set, numeric sequences, component counts, declared-k agreement)
    plus the pure-python numeric sanity (finite, non-negative,
    weights sum to 1, consistent dimensions, symmetric square
    covariances), the pure-python numeric sanity (finite,
    non-negative, weights sum to 1, consistent dimensions,
    symmetric square covariances), and the R9-P09 numeric floor:
    every covariance must be strictly positive-definite — a
    non-positive diagonal or a minimum eigenvalue <= 1e-10 means a
    degenerate component that could never have fitted (sklearn's
    reg_covar floors are ~1e-6)."""
    model = payload.get("model")
    if not isinstance(model, Mapping) or \
            set(model) != {"weights", "means", "covariances"}:
        return ["SCHEMA_MALFORMED: model must be a mapping with "
                "exactly the keys {weights, means, covariances}"]
    problems: list[str] = []
    weights, means, covs = (model["weights"], model["means"],
                            model["covariances"])
    if not _seq_of_numbers(weights):
        problems.append(
            "SCHEMA_MALFORMED: model.weights must be a non-empty "
            "sequence of numbers")
    if not isinstance(means, (list, tuple)) or not means or \
            any(not _seq_of_numbers(row) for row in means):
        problems.append(
            "SCHEMA_MALFORMED: model.means must be a non-empty "
            "sequence of numeric rows")
    if not isinstance(covs, (list, tuple)) or not covs or \
            any(not isinstance(m, (list, tuple)) or
                any(not _seq_of_numbers(row) for row in m)
                for m in covs):
        problems.append(
            "SCHEMA_MALFORMED: model.covariances must be a "
            "non-empty sequence of numeric matrices")
    if not problems and not (len(weights) == len(means) ==
                             len(covs)):
        problems.append(
            "SCHEMA_MALFORMED: model weights/means/covariances "
            "component counts disagree")
    k = payload.get("k")
    if not problems and isinstance(k, int) and \
            not isinstance(k, bool) and len(weights) != k:
        problems.append(
            f"SCHEMA_MALFORMED: model carries {len(weights)} "
            f"components but the artifact declares k={k}")
    if problems:
        return problems
    # numeric sanity — a structurally-typed but pathological model
    # must fail closed (pure-python half; PSD stays in the auditor)
    if any(not math.isfinite(float(w)) or float(w) < 0.0
           for w in weights):
        problems.append(
            "SCHEMA_MALFORMED: model.weights must be finite and "
            "non-negative")
    elif abs(sum(float(w) for w in weights) - 1.0) > 1e-6:
        problems.append(
            "SCHEMA_MALFORMED: model.weights do not sum to 1")
    d = len(means[0]) if means else 0
    if any(len(row) != d for row in means):
        problems.append(
            "SCHEMA_MALFORMED: model.means rows have inconsistent "
            "feature dimensions")
    # R9-V9: the fitted dimension IS the feature order — a model
    # trained on a different width cannot bind this matrix.
    fcols = payload.get("feature_cols")
    if isinstance(fcols, (list, tuple)) and fcols and \
            d != len(fcols):
        problems.append(
            "SCHEMA_MALFORMED: model dimension does not equal "
            "len(feature_cols) — the fitted width is unbound "
            "to the declared feature order")
    if any(not math.isfinite(float(v)) for row in means
           for v in row):
        problems.append(
            "SCHEMA_MALFORMED: model.means contain non-finite "
            "values")
    for ci, cov in enumerate(covs):
        if len(cov) != d or any(len(row) != d for row in cov):
            problems.append(
                f"SCHEMA_MALFORMED: model.covariances[{ci}] is "
                f"not a {d}x{d} square matrix")
            continue
        if any(not math.isfinite(float(v)) for row in cov
               for v in row):
            problems.append(
                f"SCHEMA_MALFORMED: model.covariances[{ci}] "
                "contains non-finite values")
            continue
        if any(abs(float(cov[i][j]) - float(cov[j][i])) > 1e-9
               for i in range(d) for j in range(d)):
            problems.append(
                f"SCHEMA_MALFORMED: model.covariances[{ci}] is "
                "not symmetric")
            continue
        # R9-P09: a degenerate component can never have fitted —
        # every diagonal entry must be positive and the minimum
        # eigenvalue must clear the documented invertibility floor
        # (1e-10; sklearn reg_covar floors are ~1e-6).
        if any(float(cov[i][i]) <= 0.0 for i in range(d)):
            problems.append(
                f"SCHEMA_MALFORMED: model.covariances[{ci}] has a "
                "non-positive diagonal — covariance components "
                "must be invertible")
            continue
        import numpy as _np
        try:
            eig = _np.linalg.eigvalsh(
                _np.asarray(cov, dtype=float))
        except Exception:
            problems.append(
                f"SCHEMA_MALFORMED: model.covariances[{ci}] is "
                "not diagonalizable")
            continue
        if float(eig.min()) <= 1e-10:
            problems.append(
                f"SCHEMA_MALFORMED: model.covariances[{ci}] is "
                "not strictly positive-definite (min eigenvalue "
                "<= 1e-10) — covariance components must be "
                "invertible")
    return problems


def _input_schema_problems(payload: Mapping[str, Any]) -> list[str]:
    """Well-formedness of ``input_schema`` — mirrors
    ``audit._producer_input_schema_findings``: feature order, row
    count, dtypes, and frame shape bound against the artifact's own
    declared fields."""
    schema = payload.get("input_schema")
    if not isinstance(schema, Mapping):
        return ["SCHEMA_MALFORMED: input_schema must be a mapping "
                "with feature_cols, n_rows, dtypes, and shape"]
    problems: list[str] = []
    missing = sorted({"feature_cols", "n_rows", "dtypes", "shape"}
                     - set(schema))
    if missing:
        return [f"SCHEMA_MALFORMED: input_schema lacks {missing}"]
    cols = schema["feature_cols"]
    if not isinstance(cols, (list, tuple)) or not cols or \
            any(not isinstance(c, str) or not c.strip()
                for c in cols):
        problems.append(
            "SCHEMA_MALFORMED: input_schema.feature_cols must be a "
            "non-empty sequence of column names")
    declared = payload.get("feature_cols")
    if isinstance(cols, (list, tuple)) and \
            isinstance(declared, (list, tuple)) and \
            list(cols) != list(declared):
        problems.append(
            "SCHEMA_MALFORMED: input_schema.feature_cols does not "
            "equal the artifact's feature_cols — the feature order "
            "binding is inconsistent")
    n_rows = schema["n_rows"]
    if isinstance(n_rows, bool) or not isinstance(n_rows, int) or \
            n_rows <= 0:
        problems.append(
            "SCHEMA_MALFORMED: input_schema.n_rows must be a "
            "positive integer")
    shape = schema["shape"]
    if not isinstance(shape, (list, tuple)) or len(shape) != 2 or \
            any(isinstance(d, bool) or not isinstance(d, int)
                or d <= 0 for d in shape):
        problems.append(
            "SCHEMA_MALFORMED: input_schema.shape must be two "
            "positive integers")
    else:
        if isinstance(cols, (list, tuple)) and \
                shape[1] != len(cols):
            problems.append(
                "SCHEMA_MALFORMED: input_schema.shape[1] != "
                "len(feature_cols)")
        if isinstance(n_rows, int) and not isinstance(n_rows, bool) \
                and n_rows > 0 and shape[0] != n_rows:
            problems.append(
                "SCHEMA_MALFORMED: input_schema.shape[0] != "
                "input_schema.n_rows")
    frame_rows = payload.get("n_rows")
    if isinstance(n_rows, int) and not isinstance(n_rows, bool) \
            and isinstance(frame_rows, int) and \
            not isinstance(frame_rows, bool) and \
            n_rows != frame_rows:
        problems.append(
            "SCHEMA_MALFORMED: input_schema.n_rows != artifact "
            "n_rows — the input frame binding is inconsistent")
    dtypes = schema["dtypes"]
    if isinstance(dtypes, Mapping):
        if isinstance(cols, (list, tuple)) and \
                set(map(str, dtypes)) != set(map(str, cols)):
            problems.append(
                "SCHEMA_MALFORMED: input_schema.dtypes keys do not "
                "cover exactly the declared feature columns")
    elif not (isinstance(dtypes, (list, tuple)) and
              isinstance(cols, (list, tuple)) and
              len(dtypes) == len(cols) and
              all(isinstance(d, str) for d in dtypes)):
        problems.append(
            "SCHEMA_MALFORMED: input_schema.dtypes must map every "
            "feature column to a dtype or list one dtype per "
            "column")
    return problems


def _source_manifest_problems(
        payload: Mapping[str, Any],
        *, verify_source_bytes: bool = True) -> list[str]:
    """Source-manifest floor — presence is covered by the
    required-fields pass; here: mapping shape, the strict boolean
    fixture marker (R9-P01), non-fixture required keys, sha256 shape
    of ``source_digests``, and — R9-P02 — byte verification of the
    declared ``source_files`` evidence under ``evidence_root`` when
    ``verify_source_bytes`` is on.  ``{"fixture": true}`` is the only
    synthetic bypass; a non-fixture manifest that cannot produce its
    bytes fails closed at every boundary that runs this floor."""
    sm = payload.get("source_manifest")
    if "source_manifest" not in payload:
        return []
    if not isinstance(sm, Mapping):
        return ["SCHEMA_MALFORMED: source_manifest must be a "
                "mapping"]
    problems: list[str] = []
    is_fixture, fixture_problems = fixture_flag(sm)
    problems.extend(fixture_problems)
    if not is_fixture:
        for key in _NONFIXTURE_MANIFEST_KEYS:
            if not sm.get(key):
                problems.append(
                    f"PROVENANCE_MISSING: non-fixture "
                    f"source_manifest lacks {key!r}")
        sds = sm.get("source_digests")
        if isinstance(sds, Sequence) and not isinstance(sds, str):
            for d in sds:
                if not _is_sha256(d):
                    problems.append(
                        "DIGEST_MALFORMED: "
                        "source_manifest.source_digests entry is "
                        "not a 64-hex sha256")
                    break
        if verify_source_bytes:
            for prob in verify_source_evidence(sm):
                problems.append(
                    "PROVENANCE_MISSING: source_manifest evidence "
                    f"byte verification failed: {prob} — a "
                    "non-fixture manifest without verified bytes "
                    "cannot support a terminal status")
    return problems


def _unit_basin_map_problems(
        payload: Mapping[str, Any]) -> list[str]:
    """The C03 unit->basin partition (R9-P10): present, non-empty,
    (non-empty string unit, non-empty string group) pairs, unique
    units covering EXACTLY the assignment sidecar's units, every
    referenced group inside the declared fit ∪ heldout universe,
    and a recomputed ``unit_basin_map_digest`` over the canonical
    pair list."""
    ubm = payload.get("unit_basin_map")
    if "unit_basin_map" not in payload:
        return []   # absence is a required-fields problem already
    if not isinstance(ubm, (list, tuple)) or not ubm:
        return ["PROVENANCE_MISSING: producer payload lacks a "
                "non-empty 'unit_basin_map' — the unit->basin "
                "partition is unbound"]
    problems: list[str] = []
    if any(not isinstance(e, (list, tuple)) or len(e) != 2 or
           not isinstance(e[0], str) or not e[0].strip() or
           not isinstance(e[1], str) or not e[1].strip()
           for e in ubm):
        problems.append(
            "SCHEMA_MALFORMED: unit_basin_map entries must be "
            "(non-empty unit, non-empty group) string pairs — "
            "None and empty labels are not a partition")
        return problems
    units = [u for u, _ in ubm]
    if len(units) != len(set(units)):
        problems.append(
            "SCHEMA_MALFORMED: unit_basin_map has duplicate unit "
            "ids — the partition is not well-defined")
    groups = {str(g) for _, g in ubm}
    declared = {str(g) for g in (payload.get("fit_groups") or ())} \
        | {str(g) for g in
           (payload.get("heldout_groups_declared") or ())}
    if not groups <= declared:
        problems.append(
            f"SCHEMA_MALFORMED: unit_basin_map references groups "
            f"not declared in fit_groups/heldout_groups_declared: "
            f"{sorted(groups - declared)}")
    # R9-V2: the fit and held-out universes are disjoint — a group
    # declared in both is fitted AND held out, so every downstream
    # train/held-out accounting silently straddles the embargo.
    overlap = sorted(
        {str(g) for g in (payload.get("fit_groups") or ())} &
        {str(g) for g in
         (payload.get("heldout_groups_declared") or ())})
    if overlap:
        problems.append(
            f"SCHEMA_MALFORMED: fit_groups and "
            f"heldout_groups_declared overlap {overlap} — a "
            "straddling group sits in both the fit surface and "
            "the holdout")
    # Exact coverage: the partition must name every unit the
    # assignment sidecar emits — no more, no less.
    assignments = payload.get("assignments")
    if isinstance(assignments, (list, tuple)) and all(
            isinstance(r, (list, tuple)) and len(r) == 3 and
            isinstance(r[0], str)
            for r in assignments):
        assigned = {r[0] for r in assignments}
        if set(units) != assigned:
            problems.append(
                "SCHEMA_MALFORMED: unit_basin_map units do not "
                "cover exactly the assignment sidecar's units — "
                f"only-in-map {sorted(set(units) - assigned)[:5]}, "
                f"only-in-assignments "
                f"{sorted(assigned - set(units))[:5]}")
    bound = payload.get("unit_basin_map_digest")
    if _is_sha256(bound):
        recomputed = sha256_canonical(
            canonical_unit_basin_pairs(ubm))
        if recomputed != bound:
            problems.append(
                "DIGEST_MISMATCH: unit_basin_map_digest does not "
                "recompute over the canonical unit->basin pairs — "
                "the partition was altered post-bind")
    return problems


def _fit_partition_problems(
        payload: Mapping[str, Any]) -> list[str]:
    """R7-C09 — the typed ``fit_partition`` binding: record type,
    required sub-keys, cross-field agreement with the artifact's
    declared fit surface, and a recomputed ``fit_partition_digest``.
    Fail closed: any disagreement between the typed partition and
    the flat provenance fields is a binding violation."""
    fp = payload.get("fit_partition")
    if "fit_partition" not in payload:
        return []   # absence is a provenance problem already
    if not isinstance(fp, Mapping):
        return ["SCHEMA_MALFORMED: fit_partition must be a "
                "mapping"]
    problems: list[str] = []
    if fp.get("record_type") != FIT_PARTITION_RECORD_TYPE:
        problems.append(
            f"SCHEMA_MALFORMED: fit_partition.record_type must be "
            f"{FIT_PARTITION_RECORD_TYPE!r}")
    for f in FIT_PARTITION_FIELDS:
        if f not in fp:
            problems.append(
                f"SCHEMA_MALFORMED: fit_partition lacks {f!r}")
    # R9-V11: the typed record admits no extras — an unbound field
    # inside a digested section is provenance the digest claims to
    # cover but the schema never declared.
    extra = sorted(set(fp) - set(FIT_PARTITION_FIELDS))
    if extra:
        problems.append(
            f"SCHEMA_MALFORMED: fit_partition carries undeclared "
            f"fields {extra} — the typed record admits exactly "
            "the documented field set")
    if problems:
        return problems
    # R9-V2: the typed partition's own universes are disjoint too.
    _fp_overlap = sorted(
        {str(g) for g in fp["train_groups"]} &
        {str(g) for g in fp["heldout_groups"]})
    if _fp_overlap:
        problems.append(
            f"SCHEMA_MALFORMED: fit_partition train_groups and "
            f"heldout_groups overlap {_fp_overlap} — a group "
            "cannot be fitted and held out")
    # R9-V11: the declared cutoff must be a real calendar date —
    # the fit window's end is a bound claim, not a free string.
    cutoff = fp.get("cutoff_iso")
    if not isinstance(cutoff, str) or not cutoff.strip():
        problems.append(
            "SCHEMA_MALFORMED: fit_partition.cutoff_iso must be "
            "a non-empty ISO date string")
    else:
        try:
            _date.fromisoformat(cutoff)
        except ValueError:
            problems.append(
                f"SCHEMA_MALFORMED: fit_partition.cutoff_iso "
                f"{cutoff!r} is not a real calendar date")
    # cross-field agreement — the typed partition may never disagree
    # with the flat provenance fields the artifact also carries
    def _sorted_strs(v):
        return sorted(str(x) for x in v) \
            if isinstance(v, (list, tuple)) else None
    tg, fg = _sorted_strs(fp.get("train_groups")), \
        _sorted_strs(payload.get("fit_groups"))
    if tg is None or fg is None or tg != fg:
        problems.append(
            "DIGEST_MISMATCH: fit_partition.train_groups does not "
            "agree with the artifact's fit_groups — the typed "
            "partition was rebound post-production")
    hg, hd = _sorted_strs(fp.get("heldout_groups")), \
        _sorted_strs(payload.get("heldout_groups_declared"))
    if hg is None or hd is None or hg != hd:
        problems.append(
            "DIGEST_MISMATCH: fit_partition.heldout_groups does "
            "not agree with heldout_groups_declared — the typed "
            "partition was rebound post-production")
    for fp_key, art_key in (("n_train_rows", "n_train_rows"),
                            ("n_rows", "n_rows"),
                            ("feature_matrix_digest",
                             "feature_matrix_digest")):
        fv, av = fp.get(fp_key), payload.get(art_key)
        if fv != av:
            problems.append(
                f"DIGEST_MISMATCH: fit_partition.{fp_key} does not "
                f"agree with the artifact's {art_key}")
    if isinstance(fp.get("feature_cols"), (list, tuple)) and \
            isinstance(payload.get("feature_cols"),
                       (list, tuple)) and \
            list(fp["feature_cols"]) != list(payload["feature_cols"]):
        problems.append(
            "DIGEST_MISMATCH: fit_partition.feature_cols does not "
            "agree with the artifact's feature_cols — the feature "
            "order binding is inconsistent")
    if not _is_sha256(fp.get("train_row_keys_digest")):
        problems.append(
            "DIGEST_MALFORMED: fit_partition.train_row_keys_digest "
            "is not a 64-hex sha256")
    declared = payload.get("fit_partition_digest")
    if _is_sha256(declared):
        try:
            recomputed = sha256_canonical(fp)
        except (TypeError, ValueError) as exc:
            problems.append(
                f"PAYLOAD_MALFORMED: fit_partition cannot be "
                f"canonically rehashed: {exc}")
        else:
            if recomputed != declared:
                problems.append(
                    "DIGEST_MISMATCH: fit_partition_digest does "
                    "not recompute over the payload's fit_partition "
                    "block — the typed partition was altered "
                    "post-bind")
    return problems


def _forecast_payload_problems(
        payload: Mapping[str, Any]) -> list[str]:
    """R7-C10 — mode-conditional ``forecast_feature_payload``
    binding.  FORECAST_REGIME requires the typed payload (a bare
    forecast_feature_set string is no longer the only binding);
    RETROSPECTIVE_REGIME must not carry it."""
    mode = payload.get("mode")
    ffp = payload.get("forecast_feature_payload")
    if mode == RegimeMode.FORECAST_REGIME.value:
        problems: list[str] = []
        if not isinstance(ffp, Mapping):
            problems.append(
                "PROVENANCE_MISSING: mode 'FORECAST_REGIME' "
                "requires a bound forecast_feature_payload mapping "
                "— bare forecast_feature_set/"
                "forecast_vintage_digests strings are not a "
                "feature-matrix binding")
            return problems
        if ffp.get("record_type") != FORECAST_PAYLOAD_RECORD_TYPE:
            problems.append(
                f"SCHEMA_MALFORMED: "
                f"forecast_feature_payload.record_type must be "
                f"{FORECAST_PAYLOAD_RECORD_TYPE!r}")
        for f in FORECAST_PAYLOAD_FIELDS:
            if f not in ffp:
                problems.append(
                    f"SCHEMA_MALFORMED: forecast_feature_payload "
                    f"lacks {f!r}")
        ffp_extra = sorted(set(ffp) - set(FORECAST_PAYLOAD_FIELDS))
        if ffp_extra:
            problems.append(
                f"SCHEMA_MALFORMED: forecast_feature_payload "
                f"carries undeclared fields {ffp_extra} — the "
                "typed record admits exactly the documented "
                "field set")
        if "forecast_feature_payload_digest" not in payload:
            problems.append(
                "PROVENANCE_MISSING: FORECAST_REGIME payload "
                "lacks 'forecast_feature_payload_digest'")
        if problems:
            return problems
        # R9-V11: the forecast feature set must live inside the
        # declared feature order, and every vintage digest must be
        # a well-formed sha256.
        fcols = payload.get("feature_cols")
        fset = ffp.get("forecast_feature_set")
        if isinstance(fset, (list, tuple)) and \
                isinstance(fcols, (list, tuple)) and fcols:
            outside = sorted({str(c) for c in fset} -
                             {str(c) for c in fcols})
            if outside:
                problems.append(
                    f"SCHEMA_MALFORMED: forecast_feature_set "
                    f"names features {outside} outside the "
                    "declared feature_cols")
        vds = ffp.get("forecast_vintage_digests")
        if isinstance(vds, (list, tuple)) and \
                any(not _is_sha256(d) for d in vds):
            problems.append(
                "DIGEST_MALFORMED: "
                "forecast_vintage_digests entries must be 64-hex "
                "sha256 digests")

        def _sorted_strs(v):
            return sorted(str(x) for x in v) \
                if isinstance(v, (list, tuple)) else None
        fs, ps = _sorted_strs(ffp.get("forecast_feature_set")), \
            _sorted_strs(payload.get("forecast_feature_set"))
        if fs is None or ps is None or fs != ps:
            problems.append(
                "DIGEST_MISMATCH: "
                "forecast_feature_payload.forecast_feature_set "
                "does not agree with the artifact's "
                "forecast_feature_set")
        vd, pd_ = _sorted_strs(ffp.get("forecast_vintage_digests")), \
            _sorted_strs(payload.get("forecast_vintage_digests"))
        if vd is None or pd_ is None or vd != pd_:
            problems.append(
                "DIGEST_MISMATCH: "
                "forecast_feature_payload.forecast_vintage_digests "
                "does not agree with the artifact's "
                "forecast_vintage_digests")
        if ffp.get("feature_matrix_digest") != \
                payload.get("feature_matrix_digest"):
            problems.append(
                "DIGEST_MISMATCH: "
                "forecast_feature_payload.feature_matrix_digest "
                "does not agree with the artifact's "
                "feature_matrix_digest")
        if isinstance(ffp.get("row_count"), int) and \
                not isinstance(ffp["row_count"], bool) and \
                isinstance(payload.get("n_rows"), int) and \
                not isinstance(payload["n_rows"], bool) and \
                ffp["row_count"] != payload["n_rows"]:
            problems.append(
                "DIGEST_MISMATCH: "
                "forecast_feature_payload.row_count does not "
                "agree with the artifact's n_rows")
        if not _is_sha256(ffp.get("row_keys_digest")):
            problems.append(
                "DIGEST_MALFORMED: "
                "forecast_feature_payload.row_keys_digest is not "
                "a 64-hex sha256")
        declared = payload.get("forecast_feature_payload_digest")
        if _is_sha256(declared):
            try:
                recomputed = sha256_canonical(ffp)
            except (TypeError, ValueError) as exc:
                problems.append(
                    f"PAYLOAD_MALFORMED: forecast_feature_payload "
                    f"cannot be canonically rehashed: {exc}")
            else:
                if recomputed != declared:
                    problems.append(
                        "DIGEST_MISMATCH: "
                        "forecast_feature_payload_digest does not "
                        "recompute over the payload's "
                        "forecast_feature_payload block")
        return problems
    if mode == RegimeMode.RETROSPECTIVE_REGIME.value:
        if ffp is not None or \
                "forecast_feature_payload_digest" in payload:
            return ["SCHEMA_MALFORMED: RETROSPECTIVE_REGIME mode "
                    "must not carry forecast_feature_payload or "
                    "forecast_feature_payload_digest — forecast "
                    "evidence cannot silently transfer into a "
                    "retrospective artifact"]
        # R9-V5: the FLAT forecast fields are banned under
        # retrospective too — a non-empty vintage/feature
        # declaration is forecast evidence whether or not the
        # typed payload rides along.
        flat_problems: list[str] = []
        for f in ("forecast_vintage_digests",
                  "forecast_feature_set"):
            v = payload.get(f)
            if isinstance(v, (list, tuple)) and v or \
                    isinstance(v, str) and v.strip():
                flat_problems.append(
                    f"SCHEMA_MALFORMED: RETROSPECTIVE_REGIME mode "
                    f"must not carry a non-empty {f} — forecast "
                    "declarations cannot ride a retrospective "
                    "artifact")
        return flat_problems
    if mode is not None:
        return [f"SCHEMA_MALFORMED: mode {mode!r} is not a "
                "declared RegimeMode value"]
    return []


def _run_manifest_problems(
        payload: Mapping[str, Any]) -> list[str]:
    """R9-P04: ``run_manifest`` must deserialize through
    ``records.deserialize_record`` as exactly ``RunManifestV0`` —
    the exact record_type tag, the exact serialized field set
    (unknown or missing fields reject), a problem-free record equal
    to its canonical ``to_dict()`` — and it must bind THIS
    artifact's input bytes, assignment output, environment digest,
    and first declared seed."""
    if "run_manifest" not in payload:
        return []   # absence is a required-fields problem already
    rm = payload["run_manifest"]
    if not isinstance(rm, Mapping):
        return ["SCHEMA_MALFORMED: run_manifest must be a mapping "
                "serializing a RunManifestV0 record"]
    if rm.get("record_type") != "RunManifestV0":
        return ["SCHEMA_MALFORMED: run_manifest.record_type must "
                "be 'RunManifestV0' — the typed run provenance "
                "record cannot be substituted or left untagged"]
    problems: list[str] = []
    try:
        rec = deserialize_record(rm)
    except (TypeError, ValueError) as exc:
        return [f"SCHEMA_MALFORMED: run_manifest does not "
                f"deserialize as RunManifestV0: {exc}"]
    if type(rec) is not RunManifestV0:
        return ["SCHEMA_MALFORMED: run_manifest did not produce a "
                "RunManifestV0 record"]
    declared_fields = {f.name for f in
                       dataclasses.fields(RunManifestV0)}
    missing = sorted(declared_fields - (set(rm) - {"record_type"}))
    if missing:
        problems.append(
            f"SCHEMA_MALFORMED: run_manifest lacks serialized "
            f"fields {missing} — the bound record must be "
            "complete, not defaulted")
    for prob in rec.problems():
        problems.append(
            f"SCHEMA_MALFORMED: run_manifest record is invalid: "
            f"{prob}")
    try:
        if canonical_json(rm) != canonical_json(rec.to_dict()):
            problems.append(
                "SCHEMA_MALFORMED: run_manifest does not equal "
                "the canonical RunManifestV0.to_dict() "
                "serialization")
    except (TypeError, ValueError) as exc:
        problems.append(
            f"PAYLOAD_MALFORMED: run_manifest cannot be "
            f"canonically rehashed: {exc}")
    if payload.get("input_bytes_digest") not in \
            tuple(rec.input_digests):
        problems.append(
            "DIGEST_MISMATCH: run_manifest.input_digests does not "
            "bind the artifact's input_bytes_digest — the run "
            "cannot name its exact inputs")
    if payload.get("assignment_digest") not in \
            tuple(rec.output_digests):
        problems.append(
            "DIGEST_MISMATCH: run_manifest.output_digests does "
            "not bind the artifact's assignment_digest — the "
            "run's outputs do not cover this artifact")
    if rec.environment_digest != \
            payload.get("environment_digest"):
        problems.append(
            "DIGEST_MISMATCH: run_manifest.environment_digest "
            "does not equal the artifact's environment_digest")
    declared_seeds = payload.get("seeds_declared")
    if isinstance(declared_seeds, (list, tuple)) and \
            declared_seeds and \
            rec.seed != declared_seeds[0]:
        problems.append(
            "SCHEMA_MALFORMED: run_manifest.seed does not equal "
            "seeds_declared[0] — the run's declared seed is "
            "unbound")
    return problems


def _input_values_problems(
        payload: Mapping[str, Any]) -> list[str]:
    """R9-P08: the encoded feature matrix itself — a non-empty
    rectangular matrix sized to ``(n_rows, len(feature_cols))``
    carrying only decodable values (finite numbers, ``None`` for
    NaN, ``"Infinity"``/``"-Infinity"`` tokens), whose 6-decimal
    semantic digest must equal EVERY carried
    ``feature_matrix_digest`` (artifact, fit_partition, and the
    forecast payload when present)."""
    if "input_values" not in payload:
        return []   # absence is a required-fields problem already
    vals = payload["input_values"]
    if not isinstance(vals, (list, tuple)) or not vals or \
            any(not isinstance(r, (list, tuple)) for r in vals):
        return ["SCHEMA_MALFORMED: input_values must be a "
                "non-empty sequence of feature rows"]
    problems: list[str] = []
    width = len(vals[0])
    if width == 0 or any(len(r) != width for r in vals):
        problems.append(
            "SCHEMA_MALFORMED: input_values is not a rectangular "
            "matrix — every row must carry the same feature count")
    n_rows = payload.get("n_rows")
    if isinstance(n_rows, int) and not isinstance(n_rows, bool) \
            and len(vals) != n_rows:
        problems.append(
            "SCHEMA_MALFORMED: input_values row count does not "
            "equal the artifact's n_rows — the bound feature "
            "matrix is inconsistent with the declared frame")
    fcols = payload.get("feature_cols")
    if isinstance(fcols, (list, tuple)) and fcols and \
            width != len(fcols) and \
            all(len(r) == width for r in vals):
        problems.append(
            "SCHEMA_MALFORMED: input_values row width does not "
            "equal len(feature_cols) — the feature order binding "
            "is inconsistent")
    for i, row in enumerate(vals):
        # R9-V12: the nonfinite-token membership test must not run
        # on unhashable values — a dict/list element is undecodable,
        # not a crash.
        bad = next((v for v in row
                    if not (v is None or
                            (isinstance(v, str) and
                             v in _NONFINITE_TOKENS) or
                            (not isinstance(v, bool) and
                             isinstance(v, (int, float)) and
                             math.isfinite(float(v))))), ...)
        if bad is not ...:
            problems.append(
                f"SCHEMA_MALFORMED: input_values[{i}] carries an "
                f"undecodable value {bad!r} — only finite "
                "numbers, None (NaN), and 'Infinity'/'-Infinity' "
                "tokens are admissible")
            break
    if problems:
        return problems
    try:
        recomputed = semantic_feature_matrix_digest(vals)
    except (TypeError, ValueError) as exc:
        return [f"PAYLOAD_MALFORMED: input_values cannot be "
                f"canonically rehashed: {exc}"]
    declared = payload.get("feature_matrix_digest")
    if _is_sha256(declared) and declared != recomputed:
        problems.append(
            "DIGEST_MISMATCH: feature_matrix_digest does not "
            "recompute over input_values under the 6-decimal "
            "semantic normalization — the feature matrix was "
            "altered post-bind")
    fp = payload.get("fit_partition")
    if isinstance(fp, Mapping) and \
            _is_sha256(fp.get("feature_matrix_digest")) and \
            fp["feature_matrix_digest"] != recomputed:
        problems.append(
            "DIGEST_MISMATCH: fit_partition.feature_matrix_digest "
            "does not recompute over input_values — the typed "
            "partition binds a different feature matrix")
    ffp = payload.get("forecast_feature_payload")
    if isinstance(ffp, Mapping) and \
            _is_sha256(ffp.get("feature_matrix_digest")) and \
            ffp["feature_matrix_digest"] != recomputed:
        problems.append(
            "DIGEST_MISMATCH: forecast_feature_payload."
            "feature_matrix_digest does not recompute over "
            "input_values — the forecast binding references a "
            "different feature matrix")
    # R9-V4: the byte digest is material-bound too — decode the
    # encoded matrix to the producer's float64 byte domain
    # (None -> NaN, the two non-finite tokens -> +-inf) and
    # recompute the contiguous-byte sha256 the producer hashed.
    declared_bytes = payload.get("input_bytes_digest")
    if _is_sha256(declared_bytes):
        import numpy as _np
        try:
            decoded = _np.asarray(
                [[_np.nan if v is None else
                  _np.inf if v == "Infinity" else
                  -_np.inf if v == "-Infinity" else float(v)
                  for v in row] for row in vals],
                dtype=_np.float64)
            actual_bytes = hashlib.sha256(
                _np.ascontiguousarray(decoded).tobytes()).hexdigest()
        except (TypeError, ValueError) as exc:
            problems.append(
                f"PAYLOAD_MALFORMED: input_values cannot be "
                f"decoded to the raw feature byte domain: {exc}")
        else:
            if actual_bytes != declared_bytes:
                problems.append(
                    "DIGEST_MISMATCH: input_bytes_digest does not "
                    "recompute over the decoded input_values bytes "
                    "— the bound raw matrix was altered post-bind")
    return problems


def _row_universe_problems(
        payload: Mapping[str, Any]) -> list[str]:
    """R9-P05/P06: the assignment sidecar IS the row universe —
    ``preprocessing.row_keys_digest`` and (for forecast mode) the
    forecast payload's ``row_keys_digest``/``row_count`` recompute
    from it; ``fit_partition.train_row_keys_digest`` recomputes
    over the assignment rows whose unit's ``unit_basin_map`` group
    is in ``fit_partition.train_groups``; and ``n_rows`` /
    ``n_train_rows`` are row counts, not claims."""
    problems: list[str] = []
    assignments = payload.get("assignments")
    rows_ok = isinstance(assignments, (list, tuple)) and \
        bool(assignments) and all(
            isinstance(r, (list, tuple)) and len(r) == 3 and
            isinstance(r[0], str) and isinstance(r[1], str) and
            isinstance(r[2], int) and not isinstance(r[2], bool)
            for r in assignments)
    if "assignments" in payload and not rows_ok:
        return ["SCHEMA_MALFORMED: assignments must be a "
                "non-empty sequence of [unit, date, regime_id] "
                "rows with string unit/date and integer "
                "regime_id — the row universe is unreadable"]
    if not rows_ok:
        return problems   # absence is a required-fields problem
    # R9-V3/V7: the row universe must be a well-formed frame — one
    # regime per unit-day (duplicate rows admit double-counted
    # support), calendar-real ISO dates, non-empty unit ids, and
    # labels inside the declared component range.
    seen_cells: set = set()
    dup_cell = False
    bad_date = False
    empty_unit = False
    k = payload.get("k")
    bad_label = False
    for u, d, r in assignments:
        if (u, d) in seen_cells:
            dup_cell = True
        seen_cells.add((u, d))
        if not u.strip():
            empty_unit = True
        try:
            _date.fromisoformat(d)
        except ValueError:
            bad_date = True
        if isinstance(k, int) and not isinstance(k, bool) and \
                k > 0 and not (0 <= r < k):
            bad_label = True
    if dup_cell:
        problems.append(
            "SCHEMA_MALFORMED: duplicate (unit, date) assignment "
            "rows — one regime per unit-day; a duplicated frame "
            "row double-counts the support surface")
    if empty_unit:
        problems.append(
            "SCHEMA_MALFORMED: assignment unit_id must be a "
            "non-empty string")
    if bad_date:
        problems.append(
            "SCHEMA_MALFORMED: assignment dates must be real "
            "ISO calendar dates (YYYY-MM-DD)")
    if bad_label:
        problems.append(
            "SCHEMA_MALFORMED: assignment regime_id is outside "
            "[0, k) — the label must index a declared component")
    unit_keys = [row_key(u, d) for u, d, _ in assignments]
    full_digest = sorted_row_key_digest(unit_keys)
    n_rows = payload.get("n_rows")
    if isinstance(n_rows, int) and not isinstance(n_rows, bool) \
            and n_rows != len(assignments):
        problems.append(
            "SCHEMA_MALFORMED: n_rows does not equal the "
            "assignment row count — the declared frame size is "
            "inconsistent with the bound sidecar")
    pre = payload.get("preprocessing")
    if isinstance(pre, Mapping):
        declared = pre.get("row_keys_digest")
        if _is_sha256(declared) and declared != full_digest:
            problems.append(
                "DIGEST_MISMATCH: preprocessing.row_keys_digest "
                "does not recompute over the assignment row "
                "universe — the fitted row keys are unbound")
    # P05: the train row universe = assignment rows whose unit's
    # declared group is inside fit_partition.train_groups (groups
    # cannot straddle the fit mask — a straddling group would sit
    # in both declared universes and is already rejected upstream).
    ubm_groups: dict[str, str] = {}
    ubm = payload.get("unit_basin_map")
    if isinstance(ubm, (list, tuple)):
        for e in ubm:
            if isinstance(e, (list, tuple)) and len(e) == 2 and \
                    isinstance(e[0], str) and isinstance(e[1], str):
                ubm_groups[e[0]] = e[1]
    fp = payload.get("fit_partition")
    if isinstance(fp, Mapping) and ubm_groups and \
            isinstance(fp.get("train_groups"), (list, tuple)):
        train_groups = {str(g) for g in fp["train_groups"]}
        train_keys = [k for k, (u, _d, _r)
                      in zip(unit_keys, assignments)
                      if ubm_groups.get(u) in train_groups]
        declared = fp.get("train_row_keys_digest")
        if _is_sha256(declared) and \
                declared != sorted_row_key_digest(train_keys):
            problems.append(
                "DIGEST_MISMATCH: fit_partition."
                "train_row_keys_digest does not recompute over "
                "the assignment rows whose unit maps into "
                "train_groups — the fit surface was rebound "
                "post-production")
        n_train = payload.get("n_train_rows")
        if isinstance(n_train, int) and \
                not isinstance(n_train, bool) and \
                n_train != len(train_keys):
            problems.append(
                "SCHEMA_MALFORMED: n_train_rows does not equal "
                "the recomputed train row count — the fit mask "
                "accounting is inconsistent")
    ffp = payload.get("forecast_feature_payload")
    if isinstance(ffp, Mapping):
        declared = ffp.get("row_keys_digest")
        if _is_sha256(declared) and declared != full_digest:
            problems.append(
                "DIGEST_MISMATCH: forecast_feature_payload."
                "row_keys_digest does not equal the assignment "
                "row-universe digest — the forecast frame is not "
                "the fitted frame")
        rc = ffp.get("row_count")
        if isinstance(rc, int) and not isinstance(rc, bool) and \
                rc != len(assignments):
            problems.append(
                "SCHEMA_MALFORMED: forecast_feature_payload."
                "row_count does not equal the assignment row "
                "count")
    return problems


def _config_cross_binding_problems(
        payload: Mapping[str, Any]) -> list[str]:
    """R9-P07: the serialized producer ``config`` must agree with
    the artifact's flat declared fields — every check mirrors a
    field ``run_regimes`` emits verbatim: mode, the declared seed
    set, the forecast bindings, the source manifest, holdout
    membership, the fit groups the mask actually used, the modal-K
    candidate universe, the applied missingness policy, and (when
    the config carries it) the data class.  Disagreement means the
    configuration was rebound post-production."""
    if "config" not in payload:
        return []   # absence is a required-fields problem already
    cfg = payload["config"]
    if not isinstance(cfg, Mapping):
        return ["SCHEMA_MALFORMED: config must be a mapping — "
                "the serialized producer configuration is "
                "unbound"]
    problems: list[str] = []

    def _strs(v):
        return sorted(str(x) for x in v) \
            if isinstance(v, (list, tuple)) else []

    if cfg.get("mode") != payload.get("mode"):
        problems.append(
            "SCHEMA_MALFORMED: config.mode does not equal the "
            "artifact's mode — the run-mode declaration diverges "
            "from the serialized configuration")
    cfg_seeds = cfg.get("seeds")
    seeds_declared = payload.get("seeds_declared")
    if isinstance(seeds_declared, (list, tuple)) and (
            not isinstance(cfg_seeds, (list, tuple)) or
            {str(s) for s in cfg_seeds} !=
            {str(s) for s in seeds_declared}):
        problems.append(
            "SCHEMA_MALFORMED: config.seeds does not equal the "
            "declared seed set — the serialized configuration "
            "diverges from seeds_declared")
    seeds = payload.get("seeds")
    if isinstance(seeds, (list, tuple)) and \
            isinstance(seeds_declared, (list, tuple)) and \
            list(seeds) != list(seeds_declared):
        problems.append(
            "SCHEMA_MALFORMED: seeds does not equal "
            "seeds_declared — the executed seed list diverges "
            "from the declared one")
    for key in ("forecast_feature_set",
                "forecast_vintage_digests"):
        if _strs(cfg.get(key)) != _strs(payload.get(key)):
            problems.append(
                f"SCHEMA_MALFORMED: config.{key} does not agree "
                f"with the artifact's {key} — the forecast "
                "binding diverges from the serialized "
                "configuration")
    if "source_manifest" in payload:
        try:
            if canonical_json(cfg.get("source_manifest")) != \
                    canonical_json(payload.get("source_manifest")):
                problems.append(
                    "SCHEMA_MALFORMED: config.source_manifest "
                    "does not deep-equal the artifact's "
                    "source_manifest — the input-governance "
                    "record diverges between the two bound "
                    "copies")
        except (TypeError, ValueError) as exc:
            problems.append(
                f"PAYLOAD_MALFORMED: config.source_manifest "
                f"cannot be canonically serialized: {exc}")
    if _strs(cfg.get("heldout_groups")) != \
            _strs(payload.get("heldout_groups_declared")):
        problems.append(
            "SCHEMA_MALFORMED: config.heldout_groups does not "
            "equal heldout_groups_declared — the holdout "
            "declaration diverges from the serialized "
            "configuration")
    cfg_train = {str(g) for g in cfg.get("train_groups") or ()}
    fit_groups = payload.get("fit_groups")
    if isinstance(fit_groups, (list, tuple)):
        outside = sorted({str(g) for g in fit_groups} - cfg_train)
        if outside:
            problems.append(
                f"SCHEMA_MALFORMED: fit_groups {outside} are "
                "outside config.train_groups — the fit mask "
                "used groups the serialized configuration never "
                "declared")
    k = payload.get("k")
    if isinstance(k, int) and not isinstance(k, bool) and \
            k not in set(cfg.get("k_candidates") or ()):
        problems.append(
            "SCHEMA_MALFORMED: the modal k is outside "
            "config.k_candidates — the selected component count "
            "was never a declared candidate")
    applied = payload.get("missingness_applied")
    if isinstance(applied, Mapping) and \
            applied.get("policy") is not None and \
            cfg.get("missingness_policy") is not None and \
            applied["policy"] != cfg["missingness_policy"]:
        problems.append(
            "SCHEMA_MALFORMED: config.missingness_policy does "
            "not equal missingness_applied.policy — the applied "
            "policy diverges from the declared one")
    if "data_class" in cfg and "data_class" in payload and \
            cfg["data_class"] != payload["data_class"]:
        problems.append(
            "SCHEMA_MALFORMED: config.data_class does not equal "
            "the artifact's data_class")
    return problems


def _missingness_problems(
        payload: Mapping[str, Any]) -> list[str]:
    """R9-V10: ``missingness_applied`` is a bound accounting record,
    not a free dict — it must declare a policy and internally-
    consistent train-row totals that agree with the artifact's
    recomputed ``n_train_rows``."""
    ma = payload.get("missingness_applied")
    if "missingness_applied" not in payload:
        return []   # absence is a required-fields problem already
    if not isinstance(ma, Mapping):
        return ["SCHEMA_MALFORMED: missingness_applied must be a "
                "mapping carrying the applied-policy accounting"]
    problems: list[str] = []
    if not isinstance(ma.get("policy"), str) or \
            not ma["policy"].strip():
        problems.append(
            "SCHEMA_MALFORMED: missingness_applied.policy must be "
            "a non-empty string")
    ints = {}
    for key in ("train_rows_total", "train_rows_fitted",
                "train_rows_dropped"):
        v = ma.get(key)
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            problems.append(
                f"SCHEMA_MALFORMED: missingness_applied.{key} "
                "must be a non-negative integer")
        else:
            ints[key] = v
    if len(ints) == 3 and \
            ints["train_rows_fitted"] + ints["train_rows_dropped"] \
            != ints["train_rows_total"]:
        problems.append(
            "SCHEMA_MALFORMED: missingness_applied fitted + "
            "dropped does not equal total — the row accounting "
            "is inconsistent")
    n_train = payload.get("n_train_rows")
    if isinstance(n_train, int) and not isinstance(n_train, bool) \
            and "train_rows_fitted" in ints and \
            ints["train_rows_fitted"] != n_train:
        problems.append(
            "SCHEMA_MALFORMED: missingness_applied."
            "train_rows_fitted does not equal n_train_rows — "
            "the applied-policy accounting disagrees with the "
            "bound fit surface")
    return problems


def _seed_stability_problems(
        payload: Mapping[str, Any]) -> list[str]:
    """R9-P12 (seed floor): ``seed_coverage`` keys are exactly the
    declared seeds carrying the declared state vocabulary; a
    terminal descriptive status may not sit over a failed or
    uncovered seed; ``per_seed_best_k`` covers the declared seeds;
    ``modal_k_frequency`` is a real frequency in [0, 1]."""
    problems: list[str] = []
    declared = payload.get("seeds_declared")
    declared_ok = isinstance(declared, (list, tuple)) and \
        bool(declared) and all(
            isinstance(s, int) and not isinstance(s, bool)
            for s in declared)
    if "seeds_declared" in payload and not declared_ok:
        problems.append(
            "SCHEMA_MALFORMED: seeds_declared must be a "
            "non-empty sequence of integers")
    coverage = payload.get("seed_coverage")
    if "seed_coverage" in payload:
        if not isinstance(coverage, Mapping) or not coverage:
            problems.append(
                "SCHEMA_MALFORMED: seed_coverage must be a "
                "non-empty seed -> state map")
        else:
            bad_states = {str(k): v for k, v in coverage.items()
                          if v not in _SEED_COVERAGE_STATES}
            if bad_states:
                problems.append(
                    "SCHEMA_MALFORMED: seed_coverage states "
                    f"outside {sorted(_SEED_COVERAGE_STATES)}: "
                    f"{bad_states}")
            if declared_ok and \
                    {str(k) for k in coverage} != \
                    {str(s) for s in declared}:
                problems.append(
                    "SCHEMA_MALFORMED: seed_coverage keys do not "
                    "equal seeds_declared — every declared seed "
                    "must be accounted for")
            if payload.get("status") == \
                    "DESCRIPTIVE_REGIME_ONLY":
                failed = sorted(
                    str(k) for k, v in coverage.items()
                    if v != "converged")
                if failed:
                    problems.append(
                        "SCHEMA_MALFORMED: declared seeds "
                        f"{failed} are not 'converged' under a "
                        "terminal descriptive status — the "
                        "artifact cannot outrun its own seed "
                        "evidence")
    best = payload.get("per_seed_best_k")
    if "per_seed_best_k" in payload:
        if not isinstance(best, Mapping):
            problems.append(
                "SCHEMA_MALFORMED: per_seed_best_k must be a "
                "seed -> k map")
        elif declared_ok and {str(k) for k in best} != \
                {str(s) for s in declared}:
            problems.append(
                "SCHEMA_MALFORMED: per_seed_best_k keys do not "
                "equal seeds_declared — every declared seed "
                "must name its best K")
        # R9-V11: every named best-k must be a declared k
        # candidate — a seed cannot select outside the sweep.
        if isinstance(best, Mapping):
            cfg = payload.get("config")
            cands = cfg.get("k_candidates") \
                if isinstance(cfg, Mapping) else None
            if isinstance(cands, (list, tuple)) and cands:
                cand_set = {int(c) for c in cands
                            if isinstance(c, int) and
                            not isinstance(c, bool)}
                bad_best = {str(k): v for k, v in best.items()
                            if isinstance(v, bool) or
                            not isinstance(v, int) or
                            v not in cand_set}
                if bad_best:
                    problems.append(
                        f"SCHEMA_MALFORMED: per_seed_best_k "
                        f"values outside config.k_candidates: "
                        f"{bad_best} — a seed's selected K must "
                        "come from the declared sweep")
    freq = payload.get("modal_k_frequency")
    if freq is not None and (
            isinstance(freq, bool) or
            not isinstance(freq, (int, float)) or
            not 0.0 <= float(freq) <= 1.0):
        problems.append(
            "SCHEMA_MALFORMED: modal_k_frequency must be a "
            "frequency in [0, 1]")
    return problems


def _gate_problems(payload: Mapping[str, Any]) -> list[str]:
    """R9-P12 (gate floor): ``stability.required_gates`` is exactly
    the declared ``REQUIRED_REGIME_GATE_NAMES`` universe, boolean
    verdicts only, status-consistent — DESCRIPTIVE_REGIME_ONLY
    requires every gate closed; CANDIDATE_ONLY and
    UNSUPERVISED_STRUCTURE_NOT_STABLE require at least one open
    gate — and the bound ``stability_report_digest`` recomputes
    over the carried stability block."""
    problems: list[str] = []
    stability = payload.get("stability")
    if "stability" not in payload:
        return problems   # absence is a required-fields problem
    if not isinstance(stability, Mapping):
        return ["SCHEMA_MALFORMED: stability must be a mapping "
                "carrying the required_gates evidence"]
    gates = stability.get("required_gates")
    if not isinstance(gates, Mapping) or not gates:
        problems.append(
            "PROVENANCE_MISSING: stability.required_gates is "
            "missing or empty — the artifact carries no gate "
            "evidence to audit")
        gates = None
    else:
        missing = sorted(REQUIRED_REGIME_GATE_NAMES - set(gates))
        if missing:
            problems.append(
                f"PROVENANCE_MISSING: required_gates omits "
                f"declared gates {missing} — an absent gate is "
                "not a closed gate")
        extra = sorted(set(gates) - REQUIRED_REGIME_GATE_NAMES)
        if extra:
            problems.append(
                f"SCHEMA_MALFORMED: required_gates carries "
                f"undeclared gates {extra} — the declared gate "
                "universe is exact, nothing more")
        if any(not isinstance(v, bool) for v in gates.values()):
            problems.append(
                "SCHEMA_MALFORMED: required_gates values must "
                "be booleans — the flat gate map admits no "
                "tri-state or numeric verdicts")
    if gates and set(gates) == REQUIRED_REGIME_GATE_NAMES and \
            all(isinstance(v, bool) for v in gates.values()):
        status = payload.get("status")
        if status == "DESCRIPTIVE_REGIME_ONLY":
            open_gates = sorted(g for g, v in gates.items()
                                if v is not True)
            if open_gates:
                problems.append(
                    f"SCHEMA_MALFORMED: required_gates "
                    f"{open_gates} are open yet the artifact "
                    "claims a terminal descriptive status — a "
                    "frozen artifact cannot outrun its own "
                    "gate evidence")
        elif status in ("CANDIDATE_ONLY",
                        "UNSUPERVISED_STRUCTURE_NOT_STABLE") and \
                all(gates.values()):
            problems.append(
                f"SCHEMA_MALFORMED: status {status!r} requires "
                "at least one open required gate — a fully "
                "closed gate map is DESCRIPTIVE_REGIME_ONLY, "
                "not a demotion")
    declared = payload.get("stability_report_digest")
    if _is_sha256(declared):
        try:
            recomputed = sha256_canonical(stability)
        except (TypeError, ValueError) as exc:
            problems.append(
                f"PAYLOAD_MALFORMED: stability block is not "
                f"canonically serializable: {exc}")
        else:
            if recomputed != declared:
                problems.append(
                    "DIGEST_MISMATCH: stability_report_digest "
                    "does not recompute over the payload's "
                    "stability block — the gate evidence may "
                    "have been rewritten post-bind")
    return problems


def _null_problems(payload: Mapping[str, Any]) -> list[str]:
    """R9-P12 (null floor): the ``nulls`` section carries the two
    declared families (``shuffled``, ``season_matched``) with their
    serialized fields, each ``family_digest`` recomputing over the
    carried record exactly as the producer bound it, the ``k1_bic``
    list present, and ``null_model_digest`` recomputing over the
    carried k1_bic list plus the family digests."""
    nulls = payload.get("nulls")
    if "nulls" not in payload:
        return []   # absence is a required-fields problem already
    if not isinstance(nulls, Mapping):
        return ["SCHEMA_MALFORMED: nulls must be a mapping "
                "carrying the declared null families"]
    problems: list[str] = []
    declared = payload.get("seeds_declared")
    if not isinstance(declared, (list, tuple)):
        declared = payload.get("seeds")
    seed_cycle = list(declared) \
        if isinstance(declared, (list, tuple)) else declared
    families: dict[str, Any] = {}
    for fam in sorted(nulls, key=str):
        rec = nulls[fam]
        if not isinstance(rec, Mapping) or \
                "family_digest" not in rec:
            continue
        families[str(fam)] = rec
    for fam in _NULL_FAMILY_NAMES:
        rec = nulls.get(fam)
        if not isinstance(rec, Mapping):
            problems.append(
                f"PROVENANCE_MISSING: nulls lacks the {fam!r} "
                "family record — the declared null evidence is "
                "incomplete")
            continue
        missing = sorted(f for f in _NULL_FAMILY_REQUIRED
                         if f not in rec)
        if missing:
            problems.append(
                f"SCHEMA_MALFORMED: nulls.{fam} lacks "
                f"serialized fields {missing} — a bound null "
                "family must carry its full record")
            continue
        # R9-V11: replicate accounting is bounded — executed
        # replicates (succeeded + failed) cannot exceed the declared
        # count.  A skipped family legitimately reports 0 + 0, so
        # equality is NOT required — the bound only.
        ns, nf, nr = (rec.get("n_succeeded"), rec.get("n_failed"),
                      rec.get("n_replicates"))
        if all(isinstance(x, int) and not isinstance(x, bool)
               for x in (ns, nf, nr)) and (ns + nf > nr or
                                           min(ns, nf, nr) < 0):
            problems.append(
                f"SCHEMA_MALFORMED: nulls.{fam} n_succeeded + "
                "n_failed exceeds n_replicates (or a count is "
                "negative) — the replicate accounting is "
                "inconsistent")
        if not _is_sha256(rec["family_digest"]):
            problems.append(
                f"DIGEST_MALFORMED: nulls.{fam}.family_digest "
                "is not a 64-hex sha256")
            continue
        try:
            expected = sha256_canonical({
                "family": fam,
                "seed_cycle": seed_cycle,
                "n_replicates": rec["n_replicates"],
                "statistic": rec["statistic"],
                "p_value": rec.get("p_value"),
                "observed": rec.get("observed"),
                "alpha": rec.get("alpha"),
                "n_succeeded": rec.get("n_succeeded"),
                "n_failed": rec.get("n_failed"),
                "status": rec.get("status"),
                "reason": rec.get("reason"),
                "selection": rec.get("selection"),
                "null_stat_min": rec.get("null_stat_min"),
                "null_stat_max": rec.get("null_stat_max"),
                "null_k_distribution":
                    rec.get("null_k_distribution", {}),
                "replicates": rec.get("replicates", [])})
        except (TypeError, ValueError) as exc:
            problems.append(
                f"PAYLOAD_MALFORMED: nulls.{fam} cannot be "
                f"canonically rehashed: {exc}")
            continue
        if rec["family_digest"] != expected:
            problems.append(
                f"DIGEST_MISMATCH: nulls.{fam}.family_digest "
                "does not recompute over the carried "
                "null-family record — the serialized replicate "
                "evidence was rewritten post-bind")
    k1_bic = nulls.get("k1_bic")
    if not isinstance(k1_bic, (list, tuple)):
        problems.append(
            "PROVENANCE_MISSING: nulls lacks the serialized "
            "'k1_bic' list — the K=1 null evidence is unbound")
    declared_nm = payload.get("null_model_digest")
    if isinstance(k1_bic, (list, tuple)) and \
            _is_sha256(declared_nm):
        try:
            expected_nm = sha256_canonical({
                "k1_bic": list(k1_bic),
                "null_families": {fam: rec.get("family_digest")
                                  for fam, rec in
                                  families.items()}})
        except (TypeError, ValueError) as exc:
            problems.append(
                f"PAYLOAD_MALFORMED: null_model_digest inputs "
                f"cannot be canonically rehashed: {exc}")
        else:
            if expected_nm != declared_nm:
                problems.append(
                    "DIGEST_MISMATCH: null_model_digest does "
                    "not recompute over the carried k1_bic "
                    "list and family digests — the null-model "
                    "binding was rewritten post-bind")
    return problems


def validate_producer_payload(
        payload: Any, *,
        verify_source_bytes: bool = True) -> list[str]:
    """The shared producer-payload provenance floor (R8-C01,
    hardened R9-P01..P12).

    Field presence over ``PRODUCER_REQUIRED_FIELDS``, 64-hex shape
    over ``PRODUCER_DIGEST_FIELDS``, row accounting, frozen/blinding
    flags, the seed-count floor, occupancy/k agreement, model and
    input-schema well-formedness plus the strictly-PSD covariance
    floor (P09), the strict-boolean fixture marker and byte-verified
    non-fixture source evidence (P01/P02), the unit->basin partition
    with exact unit coverage and its canonical pair digest (P10),
    the typed ``RunManifestV0`` deserialization and its input/output/
    environment/seed bindings (P04), the recomputed train/full row
    universes (P05/P06), the serialized-config cross-binding (P07),
    the recomputed 6-decimal feature-matrix digest over
    ``input_values`` (P08), the seed-coverage/gate/null shared
    semantics (P12), the typed ``fit_partition`` binding (R7-C09),
    the mode-conditional ``forecast_feature_payload`` binding
    (R7-C10), and a recomputed ``regime_artifact_digest`` envelope —
    a rehashed partial artifact can no longer pass any boundary that
    runs this first.

    ``verify_source_bytes=False`` restores the pure offline surface:
    non-fixture manifests get the schema floor only, no file I/O.
    """
    if not isinstance(payload, Mapping):
        return ["PAYLOAD_MALFORMED: producer payload is not a "
                "mapping"]
    problems: list[str] = []
    for field in PRODUCER_REQUIRED_FIELDS:
        if field not in payload:
            problems.append(
                f"PROVENANCE_MISSING: producer payload lacks "
                f"{field!r} — a regime artifact without complete "
                "provenance cannot support a terminal descriptive "
                "status")
    for field in PRODUCER_DIGEST_FIELDS:
        if field in payload and not _is_sha256(payload[field]):
            problems.append(
                f"DIGEST_MALFORMED: {field} is not a 64-hex "
                "sha256 — a carried digest that cannot recompute "
                "is not a binding")
    for field in ("n_train_rows", "n_rows"):
        if field in payload and (
                isinstance(payload[field], bool) or
                not isinstance(payload[field], int) or
                payload[field] <= 0):
            problems.append(
                f"SCHEMA_MALFORMED: {field} must be a positive "
                "integer")
    if all(isinstance(payload.get(f), int) and
           not isinstance(payload[f], bool) and payload[f] > 0
           for f in ("n_train_rows", "n_rows")) and \
            payload["n_train_rows"] > payload["n_rows"]:
        problems.append(
            "SCHEMA_MALFORMED: n_train_rows exceeds n_rows — the "
            "mask accounting is inconsistent")
    if payload.get("status") == "RUN_ERROR":
        problems.append(
            "STATUS_ERROR: status 'RUN_ERROR' is not a terminal "
            "producer status — an errored artifact cannot freeze "
            "or bind to association")
    if payload.get("frozen") is not True:
        problems.append(
            "SCHEMA_MALFORMED: artifact is not frozen "
            "(frozen is not True)")
    if payload.get("label_blinding") is not True:
        problems.append(
            "SCHEMA_MALFORMED: label_blinding is not True")
    seeds = payload.get("seeds")
    if isinstance(seeds, (list, tuple)) and len(seeds) < 3:
        problems.append(
            "SCHEMA_MALFORMED: fewer than three seeds — the "
            "seed-stability gate is unmet")
    occupancy = payload.get("occupancy")
    k = payload.get("k")
    if isinstance(occupancy, (list, tuple)) and \
            isinstance(k, int) and not isinstance(k, bool) and \
            len(occupancy) != k:
        problems.append(
            f"SCHEMA_MALFORMED: occupancy carries "
            f"{len(occupancy)} components but the artifact "
            f"declares k={k}")
    if "model" in payload:
        problems.extend(_model_problems(payload))
    if "input_schema" in payload:
        problems.extend(_input_schema_problems(payload))
    _pre = payload.get("preprocessing")
    if "preprocessing" in payload:
        if not isinstance(_pre, Mapping):
            problems.append(
                "SCHEMA_MALFORMED: preprocessing must be a "
                "mapping — the recorded transform contract is "
                "absent")
        elif not _pre.get("row_keys_digest"):
            problems.append(
                "SCHEMA_MALFORMED: preprocessing lacks "
                "'row_keys_digest' — the fitted row keys are "
                "unbound")
    problems.extend(_scalar_floor_problems(payload))
    problems.extend(_source_manifest_problems(
        payload, verify_source_bytes=verify_source_bytes))
    problems.extend(_unit_basin_map_problems(payload))
    problems.extend(_run_manifest_problems(payload))
    problems.extend(_missingness_problems(payload))
    problems.extend(_input_values_problems(payload))
    problems.extend(_row_universe_problems(payload))
    problems.extend(_config_cross_binding_problems(payload))
    problems.extend(_seed_stability_problems(payload))
    problems.extend(_gate_problems(payload))
    problems.extend(_null_problems(payload))
    problems.extend(_fit_partition_problems(payload))
    problems.extend(_forecast_payload_problems(payload))
    # Bound-digest recomputation — a carried digest is verified,
    # never trusted.  Each binding is recomputed over exactly the
    # material the producer hashed; per-field problems keep the
    # violated binding identifiable.  Digests whose material is not
    # self-contained in the payload (input_bytes_digest over raw
    # bytes, train_mask_digest, the k-selection/null envelopes) stay
    # with the boundary's own recomputation.
    for digest_field, material in (
            ("assignment_digest", payload.get("assignments")),
            ("config_digest", payload.get("config")),
            ("preprocessing_digest",
             payload.get("preprocessing")),
            ("run_manifest_digest",
             payload.get("run_manifest"))):
        declared = payload.get(digest_field)
        if _is_sha256(declared) and material is not None:
            try:
                recomputed = sha256_canonical(material)
            except (TypeError, ValueError) as exc:
                problems.append(
                    f"PAYLOAD_MALFORMED: {digest_field} inputs "
                    f"cannot be canonically rehashed: {exc}")
            else:
                if recomputed != declared:
                    problems.append(
                        f"DIGEST_MISMATCH: {digest_field} does "
                        "not recompute over the payload's bound "
                        "section — it was altered post-bind")
    # Envelope integrity: freeze_digest covers the payload minus
    # {freeze_digest, frozen}; regime_artifact_digest covers the
    # payload minus the freeze-emitted fields — a payload that
    # fails either was mutated (or fabricated) after production.
    fd = payload.get("freeze_digest")
    if _is_sha256(fd):
        try:
            recomputed = sha256_canonical(
                {f: v for f, v in payload.items()
                 if f not in ("freeze_digest", "frozen")})
        except (TypeError, ValueError) as exc:
            problems.append(
                f"PAYLOAD_MALFORMED: payload cannot be "
                f"canonically rehashed: {exc}")
        else:
            if recomputed != fd:
                problems.append(
                    "DIGEST_MISMATCH: freeze_digest does not "
                    "recompute over the payload — post-freeze "
                    "mutation or mislabeled artifact")
    rad = payload.get("regime_artifact_digest")
    if _is_sha256(rad):
        try:
            recomputed = sha256_canonical(
                {f: v for f, v in payload.items()
                 if f not in ("regime_artifact_digest",
                              "freeze_digest", "frozen")})
        except (TypeError, ValueError) as exc:
            problems.append(
                f"PAYLOAD_MALFORMED: payload cannot be "
                f"canonically rehashed: {exc}")
        else:
            if recomputed != rad:
                problems.append(
                    "DIGEST_MISMATCH: regime_artifact_digest does "
                    "not recompute over the payload — the artifact "
                    "was mutated after production")
    return problems
