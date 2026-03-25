---
phase: 02-refactoring
plan: 01
subsystem: database
tags: [flask, sqlite, config, db, pytest, smoke-tests]

requires: []

provides:
  - config.py with all module-level constants (BASE_DIR, DB_PATH, BACKUP_ROOT, KEY_PATH, BUILTIN_ADMIN, OFFLINE_PKG_DIR, UBUNTU_CODENAMES, ANSIBLE_* dirs, SECRET_KEY_FILE, SCHEDULER_TIMEZONES)
  - db.py with get_db(), init_db(), _migrate_db(), _init_ansible_workspace()
  - models/, services/, routes/ empty Python packages
  - tests/test_smoke_routes.py with 36 smoke tests covering all GET routes
  - tests/conftest.py app_client fixture with temp DB isolation

affects: [02-02, 02-03, 02-04, 02-05, 02-06]

tech-stack:
  added: []
  patterns:
    - "Constants extracted to config.py; all modules import from config"
    - "DB layer isolated in db.py; all routes use get_db() from db module"
    - "Test isolation via temp SQLite DB file patching db.DB_PATH"
    - "Smoke tests use parametrize over route lists"

key-files:
  created:
    - config.py
    - db.py
    - models/__init__.py
    - services/__init__.py
    - routes/__init__.py
    - tests/test_smoke_routes.py
  modified:
    - app.py
    - tests/conftest.py
    - pytest.ini

key-decisions:
  - "app.py keeps _get_local_ip() since get_nfs_target() also uses it; db.py has its own private copy for init_db()"
  - "Smoke test fixture patches db.DB_PATH (module-level var) not config.DB_PATH, because get_db() reads DB_PATH from db module globals at call time"
  - "smoke pytest mark registered in pytest.ini to avoid PytestUnknownMarkWarning"

patterns-established:
  - "Import pattern: from config import ...; from db import get_db, init_db"
  - "Test DB isolation: patch db.DB_PATH + config.DB_PATH in app_client fixture"

requirements-completed: [REF-01]

duration: 18min
completed: 2026-03-25
---

# Phase 02 Plan 01: Foundation Scaffolding Summary

**Constants extracted to config.py and DB layer isolated to db.py, with 36-test smoke suite providing regression coverage over all GET routes**

## Performance

- **Duration:** 18 min
- **Started:** 2026-03-25T20:17:17Z
- **Completed:** 2026-03-25T20:35:00Z
- **Tasks:** 2
- **Files modified:** 9

## Accomplishments

- Created config.py with all 14 module-level constants from app.py
- Created db.py with get_db(), init_db(), _migrate_db(), _init_ansible_workspace()
- Updated app.py to import from config and db; removed all moved code (432 lines removed from app.py)
- Created models/, services/, routes/ empty package directories
- Created app_client test fixture with per-test SQLite temp DB isolation
- Created 36-test smoke suite covering all GET-accessible routes — acts as regression safety net for all future refactoring plans

## Task Commits

Each task was committed atomically:

1. **Task 1: Extract constants to config.py and DB functions to db.py** - `9f771a1` (feat)
2. **Task 2: Create package directories and smoke test infrastructure** - `6798ed6` (feat)

**Plan metadata:** (docs commit pending)

## Files Created/Modified

- `config.py` - All module-level constants (BASE_DIR, DB_PATH, BACKUP_ROOT, ANSIBLE_* dirs, SCHEDULER_TIMEZONES, etc.)
- `db.py` - get_db(), init_db(), _migrate_db(), _init_ansible_workspace()
- `app.py` - Removed constants and DB function bodies; added imports from config and db
- `models/__init__.py` - Empty package marker
- `services/__init__.py` - Empty package marker
- `routes/__init__.py` - Empty package marker
- `tests/conftest.py` - Added app_client fixture with temp DB isolation
- `tests/test_smoke_routes.py` - 36 smoke tests hitting all GET routes
- `pytest.ini` - Registered smoke mark

## Decisions Made

- `app.py` retains its own `_get_local_ip()` because `get_nfs_target()` also uses it; db.py has a private copy for `init_db()`. Duplication intentional at this scaffolding stage.
- Smoke test fixture patches `db.DB_PATH` (the module-level variable that `get_db()` reads at call time) rather than `config.DB_PATH` alone, because `from config import DB_PATH` creates a copy in the db module's namespace.
- `smoke` mark registered in `pytest.ini` to avoid `PytestUnknownMarkWarning`.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Scaffold complete: config.py, db.py, models/, services/, routes/ all exist
- Smoke test baseline established: 55 total tests pass (36 smoke + 19 pre-existing)
- Plans 02-02 through 02-06 can proceed with the route/service/model decomposition
- No blockers

---
*Phase: 02-refactoring*
*Completed: 2026-03-25*
