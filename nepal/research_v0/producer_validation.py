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

from ._hashing import (_reject_nonjson, canonical_json,
                       sha256_canonical, verify_source_evidence,
                       verify_vintage_evidence)
from .gates import REQUIRED_REGIME_GATE_NAMES
from .policy import RegimeMode
from .records import (ForecastVintageV0, RunManifestV0,
                      deserialize_record)

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
#: ``family_digest`` to be a bound claim — the 12 keys the producer
#: emits unconditionally (``p_value``/``reason``/``observed`` may be
#: None; ``replicates`` and ``null_stat_min``/``null_stat_max`` are
#: conditional, see ``_NULL_FAMILY_OPTIONAL``).
_NULL_FAMILY_REQUIRED = (
    "statistic", "observed", "alpha", "n_replicates", "n_succeeded",
    "n_failed", "p_value", "reason", "status", "selection",
    "null_k_distribution", "family_digest")

#: R10-P11: the exact serialized top-level surface — every field a
#: ``run_regimes`` + ``freeze_regime_artifact`` payload may carry.
#: The 50 producer-emitted keys (verified against the live artifact),
#: the two freeze-emitted keys, the FORECAST-only typed payload pair,
#: and the R10 ``forecast_vintages`` evidence section.  Anything
#: outside this set is undeclared provenance riding the envelope.
PRODUCER_ALLOWED_FIELDS = frozenset({
    "ambiguous_fraction", "assignment_digest", "assignments",
    "associable", "config", "config_digest", "data_class",
    "disclaimer", "environment_digest", "feature_cols",
    "feature_matrix_digest", "fit_groups", "fit_partition",
    "fit_partition_digest", "fitted_on", "forecast_feature_set",
    "forecast_vintage_digests", "heldout_groups_declared",
    "input_bytes_digest", "input_schema", "input_values", "k",
    "k_selection_digest", "label_blinding", "mean_max_posterior",
    "missingness", "missingness_applied", "modal_k_frequency",
    "mode", "model", "n_rows", "n_train_rows", "null_model_digest",
    "nulls", "occupancy", "per_seed_best_k", "preprocessing",
    "preprocessing_digest", "regime_artifact_digest", "run_manifest",
    "run_manifest_digest", "seed_coverage", "seeds", "seeds_declared",
    "source_manifest", "stability", "stability_report_digest",
    "status", "terminal", "train_mask_digest", "unit_basin_map",
    "unit_basin_map_digest",
    # freeze-emitted and mode-conditional optional sections, plus
    # the optional serialization tag some frozen fixtures carry
    "frozen", "freeze_digest", "forecast_feature_payload",
    "forecast_feature_payload_digest", "forecast_vintages",
    "record_type"})

#: R10-P09: the producer's exact status machine — (terminal,
#: associable) is a function of status, never a free pair of flags.
#: RUN_ERROR is vocabulary-valid but never freezable; anything
#: outside ``_PRODUCER_STATUSES`` rejects at the vocabulary check.
_STATUS_MACHINE = {
    "DESCRIPTIVE_REGIME_ONLY": (True, True),
    "UNSUPERVISED_STRUCTURE_NOT_STABLE": (True, False),
    "CANDIDATE_ONLY": (False, False)}

#: R10-P02/P11: the exact serialized ``RegimeRunConfig`` field set —
#: the producer emits ``dataclasses.asdict(config)`` (32 fields;
#: ``forecast_vintages`` is popped — artifact-level evidence, never
#: bound configuration).  Unknown keys reject; a key that is present
#: is validated against the run-preflight semantics; keys a fixture
#: legitimately omits stay optional at the floor (the run preflight
#: is the completeness gate).
_CONFIG_FIELDS = frozenset({
    "bootstrap_block_len", "cadence", "date_col", "effort_col",
    "effort_split", "effort_waiver_reason", "elev_ablation_ari_max",
    "elevation_col", "era_boundaries", "era_col", "era_drift_max",
    "era_waiver_reason", "fitted_on", "fold_seed_policy",
    "forecast_feature_set", "forecast_vintage_digests", "gap_policy",
    "group_col", "heldout_groups", "k_candidates", "label_blinding",
    "max_missingness", "missingness_policy", "mode", "n_bootstrap",
    "n_null_replicates", "null_alpha", "season_col", "seeds",
    "source_manifest", "train_groups", "unit_col"})

#: Producer-side floors mirrored from ``science_v0.regimes`` — the
#: serialized config must respect the same constants the run
#: preflight enforces (the module cannot import science_v0; the
#: values are bound here and verified against regimes.py).
_CONFIG_MIN_BOOTSTRAP = 200      # regimes.MIN_BOOTSTRAP
_CONFIG_MIN_NULL_REPLICATES = 50  # regimes.MIN_NULL_REPLICATES
_CONFIG_MIN_SEEDS = 3             # regimes.MIN_SEEDS
_CONFIG_K_UNIVERSE = frozenset({1, 2, 3, 4, 5})   # K_CANDIDATES
_CONFIG_MISSINGNESS_POLICIES = frozenset(
    {"listwise", "bounded_impute", "stratified"})
_CONFIG_FOLD_SEED_POLICIES = frozenset({"all", "first"})
_CONFIG_EFFORT_SPLITS = frozenset({"median", "tercile", "first10"})
_CONFIG_DATA_CLASSES = frozenset(
    {"REANALYSIS", "ARCHIVED_OPERATIONAL"})

#: R10-P02: cadence is a declared fixed-length offset vocabulary —
#: the run parses it with ``pd.tseries.frequencies.to_offset`` and
#: requires a fixed-length ``Timedelta``; research_v0 cannot import
#: pandas, so the floor binds the conservative exact vocabulary the
#: producer and fixtures actually emit ('1D', '2D', '1H', …): an
#: optional integer multiplier (implicit 1) plus a fixed-length
#: unit alias.  Non-fixed offsets (M, Y, …) are not admissible —
#: they cannot produce a terminal artifact at run either.
_CADENCE_RE = re.compile(
    r"^\d*\s*(?:D|day|days|h|H|hour|hours|W|week|weeks|min|T|"
    r"minute|minutes)$")

#: R10-P11: the ``stability`` report's declared key vocabulary — the
#: producer always emits these 18 keys; fixtures legitimately carry
#: a subset, so the floor binds the vocabulary (unknown keys reject)
#: and requires ``required_gates`` (enforced in ``_gate_problems``).
_STABILITY_FIELDS = frozenset({
    "component_alignment", "drift_max_abs_mean_shift",
    "effort_sensitivity", "elevation", "era_drift",
    "fold_seed_policy", "k_instability", "leave_one_region_out",
    "locked_group_coverage", "missingness_sensitivity",
    "modal_k_frequency", "n_bootstrap", "required_gates",
    "season_refits", "seed_ari_max", "seed_ari_min", "seed_coverage",
    "temporal_block_bootstrap"})

#: R10-P05/P11: the ``preprocessing`` block's declared keys — the
#: producer emits exactly these seven; minimal fixtures legitimately
#: carry only ``row_keys_digest``, so the floor binds the vocabulary
#: and validates each carried field.
_PREPROCESSING_FIELDS = frozenset({
    "feature_order", "imputer_statistics", "imputer_strategy",
    "row_keys_digest", "scaler_mean", "scaler_var",
    "train_mask_membership_digest"})

#: R10-P11: the ``missingness_applied`` accounting record is exact.
_MISSINGNESS_APPLIED_FIELDS = frozenset({
    "policy", "train_rows_total", "train_rows_fitted",
    "train_rows_dropped"})

#: R10-P11: the ``input_schema`` record is exact.
_INPUT_SCHEMA_FIELDS = frozenset(
    {"feature_cols", "n_rows", "dtypes", "shape"})

#: R10-P10: the ``nulls`` section is exact — two declared families
#: plus the shared statistic envelope.
_NULLS_FIELDS = frozenset({
    "statistic", "observed", "alpha", "n_replicates",
    "season_era_stratified", "k1_bic", "shuffled", "season_matched"})

#: R10-P10: a null-family record's field set — ``replicates`` is
#: absent when the family was skipped (observed statistic
#: undefined); ``null_stat_min``/``null_stat_max`` are absent unless
#: the envelope completed.
_NULL_FAMILY_OPTIONAL = frozenset(
    {"replicates", "null_stat_min", "null_stat_max"})
_NULL_FAMILY_ALLOWED = frozenset(
    set(_NULL_FAMILY_REQUIRED) | set(_NULL_FAMILY_OPTIONAL))

#: The producer's null-family status vocabulary — ``_null_envelope``
#: emits exactly PASS or FAIL; a skipped/incomplete family is FAIL
#: with a bound reason.
_NULL_FAMILY_STATUSES = frozenset({"PASS", "FAIL"})

#: The declared selection vocabulary the producer binds into every
#: null-family record.
_NULL_SELECTION = "bic_sweep_declared_candidates"

#: A null replicate's serialized fields — ``input_digest`` is the
#: optional bound-input tag (absent in older minimal fixtures and on
#: replicates whose generated input was never produced).
_NULL_REPLICATE_FIELDS = frozenset(
    {"i", "gen_seed", "fit_seed", "k", "stat", "ok"})
_NULL_REPLICATE_OPTIONAL = frozenset({"input_digest"})

#: R10-P07: a non-fixture source manifest's declared keys —
#: ``fixture`` may ride along as a false marker; every other key is
#: the bound evidence contract.
_NONFIXTURE_MANIFEST_ALLOWED = frozenset(
    set(_NONFIXTURE_MANIFEST_KEYS) | {"fixture", "source_files"})


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
    # R10.1-B: a fixture manifest is exactly ``{"fixture": true}`` —
    # any extra field is evidence-shaped data smuggled past byte
    # verification (a declared source_files/evidence_root that is
    # never checked).
    if manifest.get("fixture") is True:
        extra = sorted(set(manifest) - {"fixture"})
        if extra:
            return True, [
                f"SCHEMA_MALFORMED: fixture source_manifest carries "
                f"undeclared fields {extra} — the synthetic bypass "
                "admits exactly {'fixture': true}; evidence-bearing "
                "fields require a non-fixture manifest"]
        return True, []
    return False, []


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


def _finite_float(value: Any) -> tuple[bool, float]:
    """The bounded numeric parser (R10-P01) — the ONLY way payload
    numbers may be coerced inside this module.

    Returns ``(ok, f)``: ``ok`` is False for bools, non-numeric
    types, values ``float()`` cannot coerce (``TypeError``/
    ``ValueError``/``OverflowError`` — e.g. ``10**400``, whose
    ``float()`` raises ``OverflowError: int too large to convert to
    float``), and non-finite results (inf/nan).  No numeric payload
    value may ever raise through the validator — an uncoercible
    number is a SCHEMA problem, never a crash.
    """
    if isinstance(value, bool) or \
            not isinstance(value, (int, float)):
        return False, 0.0
    try:
        f = float(value)
    except (TypeError, ValueError, OverflowError):
        return False, 0.0
    if not math.isfinite(f):
        return False, 0.0
    return True, f


def _seq_of_numbers(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and bool(value) and \
        all(_is_number(v) for v in value)


def _seq_of_finite(value: Any) -> bool:
    """A non-empty numeric sequence whose every element coerces to a
    finite float — the overflow-safe form of ``_seq_of_numbers``
    (R10-P01)."""
    return isinstance(value, (list, tuple)) and bool(value) and \
        all(_finite_float(v)[0] for v in value)


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
            any(not _finite_float(v)[0] or _finite_float(v)[1] < 0.0
                for v in occ)):
        problems.append(
            "SCHEMA_MALFORMED: occupancy must be a non-empty "
            "sequence of non-negative finite numbers")
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
            (not isinstance(payload["status"], str) or
             payload["status"] not in _PRODUCER_STATUSES):
        problems.append(
            f"SCHEMA_MALFORMED: status {payload['status']!r} is "
            "not a declared producer status")
    # R10-P09: the producer's exact status machine — (terminal,
    # associable) is a function of status, never a free flag pair:
    # DESCRIPTIVE_REGIME_ONLY is terminal AND associable;
    # UNSUPERVISED_STRUCTURE_NOT_STABLE is terminal but never
    # associable; CANDIDATE_ONLY is neither.  RUN_ERROR and unknown
    # statuses are already SCHEMA problems above (never freezable).
    status = payload.get("status")
    expected = _STATUS_MACHINE.get(status)
    if expected is not None and \
            isinstance(payload.get("terminal"), bool) and \
            isinstance(payload.get("associable"), bool) and \
            (payload["terminal"], payload["associable"]) != expected:
        problems.append(
            f"SCHEMA_MALFORMED: status {status!r} requires "
            f"terminal={expected[0]} and associable={expected[1]} "
            f"— got terminal={payload['terminal']} and "
            f"associable={payload['associable']}; the flags are a "
            "function of status, not free declarations")
    mf = payload.get("modal_k_frequency")
    if "modal_k_frequency" in payload and not _finite_float(mf)[0]:
        problems.append(
            "SCHEMA_MALFORMED: modal_k_frequency must be a "
            "finite number in [0, 1]")
    # R10-P11: the remaining scalar/sequence floors — every carried
    # field is bound, so none may be malformed.
    if "disclaimer" in payload and (
            not isinstance(payload["disclaimer"], str) or
            not payload["disclaimer"].strip()):
        problems.append(
            "SCHEMA_MALFORMED: disclaimer must be a non-empty "
            "string")
    # The optional serialization tag some frozen fixtures carry —
    # the producer emits none, so a carried tag must name the
    # declared frozen-artifact record, not borrow another record
    # class's identity.
    if "record_type" in payload and \
            payload["record_type"] != "FrozenRegimeArtifactV0":
        problems.append(
            "SCHEMA_MALFORMED: record_type must be "
            "'FrozenRegimeArtifactV0' when carried — the "
            "optional serialization tag cannot borrow another "
            "record class's identity")
    for frac in ("mean_max_posterior", "ambiguous_fraction"):
        if frac in payload and not _finite_float(payload[frac])[0]:
            problems.append(
                f"SCHEMA_MALFORMED: {frac} must be a finite "
                "number")
    miss = payload.get("missingness")
    if "missingness" in payload:
        if not isinstance(miss, Mapping):
            problems.append(
                "SCHEMA_MALFORMED: missingness must be a mapping "
                "of feature column -> missing fraction")
        else:
            fcols = payload.get("feature_cols")
            if isinstance(fcols, (list, tuple)) and fcols and \
                    any(str(k) not in {str(c) for c in fcols}
                        for k in miss):
                problems.append(
                    "SCHEMA_MALFORMED: missingness keys must be "
                    "declared feature columns")
            if any(not _finite_float(v)[0] or
                    not 0.0 <= _finite_float(v)[1] <= 1.0
                    for v in miss.values()):
                problems.append(
                    "SCHEMA_MALFORMED: missingness fractions must "
                    "be finite numbers in [0, 1]")
    fg = payload.get("fit_groups")
    if "fit_groups" in payload and (
            not isinstance(fg, (list, tuple)) or not fg or
            any(not isinstance(g, str) or not g.strip()
                for g in fg)):
        problems.append(
            "SCHEMA_MALFORMED: fit_groups must be a non-empty "
            "sequence of non-empty strings")
    elif "fit_groups" in payload and len(set(fg)) != len(fg):
        problems.append(
            "SCHEMA_MALFORMED: fit_groups must be distinct")
    hg = payload.get("heldout_groups_declared")
    if "heldout_groups_declared" in payload and (
            not isinstance(hg, (list, tuple)) or not hg or
            any(not isinstance(g, str) or not g.strip()
                for g in hg)):
        problems.append(
            "SCHEMA_MALFORMED: heldout_groups_declared must be a "
            "non-empty sequence of non-empty strings — the "
            "producer requires a declared locked holdout; a "
            "payload claiming none was never produced")
    elif "heldout_groups_declared" in payload and \
            len(set(hg)) != len(hg):
        problems.append(
            "SCHEMA_MALFORMED: heldout_groups_declared must be "
            "distinct")
    # R10-P03: the flat forecast declarations are ordered sequences
    # — duplicates are a re-counted binding, never a new vintage.
    fvd = payload.get("forecast_vintage_digests")
    if "forecast_vintage_digests" in payload:
        if not isinstance(fvd, (list, tuple)) or \
                any(not _is_sha256(d) for d in fvd):
            problems.append(
                "SCHEMA_MALFORMED: forecast_vintage_digests must "
                "be a sequence of 64-hex sha256 digests")
        elif len(set(fvd)) != len(fvd):
            problems.append(
                "SCHEMA_MALFORMED: forecast_vintage_digests must "
                "be distinct — a duplicated digest is not a "
                "second vintage")
    ffs = payload.get("forecast_feature_set")
    if "forecast_feature_set" in payload:
        if not isinstance(ffs, (list, tuple)) or \
                any(not isinstance(c, str) or not c.strip()
                    for c in ffs):
            problems.append(
                "SCHEMA_MALFORMED: forecast_feature_set must be "
                "a sequence of non-empty strings")
        elif len(set(ffs)) != len(ffs):
            problems.append(
                "SCHEMA_MALFORMED: forecast_feature_set must be "
                "distinct")
    # R9-P12/V6: the mode↔data-class binding is producer
    # semantics, not an audit nicety — a retrospective artifact
    # cannot declare operational-archive provenance and a
    # forecast artifact cannot declare reanalysis.
    mode = payload.get("mode")
    dc = payload.get("data_class")
    if mode == RegimeMode.RETROSPECTIVE_REGIME.value and \
            "data_class" in payload and dc != "REANALYSIS":
        problems.append(
            "SCHEMA_MALFORMED: RETROSPECTIVE_REGIME requires "
            f"data_class 'REANALYSIS', got {dc!r}")
    if mode == RegimeMode.FORECAST_REGIME.value and \
            "data_class" in payload and \
            dc != "ARCHIVED_OPERATIONAL":
        problems.append(
            "SCHEMA_MALFORMED: FORECAST_REGIME requires "
            f"data_class 'ARCHIVED_OPERATIONAL', got {dc!r}")
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
    # must fail closed (pure-python half; PSD stays in the auditor).
    # R10-P01: every coercion runs through _finite_float — a
    # 10**400-style payload int raises OverflowError from float(),
    # which must be a SCHEMA problem, never a crash.
    _w = [_finite_float(w) for w in weights]
    if any(not ok or f < 0.0 for ok, f in _w):
        problems.append(
            "SCHEMA_MALFORMED: model.weights must be finite and "
            "non-negative")
    elif abs(sum(f for _ok, f in _w) - 1.0) > 1e-6:
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
    if any(not _finite_float(v)[0] for row in means
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
        _cf = [[_finite_float(v) for v in row] for row in cov]
        if any(not ok for row in _cf for ok, _f in row):
            problems.append(
                f"SCHEMA_MALFORMED: model.covariances[{ci}] "
                "contains non-finite values")
            continue
        if any(abs(_cf[i][j][1] - _cf[j][i][1]) > 1e-9
               for i in range(d) for j in range(d)):
            problems.append(
                f"SCHEMA_MALFORMED: model.covariances[{ci}] is "
                "not symmetric")
            continue
        # R9-P09: a degenerate component can never have fitted —
        # every diagonal entry must be positive and the minimum
        # eigenvalue must clear the documented invertibility floor
        # (1e-10; sklearn reg_covar floors are ~1e-6).
        if any(_cf[i][i][1] <= 0.0 for i in range(d)):
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
    extra = sorted(set(schema) - _INPUT_SCHEMA_FIELDS)
    if extra:
        problems.append(
            f"SCHEMA_MALFORMED: input_schema carries undeclared "
            f"fields {extra} — the serialized input contract "
            "admits exactly {feature_cols, n_rows, dtypes, shape}")
    missing = sorted(_INPUT_SCHEMA_FIELDS - set(schema))
    if missing:
        return problems + \
            [f"SCHEMA_MALFORMED: input_schema lacks {missing}"]
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


def _preprocessing_problems(
        payload: Mapping[str, Any]) -> list[str]:
    """R10-P05/P11: the ``preprocessing`` transform contract.

    The producer emits exactly {feature_order, imputer_statistics,
    imputer_strategy, row_keys_digest, scaler_mean, scaler_var,
    train_mask_membership_digest}; minimal fixtures legitimately
    carry only ``row_keys_digest``, so the floor binds the declared
    vocabulary (unknown keys reject) and validates every carried
    field: ``row_keys_digest`` a real 64-hex (its recompute over the
    assignment row universe lives in ``_row_universe_problems``),
    ``feature_order`` equal to the artifact's feature_cols exactly,
    ``imputer_strategy`` a non-empty string, the scaler/imputer
    vectors finite numbers sized to the feature order, and
    ``train_mask_membership_digest`` a 64-hex.
    """
    pre = payload.get("preprocessing")
    if "preprocessing" not in payload:
        return []   # absence is a required-fields problem already
    if not isinstance(pre, Mapping):
        return ["SCHEMA_MALFORMED: preprocessing must be a "
                "mapping — the recorded transform contract is "
                "absent"]
    problems: list[str] = []
    extra = sorted(set(pre) - _PREPROCESSING_FIELDS)
    if extra:
        problems.append(
            f"SCHEMA_MALFORMED: preprocessing carries undeclared "
            f"fields {extra} — the transform contract admits "
            f"exactly {sorted(_PREPROCESSING_FIELDS)}")
    if not _is_sha256(pre.get("row_keys_digest")):
        problems.append(
            "DIGEST_MALFORMED: preprocessing.row_keys_digest "
            "must be a 64-hex sha256 — the fitted row keys are "
            "unbound")
    fcols = payload.get("feature_cols")
    if "feature_order" in pre:
        fo = pre["feature_order"]
        if not isinstance(fo, (list, tuple)) or \
                (isinstance(fcols, (list, tuple)) and
                 list(fo) != list(fcols)):
            problems.append(
                "SCHEMA_MALFORMED: preprocessing.feature_order "
                "must equal the artifact's feature_cols exactly "
                "— the fitted feature order is the bound order")
    if "imputer_strategy" in pre and (
            not isinstance(pre["imputer_strategy"], str) or
            not pre["imputer_strategy"].strip()):
        problems.append(
            "SCHEMA_MALFORMED: preprocessing.imputer_strategy "
            "must be a non-empty string")
    for key in ("imputer_statistics", "scaler_mean", "scaler_var"):
        if key not in pre:
            continue
        v = pre[key]
        if not isinstance(v, (list, tuple)) or \
                any(not _finite_float(x)[0] for x in v):
            problems.append(
                f"SCHEMA_MALFORMED: preprocessing.{key} must be "
                "a sequence of finite numbers")
        elif isinstance(fcols, (list, tuple)) and fcols and \
                len(v) != len(fcols):
            problems.append(
                f"SCHEMA_MALFORMED: preprocessing.{key} length "
                "must equal len(feature_cols) — the recorded "
                "parameters are bound to the feature order")
    if "train_mask_membership_digest" in pre and \
            not _is_sha256(pre["train_mask_membership_digest"]):
        problems.append(
            "DIGEST_MALFORMED: preprocessing."
            "train_mask_membership_digest must be a 64-hex "
            "sha256")
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
        # R10-P07/P11: the non-fixture manifest is an exact record —
        # only the declared evidence contract keys may ride along.
        unknown = sorted(set(sm) - _NONFIXTURE_MANIFEST_ALLOWED)
        if unknown:
            problems.append(
                f"SCHEMA_MALFORMED: non-fixture source_manifest "
                f"carries undeclared fields {unknown} — the "
                "evidence contract admits exactly "
                f"{sorted(_NONFIXTURE_MANIFEST_ALLOWED)}")
        for key in _NONFIXTURE_MANIFEST_KEYS:
            if not sm.get(key):
                problems.append(
                    f"PROVENANCE_MISSING: non-fixture "
                    f"source_manifest lacks {key!r}")
        # R10-P07: field-type floors on the declared contract — a
        # truthy non-string id or an empty container is not a
        # governed source.
        for key in ("source_id", "lineage", "evidence_root"):
            v = sm.get(key)
            if key in sm and (not isinstance(v, str) or
                              not v.strip()):
                problems.append(
                    f"SCHEMA_MALFORMED: source_manifest.{key} "
                    "must be a non-empty string")
        for key in ("units", "feature_allowlist"):
            v = sm.get(key)
            if key in sm and (
                    not isinstance(v, (list, tuple)) or not v or
                    any(not isinstance(u, str) or not u.strip()
                        for u in v)):
                problems.append(
                    f"SCHEMA_MALFORMED: source_manifest.{key} "
                    "must be a non-empty sequence of non-empty "
                    "strings")
        sds = sm.get("source_digests")
        if "source_digests" in sm and (
                not isinstance(sds, Sequence) or
                isinstance(sds, (str, bytes)) or not sds):
            problems.append(
                "SCHEMA_MALFORMED: source_manifest.source_digests "
                "must be a non-empty sequence of 64-hex digests")
        elif isinstance(sds, Sequence) and not isinstance(sds, str):
            for d in sds:
                if not _is_sha256(d):
                    problems.append(
                        "DIGEST_MALFORMED: "
                        "source_manifest.source_digests entry is "
                        "not a 64-hex sha256")
                    break
        # R10-P07: the declared file bindings — non-empty list of
        # {relpath: non-empty str, sha256: 64-hex} records with no
        # duplicate relpaths (a duplicated binding re-counts
        # evidence).  Byte verification underneath stays with
        # verify_source_evidence when verify_source_bytes is on.
        files = sm.get("source_files")
        if "source_files" in sm:
            if not isinstance(files, (list, tuple)) or not files:
                problems.append(
                    "SCHEMA_MALFORMED: source_manifest.source_files "
                    "must be a non-empty list of {relpath, sha256} "
                    "bindings")
            else:
                seen_rel: set = set()
                for i, entry in enumerate(files):
                    if not isinstance(entry, Mapping):
                        problems.append(
                            f"SCHEMA_MALFORMED: "
                            f"source_manifest.source_files[{i}] "
                            "must be a {relpath, sha256} mapping")
                        continue
                    extra = sorted(set(entry) -
                                   {"relpath", "sha256"})
                    if extra:
                        problems.append(
                            f"SCHEMA_MALFORMED: "
                            f"source_manifest.source_files[{i}] "
                            f"carries undeclared fields {extra}")
                    rel = entry.get("relpath")
                    if not isinstance(rel, str) or not rel.strip():
                        problems.append(
                            f"SCHEMA_MALFORMED: "
                            f"source_manifest.source_files[{i}]"
                            ".relpath must be a non-empty string")
                    elif rel in seen_rel:
                        problems.append(
                            f"SCHEMA_MALFORMED: "
                            f"source_manifest.source_files relpath "
                            f"{rel!r} is duplicated — one evidence "
                            "binding per file")
                    else:
                        seen_rel.add(rel)
                    if not _is_sha256(entry.get("sha256")):
                        problems.append(
                            f"DIGEST_MALFORMED: "
                            f"source_manifest.source_files[{i}]"
                            ".sha256 must be a 64-hex sha256")
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
        # R10-P05: FORECAST also requires the flat declared
        # bindings non-empty — the typed payload cross-binds them
        # but an absent/empty flat pair is itself a binding gap.
        for f in ("forecast_vintage_digests",
                  "forecast_feature_set"):
            v = payload.get(f)
            if not isinstance(v, (list, tuple)) or not v:
                problems.append(
                    f"PROVENANCE_MISSING: FORECAST_REGIME "
                    f"requires a non-empty {f} — the forecast "
                    "evidence declaration is bound, not implied")
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
        # R10-P05: row_count is a typed count, not an optional
        # hint — a string '10' or a bool is malformed outright.
        rc = ffp.get("row_count")
        if isinstance(rc, bool) or not isinstance(rc, int) or \
                rc <= 0:
            problems.append(
                "SCHEMA_MALFORMED: forecast_feature_payload."
                "row_count must be a positive integer")
        elif isinstance(payload.get("n_rows"), int) and \
                not isinstance(payload["n_rows"], bool) and \
                rc != payload["n_rows"]:
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


def _forecast_vintages_problems(
        payload: Mapping[str, Any],
        *, verify_source_bytes: bool = True) -> list[str]:
    """R10-P12: the typed forecast-vintage binding.

    ``forecast_vintages`` is the optional top-level section carrying
    the serialized ``ForecastVintageV0`` evidence records behind
    ``forecast_vintage_digests`` — the producer emits it verbatim
    inside the ``regime_artifact_digest`` envelope when the run
    declares the records.  The floor rules:

    * RETROSPECTIVE_REGIME — the section must be absent or empty;
      forecast evidence can never ride a retrospective artifact.
    * FORECAST_REGIME + ``associable is True`` — REQUIRED non-empty:
      no forecast-ready claim without byte-bound vintages.  Every
      record must deserialize as exactly ``ForecastVintageV0``
      (exact field set, ``problems() == []``, canonical ``to_dict``
      equality), its ``sha256_canonical(rec.to_dict())`` must be
      declared in ``forecast_vintage_digests``, the carried records
      must cover the declared digests bijectively, and each record
      must bind a non-empty ``evidence_root`` that passes
      ``verify_vintage_evidence`` when byte verification is on.
    * FORECAST_REGIME + ``associable`` false — optional; carried
      records are validated identically EXCEPT an empty
      ``evidence_root`` is admissible (metadata-only candidates)
      and the records need not cover every declared digest.
    """
    fv = payload.get("forecast_vintages")
    mode = payload.get("mode")
    associable = payload.get("associable") is True
    if mode == RegimeMode.RETROSPECTIVE_REGIME.value:
        if fv:
            return ["SCHEMA_MALFORMED: RETROSPECTIVE_REGIME must "
                    "not carry forecast_vintages — forecast "
                    "evidence cannot silently transfer into a "
                    "retrospective artifact"]
        return []
    if mode != RegimeMode.FORECAST_REGIME.value:
        return []   # unknown/absent mode rejects elsewhere
    needs_vintages = associable
    if fv is None:
        if needs_vintages:
            return ["PROVENANCE_MISSING: an associable "
                    "FORECAST_REGIME artifact requires a "
                    "non-empty 'forecast_vintages' section — no "
                    "forecast-ready claim without byte-bound "
                    "vintage evidence"]
        return []
    if not isinstance(fv, (list, tuple)):
        return ["SCHEMA_MALFORMED: forecast_vintages must be a "
                "sequence of serialized ForecastVintageV0 "
                "records"]
    if needs_vintages and not fv:
        return ["PROVENANCE_MISSING: an associable "
                "FORECAST_REGIME artifact requires a non-empty "
                "'forecast_vintages' section — no forecast-ready "
                "claim without byte-bound vintage evidence"]
    problems: list[str] = []
    declared = payload.get("forecast_vintage_digests")
    declared_set = {str(d) for d in declared} \
        if isinstance(declared, (list, tuple)) else None
    vfields = {f.name for f in
               dataclasses.fields(ForecastVintageV0)}
    covered: list[str] = []
    for i, rec_d in enumerate(fv):
        if not isinstance(rec_d, Mapping):
            problems.append(
                f"SCHEMA_MALFORMED: forecast_vintages[{i}] must "
                "be a serialized ForecastVintageV0 mapping")
            continue
        if rec_d.get("record_type") != "ForecastVintageV0":
            problems.append(
                f"SCHEMA_MALFORMED: forecast_vintages[{i}]."
                "record_type must be 'ForecastVintageV0' — the "
                "typed vintage record cannot be substituted or "
                "left untagged")
            continue
        missing = sorted(vfields - (set(rec_d) - {"record_type"}))
        extra = sorted(set(rec_d) - vfields - {"record_type"})
        if missing or extra:
            problems.append(
                f"SCHEMA_MALFORMED: forecast_vintages[{i}] has "
                f"missing fields {missing} and undeclared "
                f"fields {extra} — the carried record must be "
                "the complete canonical serialization")
            continue
        try:
            rec = deserialize_record(rec_d)
        except (TypeError, ValueError) as exc:
            problems.append(
                f"SCHEMA_MALFORMED: forecast_vintages[{i}] does "
                f"not deserialize as ForecastVintageV0: {exc}")
            continue
        if type(rec) is not ForecastVintageV0:
            problems.append(
                f"SCHEMA_MALFORMED: forecast_vintages[{i}] did "
                "not produce a ForecastVintageV0 record")
            continue
        # R10-P01: record evaluation runs timestamp/numeric
        # coercions that can raise on unbounded ints — an
        # uncoercible field is a SCHEMA problem, never a crash.
        try:
            rec_problems = rec.problems()
        except (TypeError, ValueError, ArithmeticError) as exc:
            rec_problems = [f"record evaluation failed: {exc}"]
        for prob in rec_problems:
            problems.append(
                f"SCHEMA_MALFORMED: forecast_vintages[{i}] "
                f"record is invalid: {prob}")
        try:
            if canonical_json(rec_d) != \
                    canonical_json(rec.to_dict()):
                problems.append(
                    f"SCHEMA_MALFORMED: forecast_vintages[{i}] "
                    "does not equal the canonical "
                    "ForecastVintageV0.to_dict() serialization")
                continue
            rec_digest = sha256_canonical(rec.to_dict())
        except (TypeError, ValueError) as exc:
            problems.append(
                f"PAYLOAD_MALFORMED: forecast_vintages[{i}] "
                f"cannot be canonically rehashed: {exc}")
            continue
        if declared_set is not None and \
                rec_digest not in declared_set:
            problems.append(
                f"DIGEST_MISMATCH: forecast_vintages[{i}] "
                "digests to a vintage not declared in "
                "forecast_vintage_digests — carried evidence "
                "must be bound")
        else:
            covered.append(rec_digest)
        # The byte binding: an associable claim requires a real
        # evidence_root and verified bytes; a metadata-only
        # candidate (empty root) is admissible only for
        # non-associable artifacts.
        root = rec_d.get("evidence_root")
        if needs_vintages:
            if not isinstance(root, str) or not root.strip():
                problems.append(
                    f"PROVENANCE_MISSING: forecast_vintages[{i}] "
                    "requires a non-empty evidence_root — an "
                    "associable forecast artifact carries "
                    "byte-bound vintages only")
            elif verify_source_bytes:
                for prob in verify_vintage_evidence(rec_d):
                    problems.append(
                        f"PROVENANCE_MISSING: "
                        f"forecast_vintages[{i}] evidence byte "
                        f"verification failed: {prob} — a "
                        "forecast-ready claim cannot stand on "
                        "unverified bytes")
        elif isinstance(root, str) and root.strip() and \
                verify_source_bytes:
            for prob in verify_vintage_evidence(rec_d):
                problems.append(
                    f"PROVENANCE_MISSING: forecast_vintages[{i}] "
                    f"evidence byte verification failed: {prob}")
    if needs_vintages and declared_set is not None and \
            sorted(covered) != sorted(declared_set):
        problems.append(
            "DIGEST_MISMATCH: forecast_vintages does not cover "
            "the declared forecast_vintage_digests bijectively "
            "— every declared vintage must be carried by "
            "exactly one record")
    return problems


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
    # R10-P01: rec.problems() runs timestamp/numeric checks whose
    # float() coercions can raise OverflowError on unbounded ints —
    # an uncoercible field is a SCHEMA problem, never a crash.
    try:
        rec_problems = rec.problems()
    except (TypeError, ValueError, ArithmeticError) as exc:
        rec_problems = [f"record evaluation failed: {exc}"]
    for prob in rec_problems:
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
    # R10-P01: the digest-sequence fields deserialize untyped — a
    # non-sequence input_digests (e.g. an int) must not crash the
    # membership test.
    in_digests = rec.input_digests \
        if isinstance(rec.input_digests, (list, tuple)) else ()
    out_digests = rec.output_digests \
        if isinstance(rec.output_digests, (list, tuple)) else ()
    if payload.get("input_bytes_digest") not in \
            tuple(in_digests):
        problems.append(
            "DIGEST_MISMATCH: run_manifest.input_digests does not "
            "bind the artifact's input_bytes_digest — the run "
            "cannot name its exact inputs")
    if payload.get("assignment_digest") not in \
            tuple(out_digests):
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
        # not a crash.  R10-P01: finite coercion runs through
        # _finite_float — an unbounded int (10**400) is an
        # undecodable value, not an OverflowError through the floor.
        bad = next((v for v in row
                    if not (v is None or
                            (isinstance(v, str) and
                             v in _NONFINITE_TOKENS) or
                            _finite_float(v)[0])), ...)
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
        except (TypeError, ValueError, OverflowError) as exc:
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
        # The producer emits cutoff_iso = max train-row date — a
        # fully recomputable binding.  A declared cutoff that is
        # not the latest fitted row date was never produced.
        train_dates = sorted(
            d for k, (u, d, _r) in zip(unit_keys, assignments)
            if ubm_groups.get(u) in train_groups)
        cutoff = fp.get("cutoff_iso")
        if isinstance(cutoff, str):
            expected = train_dates[-1] if train_dates else ""
            if cutoff != expected:
                problems.append(
                    "SCHEMA_MALFORMED: fit_partition.cutoff_iso "
                    f"{cutoff!r} does not equal the latest "
                    f"train-row date {expected!r} — the fit "
                    "window was rebound post-production")
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
    k_cands = cfg.get("k_candidates")
    # membership must not crash on unhashable candidates — a dict in
    # the list is malformed, not a TypeError through the floor; an
    # absent or non-sequence declaration cannot bind the modal k.
    if isinstance(k, int) and not isinstance(k, bool) and (
            not isinstance(k_cands, (list, tuple)) or
            k not in list(k_cands)):
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


def _config_semantic_problems(
        payload: Mapping[str, Any]) -> list[str]:
    """R10-P02: complete serialized-config semantics.

    The floor's cross-binding proves the config is THE declared one;
    this pass proves it is a VALID one — every serialized
    ``RegimeRunConfig`` field is checked against the run-preflight
    semantics the producer itself enforces (``run_regimes`` lines
    ~623-800, ``_validate_config_semantics``).  Frame-dependent
    checks ("column exists in the frame") stay at run — the floor
    requires the declared shape (non-empty string, or None where the
    dataclass admits None).  The serialized field set is exact:
    unknown keys reject (P11); a key a fixture legitimately omits
    stays optional, but a carried key may never be malformed.
    """
    cfg = payload.get("config")
    if not isinstance(cfg, Mapping):
        return []   # shape handled by required-fields/cross-binding
    problems: list[str] = []
    unknown = sorted(set(cfg) - _CONFIG_FIELDS)
    if unknown:
        problems.append(
            f"SCHEMA_MALFORMED: config carries undeclared fields "
            f"{unknown} — the serialized RegimeRunConfig admits "
            "exactly the declared field set")

    def _nn_str(key):
        if key in cfg and (not isinstance(cfg[key], str) or
                           not cfg[key].strip()):
            problems.append(
                f"SCHEMA_MALFORMED: config.{key} must be a "
                "non-empty string")

    def _opt_str(key):
        if key in cfg and cfg[key] is not None and \
                (not isinstance(cfg[key], str) or
                 not cfg[key].strip()):
            problems.append(
                f"SCHEMA_MALFORMED: config.{key} must be a "
                "non-empty string or None")

    def _frac(key, lo, hi, *, exclusive=False):
        if key in cfg:
            ok, f = _finite_float(cfg[key])
            if not ok or (exclusive and not lo < f < hi) or \
                    (not exclusive and not lo <= f <= hi):
                problems.append(
                    f"SCHEMA_MALFORMED: config.{key} must be a "
                    f"finite number in "
                    f"{'(' if exclusive else '['}{lo}, {hi}"
                    f"{')' if exclusive else ']'}")

    def _min_int(key, floor):
        if key in cfg and (isinstance(cfg[key], bool) or
                           not isinstance(cfg[key], int) or
                           cfg[key] < floor):
            problems.append(
                f"SCHEMA_MALFORMED: config.{key} must be an "
                f"integer >= {floor}")

    def _str_seq(key, *, non_empty, distinct=False):
        if key not in cfg:
            return
        v = cfg[key]
        if not isinstance(v, (list, tuple)) or \
                (non_empty and not v) or \
                any(not isinstance(s, str) or not s.strip()
                    for s in v):
            problems.append(
                f"SCHEMA_MALFORMED: config.{key} must be "
                f"{'a non-empty' if non_empty else 'a'} sequence "
                "of non-empty strings")
        elif distinct and len(set(v)) != len(v):
            problems.append(
                f"SCHEMA_MALFORMED: config.{key} must carry "
                "distinct members — a duplicated declaration is "
                "not a second binding")

    # scalar floors
    _nn_str("date_col")
    _nn_str("group_col")
    _nn_str("season_col")
    _nn_str("unit_col")
    _opt_str("effort_col")
    _opt_str("elevation_col")
    _opt_str("era_col")
    _frac("null_alpha", 0.0, 1.0, exclusive=True)
    _frac("max_missingness", 0.0, 1.0)
    _frac("elev_ablation_ari_max", 0.0, 1.0)
    if "era_drift_max" in cfg:
        ok, f = _finite_float(cfg["era_drift_max"])
        if not ok or f < 0.0:
            problems.append(
                "SCHEMA_MALFORMED: config.era_drift_max must be "
                "a finite number >= 0")
    _min_int("bootstrap_block_len", 1)
    _min_int("n_bootstrap", _CONFIG_MIN_BOOTSTRAP)
    _min_int("n_null_replicates", _CONFIG_MIN_NULL_REPLICATES)
    if "label_blinding" in cfg and cfg["label_blinding"] is not True:
        problems.append(
            "SCHEMA_MALFORMED: config.label_blinding must be "
            "True — event labels are prohibited in regime "
            "fitting")
    if "fitted_on" in cfg and cfg["fitted_on"] != "TRAIN_ONLY":
        problems.append(
            "SCHEMA_MALFORMED: config.fitted_on must be "
            "'TRAIN_ONLY'")
    if "gap_policy" in cfg and cfg["gap_policy"] != "calendar":
        problems.append(
            "SCHEMA_MALFORMED: config.gap_policy must be "
            "'calendar'")
    if "missingness_policy" in cfg and \
            (not isinstance(cfg["missingness_policy"], str) or
             cfg["missingness_policy"] not in
             _CONFIG_MISSINGNESS_POLICIES):
        problems.append(
            f"SCHEMA_MALFORMED: config.missingness_policy must "
            f"be one of {sorted(_CONFIG_MISSINGNESS_POLICIES)}")
    if "fold_seed_policy" in cfg and \
            (not isinstance(cfg["fold_seed_policy"], str) or
             cfg["fold_seed_policy"] not in
             _CONFIG_FOLD_SEED_POLICIES):
        problems.append(
            f"SCHEMA_MALFORMED: config.fold_seed_policy must be "
            f"one of {sorted(_CONFIG_FOLD_SEED_POLICIES)}")
    for flag in ("effort_waiver_reason", "era_waiver_reason"):
        if flag in cfg and not isinstance(cfg[flag], str):
            problems.append(
                f"SCHEMA_MALFORMED: config.{flag} must be a "
                "string")
    if "cadence" in cfg:
        cad = cfg["cadence"]
        if not isinstance(cad, str) or not cad.strip() or \
                not _CADENCE_RE.match(cad.strip()):
            problems.append(
                f"SCHEMA_MALFORMED: config.cadence {cad!r} is "
                "not a declared fixed-length offset — the "
                "producer's cadence vocabulary is an integer "
                "multiplier over a fixed unit (D/day/h/W/min/T "
                "and aliases)")
    if "effort_split" in cfg:
        es = cfg["effort_split"]
        es_ok = isinstance(es, str) and (
            es in _CONFIG_EFFORT_SPLITS or
            (es.startswith("quantile:") and
             es.split(":", 1)[1].replace(".", "", 1).isdigit() and
             0.0 < float(es.split(":", 1)[1]) < 1.0))
        if not es_ok:
            problems.append(
                f"SCHEMA_MALFORMED: config.effort_split {es!r} "
                "must be 'median', 'tercile', 'first10', or "
                "'quantile:<q in (0,1)>'")
    if "era_boundaries" in cfg:
        eras = cfg["era_boundaries"]
        if not isinstance(eras, (list, tuple)) or \
                any(not isinstance(e, str) or not e.strip()
                    for e in eras):
            problems.append(
                "SCHEMA_MALFORMED: config.era_boundaries must be "
                "a sequence of non-empty strings (ISO dates or "
                "era labels; may be empty)")
    # declared sets
    if "seeds" in cfg:
        seeds = cfg["seeds"]
        if not isinstance(seeds, (list, tuple)) or \
                any(isinstance(s, bool) or
                    not isinstance(s, int) or s < 0
                    for s in seeds):
            problems.append(
                "SCHEMA_MALFORMED: config.seeds must be a "
                "sequence of non-negative integers")
        elif len(set(seeds)) != len(seeds) or \
                len(set(seeds)) < _CONFIG_MIN_SEEDS:
            problems.append(
                f"SCHEMA_MALFORMED: config.seeds must carry >= "
                f"{_CONFIG_MIN_SEEDS} distinct non-negative "
                "integers")
    if "k_candidates" in cfg:
        kc = cfg["k_candidates"]
        if not isinstance(kc, (list, tuple)) or not kc or \
                any(isinstance(k, bool) or not isinstance(k, int)
                    or k not in _CONFIG_K_UNIVERSE for k in kc):
            problems.append(
                f"SCHEMA_MALFORMED: config.k_candidates must be "
                f"a non-empty sequence of ints within "
                f"{sorted(_CONFIG_K_UNIVERSE)}")
        elif len(set(kc)) != len(kc):
            problems.append(
                "SCHEMA_MALFORMED: config.k_candidates must be "
                "distinct — a duplicated candidate is not a "
                "second sweep member")
        elif 1 not in list(kc):
            problems.append(
                "SCHEMA_MALFORMED: config.k_candidates must "
                "contain the mandatory K=1 null candidate")
    _str_seq("train_groups", non_empty=True, distinct=True)
    # R10 run parity: run_regimes RUN_ERRORs without a declared
    # non-empty holdout — a serialized config with none was never
    # produced.
    _str_seq("heldout_groups", non_empty=True, distinct=True)
    tg = cfg.get("train_groups")
    hgv = cfg.get("heldout_groups")
    if isinstance(tg, (list, tuple)) and \
            isinstance(hgv, (list, tuple)) and \
            all(isinstance(g, str) for g in tg) and \
            all(isinstance(g, str) for g in hgv):
        overlap = sorted(set(tg) & set(hgv))
        if overlap:
            problems.append(
                f"SCHEMA_MALFORMED: config train_groups and "
                f"heldout_groups overlap {overlap} — a group "
                "cannot be fitted and held out")
    if "mode" in cfg and \
            (not isinstance(cfg["mode"], str) or
             cfg["mode"] not in {m.value for m in RegimeMode}):
        problems.append(
            f"SCHEMA_MALFORMED: config.mode {cfg['mode']!r} is "
            "not a declared RegimeMode value")
    if "source_manifest" in cfg and \
            not isinstance(cfg["source_manifest"], Mapping):
        problems.append(
            "SCHEMA_MALFORMED: config.source_manifest must be a "
            "mapping")
    # mode-conditional forecast bindings — FORECAST requires both
    # non-empty; anything else requires both empty.
    cfg_mode = cfg.get("mode")
    fvd = cfg.get("forecast_vintage_digests")
    if "forecast_vintage_digests" in cfg:
        if not isinstance(fvd, (list, tuple)) or \
                any(not _is_sha256(d) for d in fvd):
            problems.append(
                "SCHEMA_MALFORMED: config.forecast_vintage_"
                "digests must be a sequence of 64-hex sha256 "
                "digests")
        elif len(set(fvd)) != len(fvd):
            problems.append(
                "SCHEMA_MALFORMED: config.forecast_vintage_"
                "digests must be distinct")
        elif cfg_mode == RegimeMode.FORECAST_REGIME.value and \
                not fvd:
            problems.append(
                "SCHEMA_MALFORMED: FORECAST_REGIME requires "
                "non-empty config.forecast_vintage_digests")
        elif cfg_mode != RegimeMode.FORECAST_REGIME.value and \
                cfg_mode is not None and fvd:
            problems.append(
                "SCHEMA_MALFORMED: non-FORECAST config must not "
                "carry forecast_vintage_digests")
    ffs = cfg.get("forecast_feature_set")
    if "forecast_feature_set" in cfg:
        if not isinstance(ffs, (list, tuple)) or \
                any(not isinstance(c, str) or not c.strip()
                    for c in ffs):
            problems.append(
                "SCHEMA_MALFORMED: config.forecast_feature_set "
                "must be a sequence of non-empty strings")
        elif len(set(ffs)) != len(ffs):
            problems.append(
                "SCHEMA_MALFORMED: config.forecast_feature_set "
                "must be distinct")
        elif cfg_mode == RegimeMode.FORECAST_REGIME.value and \
                not ffs:
            problems.append(
                "SCHEMA_MALFORMED: FORECAST_REGIME requires "
                "non-empty config.forecast_feature_set")
        elif cfg_mode != RegimeMode.FORECAST_REGIME.value and \
                cfg_mode is not None and ffs:
            problems.append(
                "SCHEMA_MALFORMED: non-FORECAST config must not "
                "carry forecast_feature_set")
        else:
            fcols = payload.get("feature_cols")
            if isinstance(fcols, (list, tuple)) and fcols:
                outside = sorted(set(ffs) -
                                 {str(c) for c in fcols})
                if outside:
                    problems.append(
                        f"SCHEMA_MALFORMED: config."
                        f"forecast_feature_set names features "
                        f"{outside} outside the declared "
                        "feature_cols")
    # data_class is NOT a serialized RegimeRunConfig field — it is
    # rejected by the unknown-key check above when present.  The
    # mode↔data_class binding itself lives on the artifact's flat
    # fields (see _scalar_floor_problems).
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
    # R10-P11: the applied-policy accounting record is exact.
    ma_extra = sorted(set(ma) - _MISSINGNESS_APPLIED_FIELDS)
    if ma_extra:
        problems.append(
            f"SCHEMA_MALFORMED: missingness_applied carries "
            f"undeclared fields {ma_extra} — the accounting "
            "record admits exactly {policy, train_rows_total, "
            "train_rows_fitted, train_rows_dropped}")
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
                          if not isinstance(v, str) or
                          v not in _SEED_COVERAGE_STATES}
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
    if freq is not None:
        _ok, _f = _finite_float(freq)
        if not _ok or not 0.0 <= _f <= 1.0:
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
    # R10-P11: the stability report binds a declared vocabulary —
    # the producer emits a fixed 18-key surface; fixtures carry a
    # subset.  An undeclared key is provenance the digest claims to
    # cover but the schema never declared.
    extra_axes = sorted(set(stability) - _STABILITY_FIELDS)
    if extra_axes:
        problems.append(
            f"SCHEMA_MALFORMED: stability carries undeclared "
            f"fields {extra_axes} — the stability report admits "
            "only the producer's declared axis vocabulary")
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


def _null_replicate_problems(
        rec: Mapping[str, Any], idx: int, fam: str,
        declared_seeds: Any, cand_set: set) -> list[str]:
    """R10-P10: one serialized null-replicate record — the producer
    emits exactly {i, gen_seed, fit_seed, k, stat, ok} plus the
    optional ``input_digest`` bound-input tag."""
    problems: list[str] = []
    extra = sorted(set(rec) - _NULL_REPLICATE_FIELDS -
                   _NULL_REPLICATE_OPTIONAL)
    if extra:
        problems.append(
            f"SCHEMA_MALFORMED: nulls.{fam}.replicates[{idx}] "
            f"carries undeclared fields {extra}")
    missing = sorted(_NULL_REPLICATE_FIELDS - set(rec))
    if missing:
        problems.append(
            f"SCHEMA_MALFORMED: nulls.{fam}.replicates[{idx}] "
            f"lacks serialized fields {missing}")
        return problems
    i = rec["i"]
    if isinstance(i, bool) or not isinstance(i, int) or i < 0:
        problems.append(
            f"SCHEMA_MALFORMED: nulls.{fam}.replicates[{idx}].i "
            "must be a non-negative integer")
    for sf in ("gen_seed", "fit_seed"):
        s = rec[sf]
        if isinstance(s, bool) or not isinstance(s, int):
            problems.append(
                f"SCHEMA_MALFORMED: nulls.{fam}.replicates[{idx}]"
                f".{sf} must be an integer")
    fs = rec["fit_seed"]
    if isinstance(fs, int) and not isinstance(fs, bool) and \
            isinstance(declared_seeds, (list, tuple)) and \
            declared_seeds and \
            all(isinstance(s, int) and not isinstance(s, bool)
                for s in declared_seeds) and \
            fs not in list(declared_seeds):
        problems.append(
            f"SCHEMA_MALFORMED: nulls.{fam}.replicates[{idx}]"
            ".fit_seed is not one of the declared seeds — a "
            "replicate cannot run an undeclared seed")
    k = rec["k"]
    if k is not None and (
            isinstance(k, bool) or not isinstance(k, int) or
            (cand_set and k not in cand_set)):
        problems.append(
            f"SCHEMA_MALFORMED: nulls.{fam}.replicates[{idx}].k "
            "must be a declared k_candidate or null")
    if rec["stat"] is not None and \
            not _finite_float(rec["stat"])[0]:
        problems.append(
            f"SCHEMA_MALFORMED: nulls.{fam}.replicates[{idx}]"
            ".stat must be a finite number or null")
    if not isinstance(rec["ok"], bool):
        problems.append(
            f"SCHEMA_MALFORMED: nulls.{fam}.replicates[{idx}].ok "
            "must be a boolean")
    if "input_digest" in rec and rec["input_digest"] is not None \
            and not _is_sha256(rec["input_digest"]):
        problems.append(
            f"DIGEST_MALFORMED: nulls.{fam}.replicates[{idx}]"
            ".input_digest must be a 64-hex sha256 or null")
    return problems


def _null_problems(payload: Mapping[str, Any]) -> list[str]:
    """R9-P12 (null floor) + R10-P10 (null section semantics): the
    ``nulls`` section is an exact record {statistic, observed,
    alpha, n_replicates, season_era_stratified, k1_bic, shuffled,
    season_matched} carrying the two declared families; each family
    is an exact record whose ``family_digest`` recomputes over the
    carried record exactly as the producer bound it, whose status
    is the declared PASS/FAIL vocabulary with a bound reason, whose
    p_value/alpha/observed/stat fields are typed finite numbers (or
    null where the producer admits null), whose
    ``null_k_distribution`` counts declared candidates only, and
    whose ``replicates`` — when carried — are exact bounded
    records.  ``null_model_digest`` recomputes over the carried
    k1_bic list plus the family digests."""
    nulls = payload.get("nulls")
    if "nulls" not in payload:
        return []   # absence is a required-fields problem already
    if not isinstance(nulls, Mapping):
        return ["SCHEMA_MALFORMED: nulls must be a mapping "
                "carrying the declared null families"]
    problems: list[str] = []
    # R10-P11: the nulls envelope is exact — no undeclared family
    # or summary key may ride the null_model_digest envelope.
    nulls_extra = sorted(set(nulls) - _NULLS_FIELDS)
    if nulls_extra:
        problems.append(
            f"SCHEMA_MALFORMED: nulls carries undeclared fields "
            f"{nulls_extra} — the null envelope admits exactly "
            f"{sorted(_NULLS_FIELDS)}")
    # R10-P10: the shared statistic envelope — every carried field
    # is typed and in range; the observed statistic may be null
    # when the modal fit is K=1 (the family must then be FAIL).
    if "statistic" in nulls and \
            nulls["statistic"] != "silhouette":
        problems.append(
            "SCHEMA_MALFORMED: nulls.statistic must be "
            "'silhouette' — the declared null statistic")
    if "observed" in nulls and nulls["observed"] is not None and \
            not _finite_float(nulls["observed"])[0]:
        problems.append(
            "SCHEMA_MALFORMED: nulls.observed must be a finite "
            "number or null")
    if "alpha" in nulls:
        ok, f = _finite_float(nulls["alpha"])
        if not ok or not 0.0 < f < 1.0:
            problems.append(
                "SCHEMA_MALFORMED: nulls.alpha must be a finite "
                "number in (0, 1)")
    if "n_replicates" in nulls and (
            isinstance(nulls["n_replicates"], bool) or
            not isinstance(nulls["n_replicates"], int) or
            nulls["n_replicates"] <= 0):
        problems.append(
            "SCHEMA_MALFORMED: nulls.n_replicates must be a "
            "positive integer")
    if "season_era_stratified" in nulls and \
            not isinstance(nulls["season_era_stratified"], bool):
        problems.append(
            "SCHEMA_MALFORMED: nulls.season_era_stratified must "
            "be a boolean")
    declared = payload.get("seeds_declared")
    if not isinstance(declared, (list, tuple)):
        declared = payload.get("seeds")
    seed_cycle = list(declared) \
        if isinstance(declared, (list, tuple)) else declared
    cfg = payload.get("config")
    cands = cfg.get("k_candidates") \
        if isinstance(cfg, Mapping) else None
    cand_set = {int(c) for c in cands
                if isinstance(c, int) and not isinstance(c, bool)} \
        if isinstance(cands, (list, tuple)) else set()
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
        extra = sorted(set(rec) - _NULL_FAMILY_ALLOWED)
        if extra:
            problems.append(
                f"SCHEMA_MALFORMED: nulls.{fam} carries "
                f"undeclared fields {extra} — the family record "
                "admits exactly the producer's serialized "
                "surface")
        missing = sorted(f for f in _NULL_FAMILY_REQUIRED
                         if f not in rec)
        if missing:
            problems.append(
                f"SCHEMA_MALFORMED: nulls.{fam} lacks "
                f"serialized fields {missing} — a bound null "
                "family must carry its full record")
            continue
        # R10-P10: the family's serialized semantics — the
        # declared vocabulary and typed numerics.
        if rec.get("statistic") != "silhouette":
            problems.append(
                f"SCHEMA_MALFORMED: nulls.{fam}.statistic must "
                "be 'silhouette'")
        if rec.get("selection") != _NULL_SELECTION:
            problems.append(
                f"SCHEMA_MALFORMED: nulls.{fam}.selection must "
                f"be {_NULL_SELECTION!r} — the declared "
                "selection vocabulary")
        if not isinstance(rec.get("status"), str) or \
                rec.get("status") not in _NULL_FAMILY_STATUSES:
            problems.append(
                f"SCHEMA_MALFORMED: nulls.{fam}.status "
                f"{rec.get('status')!r} is not in "
                f"{sorted(_NULL_FAMILY_STATUSES)} — the "
                "producer's null verdict vocabulary")
        else:
            reason = rec.get("reason")
            if rec["status"] != "PASS" and (
                    not isinstance(reason, str) or
                    not reason.strip()):
                problems.append(
                    f"SCHEMA_MALFORMED: nulls.{fam} "
                    f"status={rec['status']!r} requires a "
                    "non-empty reason — an open verdict must "
                    "say why")
            elif rec["status"] == "PASS" and reason is not None:
                problems.append(
                    f"SCHEMA_MALFORMED: nulls.{fam} status="
                    "'PASS' must carry reason=null")
        if rec.get("observed") is not None and \
                not _finite_float(rec["observed"])[0]:
            problems.append(
                f"SCHEMA_MALFORMED: nulls.{fam}.observed must "
                "be a finite number or null")
        ok_a, f_a = _finite_float(rec.get("alpha"))
        if not ok_a or not 0.0 < f_a < 1.0:
            problems.append(
                f"SCHEMA_MALFORMED: nulls.{fam}.alpha must be "
                "a finite number in (0, 1)")
        pv = rec.get("p_value")
        if pv is not None:
            ok_p, f_p = _finite_float(pv)
            if not ok_p or not 0.0 <= f_p <= 1.0:
                problems.append(
                    f"SCHEMA_MALFORMED: nulls.{fam}.p_value "
                    "must be a finite number in [0, 1] or null")
        # R9-V11 + R10-P10: replicate accounting is bounded AND
        # typed — counts are non-bool ints, n_replicates positive,
        # executed replicates cannot exceed the declared count (a
        # skipped family legitimately reports 0 + 0).
        ns, nf, nr = (rec.get("n_succeeded"), rec.get("n_failed"),
                      rec.get("n_replicates"))
        for name, cnt in (("n_succeeded", ns), ("n_failed", nf),
                          ("n_replicates", nr)):
            if isinstance(cnt, bool) or not isinstance(cnt, int):
                problems.append(
                    f"SCHEMA_MALFORMED: nulls.{fam}.{name} "
                    "must be an integer")
        if all(isinstance(x, int) and not isinstance(x, bool)
               for x in (ns, nf, nr)):
            if nr <= 0 or ns < 0 or nf < 0:
                problems.append(
                    f"SCHEMA_MALFORMED: nulls.{fam} counts must "
                    "be non-negative with n_replicates > 0")
            elif ns + nf > nr:
                problems.append(
                    f"SCHEMA_MALFORMED: nulls.{fam} n_succeeded + "
                    "n_failed exceeds n_replicates — the "
                    "replicate accounting is inconsistent")
        nkd = rec.get("null_k_distribution")
        if not isinstance(nkd, Mapping):
            problems.append(
                f"SCHEMA_MALFORMED: nulls.{fam}."
                "null_k_distribution must be a mapping of "
                "declared-candidate counts")
        else:
            nkd_bad_key = any(
                cand_set and str(k) not in
                {str(c) for c in cand_set} for k in nkd)
            nkd_bad_val = any(
                isinstance(v, bool) or not isinstance(v, int)
                or v < 0 for v in nkd.values())
            if nkd_bad_key:
                problems.append(
                    f"SCHEMA_MALFORMED: nulls.{fam}."
                    "null_k_distribution keys must be declared "
                    "k_candidates")
            if nkd_bad_val:
                problems.append(
                    f"SCHEMA_MALFORMED: nulls.{fam}."
                    "null_k_distribution counts must be "
                    "non-negative integers")
            if not nkd_bad_val and isinstance(nr, int) and \
                    not isinstance(nr, bool) and \
                    sum(nkd.values()) > nr:
                problems.append(
                    f"SCHEMA_MALFORMED: nulls.{fam}."
                    "null_k_distribution counts exceed "
                    "n_replicates — a re-counted replicate is "
                    "not new null evidence")
        for side in ("null_stat_min", "null_stat_max"):
            if side in rec and rec[side] is not None and \
                    not _finite_float(rec[side])[0]:
                problems.append(
                    f"SCHEMA_MALFORMED: nulls.{fam}.{side} must "
                    "be a finite number or null")
        mn, mx = rec.get("null_stat_min"), rec.get("null_stat_max")
        ok_mn, f_mn = _finite_float(mn)
        ok_mx, f_mx = _finite_float(mx)
        if ok_mn and ok_mx and f_mn > f_mx:
            problems.append(
                f"SCHEMA_MALFORMED: nulls.{fam}.null_stat_min "
                "exceeds null_stat_max — the null support is "
                "inverted")
        reps = rec.get("replicates", ...)
        if reps is not ... and reps is not None:
            if not isinstance(reps, (list, tuple)):
                problems.append(
                    f"SCHEMA_MALFORMED: nulls.{fam}.replicates "
                    "must be a sequence of replicate records")
            else:
                ok_true = ok_false = 0
                for ri, rep in enumerate(reps):
                    if not isinstance(rep, Mapping):
                        problems.append(
                            f"SCHEMA_MALFORMED: nulls.{fam}."
                            f"replicates[{ri}] must be a "
                            "serialized replicate record")
                        continue
                    problems.extend(_null_replicate_problems(
                        rep, ri, fam, declared, cand_set))
                    if rep.get("ok") is True:
                        ok_true += 1
                    elif rep.get("ok") is False:
                        ok_false += 1
                # relaxed accounting per the fixture audit: a
                # skipped/partial family carries fewer replicate
                # rows than ns+nf — the carried ok counts can only
                # bound, not exceed, the declared tallies.
                if isinstance(ns, int) and not isinstance(ns, bool) \
                        and ok_true > ns:
                    problems.append(
                        f"SCHEMA_MALFORMED: nulls.{fam} carries "
                        "more ok replicates than n_succeeded "
                        "declares")
                if isinstance(nf, int) and not isinstance(nf, bool) \
                        and ok_false > nf:
                    problems.append(
                        f"SCHEMA_MALFORMED: nulls.{fam} carries "
                        "more failed replicates than n_failed "
                        "declares")
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
    elif not k1_bic or any(not _finite_float(v)[0]
                           for v in k1_bic):
        problems.append(
            "SCHEMA_MALFORMED: nulls.k1_bic must be a non-empty "
            "list of finite numbers — the serialized K=1 "
            "evidence")
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


def _validate_producer_payload_impl(
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

    R10 hardening: every numeric payload value is coerced through
    the bounded ``_finite_float`` parser — an unbounded int like
    ``10**400`` (whose ``float()`` raises ``OverflowError``) is a
    SCHEMA problem, never a crash (R10-P01); the serialized config
    is fully semantic-checked against the run-preflight contract
    (R10-P02); the top-level surface and every serialized section
    admit exact declared field sets (R10-P11); the status machine
    binds ``(terminal, associable)`` to ``status`` exactly
    (R10-P09); the preprocessing contract, the non-fixture
    source-manifest schema, the null-family vocabulary, and the
    typed ``forecast_vintages`` evidence binding are all validated
    (R10-P05/P07/P10/P12).

    ``verify_source_bytes=False`` restores the pure offline surface:
    non-fixture manifests get the schema floor only, no file I/O.
    """
    if not isinstance(payload, Mapping):
        return ["PAYLOAD_MALFORMED: producer payload is not a "
                "mapping"]
    # R10.1-A: JSON-shape preflight — the payload is a serialized
    # artifact, so every value must be JSON-native before any
    # membership/state-machine lookup runs.  Unhashable scalars
    # (list/dict where a string is expected), non-finite floats,
    # sets, iterators, and non-string dict keys are a structured
    # schema rejection here, never a TypeError escaping the floor.
    try:
        _reject_nonjson(payload)
    except (TypeError, ValueError) as exc:
        return [f"SCHEMA_MALFORMED: producer payload carries a "
                f"non-JSON-native value — {exc}"]
    problems: list[str] = []
    # R10-P11: the top-level surface is exact — every field a
    # producer + freeze artifact may carry is declared in
    # PRODUCER_ALLOWED_FIELDS; anything else is undeclared
    # provenance riding the envelope.
    unknown_fields = sorted(set(payload) - PRODUCER_ALLOWED_FIELDS,
                            key=str)
    if unknown_fields:
        problems.append(
            f"SCHEMA_MALFORMED: producer payload carries "
            f"undeclared fields {unknown_fields} — the artifact "
            "surface admits exactly the declared producer "
            "contract")
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
    problems.extend(_preprocessing_problems(payload))
    problems.extend(_scalar_floor_problems(payload))
    problems.extend(_source_manifest_problems(
        payload, verify_source_bytes=verify_source_bytes))
    problems.extend(_unit_basin_map_problems(payload))
    problems.extend(_run_manifest_problems(payload))
    problems.extend(_missingness_problems(payload))
    problems.extend(_input_values_problems(payload))
    problems.extend(_row_universe_problems(payload))
    problems.extend(_config_cross_binding_problems(payload))
    problems.extend(_config_semantic_problems(payload))
    problems.extend(_seed_stability_problems(payload))
    problems.extend(_gate_problems(payload))
    problems.extend(_null_problems(payload))
    problems.extend(_fit_partition_problems(payload))
    problems.extend(_forecast_payload_problems(payload))
    problems.extend(_forecast_vintages_problems(
        payload, verify_source_bytes=verify_source_bytes))
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


def validate_producer_payload(
        payload: Any, *,
        verify_source_bytes: bool = True) -> list[str]:
    """The shared producer-payload provenance floor — public,
    exception-safe wrapper.

    The implementation must return problem strings for every
    malformed input; this wrapper is the last-resort contract
    (R10.1-A): an unexpected ``TypeError``/``ValueError``/
    ``OverflowError`` from an unenumerated coercion site is
    reported as one structured SCHEMA problem, never propagated
    as a crash through freeze/adapter/audit/association.  Sites
    that ARE enumerated produce their specific problem text; this
    message names the catch-all explicitly so residual gaps stay
    visible.
    """
    try:
        return _validate_producer_payload_impl(
            payload, verify_source_bytes=verify_source_bytes)
    except (TypeError, ValueError, OverflowError) as exc:
        return ["SCHEMA_MALFORMED: producer payload rejected by "
                "the validator's exception boundary — the input "
                f"tripped an unenumerated type check "
                f"({type(exc).__name__}: {exc}); structured "
                "rejection is the contract"]
