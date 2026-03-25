---
phase: 02-refactoring
plan: "02"
subsystem: database
tags: [sqlite, repository-pattern, refactoring, flask]

# Dependency graph
requires:
  - phase: 01-bug-fixes
    provides: working app.py with all bug fixes applied
provides:
  - models/users.py — user repository (get_by_id, create, update, upsert_ad_user, etc.)
  - models/servers.py — server repository (get_all, create, update, bulk_create, etc.)
  - models/schedules.py — schedule repository (get_all_enabled, toggle, update_last_run, etc.)
  - models/jobs.py — backup job repository (create, append_log, update_status, get_stats, etc.)
  - models/settings.py — settings repository (get_settings, save_many, get_nfs_target)
  - models/ansible.py — ansible repository covering all 6 ansible_* tables
  - app.py with zero direct DB access — all SQL in models/
affects: [03-service-layer, 04-api-layer, testing]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Repository pattern: plain functions in models/*.py, each importing from db import get_db"
    - "Single-connection-per-call: each repo function opens/closes its own connection"
    - "Thin app.py wrappers: get_settings() and save_setting() remain in app.py delegating to settings_repo"

key-files:
  created:
    - models/users.py
    - models/servers.py
    - models/schedules.py
    - models/jobs.py
    - models/settings.py
    - models/ansible.py
  modified:
    - app.py
    - tests/test_bug03_scheduler.py

key-decisions:
  - "Repository functions are plain functions (not classes) — keeps import style simple"
  - "get_settings() and save_setting() kept as wrappers in app.py since they are used by non-route helpers throughout the file"
  - "models/ansible.py covers all 6 ansible_* tables in a single module to match the existing grouping in app.py"
  - "get_role_by_id() returns (role, files) tuple — app.py unpacks accordingly"

patterns-established:
  - "All DB access through models/*.py — app.py imports only init_db from db"
  - "Each model module: from db import get_db at top, plain functions returning Row objects or primitives"

requirements-completed: [REF-02]

# Metrics
duration: multi-session
completed: 2026-03-25
---

# Phase 02 Plan 02: Repository Layer Extraction Summary

**Extracted all ~260 raw SQL calls from app.py into 6 typed repository modules (models/*.py), leaving app.py with zero direct DB access and only `from db import init_db`**

## Performance

- **Duration:** multi-session (continued from previous context)
- **Started:** 2026-03-25 (session start)
- **Completed:** 2026-03-25T20:53:17Z
- **Tasks:** 2/2
- **Files modified:** 8 (6 created, 2 modified)

## Accomplishments

- Created 6 repository modules covering every table: users, servers, schedules, jobs, settings, ansible (all 6 ansible_* tables)
- Replaced all ~260 `get_db()`/`conn.execute()` calls in app.py with repository function calls
- app.py now has `grep -c "get_db()" app.py` = 0, satisfying REF-02
- All 55 tests pass (1 xfailed as expected)

## Task Commits

1. **Task 1+2: Extract SQL into repository modules** - `05fba99` (feat)

**Plan metadata:** (pending final commit)

## Files Created/Modified

- `models/users.py` — user CRUD: get_by_id, get_by_username, create, update_full, delete, upsert_ad_user, update_last_login, check_username_exists
- `models/servers.py` — server CRUD + ansible link: get_all, create, update, bulk_create, delete, get_dashboard_stats, link/unlink_ansible_host, check_exists_by_ip_or_hostname
- `models/schedules.py` — schedule CRUD: get_all_enabled, get_by_id, create, toggle, delete, get_count, update_last_run
- `models/jobs.py` — backup job CRUD: create, update_status, set_started, append_log, get_all_filtered, get_stats, get_servers_list
- `models/settings.py` — settings: get_settings, save_setting, save_many, get_nfs_target
- `models/ansible.py` — all ansible tables: hosts, groups, playbooks, runs, roles, stats (get_dashboard_stats)
- `app.py` — replaced all direct DB calls with repo calls; `from db import init_db` only
- `tests/test_bug03_scheduler.py` — updated to patch `schedule_repo.get_all_enabled` instead of removed `app.get_db`

## Decisions Made

- Repository functions are plain functions (not classes) — consistent with existing models/servers.py and models/settings.py patterns
- `get_settings()` and `save_setting()` kept as wrappers in app.py because they are called by many non-route helpers throughout the file
- `models/ansible.py` covers all 6 ansible tables in one module since they are closely related and were already co-located in app.py
- `get_role_by_id()` returns `(role, files)` tuple so the caller gets both in a single query

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed test patching get_db that no longer exists in app.py**
- **Found during:** Task 1 (verification / test run)
- **Issue:** `test_bug03_scheduler.py` patched `app.get_db` but after refactoring that name was removed from app.py
- **Fix:** Updated 3 test cases to patch `app_module.schedule_repo.get_all_enabled` instead
- **Files modified:** tests/test_bug03_scheduler.py
- **Verification:** All tests pass: `55 passed, 1 xfailed`
- **Committed in:** 05fba99 (Task 1 commit)

**2. [Rule 1 - Bug] Fixed ansible_role_edit to unpack (role, files) tuple**
- **Found during:** Task 2 / smoke test run (`/ansible/roles/1` returned 500)
- **Issue:** `ansible_repo.get_role_by_id()` returns `(role, files)` tuple but route called it as `role = get_role_by_id(rid)` then tried `dict(role)` on the tuple
- **Fix:** Changed to `role, files = ansible_repo.get_role_by_id(rid)` and removed separate `get_role_files()` call
- **Files modified:** app.py
- **Verification:** Smoke test for `/ansible/roles/1` passes; all 55 tests pass
- **Committed in:** 05fba99 (same commit)

---

**Total deviations:** 2 auto-fixed (2 Rule 1 bugs)
**Impact on plan:** Both were direct consequences of the refactoring. No scope creep.

## Issues Encountered

None beyond the deviations documented above.

## Next Phase Readiness

- Repository layer complete — app.py has zero SQL, all DB access behind typed functions
- Ready for Phase 02 Plan 03: service layer extraction (business logic out of routes)
- Thin wrappers `get_settings()`/`save_setting()` in app.py should be cleaned up in Plan 03 when helper functions are moved to services

---
*Phase: 02-refactoring*
*Completed: 2026-03-25*
