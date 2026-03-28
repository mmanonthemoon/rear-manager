---
phase: 02-refactoring
plan: "05"
subsystem: routes-layer
tags: [flask, blueprints, refactoring, routes, url_for]
dependency_graph:
  requires: [02-04]
  provides: [routes-layer-complete, blueprint-modules, thin-app-factory]
  affects: [all-templates, app.py, routes/]
tech_stack:
  added: [utils.py]
  patterns: [Flask-Blueprint, app-factory, template-namespaced-url_for]
key_files:
  created:
    - routes/auth.py
    - routes/dashboard.py
    - routes/servers.py
    - routes/schedules.py
    - routes/jobs.py
    - routes/settings.py
    - routes/users.py
    - routes/api.py
    - routes/ansible.py
    - utils.py
  modified:
    - app.py
    - templates/base.html
    - templates/servers.html
    - templates/server_detail.html
    - templates/server_form.html
    - templates/server_bulk.html
    - templates/configure.html
    - templates/dashboard.html
    - templates/jobs.html
    - templates/job_detail.html
    - templates/settings.html
    - templates/users.html
    - templates/user_form.html
    - templates/ansible_dashboard.html
    - templates/ansible_hosts.html
    - templates/ansible_host_form.html
    - templates/ansible_host_bulk.html
    - templates/ansible_groups.html
    - templates/ansible_playbooks.html
    - templates/ansible_playbook_editor.html
    - templates/ansible_run_form.html
    - templates/ansible_runs.html
    - templates/ansible_run_detail.html
    - templates/ansible_roles.html
    - templates/ansible_role_editor.html
decisions:
  - "utils.py created to extract cron_describe, safe_dirname, calc_duration from app.py — keeps app.py under 120 lines and eliminates inline duplication in route modules"
  - "SCHEDULER_TIMEZONES re-exported from app.py (imported from config) to maintain test compatibility — pre-existing test accesses app.SCHEDULER_TIMEZONES directly"
  - "Ansible API routes (/api/ansible/*) placed in routes/ansible.py Blueprint — tightly coupled to Ansible functionality per plan specification"
metrics:
  duration: "~10 minutes"
  completed_date: "2026-03-28"
  tasks_completed: 2
  files_changed: 35
---

# Phase 02 Plan 05: Blueprint Routes Extraction Summary

All 62 Flask routes extracted from app.py into 9 Blueprint modules. app.py reduced to a 74-line thin factory. All url_for() calls in Python and Jinja2 templates updated to Blueprint-namespaced format. Full test suite passes.

## Tasks Completed

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Create 8 Blueprint modules (non-Ansible) + update templates | 038dfa6 | routes/auth.py, routes/dashboard.py, routes/servers.py, routes/schedules.py, routes/jobs.py, routes/settings.py, routes/users.py, routes/api.py, 27 template files |
| 2 | Create Ansible Blueprint, extract utils.py, finalize factory | 320779f | routes/ansible.py, utils.py, app.py |

## What Was Built

**9 Blueprint modules in routes/:**

| Blueprint | Name | Routes |
|-----------|------|--------|
| auth_bp | `auth` | /login, /logout (2) |
| dashboard_bp | `dashboard` | / (1) |
| servers_bp | `servers` | /servers/* (13) |
| schedules_bp | `schedules` | /servers/*/schedules/*, /schedules/* (4) |
| jobs_bp | `jobs` | /jobs/* (5) |
| settings_bp | `settings` | /settings/* (5) |
| users_bp | `users` | /users/* (5) |
| api_bp | `api` | /api/status, /api/schedules-status, /api/offline-packages (3) |
| ansible_bp | `ansible` | /ansible/*, /api/ansible/* (26) |

**app.py:** Reduced from 1865 lines to 74 lines. Contains: secret key loader, Blueprint registrations, DB init, scheduler init.

**utils.py:** New module with `cron_describe`, `safe_dirname`, `calc_duration` extracted from app.py.

**Templates:** All url_for() calls in 25 template files updated to Blueprint-namespaced format (e.g., `url_for('login')` → `url_for('auth.login')`).

## Verification Results

- `grep -c "@app.route" app.py` returns **0**
- `wc -l app.py` returns **74** (well under 120)
- `python3 -m pytest tests/test_smoke_routes.py` — **36 passed**
- `python3 -m pytest tests/` — **55 passed, 1 xfailed**
- No bare `url_for()` calls without Blueprint prefix in templates

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Critical Functionality] Re-exported SCHEDULER_TIMEZONES from app.py**
- **Found during:** Task 2 (full test suite run)
- **Issue:** Pre-existing test `test_bug03_scheduler.py::test_scheduler_timezones_list_valid` accessed `app.SCHEDULER_TIMEZONES` directly — this was available in the old app.py via module-level import from config
- **Fix:** Added `from config import SECRET_KEY_FILE, SCHEDULER_TIMEZONES` in app.py to maintain backward compatibility
- **Files modified:** app.py
- **Commit:** 320779f

**2. [Rule 1 - Refactor] Extracted utils.py**
- **Found during:** Task 2
- **Issue:** app.py was 145 lines (over the 120-line requirement) due to _cron_describe, _safe_dirname, calc_duration_filter functions
- **Fix:** Created utils.py with these functions; updated app.py to import from utils; routes/dashboard.py and routes/servers.py updated to use utils.safe_dirname
- **Files modified:** app.py, utils.py, routes/dashboard.py, routes/servers.py
- **Commit:** 320779f

## Self-Check: PASSED
