"""Repository-root pytest configuration.

CI-01 (R10): canonical collection is ``tests/`` and ``tests/`` only.
``data/framework_inputs_v1_reconciled`` is an IGNORED symlink into
operator-local evidence storage; a bare ``pytest`` (or
``pytest --collect-only``) run at the repository root must never
silently follow it — the reconciled-input geospatial tests it
contains are a separately-gated lane (rasterio is quarantined
outside the supported dependency lock), not part of the contract
suite.  Ignoring ``data/`` wholesale is correct: the directory
carries only governed evidence bytes (tif/nc/csv/ledger artifacts
and the symlinked reconciled tree), never canonical tests.
"""

collect_ignore = ["data"]

collect_ignore_glob = ["data/**"]
