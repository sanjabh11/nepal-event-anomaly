"""Additive research-only downstream namespace for the Nepal hazard
science program (SWE swarm B).

This package implements the association and archived-vintage forecast
machinery specified in ``docs/science/run_c/`` (ASSOCIATION_PROTOCOL_V0,
FORECAST_ARCHIVE_MATRIX_V0, FORECAST_EVAL_SCAFFOLD_V0).  It imports the
``nepal.research_v0`` contracts, imports nothing from
``nepal.framework_v1``, performs no downloads or credential use, and
emits only neutral research statuses — never forecast-skill, warning,
production, or authority claims.
"""
