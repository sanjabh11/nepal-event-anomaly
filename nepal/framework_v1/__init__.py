"""nepal.framework_v1 — clean-room framework v1 package (phases 0, A, B, E, F).

Phase 0 contract and package scaffold.  This package is clean-room: it never
imports the legacy multi-event harness or phase runner, and it imports with no
data present.

Modules:
    contract    — frozen contract, study box, statuses, gates, hashes
    provenance  — deterministic JSON/CSV, sha256 manifests, raw-SLC hygiene
    catalog     — Phase A additive normalization, adjudication, gate A
    controls    — controls configuration + pre-scoring lock
    screen      — Phase B one-box label-free ranking + B-to-C gate
    validation  — Phase E geographic leave-one-group-out honest validation
    extensions  — fail-closed C/D optional interfaces (BLOCKED in v1)
    briefing    — deterministic F briefing generator
    cli         — command-line entry points
"""

from . import contract as _contract
from . import catalog as _catalog
from . import controls as _controls
from . import provenance as _provenance
from . import screen as _screen
from . import validation as _validation
from . import extensions as _extensions
from . import briefing as _briefing
from . import input_manifest as _input_manifest
from . import adapters as _adapters
from .contract import (FRAMEWORK_NAME, FRAMEWORK_VERSION, CONTRACT_SCHEMA_VERSION,
                       contract_hash, study_box, study_box_bounds,
                       GateId, OutputStatus, DatePrecision, MechanismClass,
                       THERMAL_CONTEXT, SIDECHAIN_LAYERS, TIE_BREAK_RULES,
                       COMPONENT_REGISTRY_VERSION, ACTIVE_TERRAIN_COMPONENTS,
                       ACTIVE_EXPOSURE_COMPONENTS, OPTIONAL_EXPOSURE_COMPONENTS,
                       PHASE_STATUSES)

__version__ = FRAMEWORK_VERSION
contract = _contract
catalog = _catalog
controls = _controls
provenance = _provenance
screen = _screen
validation = _validation
extensions = _extensions
briefing = _briefing
input_manifest = _input_manifest
adapters = _adapters
__all__ = [
    "FRAMEWORK_NAME", "FRAMEWORK_VERSION", "CONTRACT_SCHEMA_VERSION",
    "contract_hash", "study_box", "study_box_bounds",
    "GateId", "OutputStatus", "DatePrecision", "MechanismClass",
    "THERMAL_CONTEXT", "SIDECHAIN_LAYERS", "TIE_BREAK_RULES",
    "COMPONENT_REGISTRY_VERSION", "ACTIVE_TERRAIN_COMPONENTS",
    "ACTIVE_EXPOSURE_COMPONENTS", "OPTIONAL_EXPOSURE_COMPONENTS",
    "PHASE_STATUSES",
    "contract", "catalog", "controls", "provenance", "screen", "validation",
    "extensions", "briefing", "input_manifest", "adapters",
]
