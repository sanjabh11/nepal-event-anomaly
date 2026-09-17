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
dicts, performs no file I/O (byte verification of source evidence
stays in ``experiment_v0.audit``), and imports nothing from
``science_v0`` or ``experiment_v0``.

Problem strings are tagged ``"<CATEGORY>: <detail>"`` where CATEGORY
is one of ``PAYLOAD_MALFORMED``, ``PROVENANCE_MISSING``,
``SCHEMA_MALFORMED``, ``DIGEST_MALFORMED``, ``DIGEST_MISMATCH``;
``audit_producer_payload`` maps them to ``PRODUCER_<CATEGORY>``
finding codes.  Callers that only need to fail closed raise on any
non-empty return.
"""
from __future__ import annotations

import math
import re
from typing import Any, Mapping, Sequence

from ._hashing import sha256_canonical
from .policy import RegimeMode

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
    "fit_partition", "fit_partition_digest")

#: Carried digests are verified, never trusted: each must be a
#: 64-hex sha256 when present (absence is a provenance finding).
PRODUCER_DIGEST_FIELDS = (
    "assignment_digest", "regime_artifact_digest", "freeze_digest",
    "feature_matrix_digest", "input_bytes_digest", "config_digest",
    "train_mask_digest", "preprocessing_digest",
    "k_selection_digest", "stability_report_digest",
    "null_model_digest",
    "environment_digest", "run_manifest_digest",
    "fit_partition_digest", "forecast_feature_payload_digest")

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
#: schema floor (byte verification of ``source_files`` under
#: ``evidence_root`` stays in the auditor — this module is pure).
_NONFIXTURE_MANIFEST_KEYS = (
    "source_id", "source_digests", "units", "feature_allowlist",
    "lineage", "evidence_root")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.match(value))


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and \
        isinstance(value, (int, float))


def _seq_of_numbers(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and bool(value) and \
        all(_is_number(v) for v in value)


def _model_problems(payload: Mapping[str, Any]) -> list[str]:
    """Well-formedness of the ``model`` binding — mirrors the
    structural half of ``audit._producer_model_findings`` (exact key
    set, numeric sequences, component counts, declared-k agreement)
    plus the pure-python numeric sanity (finite, non-negative,
    weights sum to 1, consistent dimensions, symmetric square
    covariances).  The numpy PSD check stays in the auditor."""
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
        payload: Mapping[str, Any]) -> list[str]:
    """Schema-level source-manifest floor — presence is covered by
    the required-fields pass; here: mapping shape, non-fixture
    required keys, and sha256 shape of ``source_digests``.  Byte
    verification under ``evidence_root`` stays in the auditor."""
    sm = payload.get("source_manifest")
    if "source_manifest" not in payload:
        return []
    if not isinstance(sm, Mapping):
        return ["SCHEMA_MALFORMED: source_manifest must be a "
                "mapping"]
    problems: list[str] = []
    if not sm.get("fixture"):
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
    return problems


def _unit_basin_map_problems(
        payload: Mapping[str, Any]) -> list[str]:
    """The C03 unit->basin partition: present, non-empty, unique
    units, and every referenced group inside the declared
    fit ∪ heldout universe."""
    ubm = payload.get("unit_basin_map")
    if not isinstance(ubm, (list, tuple)) or not ubm:
        return ["PROVENANCE_MISSING: producer payload lacks a "
                "non-empty 'unit_basin_map' — the unit->basin "
                "partition is unbound"]
    problems: list[str] = []
    if any(not isinstance(e, (list, tuple)) or len(e) != 2
           for e in ubm):
        problems.append(
            "SCHEMA_MALFORMED: unit_basin_map entries must be "
            "(unit, group) pairs")
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
    if problems:
        return problems
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
        if "forecast_feature_payload_digest" not in payload:
            problems.append(
                "PROVENANCE_MISSING: FORECAST_REGIME payload "
                "lacks 'forecast_feature_payload_digest'")
        if problems:
            return problems

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
        return []
    if mode is not None:
        return [f"SCHEMA_MALFORMED: mode {mode!r} is not a "
                "declared RegimeMode value"]
    return []


def validate_producer_payload(payload: Any) -> list[str]:
    """The shared producer-payload provenance floor (R8-C01).

    Field presence over ``PRODUCER_REQUIRED_FIELDS``, 64-hex shape
    over ``PRODUCER_DIGEST_FIELDS``, row accounting, frozen/blinding
    flags, the seed-count floor, occupancy/k agreement, model and
    input-schema well-formedness, the source-manifest schema floor,
    the unit->basin partition, run-manifest presence, the typed
    ``fit_partition`` binding (R7-C09), the mode-conditional
    ``forecast_feature_payload`` binding (R7-C10), and a recomputed
    ``regime_artifact_digest`` envelope — a rehashed partial artifact
    can no longer pass any boundary that runs this first.

    Pure: plain dicts in, problem strings out; no file I/O.
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
    problems.extend(_source_manifest_problems(payload))
    problems.extend(_unit_basin_map_problems(payload))
    if not isinstance(payload.get("run_manifest"), Mapping):
        problems.append(
            "PROVENANCE_MISSING: producer payload lacks a "
            "'run_manifest' mapping — the typed run provenance "
            "record is required")
    # gate-evidence presence floor — an absent required_gates map
    # is not a closed gate (the universe/verdict checks stay in the
    # boundary-specific auditors)
    _stab = payload.get("stability")
    _gates = _stab.get("required_gates") \
        if isinstance(_stab, Mapping) else None
    if not isinstance(_gates, Mapping) or not _gates:
        problems.append(
            "PROVENANCE_MISSING: stability.required_gates is "
            "missing or empty — the artifact carries no gate "
            "evidence to audit")
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
