---
phase: 02-refactoring
plan: 03
subsystem: api
tags: [paramiko, ssh, flask, background-threads, services-layer]

# Dependency graph
requires:
  - phase: 02-refactoring-02
    provides: models/ layer (servers, jobs, settings, schedules, ansible repositories)
  - phase: 01-bug-fixes
    provides: working app.py baseline with all bug fixes applied
provides:
  - services/ssh.py with typed exceptions (SSHConnectionError, SSHAuthenticationError)
  - services/rear.py with ReaR config generation, offline Ubuntu install, install/configure background thread runners
  - services/jobs.py with job creation, thread management, mutable globals + accessor functions
  - app.py reduced by ~1100 lines of business logic
affects: [02-04, 02-05, 02-06]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - Service module pattern: services/*.py receive dicts from callers, never touch get_db() directly
    - Typed exception hierarchy: SSHConnectionError > SSHAuthenticationError for paramiko errors
    - App context wrapper: start_job_thread captures current_app._get_current_object() for background threads
    - Mutable globals with accessor functions: _running_jobs/_job_lock wrapped by get_running_job_ids/is_job_running/get_running_count

key-files:
  created:
    - services/ssh.py
    - services/rear.py
    - services/jobs.py
  modified:
    - app.py
    - tests/test_bug02_ssh.py

key-decisions:
  - "services/jobs.py owns _running_jobs/_job_lock globals; route handlers use accessor functions (get_running_job_ids, is_job_running, get_running_count)"
  - "start_job_thread pushes Flask app context via app.app_context() wrapper so background threads can use current_app.logger"
  - "services/rear.py imports job_service._append_log/_set_job_status to avoid duplicating log/status helpers"
  - "test_bug02_ssh.py updated to patch services.ssh module instead of app module"

patterns-established:
  - "Services never call get_db() — all DB operations delegated to models/"
  - "Typed paramiko exception hierarchy: paramiko.AuthenticationException -> SSHAuthenticationError, paramiko.SSHException/socket.timeout/OSError -> SSHConnectionError"
  - "Background thread functions (_run_install_rear, _run_configure_rear, _do_backup) called via start_job_thread which wraps them with app context"

requirements-completed: [REF-01, REF-03]

# Metrics
duration: 9min
completed: 2026-03-25
---

# Phase 02 Plan 03: Service Layer Extraction (SSH, ReaR, Jobs) Summary

**SSH, ReaR, and job management extracted from 4200-line app.py into typed service modules with paramiko exception hierarchy and Flask app-context-aware background thread management**

## Performance

- **Duration:** 9 min
- **Started:** 2026-03-25T21:36:08Z
- **Completed:** 2026-03-25T21:45:00Z
- **Tasks:** 2
- **Files modified:** 4 (3 created, 1 modified)

## Accomplishments

- Created `services/ssh.py` (418 lines) with 4 custom exception classes, 7 moved SSH functions, and typed paramiko/socket exception handling replacing bare `except Exception`
- Created `services/rear.py` (538 lines) with ReaR config generator, offline Ubuntu package installer, and `_run_install_rear`/`_run_configure_rear` background thread functions
- Created `services/jobs.py` (188 lines) with job creation, thread management, `_running_jobs`/_job_lock globals wrapped in accessor functions, and app-context-aware `start_job_thread`
- app.py reduced by ~1100 lines; all route handlers delegating to service modules; no bare SSH/ReaR/job function definitions remain in app.py

## Task Commits

1. **Task 1: Create services/ssh.py with typed exceptions** - `b266703` (feat)
2. **Task 2: Create services/rear.py and services/jobs.py** - `c6ca6bf` (feat)

## Files Created/Modified

- `services/ssh.py` - SSH client, exec stream, test connection, OS info, upload file + SSHConnectionError/SSHAuthenticationError hierarchy
- `services/rear.py` - get_offline_pkg_status, get_ubuntu_codename_via_ssh, ssh_install_offline_ubuntu, generate_rear_config, _run_install_rear, _run_configure_rear + ReaRInstallError/ReaRConfigError
- `services/jobs.py` - _append_log, _set_job_status, _do_backup, start_job_thread (app-context wrapper), create_job, _running_jobs/_job_lock globals + accessor functions
- `app.py` - Removed all moved function definitions and globals; added service imports; route handlers updated to call service modules
- `tests/test_bug02_ssh.py` - Updated patch targets from `app` module to `services.ssh` module

## Decisions Made

- `services/jobs.py` owns the `_running_jobs` and `_job_lock` globals; route handlers use `get_running_job_ids()`, `is_job_running()`, `get_running_count()` accessor functions to avoid exposing mutable state directly
- `start_job_thread` captures `current_app._get_current_object()` in the calling request context and pushes it inside the background thread via `app.app_context()`, ensuring `current_app.logger` works in threads
- `services/rear.py` delegates log/status operations to `job_service._append_log` and `job_service._set_job_status` rather than duplicating the helpers
- Tests updated to patch at the `services.ssh` module boundary since functions no longer live in `app`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Updated test patch targets after SSH function relocation**
- **Found during:** Task 1 (Create services/ssh.py)
- **Issue:** `tests/test_bug02_ssh.py` patched `app.build_ssh_client` and called `app.ssh_exec_stream` — both removed from `app.py` after extraction, causing `AttributeError` in test suite
- **Fix:** Updated `_call_ssh_exec` helper to patch `services.ssh.build_ssh_client` and call `ssh_module.ssh_exec_stream`; updated timeout test to patch `services.ssh.time.monotonic` and `services.ssh.time.sleep`
- **Files modified:** `tests/test_bug02_ssh.py`
- **Verification:** `python3 -m pytest tests/ -x -q` — 55 passed, 1 xfailed
- **Committed in:** b266703 (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 - bug)
**Impact on plan:** Necessary fix to maintain test suite validity after module relocation. No scope creep.

## Issues Encountered

None beyond the test patch target fix documented above.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- 3 service modules fully operational; app.py refactored to delegate to them
- Ready for Plan 04 (Ansible service extraction) and Plan 05 (routes decomposition)
- `_scheduler_run_backup` in app.py still references `job_service._do_backup` and `rear_service._run_*` via `start_job_thread` — working correctly
- No blockers

---
*Phase: 02-refactoring*
*Completed: 2026-03-25*
