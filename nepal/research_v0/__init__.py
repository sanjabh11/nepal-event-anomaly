"""Additive research-only namespace for the Nepal hazard science program.

This package implements the v0 science-design contracts described in
``docs/science/`` (``HAZARD_EVENT_INVENTORY_DECISION_MATRIX_V0`` and
``INFORMATION_CUTOFF_TARGET_POLICY_V0``).  It is intentionally isolated
from the frozen ``nepal.framework_v1`` package: it imports nothing from
it, writes to its own output root, and exposes its own CLI namespace.

Nothing here downloads data, emits ``FMX_READY``, or asserts operational,
warning, production, or scientific-validation authority.  Envelope
construction is refused unless a complete design-approval binding is
supplied (see :mod:`nepal.research_v0.gates`).
"""
