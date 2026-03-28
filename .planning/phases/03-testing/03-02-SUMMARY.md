---
phase: 03-testing
plan: 02
subsystem: testing
tags: [pytest, unittest.mock, rear, ssh, integration-tests, unit-tests]

# Dependency graph
requires:
  - phase: 03-testing-01
    provides: conftest.py fixtures (server_dict, app_context, app_with_db)
  - phase: 02-refactoring
    provides: services/rear.py with isolated service boundaries for patching
provides:
  - Unit tests for generate_rear_config verifying OUTPUT/BACKUP/BACKUP_URL/excludes/migration mode
  - Integration tests for _run_install_rear verifying Ubuntu apt-get and RHEL dnf command sequences
  - Integration test for _run_configure_rear verifying mkdir->upload->rear dump order
affects: [03-testing-03]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - Patch ssh_service at module boundary (services.rear.ssh_service.*) to intercept all SSH calls
    - Seed job records via job_repo.create() before calling background thread functions that need DB
    - Capture issued_commands list in fake_exec_stream side_effect to assert call order
    - Patch get_ubuntu_codename_via_ssh directly to skip SSH client construction in Ubuntu path

key-files:
  created:
    - tests/test_rear_service.py
  modified: []

key-decisions:
  - "Patch services.rear.get_ubuntu_codename_via_ssh directly (not build_ssh_client) to bypass SSH entirely in Ubuntu install path"
  - "Use app_with_db fixture for integration tests since _run_install_rear/_run_configure_rear call job_service._set_job_status and job_repo.set_started requiring DB context"
  - "Config generation tests skip app_context fixture — generate_rear_config does not call current_app.logger"

patterns-established:
  - "Integration test pattern: seed job record -> patch all ssh_service functions -> call background runner -> assert issued_commands"

requirements-completed: [TEST-02]

# Metrics
duration: 4min
completed: 2026-03-28
---

# Phase 3 Plan 02: ReaR Service Tests Summary

**6 pytest tests (3 unit + 3 integration) verifying ReaR config generation correctness and SSH command sequences for install and configure flows, all running without a real SSH server**

## Performance

- **Duration:** ~4 min
- **Started:** 2026-03-28T20:51:13Z
- **Completed:** 2026-03-28T20:55:30Z
- **Tasks:** 2
- **Files modified:** 1

## Accomplishments

- 3 unit tests for `generate_rear_config` covering OUTPUT/BACKUP/BACKUP_URL lines, global+server exclude merging, and commented migration mode toggle
- 3 integration tests for `_run_install_rear` (Ubuntu apt-get path, RHEL dnf path) and `_run_configure_rear` (mkdir->upload->rear dump order)
- Full test suite remains green: 67 passed, 1 xfailed, 0 failed

## Task Commits

Each task was committed atomically:

1. **Task 1: Write ReaR config generation unit tests** - `0259a59` (test)
2. **Task 2: Write ReaR install and configure integration tests** - `4402a4f` (test)

## Files Created/Modified

- `tests/test_rear_service.py` - 6 tests: 3 config generation unit tests + 3 install/configure integration tests (189 lines)

## Decisions Made

- Patched `services.rear.get_ubuntu_codename_via_ssh` directly rather than `build_ssh_client` — the Ubuntu path calls `get_ubuntu_codename_via_ssh` which itself calls build_ssh_client; patching at the higher level is simpler and avoids mock chain complexity.
- Used `app_with_db` fixture for integration tests since background runner functions call `job_service._set_job_status` (which calls `job_repo.update_status`) and `job_repo.set_started`, both requiring a live DB connection with a real job record.
- Config generation unit tests do not use `app_context` — `generate_rear_config` only calls `settings_repo.get_nfs_target` (patched) and does not use `current_app.logger`.

## Deviations from Plan

None — plan executed exactly as written.

## Issues Encountered

None — all 6 tests passed on first run. The mock patch targets (module-level imports in services/rear.py) were correctly specified in the plan.

## User Setup Required

None — no external service configuration required.

## Next Phase Readiness

- TEST-02 satisfied: ReaR install and configuration flows have integration tests verifying correct SSH commands in correct order
- All 6 tests run without a real SSH server or remote host
- Ready for Phase 3 Plan 03 (remaining test coverage)

---
*Phase: 03-testing*
*Completed: 2026-03-28*
