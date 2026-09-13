"""Runtime import boundary for the quarantined legacy harnesses (BOUND-02).

:func:`~nepal.framework_v1.research_boundaries.\
scan_source_for_quarantined_imports` is a static AST/source scan — it is
not a sandbox.  Nothing in it stops a running interpreter from importing
a quarantined module through ``importlib``, ``__import__``, or an
indirect dependency chain.  This module installs the runtime half of the
boundary: a meta-path finder that fails closed (raises ``ImportError``)
whenever a quarantined module name from ``QUARANTINED_MODULES`` is a
substring of the requested module name.

The finder is inserted ahead of the path-based finder so that
quarantined modules which exist on disk are still blocked.
"""
from __future__ import annotations

import importlib.abc
import sys

from .research_boundaries import QUARANTINED_MODULES


class _QuarantineFinder(importlib.abc.MetaPathFinder):
    """Meta-path finder that refuses quarantined module imports."""

    def find_spec(self, fullname, path=None, target=None):
        if any(q in fullname for q in QUARANTINED_MODULES):
            raise ImportError(
                f"import of quarantined module {fullname!r} is blocked "
                "by the runtime research boundary")
        return None


_FINDER = _QuarantineFinder()


def install_import_guard():
    """Install the quarantine finder on ``sys.meta_path``.

    Idempotent: re-installing while active is a no-op.  Returns the
    finder object.
    """
    if not guard_active():
        sys.meta_path.insert(0, _FINDER)
    return _FINDER


def uninstall_import_guard():
    """Remove the quarantine finder.  Safe to call when not installed."""
    while _FINDER in sys.meta_path:
        sys.meta_path.remove(_FINDER)


def guard_active() -> bool:
    """True when the quarantine finder is installed on ``sys.meta_path``."""
    return _FINDER in sys.meta_path
