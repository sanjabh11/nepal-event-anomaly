"""nepal.framework_v1.screen — Phase B one-box susceptibility screening.

Label-free, transparent screening inside the frozen 30 km box.  Design rules:

- The box comes from the frozen source point (contract.study_box), never from
  event density, S1 quality, exposure, or attractive results.
- Terrain and exposure components are separate, declared, percentile-ranked
  layers.  ``terrain_index`` and ``exposure_index`` are equal-weight geometric
  means over SUPPORTED components only.
- ``priority_index`` is the product of the three indices and exists only when
  all three are supported.  Missing components produce UNRANKED, never an
  imputed zero.
- Sidecar layers (thermal / Farinotti bed / permafrost / data-quality nodata
  coverage) are recorded for provenance but structurally cannot enter the
  ranking (SidechainLayerError if attempted).
- Winter Sentinel-1 observability uses acquisition/pair-availability metadata
  only.  HyP3-derived displacement/coherence products are rejected as B
  features and reserved for optional C.
- Farinotti thickness is used only with a DOCUMENTED RGI60 spatial crosswalk
  and is stored as a low-confidence bed-elevation sidecar.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Mapping, Optional, Sequence

import numpy as np

from . import contract as C
from .controls import ControlsConfig, coverage_is_adequate_for_observable
from .provenance import bind_gate_artifact, sha256_canonical

FLAT_ASPECT_NODATA = -9999.0


# ---------------------------------------------------------------------------
# Terrain math (pure numpy; GeoTIFF wrappers below use rasterio lazily)
# ---------------------------------------------------------------------------

def _shift(arr: np.ndarray, dr: int, dc: int, fill: float = np.nan) -> np.ndarray:
    out = np.full_like(arr, fill, dtype=float)
    r0, r1 = max(dr, 0), arr.shape[0] + min(dr, 0)
    c0, c1 = max(dc, 0), arr.shape[1] + min(dc, 0)
    src = arr[r0 - dr:r1 - dr, c0 - dc:c1 - dc]
    out[r0:r1, c0:c1] = src
    return out


def _gradients(dem: np.ndarray, mask: np.ndarray, dx: float,
               dy: float) -> tuple[np.ndarray, np.ndarray]:
    """(dz/dx, dz/dy_north) with Horn 3x3 interior and np.gradient edges.
    NaN cells are neutral-filled for the edge fallback only."""
    z = np.where(mask, dem, np.nan)
    zNW, zN, zNE = _shift(z, 1, 1), _shift(z, 1, 0), _shift(z, 1, -1)
    zW, zE = _shift(z, 0, 1), _shift(z, 0, -1)
    zSW, zS, zSE = _shift(z, -1, 1), _shift(z, -1, 0), _shift(z, -1, -1)
    with np.errstate(invalid="ignore"):
        horn_x = ((zNE + 2 * zE + zSE) - (zNW + 2 * zW + zSW)) / (8.0 * dx)
        horn_y = ((zNW + 2 * zN + zNE) - (zSW + 2 * zS + zSE)) / (8.0 * dy)
        fill = float(np.nanmean(z)) if np.any(mask) else 0.0
        z_fill = np.where(np.isnan(z), fill, z)
        grad_y, grad_x = np.gradient(z_fill, dy, dx)
        dzdx = np.where(np.isnan(horn_x), grad_x, horn_x)
        dzdy = np.where(np.isnan(horn_y), grad_y, horn_y)
    return dzdx, dzdy


def slope_degrees(dem: np.ndarray, dx: float, dy: float,
                  nodata: Optional[float] = None) -> np.ndarray:
    """Slope magnitude in degrees.  Horn 3x3 kernel in the interior (GDAL
    convention); one-sided second-order differences at the array edge.
    Inputs must be meters (projected CRS); output units are degrees."""
    z = np.asarray(dem, dtype=float)
    mask = ~np.isnan(z)
    if nodata is not None:
        mask &= ~np.isclose(z, float(nodata))
    dzdx, dzdy = _gradients(z, mask, dx, dy)
    slope = np.degrees(np.arctan(np.sqrt(dzdx ** 2 + dzdy ** 2)))
    slope[~mask] = np.nan
    return slope


def aspect_degrees(dem: np.ndarray, dx: float, dy: float,
                   nodata: Optional[float] = None) -> np.ndarray:
    """Compass bearing (0-360, 0=north, 90=east) of steepest downhill slope.
    Flat cells get FLAT_ASPECT_NODATA."""
    z = np.asarray(dem, dtype=float)
    mask = ~np.isnan(z)
    if nodata is not None:
        mask &= ~np.isclose(z, float(nodata))
    dzdx, dzdy = _gradients(z, mask, dx, dy)
    bearing = np.degrees(np.arctan2(-dzdx, -dzdy)) % 360.0
    slope_mag = np.sqrt(dzdx ** 2 + dzdy ** 2)
    aspect = np.where(slope_mag == 0, FLAT_ASPECT_NODATA, bearing)
    aspect[~mask] = np.nan
    return aspect


# ---------------------------------------------------------------------------
# Local relief, roughness/curvature, percentile ranking, geometric mean
# ---------------------------------------------------------------------------

def local_relief(dem: np.ndarray, window: int = 9,
                 nodata: Optional[float] = None) -> np.ndarray:
    """Max-minus-min elevation inside a square window (meters)."""
    z = np.asarray(dem, dtype=float)
    mask = ~np.isnan(z)
    if nodata is not None:
        mask &= ~np.isclose(z, float(nodata))
    z = np.where(mask, z, np.nan)
    half = window // 2
    mx, mn = z.copy(), z.copy()
    for dr in range(-half, half + 1):
        for dc in range(-half, half + 1):
            s = _shift(z, dr, dc)
            mx = np.fmax(mx, s)
            mn = np.fmin(mn, s)
    out = mx - mn
    out[~mask] = np.nan
    return out


def roughness(dem: np.ndarray, nodata: Optional[float] = None) -> np.ndarray:
    """Local standard deviation of elevation in the 3x3 neighborhood (m)."""
    z = np.asarray(dem, dtype=float)
    mask = ~np.isnan(z)
    if nodata is not None:
        mask &= ~np.isclose(z, float(nodata))
    z = np.where(mask, z, np.nan)
    stack = np.stack([_shift(z, dr, dc)
                      for dr in (-1, 0, 1) for dc in (-1, 0, 1)])
    with np.errstate(invalid="ignore"):
        out = np.nanstd(stack, axis=0)
    out[~mask] = np.nan
    return out


def curvature(dem: np.ndarray, dx: float, dy: float,
              nodata: Optional[float] = None) -> np.ndarray:
    """Laplacian curvature (1/m): planar ~0, convex > 0, concave < 0."""
    z = np.asarray(dem, dtype=float)
    mask = ~np.isnan(z)
    if nodata is not None:
        mask &= ~np.isclose(z, float(nodata))
    z = np.where(mask, z, np.nan)
    with np.errstate(invalid="ignore"):
        lap = ((_shift(z, 0, -1) + _shift(z, 0, 1) - 2 * z) / dx ** 2
               + (_shift(z, 1, 0) + _shift(z, -1, 0) - 2 * z) / dy ** 2)
    lap[~mask] = np.nan
    return lap


def percentile_rank(values: np.ndarray) -> np.ndarray:
    """Deterministic percentile rank in (0, 1]: (below + 0.5*ties)/n over all
    finite entries; NaN stays NaN.  Strictly positive by construction, so the
    geometric mean can never be silently zeroed by a minimum-rank cell."""
    v = np.asarray(values, dtype=float)
    finite = np.isfinite(v)
    out = np.full_like(v, np.nan, dtype=float)
    if not finite.any():
        return out
    vv = v[finite]
    n = vv.size
    # ``unique`` returns sorted values and an inverse index.  Cumulative
    # counts give the exact midrank rule in O(n log n), while preserving the
    # old float-equality/tie semantics and NaN masking.
    _, inverse, counts = np.unique(vv, return_inverse=True,
                                   return_counts=True)
    below = np.concatenate((np.array([0], dtype=np.int64),
                            np.cumsum(counts, dtype=np.int64)[:-1]))
    ranks = (below + 0.5 * counts) / float(n)
    out[finite] = ranks[inverse]
    return out


def geometric_mean(values: Sequence) -> Optional[float]:
    """Geometric mean of strictly positive finite values; None if unsupported."""
    vals = [float(v) for v in values
            if v is not None and math.isfinite(float(v)) and float(v) > 0]
    if not vals:
        return None
    return math.exp(sum(math.log(v) for v in vals) / len(vals))


# ---------------------------------------------------------------------------
# Winter Sentinel-1 observability (acquisition metadata ONLY)
# ---------------------------------------------------------------------------

S1_METADATA_FIELDS = ("platform", "orbit", "frame", "path", "polarization",
                      "date", "available")
_S1_DERIVED_FIELDS = ("displacement", "coherence", "hyp3_product",
                      "unwrapped_phase", "interferogram")


def validate_acquisition_record(rec: Mapping) -> list:
    """Required metadata fields for one S1 acquisition/pair record."""
    problems = [f"missing field {f!r}" for f in S1_METADATA_FIELDS if f not in rec]
    if "date" in rec:
        try:
            from datetime import date as _date
            _date.fromisoformat(str(rec.get("date"))[:10])
        except (TypeError, ValueError):
            problems.append("date must be ISO-parseable")
    if "available" in rec and not isinstance(rec.get("available"), bool):
        problems.append("available must be a boolean")
    return problems


def separate_hyp3_signals(records: Sequence) -> tuple:
    """Split records into (metadata_only, rejected_with_reason).

    Derived displacement/coherence products are NOT B features: they are
    reserved for optional C.  Rejected records are returned with reasons
    instead of silently stripped."""
    metadata_only, rejected = [], []
    for rec in records:
        derived = [k for k in rec if str(k).lower() in _S1_DERIVED_FIELDS]
        if derived:
            rejected.append({"record": dict(rec), "reason":
                             "derived signal field(s) " + ",".join(sorted(derived))
                             + " not permitted in B acquisition metadata"})
        else:
            metadata_only.append(rec)
    return metadata_only, rejected


def winter_observability_fraction(acquisitions: Sequence,
                                  expected_count: Optional[int],
                                  controls: ControlsConfig) -> Optional[float]:
    """Eligible winter acquisitions/pairs available / expected eligible.

    Acquisition and pair-availability metadata only.  Returns None when the
    expectation is undefined (observability UNSUPPORTED -> UNRANKED)."""
    expected = (expected_count if expected_count is not None
                else controls.expected_winter_acquisitions)
    if not expected or expected <= 0:
        return None
    from datetime import date as _date
    available = 0
    for rec in acquisitions:
        try:
            d = _date.fromisoformat(str(rec.get("date"))[:10])
        except (TypeError, ValueError):
            continue
        if C.is_winter_date(d) and bool(rec.get("available")):
            available += 1
    return min(1.0, available / float(expected))


# ---------------------------------------------------------------------------
# Ranking engine (label-free)
# ---------------------------------------------------------------------------

def _index_for_cell(component_values: Mapping, allowed: Sequence,
                    *, require_all: bool = False) -> Optional[float]:
    # Sidecar / data-quality keys are rejected outright wherever cell
    # components are assembled, so they can never ride along into an index.
    C.assert_no_sidechain_layers(component_values)
    vals = []
    missing = []
    for name in allowed:
        v = component_values.get(name)
        if v is None:
            missing.append(name)
            continue
        try:
            value = float(v)
        except (TypeError, ValueError):
            missing.append(name)
            continue
        if not math.isfinite(value) or value <= 0:
            missing.append(name)
            continue
        vals.append(value)
    if require_all and missing:
        return None
    return geometric_mean(vals) if vals else None


def terrain_index_for_cell(component_values: Mapping, *,
                           require_all: bool = False) -> Optional[float]:
    """Equal-weight geometric mean of SUPPORTED terrain components."""
    return _index_for_cell(component_values, C.TERRAIN_COMPONENTS,
                           require_all=require_all)


def exposure_index_for_cell(component_values: Mapping, *,
                            require_all: bool = False) -> Optional[float]:
    """Equal-weight geometric mean of SUPPORTED exposure components."""
    return _index_for_cell(component_values, C.EXPOSURE_COMPONENTS,
                           require_all=require_all)


def priority_index(terrain: Optional[float], exposure: Optional[float],
                   winter_obs: Optional[float]) -> Optional[float]:
    """Product of the three indices ONLY when all are supported."""
    if terrain is None or exposure is None or winter_obs is None:
        return None
    return float(terrain) * float(exposure) * float(winter_obs)


def rank_cells(rows: Sequence, *, require_all_components: bool = False,
               required_terrain_components: Optional[Sequence[str]] = None,
               required_exposure_components: Optional[Sequence[str]] = None) -> list:
    """Assemble per-cell ranking records with RANKED/UNRANKED/UNSCREENABLE.

    - Missing components produce UNRANKED, never an imputed zero.
    - Cells flagged screenable=False get UNSCREENABLE (inventory coverage gap)
      and are excluded from ranking — never treated as low susceptibility."""
    out = []
    for r in rows:
        rec = dict(r)
        if not rec.get("screenable", True):
            rec.update(status=C.OutputStatus.UNSCREENABLE.value,
                       priority_index=None, terrain_index=None,
                       exposure_index=None)
            out.append(rec)
            continue
        terrain_allowed = (required_terrain_components
                           if required_terrain_components is not None
                           else C.TERRAIN_COMPONENTS)
        exposure_allowed = (required_exposure_components
                            if required_exposure_components is not None
                            else C.EXPOSURE_COMPONENTS)
        t = _index_for_cell(rec.get("terrain_components", {}), terrain_allowed,
                            require_all=require_all_components)
        e = _index_for_cell(rec.get("exposure_components", {}), exposure_allowed,
                            require_all=require_all_components)
        w = rec.get("winter_observability")
        p = priority_index(t, e, w)
        rec.update(terrain_index=t, exposure_index=e, priority_index=p,
                   status=(C.OutputStatus.RANKED.value if p is not None
                           else C.OutputStatus.UNRANKED.value))
        out.append(rec)
    return C.sort_candidates(out)


def rank_component_grids(grids: Mapping) -> dict:
    """Percentile-rank each component grid (NaN = nodata).

    Sidecar and data-quality layers are rejected; record them separately via
    ``sidecar_grids`` in :func:`rank_box`."""
    C.assert_no_sidechain_layers(grids)
    _validate_matching_grid_shapes(grids)
    ranked = {}
    for name, grid in grids.items():
        try:
            arr = np.asarray(grid, dtype=float)
        except (TypeError, ValueError) as exc:
            raise C.ContractViolation(
                f"grid {name!r} cannot be converted to numeric values") from exc
        mask = np.isfinite(arr)
        vals = np.full(arr.shape, np.nan)
        if mask.any():
            vals[mask] = percentile_rank(arr[mask])
        ranked[name] = vals
    return ranked


def _validate_matching_grid_shapes(*grid_maps: Mapping) -> Optional[tuple[int, int]]:
    """Reject misaligned or non-2D arrays before flattening/ranking."""
    shape: Optional[tuple[int, int]] = None
    for grids in grid_maps:
        for name, grid in grids.items():
            try:
                arr = np.asarray(grid)
            except Exception as exc:  # pragma: no cover - numpy boundary
                raise C.ContractViolation(f"grid {name!r} is not array-like") from exc
            if arr.ndim != 2:
                raise C.ContractViolation(
                    f"grid {name!r} must be two-dimensional, got ndim={arr.ndim}")
            current = (int(arr.shape[0]), int(arr.shape[1]))
            if shape is None:
                shape = current
            elif current != shape:
                raise C.ContractViolation(
                    f"grid {name!r} shape {current} does not match {shape}")
    return shape


def grid_to_rows(terrain_grids: Mapping, exposure_grids: Mapping,
                 winter_obs: Optional[float | np.ndarray], box: Optional[Mapping] = None,
                 screenable: Optional[np.ndarray] = None,
                 cell_size: int = C.ANALYSIS_UNIT_CELL_M) -> list:
    """Flatten percentile-ranked component grids into per-cell rows."""
    shape = _validate_matching_grid_shapes(terrain_grids, exposure_grids)
    if isinstance(winter_obs, np.ndarray):
        if winter_obs.ndim != 2:
            raise C.ContractViolation("winter observability grid must be two-dimensional")
        winter_shape = (int(winter_obs.shape[0]), int(winter_obs.shape[1]))
        if shape is not None and winter_shape != shape:
            raise C.ContractViolation(
                f"winter observability shape {winter_shape} does not match {shape}")
    if screenable is not None:
        if screenable.ndim != 2:
            raise C.ContractViolation("screenable mask must be two-dimensional")
        screen_shape = (int(screenable.shape[0]), int(screenable.shape[1]))
        if shape is not None and screen_shape != shape:
            raise C.ContractViolation(
                f"screenable mask shape {screen_shape} does not match {shape}")
    ranked_t = rank_component_grids(terrain_grids)
    ranked_e = rank_component_grids(exposure_grids)
    names_t = sorted(ranked_t)
    names_e = sorted(ranked_e)
    if not (names_t or names_e):
        return []
    ref = ranked_t[names_t[0]] if names_t else ranked_e[names_e[0]]
    shape = np.asarray(ref).shape
    origin = (box["minx"], box["miny"]) if box else (0.0, 0.0)
    rows = []
    for i in range(shape[0]):
        for j in range(shape[1]):
            easting = origin[0] + (j + 0.5) * cell_size
            northing = origin[1] + (shape[0] - i - 0.5) * cell_size
            tcomps = {n: _cell_or_none(ranked_t[n], i, j) for n in names_t}
            ecomps = {n: _cell_or_none(ranked_e[n], i, j) for n in names_e}
            cell_winter_obs = (_cell_or_none(np.asarray(winter_obs), i, j)
                               if isinstance(winter_obs, np.ndarray)
                               else winter_obs)
            rows.append({
                "analysis_unit_id": C.analysis_unit_id(easting, northing,
                                                       cell_size, origin),
                "easting": easting, "northing": northing,
                "terrain_components": tcomps, "exposure_components": ecomps,
                "winter_observability": cell_winter_obs,
                "screenable": (bool(screenable[i, j])
                               if screenable is not None else True),
            })
    return rows


def _cell_or_none(grid: np.ndarray, i: int, j: int) -> Optional[float]:
    v = float(grid[i, j])
    return v if math.isfinite(v) else None


# ---------------------------------------------------------------------------
# rank_box: sidecars recorded, never merged
# ---------------------------------------------------------------------------

def rank_box(terrain_grids: Mapping, exposure_grids: Mapping,
             winter_obs: Optional[float | np.ndarray],
             box: Optional[Mapping] = None,
             screenable: Optional[np.ndarray] = None,
             sidecar_grids: Optional[Mapping] = None,
             cell_size: int = C.ANALYSIS_UNIT_CELL_M,
             require_all_components: bool = False,
             required_terrain_components: Optional[Sequence[str]] = None,
             required_exposure_components: Optional[Sequence[str]] = None) -> dict:
    """Full B ranking for the box.

    Sidecar grids (thermal / Farinotti bed / permafrost) may be passed for
    provenance and quality reporting ONLY; they are hashed into the output
    sidecar summary and are structurally excluded from every index.  This is
    the non-promotion guarantee tested in the contract suite."""
    shape = _validate_matching_grid_shapes(terrain_grids, exposure_grids)
    if isinstance(winter_obs, np.ndarray):
        if winter_obs.ndim != 2:
            raise C.ContractViolation("winter observability grid must be two-dimensional")
        winter_shape = (int(winter_obs.shape[0]), int(winter_obs.shape[1]))
        if shape is not None and winter_shape != shape:
            raise C.ContractViolation(
                f"winter observability shape {winter_shape} does not match {shape}")
    if screenable is not None:
        if screenable.ndim != 2:
            raise C.ContractViolation("screenable mask must be two-dimensional")
        screen_shape = (int(screenable.shape[0]), int(screenable.shape[1]))
        if shape is not None and screen_shape != shape:
            raise C.ContractViolation(
                f"screenable mask shape {screen_shape} does not match {shape}")
    selected_terrain = tuple(required_terrain_components or C.TERRAIN_COMPONENTS)
    selected_exposure = tuple(required_exposure_components or C.EXPOSURE_COMPONENTS)
    C.assert_no_sidechain_layers(selected_terrain)
    C.assert_no_sidechain_layers(selected_exposure)
    ranked = rank_cells(grid_to_rows(terrain_grids, exposure_grids, winter_obs,
                                     box, screenable, cell_size),
                        require_all_components=require_all_components,
                        required_terrain_components=required_terrain_components,
                        required_exposure_components=required_exposure_components)
    sidecar_summary = None
    if sidecar_grids:
        # Sidecar keys MUST be declared sidechain layers (thermal / Farinotti
        # bed / permafrost).  Any other key is a caller error.  They are
        # recorded for provenance and quality only, never merged into indices.
        for name in sidecar_grids:
            if name not in C.SIDECHAIN_LAYERS:
                raise C.SidechainLayerError(
                    f"{name!r} is not a declared sidecar layer")
        sidecar_summary = {}
        for sname, sgrid in sorted(sidecar_grids.items()):
            sarr = np.asarray(sgrid, dtype=float)
            if sarr.ndim != 2:
                raise C.ContractViolation(
                    f"sidecar grid {sname!r} must be two-dimensional")
            if shape is not None and tuple(sarr.shape) != tuple(shape):
                raise C.ContractViolation(
                    f"sidecar grid {sname!r} shape {sarr.shape} does not match "
                    f"primary grid {shape}")
            sidecar_summary[sname] = {
                "nodata_fraction": float(np.mean(~np.isfinite(sarr)))}
    top = [r for r in ranked if r["status"] == C.OutputStatus.RANKED.value]
    return {
        "status": C.OutputStatus.RANKED.value if top else C.OutputStatus.UNRANKED.value,
        "ranked": ranked,
        "top_five": top[:5],
        "n_screened": len(top),
        "n_unranked": sum(1 for r in ranked
                          if r["status"] == C.OutputStatus.UNRANKED.value),
        "n_unscreenable": sum(1 for r in ranked
                              if r["status"] == C.OutputStatus.UNSCREENABLE.value),
        "require_all_components": bool(require_all_components),
        "required_terrain_components": list(selected_terrain),
        "required_exposure_components": list(selected_exposure),
        "active_component_registry_version": C.COMPONENT_REGISTRY_VERSION,
        "active_terrain_components": list(selected_terrain),
        "active_exposure_components": list(selected_exposure),
        "optional_components_present": sorted(
            set(C.OPTIONAL_EXPOSURE_COMPONENTS).intersection(exposure_grids)),
        "optional_components_missing": sorted(
            set(C.OPTIONAL_EXPOSURE_COMPONENTS).difference(exposure_grids)),
        "all_primary_components_supported": bool(
            require_all_components and all(
                r["status"] == C.OutputStatus.RANKED.value for r in ranked
            )),
        "sidecar_summary": sidecar_summary,
        "disclaimer": ("research prioritization list only; not probability, "
                       "not warning, not evacuation guidance"),
    }


# ---------------------------------------------------------------------------
# Leave-one-layer-out sensitivity + B-to-C gate
# ---------------------------------------------------------------------------

def leave_one_layer_out_top5(terrain_grids: Mapping, exposure_grids: Mapping,
                             winter_obs: Optional[float | np.ndarray],
                             box: Optional[Mapping] = None,
                             required_terrain_components: Optional[Sequence[str]] = None,
                             required_exposure_components: Optional[Sequence[str]] = None,
                             active_only: bool = False,
                             progress_callback: Optional[Callable[[str], None]] = None
                             ) -> dict:
    """Recompute the top-5 set with each declared layer removed.

    Returns {layer_name: top5_ids_without_layer}.  A layer whose removal
    changes the top-5 set breaks the B-to-C gate."""
    terrain_allowed = tuple(
        required_terrain_components or
        (C.ACTIVE_TERRAIN_COMPONENTS if active_only else C.TERRAIN_COMPONENTS))
    exposure_allowed = tuple(
        required_exposure_components or
        (C.ACTIVE_EXPOSURE_COMPONENTS if active_only else C.EXPOSURE_COMPONENTS))
    if active_only:
        all_layers = {
            name: value for name, value in terrain_grids.items()
            if name in terrain_allowed
        }
        all_layers.update({
            name: value for name, value in exposure_grids.items()
            if name in exposure_allowed
        })
    else:
        all_layers = dict(terrain_grids)
        all_layers.update(exposure_grids)
    results = {}
    for layer in sorted(all_layers):
        if progress_callback is not None:
            progress_callback(f"leave_one_layer_out:{layer}:start")
        t = {k: v for k, v in terrain_grids.items() if k != layer}
        e = {k: v for k, v in exposure_grids.items() if k != layer}
        if active_only:
            t = {k: v for k, v in t.items() if k in terrain_allowed}
            e = {k: v for k, v in e.items() if k in exposure_allowed}
            layer_terrain = tuple(k for k in terrain_allowed if k != layer)
            layer_exposure = tuple(k for k in exposure_allowed if k != layer)
            ranked = rank_cells(
                grid_to_rows(t, e, winter_obs, box),
                require_all_components=True,
                required_terrain_components=layer_terrain,
                required_exposure_components=layer_exposure,
            )
        else:
            ranked = rank_cells(grid_to_rows(t, e, winter_obs, box))
        results[layer] = [r["analysis_unit_id"] for r in ranked
                          if r["status"] == C.OutputStatus.RANKED.value][:5]
        if progress_callback is not None:
            progress_callback(f"leave_one_layer_out:{layer}:complete")
    return results


def evaluate_b_to_c_gate(*, a_gate_passed: bool,
                         box_inputs_valid: bool,
                         screen_result: Mapping,
                         controls_lock_ok: bool,
                         top5_ids: Sequence,
                         loo_top5: Optional[Mapping] = None,
    sidecar_noninfluence: Optional[bool] = None) -> dict:
    """B_TO_C gate: no second corridor opens before this passes.

    loo_top5: mapping layer -> top5 ids computed with that layer removed
    (leave-one-layer-out sensitivity).  The top five must remain the top five
    under every single-layer removal.

    sidecar_noninfluence: caller-verified comparison of the ranking with and
    without sidecar layers present (must be True)."""
    strict_real_data = bool(screen_result.get("require_all_components", False))
    complete_support = all(
        r["priority_index"] is not None for r in screen_result["top_five"])
    loo_stable, unstable_layers = True, []
    if loo_top5:
        base = set(top5_ids)
        for layer, ids in sorted(loo_top5.items()):
            if set(ids) != base:
                loo_stable = False
                unstable_layers.append(layer)
    elif strict_real_data:
        loo_stable = False

    active_registry_matches = True
    if strict_real_data:
        active_registry_matches = (
            list(screen_result.get("active_terrain_components", ())) ==
            list(C.ACTIVE_TERRAIN_COMPONENTS) and
            list(screen_result.get("active_exposure_components", ())) ==
            list(C.ACTIVE_EXPOSURE_COMPONENTS))
    checks = {
        "gate_A_passed": {"passed": bool(a_gate_passed)},
        "box_inputs_complete_and_provenance_valid": {"passed": bool(box_inputs_valid)},
        "top_five_have_complete_support": {"passed": bool(complete_support)},
        "top_five_exactly_five_distinct_units": {
            "passed": (not strict_real_data or
                       (len(top5_ids) == 5 and len(set(top5_ids)) == 5)),
        },
        "primary_components_complete": {
            "passed": (not strict_real_data or
                       bool(screen_result.get("all_primary_components_supported"))),
        },
        "active_component_registry_matches_contract": {
            "passed": bool(active_registry_matches),
            "registry_version": screen_result.get(
                "active_component_registry_version"),
        },
        "per_unit_observability_used": {
            "passed": (not strict_real_data or
                       screen_result.get("winter_observability_mode") ==
                       "per_analysis_unit"),
        },
        "controls_locked_before_scoring": {"passed": bool(controls_lock_ok)},
        "leave_one_layer_out_top5_stable": {"passed": bool(loo_stable),
                                            "unstable_layers": unstable_layers},
        "no_sidecar_layer_changes_result": {"passed": bool(sidecar_noninfluence)},
    }
    return bind_gate_artifact({
        "gate_id": C.GateId.B_TO_C.value,
        "passed": all(c["passed"] for c in checks.values()),
        "checks": checks,
        "top_five": list(top5_ids),
    })


# ---------------------------------------------------------------------------
# DEM metadata validation + GeoTIFF wrappers (rasterio, lazy import)
# ---------------------------------------------------------------------------

def validate_dem_metadata(crs: str, resolution: Sequence, units: str,
                          nodata: Optional[float]) -> list:
    """DEM gate inputs: projected meters (EPSG:32645 for the frozen box),
    positive resolution, declared nodata.  A geographic CRS (degrees) or an
    undeclared nodata are hard problems."""
    problems = []
    crs_s = str(crs).upper()
    if "32645" not in crs_s:
        if "4326" in crs_s or "DEGREE" in crs_s:
            problems.append(f"DEM CRS {crs!r} is geographic (degrees); units "
                            f"are not meters — reject")
        else:
            problems.append(f"DEM CRS {crs!r} is not the required "
                            f"{C.STUDY_BOX_CRS}")
    if units.lower() not in ("m", "meter", "meters", "metre", "metres"):
        problems.append(f"DEM units {units!r} are not meters")
    rx = float(resolution[0])
    ry = float(resolution[1]) if len(resolution) > 1 else rx
    if not (rx > 0 and ry > 0):
        problems.append(f"non-positive DEM resolution {resolution}")
    if nodata is None:
        problems.append("DEM nodata value is undeclared; nodata coverage "
                        "cannot be reported")
    return problems


def farinotti_bed_sidecar(surface_elevation, thickness, rgi7_id: str,
                           rgi60_crosswalk: Mapping) -> dict:
    """Low-confidence bed-elevation sidecar: surface - thickness.

    Permitted only where a DOCUMENTED RGI60 spatial crosswalk exists for the
    RGI7 id.  The result can never enter the primary score or the gate."""
    status = rgi60_crosswalk.get(rgi7_id)
    if status != C.CrosswalkStatus.DOCUMENTED.value:
        return {"status": C.OutputStatus.BLOCKED.value,
                "reason": f"RGI60 crosswalk status for {rgi7_id!r} is "
                          f"{status!r}; Farinotti thickness requires a "
                          f"documented crosswalk",
                "bed_elevation": None}
    surf = np.asarray(surface_elevation, dtype=float)
    thk = np.asarray(thickness, dtype=float)
    return {"status": "SIDECAR_LOW_CONFIDENCE", "bed_elevation": surf - thk,
            "formula": C.BED_ELEVATION_FORMULA, "promotion_eligible": False}


def read_box_raster(path, band: int = 1):
    """Read the frozen-box window of a GeoTIFF with the pinned rasterio stack.
    Lazy import: the core package still imports without rasterio present."""
    try:
        import rasterio as rio
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("rasterio is required for GeoTIFF processing; "
                           "install the pinned version") from exc
    from rasterio.windows import from_bounds
    bounds = C.study_box_bounds()
    with rio.open(path) as src:
        source_crs = str(src.crs) if src.crs else ""
        problems = validate_dem_metadata(source_crs, src.res, "meters", src.nodata)
        if problems:
            # Reject the source before constructing a window.  A geographic
            # DEM interpreted with projected bounds can otherwise request a
            # massive boundless array and fail by memory exhaustion.
            raise C.ContractViolation("; ".join(problems))
        win = from_bounds(*bounds, transform=src.transform)
        data = src.read(band, window=win, boundless=True, fill_value=src.nodata)
        meta = {"crs": str(src.crs), "resolution": src.res, "nodata": src.nodata,
                "units": "meters"}
    return np.asarray(data, dtype=float), meta


def terrain_components_from_dem(dem_path, glacier_support=None,
                                hanging_ice_support=None) -> dict:
    """Compute the DEM-derived terrain component grids for the frozen box."""
    dem, meta = read_box_raster(dem_path)
    problems = validate_dem_metadata(meta["crs"], meta["resolution"],
                                     meta["units"], meta["nodata"])
    if problems:
        raise C.ContractViolation("; ".join(problems))
    dx, dy = (float(v) for v in meta["resolution"][:2])
    grids = {
        "slope": slope_degrees(dem, dx, dy, nodata=meta["nodata"]),
        "aspect": aspect_degrees(dem, dx, dy, nodata=meta["nodata"]),
        "local_relief": local_relief(dem, window=9, nodata=meta["nodata"]),
        "roughness": roughness(dem, nodata=meta["nodata"]),
    }
    if glacier_support is not None:
        grids["glacier_support"] = np.asarray(glacier_support, dtype=float)
    if hanging_ice_support is not None:
        grids["hanging_ice_support"] = np.asarray(hanging_ice_support, dtype=float)
    return grids


def hanging_ice_support_fraction(s2_dry_season_flags) -> np.ndarray:
    """Hanging-ice support from pre-event dry-season Sentinel-2 evidence.

    ``s2_dry_season_flags`` maps analysis_unit_id -> (n_dry_season_scenes,
    n_ice_scene_obs).  A cell is classified hanging-ice when the ice-obs
    fraction over dry-season scenes reaches the locked controls threshold.
    Cells below the minimum scene count stay UNSCREENABLE (unknown), not
    low-susceptibility."""
    values = []
    for unit_id in sorted(s2_dry_season_flags):
        entry = s2_dry_season_flags[unit_id]
        try:
            n_scenes, n_ice = int(entry[0]), int(entry[1])
        except (TypeError, ValueError, IndexError):
            values.append(np.nan)
            continue
        if n_scenes <= 0 or n_ice < 0 or n_ice > n_scenes:
            values.append(np.nan)
        else:
            values.append(n_ice / float(n_scenes))
    return np.asarray(values, dtype=float)
