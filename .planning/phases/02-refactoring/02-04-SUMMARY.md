---
phase: 02-refactoring
plan: "04"
subsystem: services-layer
tags: [refactoring, services, auth, scheduler, ansible, extraction]
dependency_graph:
  requires: ["02-03"]
  provides: ["services/auth.py", "services/scheduler.py", "services/ansible.py"]
  affects: ["app.py"]
tech_stack:
  added: []
  patterns:
    - "Service module pattern: business logic in services/, routes in app.py"
    - "Optional import guard (HAS_SCHEDULER, HAS_LDAP) preserved in service modules"
    - "Background thread app context via current_app._get_current_object()"
    - "Typed exception catch (JobLookupError) replacing bare except"
key_files:
  created:
    - services/auth.py
    - services/scheduler.py
    - services/ansible.py
  modified:
    - app.py
    - tests/test_bug03_scheduler.py
decisions:
  - "login_required and admin_required placed in services/auth.py (not routes/auth.py) to avoid circular imports when multiple route modules need them"
  - "start_ansible_run() wraps _do_ansible_run with Flask app context — APScheduler threads get context via _scheduler_run_backup wrapper"
  - "get_running_proc() accessor in services/ansible.py replaces direct _ansible_running dict access in cancel route"
  - "get_all_jobs() accessor in services/scheduler.py replaces direct _scheduler.get_jobs() call in api_schedules_status route"
metrics:
  duration: "10 minutes"
  completed_date: "2026-03-25"
  tasks_completed: 2
  files_modified: 5
---

# Phase 2 Plan 4: Auth, Scheduler, and Ansible Service Extraction Summary

**One-liner:** Extracted auth (LDAP+local), APScheduler management, and Ansible orchestration from app.py into dedicated service modules, completing the 6-module services layer.

## What Was Built

Three new service modules complete the services layer started in Plan 03:

- **services/auth.py** — `authenticate_local`, `authenticate_ad` (with ldap3/werkzeug optional import guards), `login_required`, `admin_required` decorators. Imported back into app.py so existing route decorators need no changes.

- **services/scheduler.py** — `init_scheduler`, `_restart_scheduler_with_timezone`, `_add_scheduler_job`, `_remove_scheduler_job`, `get_next_run`, `get_all_jobs`. `_scheduler` global owns the APScheduler instance. `_scheduler_run_backup` APScheduler callback wraps itself with app context since it runs in APScheduler's thread pool.

- **services/ansible.py** — All Ansible functions: `_ansible_check`, `_ansible_version`, `_generate_inventory`, `_dict_to_yaml`, `_sync_playbook_to_disk`, `_sync_role_to_disk`, `_append_run_log`, `_set_run_status`, `_do_ansible_run`, `start_ansible_run`, `_save_ansible_host`, `_save_playbook`. Mutable globals `_ansible_running` and `_ansible_run_lock` live here with accessor functions (`get_running_proc`, `get_active_run_ids`, `is_run_active`). `start_ansible_run()` wraps `_do_ansible_run` with Flask app context for background threads.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Tests patching wrong module after function move**
- **Found during:** Task 1 verification
- **Issue:** `tests/test_bug03_scheduler.py` patched `app_module.BackgroundScheduler`, `app_module.schedule_repo`, etc. After moving scheduler functions to `services/scheduler.py`, these patches no longer affected the actual code paths.
- **Fix:** Updated all scheduler test patches to target `services.scheduler` module attributes directly.
- **Files modified:** `tests/test_bug03_scheduler.py`
- **Commit:** 33eb889

**2. [Rule 2 - Missing functionality] _scheduler accessed directly in api_schedules_status route**
- **Found during:** Task 1 verification (test failure)
- **Issue:** `api_schedules_status` route referenced `_scheduler` global directly. After move, `_scheduler` is `None` in app.py scope — NameError at runtime.
- **Fix:** Added `get_all_jobs()` accessor to `services/scheduler.py`; updated route to call `get_scheduler_jobs()` (imported alias).
- **Files modified:** `services/scheduler.py`, `app.py`
- **Commit:** 33eb889

**3. [Rule 2 - Missing functionality] ansible_playbook_run route created thread without app context**
- **Found during:** Task 2 implementation review
- **Issue:** Route directly called `threading.Thread(target=_do_ansible_run)` without pushing Flask app context, relying on the background thread to access DB models without an app context.
- **Fix:** Route now calls `start_ansible_run()` which provides the app context wrapper — consistent with how `start_job_thread` works in services/jobs.py.
- **Files modified:** `app.py`
- **Commit:** 1859158

## Plan Success Criteria Status

- [x] 3 additional service modules exist: auth.py, scheduler.py, ansible.py
- [x] Total of 6 service modules across Plans 3 and 4
- [x] No get_db() calls in any services/ file
- [x] Scheduler bare except replaced with JobLookupError catch
- [x] Background threads (scheduler callbacks, ansible runs) have app context
- [x] Mutable globals (_scheduler, _ansible_running, _ansible_run_lock) moved to their owning service
- [x] app.py contains only routes + app factory
- [x] All tests pass (55 passed, 1 xfailed)

## Self-Check: PASSED

- services/auth.py: FOUND
- services/scheduler.py: FOUND
- services/ansible.py: FOUND
- Commit 33eb889 (Task 1): FOUND
- Commit 1859158 (Task 2): FOUND
