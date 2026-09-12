"""nepal.framework_v1.cli — command-line entry points (no data required to run
``contract``; the other subcommands consume files).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

from . import contract as C
from .provenance import (bind_artifact_envelope, bind_gate_artifact,
                         gate_input_artifact_sha256, sha256_canonical,
                         sha256_file, write_deterministic_json)


def _print(out: str) -> None:
    sys.stdout.write(out if out.endswith("\n") else out + "\n")


def cmd_contract(args) -> int:
    from .input_manifest import contract_source_inventory
    payload = C.contract_dict()
    payload["contract_sha256"] = C.contract_hash()
    payload["contract_source_inventory"] = contract_source_inventory(
        args.repo_root)
    if args.verify:
        payload["frozen_files_verification"] = C.verify_frozen_files(
            args.repo_root)
    _print(json.dumps(payload, sort_keys=True, indent=2,
                      ensure_ascii=True, default=str))
    if args.verify:
        return 0 if payload["frozen_files_verification"].get("ok") else 2
    return 0


def cmd_catalog(args) -> int:
    from .catalog import build_catalog, write_phase_a_artifacts
    from .controls import ControlsConfig
    raw = json.loads(Path(args.raw).read_text(encoding="utf-8"))
    config = (ControlsConfig.from_dict(json.loads(
        Path(args.controls).read_text(encoding="utf-8")))
        if args.controls else ControlsConfig())
    overrides = (json.loads(Path(args.overrides).read_text(encoding="utf-8"))
                 if args.overrides else None)
    result = build_catalog(raw, controls=config, access_date=args.access_date,
                           overrides=overrides)
    paths = write_phase_a_artifacts(result, args.out)
    _print(json.dumps({
        "gate_id": "A_CATALOG", "passed": result["gate"]["passed"],
        "n_eligible": result["gate"]["n_eligible"],
        "artifacts": {k: str(v) for k, v in paths.items()}},
        sort_keys=True, indent=2))
    return 0 if result["gate"]["passed"] else 3


def _load_json(path: str | Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _strict_b_diagnostic(errors: list[str], *, reason: str,
                         details: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build an authenticated, fail-closed strict-B diagnostic envelope."""
    unique_errors = sorted(set(str(error) for error in errors if str(error)))
    gate = bind_gate_artifact({
        "gate_id": C.GateId.B_TO_C.value,
        "passed": False,
        "checks": {"strict_input_preconditions": {"passed": False}},
        "problems": unique_errors,
    })
    result: dict[str, Any] = {
        "profile_id": "FRAMEWORK_V1_FULL",
        "framework_version": C.FRAMEWORK_VERSION,
        "status": C.OutputStatus.BLOCKED.value,
        "gate_id": C.GateId.B_TO_C.value,
        "gate_passed": False,
        "phase_status": C.PHASE_STATUS_B_TO_C_BLOCKED,
        "reason": reason,
        "errors": unique_errors,
        "blocked_reasons": unique_errors,
        "promotion_eligible": False,
        "production_authorized": False,
        "no_claims": [
            "No B-to-C authorization",
            "No scientific, warning, production, or authority claim",
        ],
        "gate": gate,
    }
    if isinstance(details, Mapping):
        result.update(dict(details))
    return bind_artifact_envelope(result)


def _write_strict_b_diagnostic(path: str | Path, errors: list[str], *,
                               reason: str,
                               details: Mapping[str, Any] | None = None) -> dict[str, Any]:
    result = _strict_b_diagnostic(errors, reason=reason, details=details)
    write_deterministic_json(path, result)
    return result


def _strict_e_diagnostic(summary: Mapping[str, Any], *,
                         errors: list[str]) -> dict[str, Any]:
    """Build an authenticated diagnostic that is not an E gate artifact."""
    unique_errors = sorted(set(str(error) for error in errors if str(error)))
    return bind_artifact_envelope({
        "profile_id": "FRAMEWORK_V1_FULL",
        "framework_version": C.FRAMEWORK_VERSION,
        "artifact_kind": "E_BLOCKED_DIAGNOSTIC",
        "status": C.PHASE_STATUS_E_BLOCKED,
        "gate_id": C.GateId.E_VALIDATION.value,
        "promotion_eligible": False,
        "production_authorized": False,
        "summary": dict(summary),
        "errors": unique_errors,
        "blocked_reasons": unique_errors,
        "no_claims": [
            "This is not a verified E validation artifact",
            "No warning, production, scientific, or authority claim",
        ],
    })


def _ensure_a_catalog_envelope(catalog_gate: Mapping[str, Any],
                               artifact_paths: Mapping[str, Any],
                               controls_lock: Any) -> dict[str, Any]:
    """Bind a direct Phase-A gate into the authenticated pipeline handoff."""
    if (isinstance(catalog_gate, Mapping) and
            isinstance(catalog_gate.get("gate"), Mapping)):
        return dict(catalog_gate)
    if not isinstance(catalog_gate, Mapping):
        raise TypeError("A_CATALOG gate must be a mapping")
    artifact_hashes: dict[str, Any] = {}
    for name, raw_path in sorted(artifact_paths.items(), key=lambda pair: str(pair[0])):
        path = Path(raw_path)
        artifact_hashes[str(name)] = sha256_file(path) if path.is_file() else None
    controls_hash = (controls_lock.sha256
                     if hasattr(controls_lock, "sha256") else None)
    return bind_artifact_envelope({
        "profile_id": "FRAMEWORK_V1_FULL",
        "framework_version": C.FRAMEWORK_VERSION,
        "artifact_kind": "A_CATALOG",
        "status": (C.PHASE_STATUS_A_READY
                    if catalog_gate.get("passed") is True
                    else C.PHASE_STATUS_A_BLOCKED),
        "gate_id": C.GateId.A_CATALOG.value,
        "promotion_eligible": False,
        "production_authorized": False,
        "gate": dict(catalog_gate),
        "provenance": {
            "framework_contract_sha256": C.contract_hash(),
            "controls_lock_sha256": controls_hash,
            "catalog_artifact_sha256": artifact_hashes,
            "claim_scope": "research_only_no_operational_authorization",
        },
        "no_claims": [
            "No independent field adjudication",
            "No warning, production, or authority authorization",
        ],
    })


def cmd_manifest(args) -> int:
    from .input_manifest import verify_input_manifest
    manifest = _load_json(args.manifest)
    result = verify_input_manifest(
        manifest, args.root,
        expected_contract_sha256=args.expected_contract_sha256,
        expected_framework_contract_sha256=args.expected_framework_contract_sha256,
        repo_root=args.repo_root,
        required_artifact_ids=args.require_artifact,
    )
    payload = result.to_dict()
    if args.out:
        write_deterministic_json(args.out, payload)
    _print(json.dumps(payload, sort_keys=True, indent=2))
    return 0 if result.ok and (not args.require_primary or result.can_run_primary) else 2


def cmd_integrity_poc(args) -> int:
    from .integrity_poc import IntegrityPocConfig, run_integrity_poc

    config = IntegrityPocConfig(
        repo_root=Path(args.repo_root),
        source_manifest=(Path(args.source_manifest)
                         if args.source_manifest else None),
        real_root=Path(args.real_root),
        real_artifact_id=args.real_artifact_id,
        output_path=Path(args.out),
        expected_data_contract_sha256=args.expected_data_contract_sha256,
        expected_framework_contract_sha256=args.expected_framework_contract_sha256,
        timeout_seconds=args.timeout_seconds,
        warning_seconds=args.warning_seconds,
        chunk_bytes=args.chunk_bytes,
        fixture_bytes=args.fixture_bytes,
        max_peak_rss_mib=args.max_peak_rss_mib,
        minimum_free_gib=args.minimum_free_gib,
        scope_manifest=(Path(args.scope_manifest)
                        if args.scope_manifest else None),
        scope_root=(Path(args.scope_root) if args.scope_root else None),
        trusted_manifest_sha256=args.trusted_manifest_sha256,
        generate_demo_anchor=args.generate_demo_anchor,
        run_b_loader_boundary=args.run_b_loader_boundary,
    )
    try:
        result = run_integrity_poc(config)
    except (OSError, TypeError, ValueError, RuntimeError, C.FrameworkError) as exc:
        result = {
            "profile_id": "INTEGRITY_POC_V1",
            "poc_status": "INTEGRITY_POC_FAILED",
            "gate_id": "INTEGRITY_POC_V1",
            "promotion_eligible": False,
            "errors": [str(exc)],
            "exit_code": 5,
        }
    _print(json.dumps(result, sort_keys=True, indent=2, ensure_ascii=True))
    return int(result.get("exit_code", 5))


def cmd_preflight(args) -> int:
    from .preflight import run_preflight
    result = run_preflight(
        args.repo_root,
        handoff_root=args.handoff_root,
        expected_authoritative_root=args.expected_root,
        minimum_free_gib=args.minimum_free_gib,
    )
    if args.out:
        write_deterministic_json(args.out, result)
    _print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result["ok"] else 2


def cmd_verify(args) -> int:
    from .verification import run_verification
    result = run_verification(args.repo_root,
                              timeout_seconds=args.timeout_seconds)
    if args.out:
        write_deterministic_json(args.out, result)
    _print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result["ok"] else 5


def _grid(spec, base_dir=None):
    import numpy as np
    if not isinstance(spec, dict):
        raise ValueError("screen component specification must be an object")
    if "values" in spec:
        return np.asarray(spec["values"], dtype=float)
    path = spec.get("path")
    if isinstance(path, str) and path:
        if base_dir is not None and not Path(path).is_absolute():
            path = str(Path(base_dir) / path)
        suffix = Path(path).suffix.lower()
        if suffix == ".npy":
            return np.asarray(np.load(path, allow_pickle=False), dtype=float)
        if suffix == ".npz":
            key = str(spec.get("key", "arr_0"))
            with np.load(path, allow_pickle=False) as archive:
                if key not in archive.files:
                    raise ValueError(f"NPZ component key not found: {key}")
                return np.asarray(archive[key], dtype=float)
        if suffix in {".tif", ".tiff"}:
            from .adapters import read_target_raster
            values, _ = read_target_raster(path)
            return values
        raise ValueError(f"unsupported screen component file type: {suffix}")
    raise ValueError("screen config grids must provide inline 'values' "
                     "or a target-aligned .npy/.npz/.tif path")


def _registered_strict_path(spec, name, manifest, manifest_root):
    """Validate that a strict component points to a READY B artifact."""
    if not isinstance(spec, dict):
        return [f"strict component {name!r} must be an object"]
    if "values" in spec:
        return [f"strict component {name!r} cannot use inline values"]
    path = spec.get("path")
    if not isinstance(path, str) or not path:
        return [f"strict component {name!r} requires a registered path"]
    root = Path(manifest_root).resolve()
    candidate = (Path(path) if Path(path).is_absolute()
                 else Path(manifest_root) / path).resolve(strict=False)
    try:
        relative = candidate.relative_to(root).as_posix()
    except ValueError:
        return [f"strict component {name!r} path escapes manifest root"]
    artifacts = manifest.get("artifacts", [])
    matches = [a for a in artifacts if isinstance(a, dict) and
               a.get("relative_path") == relative]
    if not matches:
        return [f"strict component {name!r} path is not registered in the manifest"]
    artifact = matches[0]
    errors = []
    if artifact.get("status") != "READY":
        errors.append(f"strict component {name!r} artifact is not READY")
    if artifact.get("role") != "B":
        errors.append(f"strict component {name!r} artifact role is not B")
    return errors


def _strict_screen_config_errors(config, manifest, manifest_root):
    errors = []
    if not isinstance(config, dict):
        return ["screen config must be an object"]
    for group_name in ("terrain", "exposure"):
        group = config.get(group_name, {})
        if not isinstance(group, dict):
            errors.append(f"strict {group_name} components must be an object")
            continue
        for name, spec in group.items():
            errors.extend(_registered_strict_path(
                spec, f"{group_name}.{name}", manifest, manifest_root))
    obs_path = config.get("observability_by_unit_path")
    errors.extend(_registered_strict_path(
        {"path": obs_path} if obs_path is not None else {},
        "observability_by_unit", manifest, manifest_root))
    return sorted(set(errors))


def cmd_screen(args) -> int:
    if args.strict:
        from .adapters import (build_b_screen_from_bundle,
                               load_verified_b_input_bundle)
        from .controls import load_controls_lock
        from .preflight import run_preflight
        from .provenance import verify_artifact_envelope

        output_path = Path(args.out).resolve(strict=False)
        protected_roots = [Path(value).resolve(strict=False) for value in (
            args.repo_root, args.manifest_root, args.expected_root)
            if value]
        if any(output_path == root or root in output_path.parents
               for root in protected_roots):
            _print("strict B output must be outside protected roots")
            return 2
        requested_output = Path(args.out)
        if requested_output.exists() or requested_output.is_symlink():
            _print("strict B output must be a new path; refusing to overwrite "
                   "an existing result")
            return 2
        requested_checkpoint = (Path(args.checkpoint) if args.checkpoint else
                                requested_output.with_name(
                                    requested_output.name + ".checkpoint.json"))
        checkpoint_path = requested_checkpoint.resolve(strict=False)
        if (checkpoint_path == output_path or
                output_path in checkpoint_path.parents):
            _print("strict B checkpoint must be distinct from the result path")
            return 2
        if any(checkpoint_path == root or root in checkpoint_path.parents
               for root in protected_roots):
            _print("strict B checkpoint must be outside protected roots")
            return 2
        if (requested_checkpoint.exists() or
                requested_checkpoint.is_symlink()):
            _print("strict B checkpoint must be a new path; refusing to "
                   "overwrite an existing checkpoint")
            return 2

        try:
            config = _load_json(args.config) if args.config else {}
        except (OSError, TypeError, ValueError) as exc:
            result = _write_strict_b_diagnostic(
                args.out, [f"strict screen config could not be loaded: {exc}"],
                reason="strict screen input loading failed")
            _print(f"B_SCREEN status: {result['status']}")
            return 2
        config_errors: list[str] = []
        if not isinstance(config, dict):
            config_errors.append("strict screen config must be an object")
        else:
            # Strict screening resolves components only through the canonical
            # B manifest mapping.  The legacy config is a compatibility shell;
            # it can never supply arrays, paths, sidecars, or gate booleans.
            for group_name in ("terrain", "exposure"):
                group = config.get(group_name)
                if isinstance(group, dict) and group:
                    for name, spec in group.items():
                        if isinstance(spec, dict) and "values" in spec:
                            config_errors.append(
                                f"strict component {group_name}.{name} cannot use inline values")
                        else:
                            config_errors.append(
                                f"strict component {group_name}.{name} paths are not accepted; "
                                "use the canonical B manifest mapping")
            for key in ("observability_by_unit_path", "sidecars", "a_gate_passed"):
                if key in config:
                    config_errors.append(
                        f"strict screen config field {key!r} is not an authorization input")
        if config_errors:
            result = _write_strict_b_diagnostic(
                args.out, config_errors,
                reason="strict screen accepts only the verified B bundle")
            _print(f"B_SCREEN status: {result['status']}")
            return 2
        if (not args.manifest or not args.manifest_root or
                not args.controls_lock or not args.a_gate or
                not args.repo_root or not args.expected_root):
            result = _write_strict_b_diagnostic(
                args.out,
                ["strict screen requires --manifest, --manifest-root, "
                 "--controls-lock, --a-gate, --repo-root, and --expected-root"],
                reason="strict screen required inputs are missing")
            _print(f"B_SCREEN status: {result['status']}")
            return 2
        try:
            manifest = _load_json(args.manifest)
        except (OSError, TypeError, ValueError) as exc:
            result = _write_strict_b_diagnostic(
                args.out, [f"strict screen manifest could not be loaded: {exc}"],
                reason="strict screen input loading failed")
            _print(f"B_SCREEN status: {result['status']}")
            return 2
        try:
            preflight = run_preflight(
                args.repo_root, handoff_root=args.manifest_root,
                expected_authoritative_root=args.expected_root)
        except (OSError, TypeError, ValueError, RuntimeError) as exc:
            result = _write_strict_b_diagnostic(
                args.out, [f"strict screen preflight failed: {exc}"],
                reason="authoritative preflight could not be completed")
            _print(f"B_SCREEN status: {result['status']}")
            return 2
        if not preflight["ok"]:
            result = _write_strict_b_diagnostic(
                args.out, list(preflight.get("failures", [])) or
                ["authoritative preflight did not pass"],
                reason="authoritative preflight did not pass",
                details={"preflight": preflight})
            _print(f"B_SCREEN status: {result['status']}")
            return 2
        if (not args.expected_contract_sha256 or
                not args.expected_framework_contract_sha256):
            result = _write_strict_b_diagnostic(
                args.out,
                ["strict screen requires both explicit data and framework "
                 "contract hashes"],
                reason="strict screen contract bindings are missing")
            _print(f"B_SCREEN status: {result['status']}")
            return 2
        try:
            lock = load_controls_lock(_load_json(args.controls_lock))
            a_gate = _load_json(args.a_gate)
            bundle = load_verified_b_input_bundle(
                args.manifest_root, manifest,
                expected_contract_sha256=args.expected_contract_sha256,
                expected_framework_contract_sha256=args.expected_framework_contract_sha256,
                controls_lock=lock,
                repo_root=args.repo_root,
            )
        except (OSError, ValueError, TypeError, RuntimeError, C.FrameworkError) as exc:
            result = _write_strict_b_diagnostic(
                args.out, [f"strict screen input loading failed: {exc}"],
                reason="strict screen input loading failed")
            _print(f"B_SCREEN status: {result['status']}")
            return 2
        if not bundle.ok:
            result = _write_strict_b_diagnostic(
                args.out,
                list(bundle.errors) or
                ["verified B bundle did not authorize primary inputs"],
                reason="verified B bundle did not authorize primary inputs",
                details={"warnings": list(bundle.warnings),
                         "bundle": bundle.to_dict()})
            _print(f"B_SCREEN status: {result['status']}")
            return 2
        try:
            result = build_b_screen_from_bundle(
                bundle, controls_lock=lock, a_gate_artifact=a_gate,
                timeout_seconds=args.timeout_seconds,
                checkpoint_path=checkpoint_path)
        except (OSError, TypeError, ValueError, RuntimeError, C.FrameworkError) as exc:
            result = _strict_b_diagnostic(
                [f"strict B execution failed: {exc}"],
                reason="strict B execution failed")
        envelope_ok, envelope_errors = verify_artifact_envelope(result)
        if not envelope_ok:
            result = _strict_b_diagnostic(
                ["strict B result envelope failed verification", *envelope_errors],
                reason="strict B result envelope failed verification",
                details={"candidate_result": result})
            write_deterministic_json(args.out, result)
            _print(f"B_SCREEN status: {result['status']}")
            return 5
        write_deterministic_json(args.out, result)
        _print(f"B_SCREEN status: {result.get('status', C.OutputStatus.BLOCKED.value)}")
        if result.get("gate_passed") is True:
            return 0
        if result.get("status") == C.B_TIMEOUT_STATUS:
            return 4
        if result.get("status") == C.PHASE_STATUS_SCREEN_RANKED:
            return 3
        return 2

    if not args.config:
        result = {"status": C.OutputStatus.BLOCKED.value,
                  "errors": ["non-strict screen requires --config"]}
        write_deterministic_json(args.out, result)
        _print(f"B_SCREEN status: {result['status']}")
        return 2
    config = _load_json(args.config)
    from .screen import rank_box
    grids = {k: _grid(v) for k, v in config.get("terrain", {}).items()}
    exp = {k: _grid(v) for k, v in config.get("exposure", {}).items()}
    result = rank_box(grids, exp, config.get("winter_observability"),
                      box=config.get("box"),
                      require_all_components=False)
    write_deterministic_json(args.out, result)
    _print(f"B_SCREEN written: {args.out} "
           f"({len(result['ranked'])} units screened)")
    return 0


def cmd_validate(args) -> int:
    from .validation import evaluate_e_gate, run_validation, write_validation_artifact
    from .controls import load_controls_lock
    from .input_manifest import InputManifestVerification, verify_phase_manifest
    from .provenance import verify_gate_input
    events = _load_json(args.events)
    ctrl = _load_json(args.controls)
    holdout = _load_json(args.holdout)
    lock = load_controls_lock(_load_json(args.lock))
    features = (_load_json(args.features)
                if args.features else None)
    input_manifest = (_load_json(args.manifest) if args.manifest else None)
    manifest_check = None
    if args.strict:
        if input_manifest is not None and args.manifest_root:
            manifest_check = verify_phase_manifest(
                input_manifest, args.manifest_root, args.manifest_phase,
                expected_contract_sha256=args.expected_contract_sha256,
                expected_framework_contract_sha256=args.expected_framework_contract_sha256,
                repo_root=args.repo_root)
        else:
            manifest_check = {"ok": False, "can_run_primary": False,
                              "errors": [
                                  "strict validation requires --manifest and --manifest-root"],
                              "warnings": []}
    if isinstance(manifest_check, InputManifestVerification):
        manifest_verification_payload = manifest_check
        manifest_verified = (manifest_check.ok and
                             manifest_check.can_run_primary)
    elif isinstance(manifest_check, dict):
        manifest_verification_payload = manifest_check
        manifest_verified = (manifest_check.get("ok") is True and
                             manifest_check.get("can_run_primary") is True)
    else:
        manifest_verification_payload = None
        manifest_verified = False
    a_gate = (_load_json(args.a_gate) if args.a_gate else None)
    b_gate = (_load_json(args.b_gate) if args.b_gate else None)
    a_gate_passed = a_gate.get("passed") if isinstance(a_gate, dict) else None
    b_gate_passed = b_gate.get("passed") if isinstance(b_gate, dict) else None
    if args.strict:
        a_ok, a_inner, _, _ = verify_gate_input(
            a_gate, expected_gate_id=C.GateId.A_CATALOG.value)
        b_gate_ok, b_inner, _, _ = verify_gate_input(
            b_gate, expected_gate_id=C.GateId.B_TO_C.value,
            require_outer_envelope=True)
        # These booleans are derived locally from verified artifacts solely to
        # let the legacy E runner construct its candidate summary.  They are
        # not passed to evaluate_e_gate, which treats caller booleans as
        # untrusted compatibility inputs.
        a_gate_passed = bool(a_ok and isinstance(a_inner, Mapping) and
                             a_inner.get("passed") is True)
        b_gate_passed = bool(b_gate_ok and isinstance(b_inner, Mapping) and
                             b_inner.get("passed") is True)
    summary = run_validation(events, ctrl, controls_lock=lock,
                             holdout_plan=holdout,
                             min_pairwise_n=args.min_pairs,
                             feature_config=features,
                             input_manifest=input_manifest,
                             input_manifest_verification=manifest_verification_payload,
                             required_features=args.required_feature,
                             strict_contract=args.strict,
                             a_gate_passed=a_gate_passed,
                             b_gate_passed=b_gate_passed,
                             a_gate_artifact=(a_gate if args.strict else None),
                             b_gate_artifact=(b_gate if args.strict else None))
    e_gate = {"passed": True}
    if args.strict:
        e_gate = evaluate_e_gate(
            summary,
            a_gate_artifact=a_gate,
            b_gate_artifact=b_gate,
            controls_lock=lock,
            holdout_plan=holdout,
            input_manifest_verification=manifest_check,
        )
        manifest_hash = None
        if isinstance(manifest_check, InputManifestVerification):
            manifest_hash = manifest_check.checks.get("manifest_sha256")
        elif isinstance(manifest_check, Mapping):
            checks = manifest_check.get("checks")
            if isinstance(checks, Mapping):
                manifest_hash = checks.get("manifest_sha256")
        try:
            write_validation_artifact(
                args.summary,
                summary,
                e_gate,
                provenance={
                    "framework_contract_sha256": C.contract_hash(),
                    "a_gate_artifact_sha256": gate_input_artifact_sha256(
                        a_gate if isinstance(a_gate, Mapping) else {}),
                    "b_artifact_sha256": gate_input_artifact_sha256(
                        b_gate if isinstance(b_gate, Mapping) else {}),
                    "controls_lock_sha256": lock.sha256,
                    "input_manifest_sha256": manifest_hash,
                    "holdout_plan_sha256": (
                        holdout.get("plan_sha256")
                        if isinstance(holdout, Mapping) else None),
                    "summary_sha256": sha256_canonical(dict(summary)),
                    "event_ids": sorted(
                        str(item.get("event_id")) for item in events
                        if isinstance(item, Mapping) and
                        item.get("event_id") is not None),
                    "control_unit_ids": sorted(
                        str(item.get("unit_id")) for item in ctrl
                        if isinstance(item, Mapping) and
                        item.get("unit_id") is not None),
                    "claim_scope": "research_only_no_operational_authorization",
                },
                strict_contract=True,
            )
        except ValueError as exc:
            diagnostic = _strict_e_diagnostic(summary, errors=[str(exc)])
            write_deterministic_json(args.summary, diagnostic)
            _print(f"E_VALIDATION status: {C.PHASE_STATUS_E_BLOCKED} ({exc})")
            return 4
    else:
        write_deterministic_json(args.summary, summary)
    _print(f"E_VALIDATION status: {summary['status']}")
    valid_result = summary["status"] in {"PASS", "NULL", "INDETERMINATE"}
    return 0 if valid_result and (not args.strict or e_gate["passed"]) else 4


def cmd_brief(args) -> int:
    from .briefing import generate_briefing, write_briefing
    document = _load_json(args.summary)
    summary = document.get("summary", document)
    default_e_gate = document.get("gate")
    catalog_gate = (_load_json(args.catalog_gate)
                    if args.catalog_gate else None)
    screen_gate = (_load_json(args.screen_gate)
                   if args.screen_gate else None)
    validation_gate = (_load_json(args.validation_gate)
                       if args.validation_gate else None)
    # ``--gate`` remains a compatibility alias for the old ambiguous option;
    # it is interpreted as E only and never as A.
    if validation_gate is None and args.gate:
        validation_gate = _load_json(args.gate)
    if validation_gate is None:
        validation_gate = default_e_gate
    try:
        text = generate_briefing(summary, catalog_gate=catalog_gate,
                                 screen_gate=screen_gate,
                                 validation_gate=validation_gate,
                                 contract_hash=C.contract_hash(),
                                 strict=args.strict)
    except ValueError as exc:
        _print(f"F_BRIEF status: {C.OutputStatus.BLOCKED.value} ({exc})")
        return 4
    write_briefing(args.out, text)
    _print(f"briefing written: {args.out}")
    return 0


def cmd_run(args) -> int:
    """Run the bounded A/B orchestration with fail-closed downstream phases."""
    from .catalog import build_catalog, write_phase_a_artifacts
    from .controls import ControlsConfig
    from .input_manifest import verify_input_manifest

    manifest = _load_json(args.manifest)
    manifest_check = verify_input_manifest(
        manifest, args.manifest_root,
        expected_contract_sha256=args.expected_contract_sha256,
        expected_framework_contract_sha256=args.expected_framework_contract_sha256,
        required_artifact_ids=args.require_artifact,
    )
    controls = ControlsConfig.from_dict(_load_json(args.controls)) \
        if args.controls else ControlsConfig()
    raw = _load_json(args.raw)
    catalog = build_catalog(raw, controls=controls, access_date=args.access_date)
    out = Path(args.out)
    a_paths = write_phase_a_artifacts(catalog, out / "catalog")
    report: dict[str, object] = {
        "framework_version": C.FRAMEWORK_VERSION,
        "input_manifest_verification": manifest_check.to_dict(),
        "A_CATALOG": catalog["gate"],
        "B_SCREEN": {"status": C.OutputStatus.BLOCKED.value,
                      "reason": "B not run by this bounded invocation"},
        "E_VALIDATION": {"status": C.OutputStatus.BLOCKED.value,
                          "reason": "E requires a verified B artifact"},
        "artifacts": {name: str(path) for name, path in a_paths.items()},
    }
    if not manifest_check.can_run_primary:
        report["B_SCREEN"] = {
            "status": C.OutputStatus.BLOCKED.value,
            "reason": "required reconciled primary input is not consumable",
            "errors": list(manifest_check.errors),
            "warnings": list(manifest_check.warnings),
        }
    elif not catalog["gate"]["passed"]:
        report["B_SCREEN"] = {
            "status": C.OutputStatus.BLOCKED.value,
            "reason": "A_CATALOG gate failed; no B promotion",
        }
    write_deterministic_json(out / "run_report.json", report)
    _print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if (manifest_check.ok and manifest_check.can_run_primary and
                 catalog["gate"]["passed"]) else 5


def cmd_pipeline(args) -> int:
    """Run the verified A-to-B-to-E-to-F path with fail-closed handoffs."""
    from .adapters import (build_b_screen_from_bundle,
                           load_verified_b_input_bundle)
    from .briefing import (build_briefing_artifact, generate_briefing,
                           verify_briefing_artifact, write_briefing)
    from .catalog import (build_catalog, materialize_phase_a,
                          verify_phase_a_envelope)
    from .controls import ControlsConfig
    from . import input_manifest as input_manifest_module
    from .orchestrator import (bind_pipeline_report,
                               load_verified_pipeline_checkpoint,
                               pipeline_input_fingerprint,
                               verify_pipeline_report,
                               write_pipeline_checkpoint)
    from .preflight import run_preflight
    from .provenance import (gate_input_artifact_sha256,
                             verify_artifact_envelope, verify_gate_input)
    from .validation import (evaluate_e_gate, run_validation,
                             write_validation_artifact)

    out = Path(args.out)
    if out.is_symlink():
        _print("pipeline output must not be a symlink")
        return 2
    output_path = out.resolve()
    # Protect every input root before preflight or any output directory is
    # created.  A failed G0 cannot justify writing a diagnostic into a root
    # that the pipeline is supposed to treat as immutable.
    protected_roots = [Path(value).resolve() for value in (
        args.repo_root, args.expected_root, args.manifest_root) if value]
    if any(output_path == root or root in output_path.parents
           for root in protected_roots):
        _print("pipeline output must be outside the repository, authoritative "
               "checkout, and handoff root")
        return 2
    checkpoint_path = output_path / "pipeline_checkpoint.json"
    if output_path.exists():
        if output_path.is_symlink() or not output_path.is_dir():
            _print("pipeline output must be a non-symlink directory")
            return 2
        try:
            output_symlinks = sorted(
                path for path in output_path.rglob("*")
                if path.is_symlink())
        except OSError as exc:
            _print(f"pipeline output symlink scan failed: {exc}")
            return 2
        if output_symlinks:
            _print("pipeline output must not contain symlinks: "
                   f"{output_symlinks[0]}")
            return 2
        try:
            existing_entries = tuple(output_path.iterdir())
        except OSError as exc:
            _print(f"pipeline output directory could not be inspected: {exc}")
            return 2
        if existing_entries and not args.resume:
            _print("pipeline output must be a fresh directory; use --resume "
                   "only with a verified resumable checkpoint")
            return 2
        if args.resume and not checkpoint_path.is_file():
            _print("--resume requires an existing pipeline checkpoint")
            return 2
    elif args.resume:
        _print("--resume requires an existing pipeline output directory")
        return 2
    fingerprint_paths = [
        args.raw,
        args.manifest,
        Path(args.repo_root) / input_manifest_module.AUTHORITATIVE_DATA_CONTRACT_PATH,
        Path(args.repo_root) / input_manifest_module.AUTHORITATIVE_FRAMEWORK_CONTRACT_PATH,
        Path(args.repo_root) / C.PREREGISTRATION_PATH,
    ]
    for optional_path in (args.controls_config, args.events,
                          args.validation_controls, args.holdout, args.features):
        if optional_path:
            fingerprint_paths.append(optional_path)
    input_fingerprint = pipeline_input_fingerprint(
        fingerprint_paths,
        values={
            "expected_root": str(Path(args.expected_root).resolve()),
            "manifest_root": str(Path(args.manifest_root).resolve()),
            "expected_contract_sha256": args.expected_contract_sha256,
            "expected_framework_contract_sha256": (
                args.expected_framework_contract_sha256),
            "minimum_free_gib": args.minimum_free_gib,
            "timeout_seconds": args.timeout_seconds,
            "min_pairs": args.min_pairs,
            "access_date": args.access_date,
            "required_features": list(args.required_feature),
        },
    )
    try:
        preflight = run_preflight(
            args.repo_root,
            handoff_root=args.manifest_root,
            expected_authoritative_root=args.expected_root,
            minimum_free_gib=args.minimum_free_gib,
        )
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        # G0 must remain machine-readable even when the preflight host path is
        # unavailable (for example, shutil.disk_usage on a missing checkout).
        # The report is a blocked diagnostic; no phase materialization follows.
        preflight = {
            "status": "BASELINE_BLOCKED",
            "ok": False,
            "data_source_status": C.PREREGISTRATION_DATA_SOURCE_STATUS,
            "failures": [f"preflight could not be completed: {exc}"],
            "checks": {},
        }
    if preflight["ok"]:
        # The unconditional boundary check above is intentionally repeated as
        # a defensive assertion after G0; no later refactor may move writes
        # ahead of the immutable-root guard.
        if any(output_path == root or root in output_path.parents
               for root in protected_roots):
            _print("pipeline output must be outside protected roots")
            return 2
    out.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {
        "profile_id": "FRAMEWORK_V1_FULL",
        "framework_version": C.FRAMEWORK_VERSION,
        "preflight": preflight,
        "resume_requested": bool(args.resume),
        "input_fingerprint": input_fingerprint,
        "A_CATALOG": {"status": C.PHASE_STATUS_A_BLOCKED,
                      "reason": "not run"},
        "B_SCREEN": {"status": C.PHASE_STATUS_B_TO_C_BLOCKED,
                      "reason": "not run"},
        "E_VALIDATION": {"status": C.PHASE_STATUS_E_BLOCKED,
                          "reason": "requires verified A and B"},
        "F_BRIEFING": {"status": C.PHASE_STATUS_F_BLOCKED,
                        "reason": "requires verified A, B, and E"},
    }

    def _finish(code: int) -> int:
        bound = bind_pipeline_report(
            report, exit_code=code, input_fingerprint=input_fingerprint)
        report_ok, report_errors = verify_pipeline_report(
            bound, artifact_root=out)
        if not report_ok:
            _print("pipeline report failed strict handoff verification: " +
                   "; ".join(report_errors))
            return 5
        try:
            write_deterministic_json(out / "pipeline_report.json", bound)
        except OSError as exc:
            _print(f"pipeline report write failed: {exc}")
            return 5
        try:
            _checkpoint("pipeline_report", "TERMINAL",
                        report_sha256=bound["artifact_sha256"],
                        exit_code=code)
        except OSError as exc:
            _print(f"pipeline checkpoint write failed: {exc}")
            return 5
        _print(json.dumps(bound, sort_keys=True, indent=2))
        return code

    def _checkpoint(stage: str, run_state: str = "RUNNING",
                    **extra: object) -> None:
        payload: dict[str, Any] = {"run_state": run_state, "stage": stage}
        payload.update(extra)
        write_pipeline_checkpoint(checkpoint_path, payload,
                                  input_fingerprint=input_fingerprint)

    if not preflight["ok"]:
        if args.resume:
            _print("pipeline resume blocked by failed preflight: " +
                   "; ".join(str(item) for item in
                             preflight.get("failures", [])))
            return 2
        return _finish(2)

    if args.resume:
        previous, checkpoint_errors = load_verified_pipeline_checkpoint(
            checkpoint_path, input_fingerprint=input_fingerprint)
        if previous is None:
            _print("pipeline resume blocked by checkpoint verification: " +
                   "; ".join(checkpoint_errors))
            return 2
        report["resume"] = {
            "status": "VERIFIED_REPLAY",
            "previous_stage": previous.get("stage"),
            "previous_run_state": previous.get("run_state"),
            "checkpoint_sha256": previous.get("artifact_sha256"),
            "note": ("Outputs are never trusted for gate skipping; the pipeline "
                     "replays from the first verified stage."),
        }
    _checkpoint("preflight", preflight_status=preflight.get("status"))

    try:
        manifest = _load_json(args.manifest)
        raw = _load_json(args.raw)
        controls = (ControlsConfig.from_dict(_load_json(args.controls_config))
                    if args.controls_config else ControlsConfig())
        catalog = build_catalog(raw, controls=controls,
                                access_date=args.access_date)
        a_paths = materialize_phase_a(
            catalog,
            out / "catalog",
            source_catalog_path=args.raw,
            data_contract_sha256=args.expected_contract_sha256,
            access_date=args.access_date,
        )
        a_envelope_path = a_paths["envelope"]
        a_gate_artifact = _load_json(a_envelope_path)
        a_gate_ok, a_gate, _, a_gate_errors = verify_gate_input(
            a_gate_artifact, expected_gate_id=C.GateId.A_CATALOG.value)
        a_deep_ok, a_deep_errors = verify_phase_a_envelope(
            a_gate_artifact,
            out_dir=out / "catalog",
            source_catalog_path=args.raw,
            data_contract_sha256=args.expected_contract_sha256,
            expected_framework_contract_sha256=(
                args.expected_framework_contract_sha256),
        )
        a_gate_passed = bool(
            a_deep_ok and
            a_gate_ok and isinstance(a_gate, Mapping) and
            a_gate.get("passed") is True)
        report["A_CATALOG"] = {
            "status": (C.PHASE_STATUS_A_READY if a_gate_passed
                       else C.PHASE_STATUS_A_BLOCKED),
            "gate": a_gate_artifact,
            "artifacts": {name: str(path) for name, path in a_paths.items()},
            "controls_lock_sha256": catalog["controls_lock"].sha256,
        }
        if a_gate_passed:
            report["A_CATALOG"].update({
                "envelope_verified": True,
                "artifact": str(a_envelope_path),
                "artifact_sha256": a_gate_artifact.get("artifact_sha256"),
                "artifact_file_sha256": sha256_file(a_envelope_path),
            })

        if not a_gate_passed:
            report["A_CATALOG"]["verification_errors"] = sorted(set(
                a_gate_errors + a_deep_errors))
            report["B_SCREEN"] = {
                "status": C.PHASE_STATUS_B_TO_C_BLOCKED,
                "reason": "verified A_CATALOG gate did not pass",
            }
            _checkpoint("A_CATALOG", "BLOCKED", gate_artifact_verified=a_gate_ok)
            return _finish(3)
        _checkpoint("A_CATALOG", artifact_paths={
            name: str(path) for name, path in a_paths.items()},
                     gate_artifact_sha256=gate_input_artifact_sha256(
                         a_gate_artifact))

        bundle = load_verified_b_input_bundle(
            args.manifest_root, manifest,
            expected_contract_sha256=args.expected_contract_sha256,
            expected_framework_contract_sha256=args.expected_framework_contract_sha256,
            repo_root=args.repo_root,
            controls_lock=catalog["controls_lock"],
        )
        if not bundle.ok:
            report["B_SCREEN"] = {
                "status": C.PHASE_STATUS_B_TO_C_BLOCKED,
                "reason": "verified B bundle did not authorize primary inputs",
                "errors": list(bundle.errors),
                "warnings": list(bundle.warnings),
            }
            _checkpoint("B_INPUT", "BLOCKED", errors=list(bundle.errors))
            return _finish(2)

        b_result = build_b_screen_from_bundle(
            bundle, controls_lock=catalog["controls_lock"],
            a_gate_artifact=a_gate_artifact,
            timeout_seconds=args.timeout_seconds,
            checkpoint_path=out / "b_checkpoint.json",
        )
        b_path = out / "b_screen.json"
        write_deterministic_json(b_path, b_result)
        b_envelope_ok, b_envelope_errors = verify_artifact_envelope(b_result)
        report["B_SCREEN"] = {
            "status": b_result.get("phase_status", C.PHASE_STATUS_B_TO_C_BLOCKED),
            "screen_status": b_result.get("status"),
            "gate_passed": b_result.get("gate_passed", False),
            "envelope_verified": b_envelope_ok,
            "verification_errors": b_envelope_errors,
            "artifact": str(b_path),
            "artifact_sha256": b_result.get("artifact_sha256"),
            "artifact_file_sha256": sha256_file(b_path),
        }
        _checkpoint("B_SCREEN", "COMPLETED" if b_envelope_ok else "FAILED",
                     artifact_sha256=b_result.get("artifact_sha256"),
                     gate_passed=b_result.get("gate_passed", False))
        if not b_envelope_ok:
            report["B_SCREEN"]["reason"] = "B result envelope failed verification"
            return _finish(5)
        if b_result.get("status") == C.B_TIMEOUT_STATUS:
            return _finish(4)
        if b_result.get("gate_passed") is not True:
            return _finish(3)

        if not (args.events and args.validation_controls and args.holdout):
            report["E_VALIDATION"] = {
                "status": C.PHASE_STATUS_E_BLOCKED,
                "reason": "E requires --events, --validation-controls, and --holdout",
            }
            _checkpoint("E_VALIDATION", "BLOCKED", reason="required inputs missing")
            return _finish(3)

        events = _load_json(args.events)
        validation_controls = _load_json(args.validation_controls)
        holdout = _load_json(args.holdout)
        features = (_load_json(args.features) if args.features else None)
        summary = run_validation(
            events, validation_controls,
            controls_lock=catalog["controls_lock"], holdout_plan=holdout,
            min_pairwise_n=args.min_pairs, feature_config=features,
            input_manifest=manifest,
            input_manifest_verification=bundle.verification,
            required_features=args.required_feature,
            strict_contract=True,
            a_gate_artifact=a_gate_artifact,
            b_gate_artifact=b_result,
        )
        e_gate = evaluate_e_gate(
            summary, a_gate_artifact=a_gate_artifact, b_gate_artifact=b_result,
            controls_lock=catalog["controls_lock"], holdout_plan=holdout,
            input_manifest_verification=bundle.verification,
        )
        e_path = out / "validation.json"
        e_artifact = write_validation_artifact(
            e_path,
            summary,
            e_gate,
            provenance={
                "framework_contract_sha256": C.contract_hash(),
                "a_gate_artifact_sha256": gate_input_artifact_sha256(
                    a_gate_artifact),
                "b_artifact_sha256": b_result.get("artifact_sha256"),
                "controls_lock_sha256": catalog["controls_lock"].sha256,
                "input_manifest_sha256": (
                    bundle.verification.checks.get("manifest_sha256")
                    if bundle.verification is not None else None),
                "summary_sha256": sha256_canonical(dict(summary)),
                "event_ids": sorted(
                    str(item.get("event_id")) for item in events
                    if isinstance(item, dict) and item.get("event_id") is not None),
                "control_unit_ids": sorted(
                    str(item.get("unit_id")) for item in validation_controls
                    if isinstance(item, dict) and item.get("unit_id") is not None),
                "holdout_plan_sha256": holdout.get("plan_sha256")
                if isinstance(holdout, dict) else None,
                "claim_scope": "research_only_no_operational_authorization",
            },
            strict_contract=True,
        )
        e_envelope_ok, e_envelope_errors = verify_artifact_envelope(e_artifact)
        report["E_VALIDATION"] = {
            "status": e_artifact["status"],
            "gate_passed": e_gate.get("passed", False),
            "envelope_verified": e_envelope_ok,
            "verification_errors": e_envelope_errors,
            "artifact": str(e_path),
            "artifact_sha256": e_artifact.get("artifact_sha256"),
            "artifact_file_sha256": sha256_file(e_path),
        }
        _checkpoint("E_VALIDATION", "COMPLETED" if e_envelope_ok else "FAILED",
                     artifact_sha256=e_artifact.get("artifact_sha256"),
                     gate_passed=e_gate.get("passed", False))
        if not e_envelope_ok:
            return _finish(5)
        if not e_gate.get("passed", False):
            return _finish(3)

        briefing = generate_briefing(
            summary, catalog_gate=a_gate_artifact, screen_gate=b_result,
            validation_gate=e_artifact, contract_hash=C.contract_hash(),
            strict=True,
        )
        f_path = out / "briefing.md"
        write_briefing(f_path, briefing)
        f_artifact = build_briefing_artifact(
            briefing,
            summary=summary,
            catalog_gate=a_gate_artifact,
            screen_gate=b_result,
            validation_gate=e_artifact,
            contract_hash=C.contract_hash(),
            strict=True,
        )
        f_artifact_path = out / "briefing.json"
        # The builder includes the complete upstream envelopes.  Persist with
        # the deterministic writer so the result is atomic, then verify the
        # exact object that is handed to the report.
        write_deterministic_json(f_artifact_path, f_artifact)
        f_ok, f_errors = verify_briefing_artifact(f_artifact)
        report["F_BRIEFING"] = {
            "status": C.PHASE_STATUS_F_READY if f_ok else C.PHASE_STATUS_F_BLOCKED,
            "artifact": str(f_path),
            "envelope": str(f_artifact_path),
            "envelope_verified": f_ok,
            "verification_errors": f_errors,
            "artifact_sha256": f_artifact.get("artifact_sha256"),
            "artifact_file_sha256": sha256_file(f_path),
            "envelope_file_sha256": sha256_file(f_artifact_path),
        }
        _checkpoint("F_BRIEFING", "COMPLETED" if f_ok else "FAILED",
                     artifact=str(f_path),
                     envelope_sha256=f_artifact.get("artifact_sha256"))
        if not f_ok:
            return _finish(5)
    except (OSError, TypeError, ValueError, RuntimeError, C.FrameworkError) as exc:
        report["pipeline_error"] = str(exc)
        try:
            _checkpoint("exception", "FAILED", error=str(exc))
        except OSError:
            pass
        return _finish(5)

    return _finish(0)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nepal-fw",
        description="nepal.framework_v1 clean-room pipeline (phases 0/A/B/E/F)")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("contract", help="print frozen contract + hash")
    c.add_argument("--verify", action="store_true",
                   help="verify frozen files against pinned hashes")
    c.add_argument("--repo-root", default=".", help="repository root")
    c.set_defaults(func=cmd_contract)

    a = sub.add_parser("catalog", help="build Phase A artifacts")
    a.add_argument("--raw", required=True, help="raw records JSON")
    a.add_argument("--out", required=True, help="output directory")
    a.add_argument("--controls", default=None, help="controls JSON")
    a.add_argument("--overrides", default=None, help="adjudication overrides JSON")
    a.add_argument("--access-date", default=None, help="data access date")
    a.set_defaults(func=cmd_catalog)

    b = sub.add_parser("screen", help="run Phase B ranking on a config JSON")
    b.add_argument("--config", required=False,
                   help="legacy compatibility config; strict mode ignores component paths")
    b.add_argument("--out", default="screen_result.json")
    b.add_argument("--strict", action="store_true",
                   help="require reconciled manifest and real-data B gates")
    b.add_argument("--manifest", default=None)
    b.add_argument("--manifest-root", default=None)
    b.add_argument("--repo-root", default=None,
                   help="authoritative checkout containing contract sources")
    b.add_argument("--expected-root", default=None,
                   help="absolute authoritative checkout root")
    b.add_argument("--controls-lock", default=None)
    b.add_argument("--a-gate", default=None,
                   help="hash-bound A_CATALOG gate JSON required by strict mode")
    b.add_argument("--timeout-seconds", type=float, default=None,
                   help="bounded strict B execution deadline")
    b.add_argument("--checkpoint", default=None,
                   help="atomic strict B progress/checkpoint JSON path")
    b.add_argument("--expected-contract-sha256", default=None)
    b.add_argument("--expected-framework-contract-sha256",
                   default=None)
    b.add_argument("--require-artifact", action="append", default=[])
    b.set_defaults(func=cmd_screen)

    e = sub.add_parser("validate", help="run Phase E validation")
    e.add_argument("--events", required=True)
    e.add_argument("--controls", required=True)
    e.add_argument("--lock", required=True, help="controls lock JSON")
    e.add_argument("--holdout", required=True)
    e.add_argument("--features", default=None)
    e.add_argument("--summary", required=True)
    e.add_argument("--min-pairs", type=int, default=30)
    e.add_argument("--strict", action="store_true",
                   help="enforce manifest/gate/one-to-one validation contract")
    e.add_argument("--manifest", default=None)
    e.add_argument("--manifest-root", default=None)
    e.add_argument("--repo-root", default=None,
                   help="authoritative checkout containing contract sources")
    e.add_argument("--manifest-phase", choices=("A", "B"), default="B",
                   help="phase-specific manifest contract (default: B)")
    e.add_argument("--expected-contract-sha256", default=None)
    e.add_argument("--expected-framework-contract-sha256",
                   default=C.contract_hash())
    e.add_argument("--require-artifact", action="append", default=[])
    e.add_argument("--a-gate", default=None)
    e.add_argument("--b-gate", default=None)
    e.add_argument("--required-feature", action="append", default=[])
    e.set_defaults(func=cmd_validate)

    f = sub.add_parser("brief", help="generate deterministic briefing")
    f.add_argument("--summary", required=True)
    f.add_argument("--catalog-gate", default=None,
                   help="A_CATALOG gate JSON")
    f.add_argument("--screen-gate", default=None,
                   help="B_TO_C/B_SCREEN gate JSON")
    f.add_argument("--validation-gate", default=None,
                   help="E_VALIDATION gate JSON")
    f.add_argument("--gate", default=None)
    f.add_argument("--strict", action="store_true",
                   help="require verified A/B/E envelopes before generating F")
    f.add_argument("--out", required=True)
    f.set_defaults(func=cmd_brief)

    m = sub.add_parser("manifest", help="verify a reconciled input manifest")
    m.add_argument("--manifest", required=True)
    m.add_argument("--root", required=True)
    m.add_argument("--repo-root", default=None,
                   help="authoritative checkout containing contract sources")
    m.add_argument("--out", default=None)
    m.add_argument("--expected-contract-sha256", default=None)
    m.add_argument("--expected-framework-contract-sha256",
                   default=C.contract_hash())
    m.add_argument("--require-artifact", action="append", default=[])
    m.add_argument("--require-primary", action="store_true")
    m.set_defaults(func=cmd_manifest)

    i = sub.add_parser(
        "integrity-poc",
        help="run the narrow SHA-256 integrity PoC and tamper benchmark",
    )
    i.add_argument("--repo-root", required=True)
    i.add_argument("--source-manifest", default=None,
                   help="live source manifest used to select one real artifact")
    i.add_argument("--real-root", required=True,
                   help="root containing the selected real artifact")
    i.add_argument("--real-artifact-id", default="osm_geofabrik_nepal")
    i.add_argument("--scope-manifest", default=None,
                   help="previously materialized PoC scope manifest for strict replay")
    i.add_argument("--scope-root", default=None,
                   help="root containing the strict replay scope artifact")
    i.add_argument("--trusted-manifest-sha256", default=None,
                   help="external trusted digest for a materialized scope manifest")
    i.add_argument("--generate-demo-anchor", action="store_true",
                   help="explicit PoC-only run-generated anchor; not authenticity proof")
    i.add_argument("--expected-data-contract-sha256", required=True)
    i.add_argument("--expected-framework-contract-sha256", required=True)
    i.add_argument("--out", required=True)
    i.add_argument("--timeout-seconds", type=float, default=120.0)
    i.add_argument("--warning-seconds", type=float, default=30.0)
    i.add_argument("--chunk-bytes", type=int, default=1 << 20)
    i.add_argument("--fixture-bytes", type=int, default=64 << 10)
    i.add_argument("--max-peak-rss-mib", type=float, default=512.0)
    i.add_argument("--minimum-free-gib", type=float, default=4.0)
    i.add_argument("--run-b-loader-boundary", action="store_true",
                   help="optional diagnostic of the current full B loader")
    i.set_defaults(func=cmd_integrity_poc)

    q = sub.add_parser("preflight", help="capture a read-only checkout/data baseline")
    q.add_argument("--repo-root", default=".")
    q.add_argument("--handoff-root", default=None)
    q.add_argument("--expected-root", default=None)
    q.add_argument("--minimum-free-gib", type=float, default=8.0)
    q.add_argument("--out", default=None)
    q.set_defaults(func=cmd_preflight)

    v = sub.add_parser("verify", help="run the canonical bounded verification suite")
    v.add_argument("--repo-root", default=".")
    v.add_argument("--timeout-seconds", type=float, default=None,
                   help="per-check timeout; timeout is reported as INCOMPLETE")
    v.add_argument("--out", default=None)
    v.set_defaults(func=cmd_verify)

    p0 = sub.add_parser(
        "pipeline", help="run the verified A-to-B-to-E-to-F path")
    p0.add_argument("--repo-root", default=".")
    p0.add_argument("--expected-root", required=True,
                    help="absolute authoritative checkout root")
    p0.add_argument("--raw", required=True,
                    help="preserved raw catalog records JSON")
    p0.add_argument("--manifest", required=True,
                    help="canonical Phase B manifest JSON")
    p0.add_argument("--manifest-root", required=True,
                    help="root containing the manifest's registered files")
    p0.add_argument("--out", required=True,
                    help="successor output directory; existing data is not removed")
    p0.add_argument("--resume", action="store_true",
                    help="resume only after verifying the pipeline checkpoint and input fingerprint")
    p0.add_argument("--controls-config", default=None,
                    help="pre-scoring A/B controls JSON; defaults to frozen controls")
    p0.add_argument("--access-date", default=None)
    p0.add_argument("--expected-contract-sha256", required=True,
                    help="explicit data-contract SHA-256")
    p0.add_argument("--expected-framework-contract-sha256", required=True,
                    help="explicit framework-contract SHA-256")
    p0.add_argument("--minimum-free-gib", type=float, default=8.0)
    p0.add_argument("--timeout-seconds", type=float, default=None,
                    help="bounded Phase B deadline")
    p0.add_argument("--events", default=None,
                    help="strict E event-score JSON; omitted means E is blocked")
    p0.add_argument("--validation-controls", default=None,
                    help="strict E control-score JSON")
    p0.add_argument("--holdout", default=None,
                    help="strict E frozen holdout-plan JSON")
    p0.add_argument("--features", default=None)
    p0.add_argument("--min-pairs", type=int, default=30)
    p0.add_argument("--required-feature", action="append", default=[])
    p0.set_defaults(func=cmd_pipeline)

    r = sub.add_parser("run", help="run bounded A then fail-closed downstream report")
    r.add_argument("--raw", required=True)
    r.add_argument("--manifest", required=True)
    r.add_argument("--manifest-root", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--controls", default=None)
    r.add_argument("--access-date", default=None)
    r.add_argument("--expected-contract-sha256", default=None)
    r.add_argument("--expected-framework-contract-sha256",
                   default=C.contract_hash())
    r.add_argument("--require-artifact", action="append", default=[])
    r.set_defaults(func=cmd_run)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
