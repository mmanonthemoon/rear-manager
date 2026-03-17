---
phase: 01-bug-fixes
plan: 02
subsystem: ssh-execution, scheduler
tags: [bug-fix, ssh, timeout, apscheduler, timezone, settings-ui]
dependency_graph:
  requires: [01-01]
  provides: [ssh-timeout-bug02, scheduler-timezone-bug03]
  affects: [app.py, templates/settings.html]
tech_stack:
  added: []
  patterns: [monotonic-timeout, cfg-dict-get-with-default, scheduler-restart]
key_files:
  created:
    - tests/test_bug02_ssh.py
    - tests/test_bug03_scheduler.py
  modified:
    - app.py
    - templates/settings.html
decisions:
  - "Remove buf = b'' after password send to preserve accumulated buffer across recv() calls"
  - "Use time.monotonic() for 30-second timeout to avoid wall-clock skew"
  - "Timeout check only when actual_method in (sudo, su) and not pass_sent — avoids false timeouts"
  - "SCHEDULER_TIMEZONES constant defined once at module level for both template and validation"
  - "scheduler tab in settings_page POST handles its own conn.close() and return — does not fall through to general keys loop"
metrics:
  duration: "8 minutes"
  completed_date: "2026-03-17"
  tasks_completed: 2
  files_changed: 4
---

# Phase 1 Plan 2: SSH Timeout and Configurable Scheduler Timezone Summary

SSH sudo prompt hang fixed with 30-second monotonic deadline and split-buffer preservation; APScheduler timezone made configurable via a new Zamanlayici settings tab backed by pytz validation.

## Tasks Completed

| # | Name | Commit | Files |
|---|------|--------|-------|
| 1 | Fix BUG-02: SSH sudo prompt timeout and buffer accumulation | b1c035c | app.py, tests/test_bug02_ssh.py |
| 2 | Fix BUG-03: Configurable APScheduler timezone via settings page | 199c4d7 | app.py, templates/settings.html, tests/test_bug03_scheduler.py |

## What Was Built

### BUG-02: SSH sudo prompt timeout (Task 1)

**Problem:** `ssh_exec_stream` had two bugs:
1. The `buf = b''` reset after sending the become password destroyed accumulated buffer data, causing split-prompt detection to fail (prompt arriving in multiple recv() chunks would not be recognized)
2. No timeout existed in the `else: time.sleep(0.05)` branch — a missing sudo prompt caused infinite hang

**Fix:**
- Added `prompt_deadline = time.monotonic() + 30` immediately after `chan.exec_command(wrapped_cmd)`
- Removed the two `buf = b''` statements following `pass_sent = True` in the sudo and su branches
- Added timeout check in the `else:` sleep branch: when `actual_method in ('sudo', 'su')` and `not pass_sent` and `time.monotonic() > prompt_deadline`, returns exit_code=1 with `[HATA] Sudo prompt not received` message

**Tests (5 passing):**
- `test_prompt_detected_single_chunk` — password sent when prompt arrives in one chunk
- `test_prompt_detected_split_chunks` — password sent when prompt split across two chunks
- `test_prompt_timeout` — non-zero exit and timeout message when prompt never arrives
- `test_no_timeout_when_no_sudo` — normal completion when become_method='none'
- `test_wrong_password_detected` — [HATA] Become and exit_code=1 on wrong password

### BUG-03: Configurable APScheduler timezone (Task 2)

**Problem:** `init_scheduler()` had `BackgroundScheduler(timezone='Europe/Istanbul', daemon=True)` hardcoded. Changing timezone required a code edit. No UI existed for it.

**Fix:**
- Added `SCHEDULER_TIMEZONES` constant (10 entries) near top of app.py
- Modified `init_scheduler()`: reads `scheduler_timezone` from `get_settings()`, defaults to `Europe/Istanbul`
- Added `_restart_scheduler_with_timezone(new_tz)` function: shuts down running scheduler, creates new one, re-adds all enabled schedule jobs
- Added `elif tab == 'scheduler':` block in `settings_page()` POST handler with pytz validation
- Added `scheduler_timezones=SCHEDULER_TIMEZONES` to `render_template` call
- Added Zamanlayici tab link in settings.html navigation (shown only when `has_scheduler`)
- Added scheduler tab pane with timezone `<select>` dropdown and hidden `tab=scheduler` input

**Tests (5 passing):**
- `test_timezone_from_db` — BackgroundScheduler called with DB timezone
- `test_timezone_default_when_not_in_db` — defaults to Europe/Istanbul
- `test_scheduler_restart_reloads_jobs` — old scheduler shut down, new created, jobs re-added
- `test_invalid_timezone_rejected` — pytz raises UnknownTimeZoneError for invalid strings
- `test_scheduler_timezones_list_valid` — all SCHEDULER_TIMEZONES entries valid in pytz

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Test server dict used wrong become password field**
- **Found during:** Task 1 RED phase
- **Issue:** `_make_server()` helper used `ssh_password='pass'` but `_get_become_password()` reads `ssh_password` when `become_same_pass=1`, returning 'pass' instead of test's expected 'secret'
- **Fix:** Set `become_same_pass='0'` and `become_password=become_pass` in test helper so become password is used directly
- **Files modified:** tests/test_bug02_ssh.py
- **Commit:** b1c035c (same task commit)

**2. [Rule 2 - Missing validation] settings_page scheduler tab leaked open DB connection**
- **Found during:** Task 2 implementation
- **Issue:** The `scheduler` tab branch called `save_setting()` (which opens its own connection) but the outer `conn = get_db()` was still open. The plan's code sketch didn't close `conn` before the early return.
- **Fix:** Added `conn.close()` before the flash/redirect in the scheduler branch to avoid leaked DB handle
- **Files modified:** app.py
- **Commit:** 199c4d7 (same task commit)

## Test Results

```
19 passed, 1 xfailed in 78.76s
```

Full suite green. The xfailed test is `test_unprotected_access_can_fail` — an intentionally probabilistic race-condition demonstration (marked xfail).

## Self-Check: PASSED

All files exist and all commits verified on disk.
