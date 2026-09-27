# Watcher — Two-Lane Isolation Monitor

**Role:** Independent observer. Does NOT write to either lane's scope.
**Polling interval:** 10 minutes
**Started:** 2026-09-11

## Lane Boundaries

| Lane | Write scope | Must NOT touch |
|---|---|---|
| Codex (coding) | `nepal/framework_v1/`, `tests/test_framework_v1_*.py`, CLI, schemas, adapters, gates | `data/framework_inputs_v1/`, `data/framework_inputs_v1_tmp/`, `preregistration.md`, legacy `data/*.json`, `data/*.nc` |
| GLM2 (data) | `data/framework_inputs_v1/`, `data/framework_inputs_v1_tmp/`, manifest only | `nepal/`, `tests/`, `preregistration.md`, legacy `data/*.json`, `data/*.nc` |

## Frozen Artifact Guards (SHA-256)

| File | Expected SHA-256 |
|---|---|
| `preregistration.md` | `0e7ce3c2e347a955f7495d719bb9232465ac9c25a5266656865800dbc963da7c` |
| `data/gmm_false_positive_results.json` | `fb711617194d41ed4f9b109f72c07558c22d247b6266b4dbb59f211a76cfd2bc` |
| `data/locked_jja_events.json` | `536125c44033cd6852e82c4718cca2a9138ca0f369e2cecf84de4c215af427eb` |
| `data/features_nepal_jja_2001_2026.csv` | `444f2ac0904757d67e68a0c8cbec8544a355a0a16c42ed7ffa86c2556a338041` |

## Checks per cycle

1. Frozen artifact hash integrity (4 files)
2. Codex lane: no writes to `data/framework_inputs_v1/` or `data/framework_inputs_v1_tmp/`
3. GLM2 lane: no writes to `nepal/`, `tests/`, `preregistration.md`
4. Disk space (< 5 GB persistent, < 8 GB temp)
5. Test suite status (framework_v1 tests pass)
6. Manifest artifact count and status distribution
7. Recent file modifications (last 10 min) in each lane's scope
