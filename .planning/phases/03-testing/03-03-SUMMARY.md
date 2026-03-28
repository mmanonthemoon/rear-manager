---
phase: 03-testing
plan: 03
subsystem: testing
tags: [pytest, ansible, inventory, subprocess, mock, winrm, ssh, become]

# Dependency graph
requires:
  - phase: 03-01
    provides: conftest.py app_with_db fixture, initialized DB schema for service-layer tests
  - phase: 02-refactoring
    provides: services/ansible.py and models/ansible.py as isolated testable modules
provides:
  - Unit tests for Ansible inventory generation (Linux SSH, Windows WinRM, grouped hosts, become vars)
  - CRUD tests for ansible host repository (create/read/delete cycle)
  - Integration tests for _do_ansible_run (success, failure, FileNotFoundError)
affects: [03-testing]

# Tech tracking
tech-stack:
  added: []
  patterns: [monkeypatch module-level constants for filesystem isolation, patch.object for internal function patching, patch target services.ansible.subprocess.Popen for subprocess mocking]

key-files:
  created: [tests/test_ansible_service.py]
  modified: []

key-decisions:
  - "monkeypatch.setattr(ansible_service, 'ANSIBLE_INV_DIR') patches module-level constants so _generate_inventory writes to tmp_path, not real ansible/inventories/"
  - "create_group returns None (no id), so group id is fetched via direct DB query after creation for set_host_groups call"
  - "patch.object(ansible_service, '_generate_inventory') intercepts inventory generation in run tests to avoid DB/filesystem setup"
  - "_do_ansible_run called directly (not via start_ansible_run) in tests — avoids thread complexity while app context is already provided by app_with_db fixture"

patterns-established:
  - "Ansible run tests: seed run via create_run, patch Popen + _generate_inventory, call _do_ansible_run directly, assert DB row status/exit_code"
  - "Inventory tests: patch three ansible dir constants to tmp_path, seed host/group via repo functions, call _generate_inventory, assert YAML string content"

requirements-completed: [TEST-03]

# Metrics
duration: 5min
completed: 2026-03-28
---

# Phase 3 Plan 03: Ansible Service Tests Summary

**8 pytest tests covering Ansible inventory generation (4 cases), host CRUD, and playbook run execution (success/failure/not-found) — all pass without ansible-playbook binary or real servers**

## Performance

- **Duration:** ~5 min
- **Started:** 2026-03-28T20:54:55Z
- **Completed:** 2026-03-28T21:00:00Z
- **Tasks:** 2
- **Files modified:** 1

## Accomplishments

- 4 inventory tests validate YAML output for Linux SSH, Windows WinRM, grouped hosts, and become variable cases using monkeypatched tmp_path dirs
- 1 host CRUD test exercises the full create/read/delete lifecycle via ansible_repo functions
- 3 run execution tests validate _do_ansible_run status updates via mocked subprocess.Popen — no real binary needed
- Full test suite (75 tests + 1 xfail) passes with zero regressions

## Task Commits

Each task was committed atomically:

1. **Task 1+2: Ansible inventory, CRUD, and run execution tests** - `53b4246` (feat)

**Plan metadata:** (docs commit below)

## Files Created/Modified

- `tests/test_ansible_service.py` - 8 tests covering inventory generation, host CRUD, and _do_ansible_run execution paths

## Decisions Made

- `create_group` doesn't return the new group id, so the grouped host test fetches the id via a direct DB query after creation — this is the only way to get the id without modifying the model
- `_do_ansible_run` is called directly in tests (not via `start_ansible_run`) to avoid thread spawning; `app_with_db` fixture provides the required app context
- `patch.object(ansible_service, '_generate_inventory')` used in run tests to bypass the full inventory generation path (DB + filesystem) when testing only the subprocess execution logic

## Deviations from Plan

None - plan executed exactly as written.

The only notable adaptation was that `create_group` doesn't return the group id (as the plan's interface section implied), but this was handled with a DB query rather than any code modification.

## Issues Encountered

None — all 8 tests passed on first run. The existing service implementation matched the test expectations exactly.

## Next Phase Readiness

- TEST-03 satisfied: Ansible host registration, inventory generation, and playbook execution have test coverage
- Phase 03-testing complete: all 3 plans executed (SSH service, ReaR service, Ansible service tests)
- Codebase has full regression protection across SSH, ReaR, and Ansible service layers

---
*Phase: 03-testing*
*Completed: 2026-03-28*
