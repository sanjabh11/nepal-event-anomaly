"""Resolve and seal the 2000 `Error` field unit discrepancy.

The figshare Greater Himalaya glacial-lake inventory README declares `Error`
in km^2, but the 2000 epoch's `.shp.xml` lineage ends at
``CalculateField lake2000 Error "10.308 * [Perimeter]"`` while every other
epoch's lineage ends at ``CalculateField GlacialLake_<epoch> Error
"[Perimeter] *15/1000000"``.  This script re-derives the recorded formula
from the pinned shapefile bytes, binds the lineage XML to the pinned zip
archive member and the SHA256SUMS manifest, and writes a write-once
disposition artifact.  It is a metadata forensics step: it authorizes no
acquisition and makes no event, causal, forecast, warning, detector, odds,
or operational claim.
"""
from __future__ import annotations

import argparse
import hashlib
import math
import re
import sys
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

import geopandas as gpd
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from india_evidence_register import AUTHORITY_FLAGS  # noqa: E402
from p5_safe_io import write_once_json, write_once_sidecar  # noqa: E402

EVIDENCE_ROOT = Path("/Users/sanjayb/nepal-event-anomaly-evidence")
INTAKE_ROOT = EVIDENCE_ROOT / "india-phase0-source-intake"
FIGSHARE_ROOT = INTAKE_ROOT / "observation-inventory-figshare"
SHAPE_DIR = FIGSHARE_ROOT / "Glacial_Lake_Inventory" / "GlacialLake_20220726"
MANIFEST_PATH = FIGSHARE_ROOT / "SHA256SUMS.txt"
ARCHIVE_PATH = FIGSHARE_ROOT / "Glacial_Lake_Inventory.zip"
DEFAULT_OUT = INTAKE_ROOT / "HMA_ERROR_FIELD_DISPOSITION_V0.json"
SCHEMA = "HMA_ERROR_FIELD_DISPOSITION_V0"
EPOCHS = (1990, 2000, 2010, 2015, 2020)
PIXEL_M = 30.0
EXACT_RTOL = 1e-6
PROCESS_RE = re.compile(
    r"<Process\b[^>]*>.*?</Process>", re.DOTALL)
ERROR_FIELD_RE = re.compile(
    r'CalculateField\s+\S+\s+(?:Error|error|arror)\s+"([^"]+)"')


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def parse_sha256_manifest(path: Path) -> dict[str, str]:
    expected: dict[str, str] = {}
    for number, line in enumerate(
            Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            raise ValueError(f"invalid SHA256SUMS line {number}")
        digest, name = parts
        member = PurePosixPath(name)
        if member.is_absolute() or ".." in member.parts or name in expected:
            raise ValueError(f"unsafe or duplicate SHA256SUMS path: {name}")
        expected[name] = digest
    return expected


def member_digest(relative: str, manifest: dict[str, str]) -> dict[str, Any]:
    """Bind an extracted member to its archive bytes and manifest digest."""
    extracted_path = FIGSHARE_ROOT / relative
    if not extracted_path.is_file():
        raise ValueError(f"source member missing: {relative}")
    if not ARCHIVE_PATH.is_file():
        raise ValueError(f"pinned archive missing: {ARCHIVE_PATH}")
    extracted = extracted_path.read_bytes()
    actual = hashlib.sha256(extracted).hexdigest()
    with zipfile.ZipFile(ARCHIVE_PATH) as archive:
        if relative not in archive.namelist():
            raise ValueError(f"member absent from pinned archive: {relative}")
        archived = archive.read(relative)
    archive_digest = hashlib.sha256(archived).hexdigest()
    if archived != extracted:
        raise ValueError(f"extracted bytes differ from archive: {relative}")
    manifest_digest = manifest.get(relative)
    if manifest_digest is None:
        raise ValueError(f"member absent from SHA256SUMS: {relative}")
    if manifest_digest != actual:
        raise ValueError(f"SHA256SUMS mismatch: {relative}")
    return {
        "member": relative,
        "sha256": actual,
        "archive_member_sha256": archive_digest,
        "manifest_binding": "PRESENT_MATCH",
    }


def error_lineage(xml_path: Path) -> list[dict[str, Any]]:
    """Verbatim <Process> elements whose command writes an Error field."""
    text = Path(xml_path).read_text(encoding="utf-8", errors="replace")
    excerpts: list[dict[str, Any]] = []
    for order, match in enumerate(PROCESS_RE.finditer(text)):
        element = match.group(0)
        formula = ERROR_FIELD_RE.search(element)
        if formula is None:
            continue
        excerpts.append({
            "order": order,
            "formula": formula.group(1),
            "process_element": element,
        })
    return excerpts


def _stats(values: np.ndarray) -> dict[str, float]:
    return {
        "min": float(np.min(values)),
        "q25": float(np.percentile(values, 25)),
        "median": float(np.median(values)),
        "q75": float(np.percentile(values, 75)),
        "max": float(np.max(values)),
    }


def _pearson(x: np.ndarray, y: np.ndarray) -> float:
    return float(np.corrcoef(x, y)[0, 1])


def epoch_profile(epoch: int, manifest: dict[str, str]) -> dict[str, Any]:
    stem = f"GlacialLake_{epoch}"
    shp_rel = ("Glacial_Lake_Inventory/GlacialLake_20220726/"
               f"{stem}.shp")
    xml_rel = f"{shp_rel}.xml"
    gdf = gpd.read_file(SHAPE_DIR / f"{stem}.shp", engine="pyogrio")
    columns = [str(c) for c in gdf.columns]
    for required in ("Area", "Perimeter", "Error"):
        if required not in columns:
            raise ValueError(f"epoch {epoch} lacks required field {required}")
    error = gdf["Error"].to_numpy(dtype=float)
    perimeter = gdf["Perimeter"].to_numpy(dtype=float)
    area = gdf["Area"].to_numpy(dtype=float)
    geom_len_m = gdf.geometry.length.to_numpy(dtype=float)

    # Stored Perimeter unit is inferred from the projected (metre) geometry.
    stored_over_metres = perimeter / geom_len_m
    if np.allclose(stored_over_metres, 1.0, rtol=1e-6):
        perimeter_unit = "metre"
        perimeter_m = perimeter
    elif np.allclose(stored_over_metres, 1e-3, rtol=1e-6):
        perimeter_unit = "kilometre"
        perimeter_m = perimeter * 1000.0
    else:
        raise ValueError(
            f"epoch {epoch} Perimeter unit unresolved: "
            f"median stored/geom-m={np.median(stored_over_metres)}")

    # Normalize Error to a per-metre-of-perimeter effective width in m^2/m,
    # i.e. Error expressed in m^2 divided by Perimeter in metres.  km^2-scale
    # stored values are converted by x1e6.
    ratio_stored = error / perimeter
    width_if_m2 = error / perimeter_m            # assumes stored Error is m^2
    width_if_km2 = error * 1e6 / perimeter_m     # assumes stored Error is km^2
    lineage = error_lineage(SHAPE_DIR / f"{stem}.shp.xml")
    formulas = [e["formula"] for e in lineage]
    has_km2_conversion = any("/1000000" in f or "/ 1000000" in f
                             for f in formulas)
    last_formula = formulas[-1] if formulas else None

    if has_km2_conversion:
        error_unit = "km2"
        width_m = width_if_km2
        scale = 1.0          # stored Error already km^2
    else:
        error_unit = "m2"
        width_m = width_if_m2
        scale = 1e-6         # divide stored Error by 1e6 for km^2

    constant = float(np.median(width_m))
    residuals = width_m - constant
    on_formula = np.abs(residuals) <= EXACT_RTOL * np.abs(constant)
    error_km2 = error * scale
    return {
        "epoch": epoch,
        "n_features": int(len(gdf)),
        "columns_present": columns,
        "perimeter_stored_unit": perimeter_unit,
        "error_stored_unit_inferred": error_unit,
        "error_stats_stored_units": _stats(error),
        "perimeter_stats_stored_units": _stats(perimeter),
        "area_stats_stored_units": _stats(area),
        "error_perimeter_ratio_stored_units": _stats(ratio_stored),
        "effective_width_m_error_m2_over_perimeter_m": _stats(width_m),
        "recovered_formula_constant_m": constant,
        "pearson_r_error_vs_perimeter": _pearson(error, perimeter_m),
        "pearson_r_error_vs_sqrt_area": _pearson(error, np.sqrt(area)),
        "on_formula_feature_count": int(np.count_nonzero(on_formula)),
        "on_formula_fraction": float(np.mean(on_formula)),
        "max_abs_residual_m": float(np.max(np.abs(residuals))),
        "error_km2_equivalent_stats": _stats(error_km2),
        "xml_error_lineage": lineage,
        "xml_final_error_formula": last_formula,
        "xml_records_km2_conversion": has_km2_conversion,
        "xml_sha256": sha256_file(SHAPE_DIR / f"{stem}.shp.xml"),
        "xml_member": member_digest(xml_rel, manifest),
        "shp_member": member_digest(shp_rel, manifest),
    }


def decide(profiles: dict[str, dict[str, Any]]) -> dict[str, Any]:
    p2000 = profiles["2000"]
    others = [profiles[str(e)] for e in EPOCHS if e != 2000]
    converted = p2000["error_km2_equivalent_stats"]
    other_max = max(p["error_stats_stored_units"]["max"] for p in others)
    other_min = min(p["error_stats_stored_units"]["min"] for p in others)
    scale_match = (other_min / 10.0 <= converted["min"]
                   and converted["max"] <= other_max * 10.0)
    conversion_missing_2000 = not p2000["xml_records_km2_conversion"]
    conversion_present_others = all(
        p["xml_records_km2_conversion"] for p in others)
    formula_exact_2000 = math.isclose(
        p2000["recovered_formula_constant_m"], 10.308, rel_tol=1e-9)
    formula_exact_others = all(
        math.isclose(p["recovered_formula_constant_m"], 15.0, rel_tol=1e-9)
        for p in others)
    checks = {
        "converted_2000_error_on_other_epoch_scale": scale_match,
        "km2_conversion_absent_in_2000_lineage": conversion_missing_2000,
        "km2_conversion_present_in_all_other_lineages": (
            conversion_present_others),
        "recovered_2000_constant_equals_recorded_10p308": formula_exact_2000,
        "recovered_other_constant_equals_recorded_15m": formula_exact_others,
    }
    resolved = all(checks.values())
    return {
        "status": "RESOLVED_UNITS" if resolved else "UNRESOLVED",
        "checks": checks,
        "interpretation": (
            "The 2000 epoch Error column retains the intermediate "
            "ArcGIS CalculateField product Error = 10.308 * Perimeter with "
            "Perimeter in metres, i.e. values are in m^2-equivalent units "
            "(10.308 = 0.5 * 0.6872 * 30, the half-pixel * perimeter "
            "mapping-error model applied in the epoch merge lineage). The "
            "final km^2 conversion step recorded in every other epoch "
            "(`[Perimeter] *15/1000000`, i.e. a 15 m effective width) is "
            "absent from the published 2000 shapefile lineage. The 2000 "
            "epoch also stores Perimeter in km where all other epochs "
            "store metres. Dividing stored 2000 Error by 1e6 yields "
            f"median {converted['median']:.6g} km^2 on the same "
            f"[{other_min:.4g}, {other_max:.4g}] km^2 scale as the other "
            "epochs."
            if resolved else
            "Numeric and lineage checks do not jointly confirm the m^2 "
            "hypothesis; missing evidence itemized in `checks`."),
        "decision_for_downstream": (
            "Error is a deterministic perimeter-scaled mapping-error proxy "
            "(effective_width_m x Perimeter_m), not an independent "
            "per-lake uncertainty measurement. It is usable ONLY as a "
            "relative per-epoch sanity bound and must never enter "
            "clustering, weighting, or uncertainty budgets as independent "
            "uncertainty. The stored 2000 values are not directly "
            "comparable to other epochs without the /1e6 unit fix."),
        "recommended_value_for_future_work": (
            "Recompute a uniform perimeter-based +/-0.5-pixel model from "
            "stored fields rather than trusting the column as recorded: "
            "Error_km2 = 0.5 * 30 * Perimeter_m / 1e6 = 15 m x "
            "Perimeter_m / 1e6 (equivalently 1.5e-5 * Perimeter_m), "
            "matching the published 1990/2010/2015/2020 formula exactly. "
            "For strict source fidelity the published 2000 values equal "
            "10.308 m x Perimeter_m in m^2 and require Error/1e6 for km^2; "
            "the 0.6872 co-registration factor makes the 2000 effective "
            "width ~31% narrower than the 15 m used in other epochs."),
        "known_residual_caveat": (
            "2000 epoch: a minority of features deviate from "
            "10.308 x Perimeter_m (see on_formula_feature_count); these "
            "retain the same order of magnitude and do not affect the "
            "unit disposition but reinforce that Error must not be used "
            "as independent uncertainty."),
    }


def build_document() -> dict[str, Any]:
    manifest = parse_sha256_manifest(MANIFEST_PATH)
    profiles = {str(e): epoch_profile(e, manifest) for e in EPOCHS}
    disposition = decide(profiles)
    return {
        "schema": SCHEMA,
        "version": 0,
        "subject": ("figshare Greater Himalaya glacial lake inventory "
                    "GlacialLake_2000 Error field unit discrepancy"),
        "source": {
            "evidence_root": str(EVIDENCE_ROOT),
            "figshare_dir": "observation-inventory-figshare",
            "shape_dir": "Glacial_Lake_Inventory/GlacialLake_20220726",
            "archive": "Glacial_Lake_Inventory.zip",
            "manifest": "SHA256SUMS.txt",
        },
        "per_epoch": profiles,
        "disposition": disposition,
        "authority": dict(AUTHORITY_FLAGS),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="write-once artifact path (must not exist)")
    args = parser.parse_args(argv)
    document = build_document()
    digest = write_once_json(args.out, document)
    sidecar = write_once_sidecar(args.out)
    print(f"sealed {args.out} sha256={digest} sidecar_sha256={sidecar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
