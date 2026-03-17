---
phase: 01-bug-fixes
plan: "01"
subsystem: app.py threading and session management
tags: [bug-fix, threading, flask, session, pytest]
dependency_graph:
  requires: []
  provides: [pytest-infrastructure, BUG-01-fix, BUG-04-fix]
  affects: [app.py, tests/]
tech_stack:
  added: [pytest]
  patterns: [threading.Lock context manager, file-based secret key persistence]
key_files:
  created:
    - pytest.ini
    - tests/__init__.py
    - tests/conftest.py
    - tests/test_bug01_lock.py
    - tests/test_bug04_secret.py
  modified:
    - app.py
    - .gitignore
decisions:
  - "Hold _job_lock only for the dict operation itself (microseconds), not across DB queries or render_template calls — captures value into local variable first"
  - "secret.key stored in BASE_DIR with 0o600 permissions; empty file triggers regeneration"
  - "xfail used for test_unprotected_access_can_fail since race condition is probabilistic"
metrics:
  duration_minutes: 7
  completed_date: "2026-03-17"
  tasks_completed: 3
  tasks_total: 3
  files_created: 5
  files_modified: 2
---

# Phase 1 Plan 01: BUG-01 and BUG-04 Bug Fixes Summary

**One-liner:** Lock-protected _running_jobs reads at all 6 sites plus persistent secret.key file via _load_or_create_secret_key(), with pytest infrastructure for the entire phase.

## Objective

Fix the _running_jobs race condition (BUG-01) and the session-loss-on-restart secret key bug (BUG-04), and set up the pytest test infrastructure for the entire phase.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Set up pytest infrastructure and write tests for BUG-01 and BUG-04 | 29d3b39 | pytest.ini, tests/__init__.py, tests/conftest.py, tests/test_bug01_lock.py, tests/test_bug04_secret.py |
| 2 | Fix BUG-04 — Persistent secret key via file | 8dc6b55 | app.py, .gitignore |
| 3 | Fix BUG-01 — Wrap all _running_jobs reads with _job_lock | 9317ccf | app.py |

## Results

### BUG-01: _running_jobs race condition

All 6 unprotected read sites are now wrapped with `with _job_lock:`:

| Site | Location | Operation | Fix |
|------|----------|-----------|-----|
| 1 | dashboard() stats dict | `len(_running_jobs)` | Captured into `_running_count` before stats dict |
| 2 | server_detail() | `set(_running_jobs.keys())` | Wrapped with lock |
| 3 | jobs_list() render_template | `set(_running_jobs.keys())` | Extracted to local var before render |
| 4 | job_detail() render_template | `jid in _running_jobs` | Extracted to `_is_running` before render |
| 5 | job_log_api() jsonify | `jid in _running_jobs` | Extracted to `_is_running` before jsonify |
| 6 | api_status() for loop | `list(_running_jobs.keys())` | Extracted to `_job_ids` before for loop |

`grep -c "with _job_lock:" app.py` = 8 (2 existing in start_job_thread + 6 new).

### BUG-04: session-loss-on-restart

Added `SECRET_KEY_FILE` constant and `_load_or_create_secret_key()` function before `app = Flask(__name__)`. The function reads from `BASE_DIR/secret.key` on startup, generating a new 64-char hex key and saving it with 0o600 permissions if the file is missing or empty.

### Test Infrastructure

10 test items collected across 2 test files:
- `tests/test_bug01_lock.py`: 4 passed, 1 xfailed (probabilistic race test)
- `tests/test_bug04_secret.py`: 5 passed

## Verification Results

```
python3 -m pytest tests/test_bug01_lock.py tests/test_bug04_secret.py -v
9 passed, 1 xfailed in ~60s

grep -c "with _job_lock:" app.py  →  8
grep "app.secret_key = _load_or_create_secret_key()" app.py  →  found
python3 -c "import app"  →  no errors
```

## Decisions Made

1. **Lock scope**: Hold `_job_lock` only for the dict operation itself (microseconds), not across DB queries or `render_template` calls. This minimizes lock contention in route handlers.
2. **Secret key storage**: File in `BASE_DIR` with 0o600 permissions. Empty file triggers regeneration (not treated as valid). No DB storage, no env var — simpler for this deployment model.
3. **xfail test**: `test_unprotected_access_can_fail` is marked `xfail(strict=False)` because race conditions are probabilistic and may not trigger in every CI run.

## Deviations from Plan

**1. [Rule 3 - Blocking] Flask not installed**
- **Found during:** Task 1 (test collection)
- **Issue:** `ModuleNotFoundError: No module named 'flask'` when collecting test_bug04_secret.py (which imports app)
- **Fix:** Installed flask, paramiko, apscheduler, werkzeug via pip3 --break-system-packages
- **Files modified:** system packages only
- **Commit:** included in task 1

## Self-Check

- [x] pytest.ini exists and contains `testpaths = tests`
- [x] tests/conftest.py contains `def tmp_base_dir` and `def mock_running_jobs`
- [x] tests/test_bug01_lock.py contains `def test_concurrent_access` and `def test_lock_protects_len_read`
- [x] tests/test_bug04_secret.py contains `def test_creates_key_file`, `def test_reads_existing_key`, `def test_key_stable`
- [x] 10 test items collected (>= 9 required)
- [x] app.py contains `SECRET_KEY_FILE`, `_load_or_create_secret_key`, `app.secret_key = _load_or_create_secret_key()`
- [x] Old `app.secret_key = secrets.token_hex(32)` removed
- [x] `grep -c "with _job_lock:" app.py` = 8
- [x] `python3 -c "import app"` passes
