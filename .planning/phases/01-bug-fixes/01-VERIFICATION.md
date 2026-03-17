---
phase: 01-bug-fixes
verified: 2026-03-17T00:00:00Z
status: passed
score: 9/9 must-haves verified
re_verification: false
---

# Phase 1: Bug Fixes Verification Report

**Phase Goal:** Fix four critical bugs (BUG-01 through BUG-04) with tests
**Verified:** 2026-03-17
**Status:** passed
**Re-verification:** No — initial verification

---

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | All 6 unprotected `_running_jobs` read sites are wrapped with `_job_lock` | VERIFIED | `grep -c "with _job_lock:" app.py` = 8; all 6 new sites confirmed inside lock context at lines 1955, 2315, 2714, 2734, 2749, 3062 |
| 2 | `app.secret_key` is loaded from a persistent file (`secret.key`) rather than regenerated on every startup | VERIFIED | `app.secret_key = _load_or_create_secret_key()` at line 106; `app.secret_key = secrets.token_hex(32)` is gone |
| 3 | If `secret.key` does not exist, it is created with a new key and 0o600 permissions | VERIFIED | `os.chmod(SECRET_KEY_FILE, 0o600)` present at line 101; `test_file_permissions` and `test_creates_key_file` both pass |
| 4 | Pytest is installed and a test suite runs green | VERIFIED | `19 passed, 1 xfailed in 65.05s` — all 4 test files collected and run |
| 5 | SSH sudo prompt is detected reliably even when split across multiple `recv()` calls | VERIFIED | `buf = b''` reset removed; `test_prompt_detected_split_chunks` passes |
| 6 | If sudo prompt not received within 30 seconds, job fails with a clear error message | VERIFIED | `prompt_deadline = time.monotonic() + 30` at line 1098; `"Sudo prompt not received"` at line 1194; `test_prompt_timeout` passes |
| 7 | APScheduler reads timezone from DB settings on startup, not from a hardcoded string | VERIFIED | `cfg.get('scheduler_timezone', 'Europe/Istanbul')` at line 1784; no bare `BackgroundScheduler(timezone='Europe/Istanbul'...)` remains |
| 8 | Admin can select timezone from a dropdown on the settings page and scheduler restarts with new timezone | VERIFIED | `<select name="scheduler_timezone">` in settings.html line 528; `_restart_scheduler_with_timezone(tz)` called in POST handler at line 2809 |
| 9 | After scheduler restart, all enabled schedules are re-added | VERIFIED | `_restart_scheduler_with_timezone` queries `schedules WHERE enabled=1` and calls `_add_scheduler_job` for each; `test_scheduler_restart_reloads_jobs` passes |

**Score:** 9/9 truths verified

---

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `app.py` | Lock-protected `_running_jobs` reads and persistent secret key; SSH timeout; configurable scheduler timezone | VERIFIED | All 4 fixes present and substantive |
| `tests/conftest.py` | Shared pytest fixtures | VERIFIED | Contains `def tmp_base_dir` and `def mock_running_jobs` |
| `tests/test_bug01_lock.py` | Thread safety tests for `_running_jobs` | VERIFIED | 4 passed, 1 xfailed (probabilistic race — correct) |
| `tests/test_bug04_secret.py` | Secret key persistence tests | VERIFIED | 5 passed |
| `pytest.ini` | Pytest configuration | VERIFIED | `testpaths = tests`, `python_files = test_*.py`, `python_functions = test_*` |
| `tests/test_bug02_ssh.py` | SSH prompt detection and timeout tests | VERIFIED | 5 passed |
| `tests/test_bug03_scheduler.py` | Scheduler timezone configuration tests | VERIFIED | 5 passed |
| `templates/settings.html` | Timezone dropdown in settings UI | VERIFIED | Zamanlayici tab, `<select name="scheduler_timezone">`, `scheduler_timezones` loop |

---

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `app.py` | `secret.key` | `_load_or_create_secret_key` reads/writes file | WIRED | Function defined at line 92; called at line 106 |
| `app.py` | `threading.Lock` | `with _job_lock:` around all `_running_jobs` access | WIRED | 8 lock usages confirmed; all 6 new read sites verified in context |
| `app.py init_scheduler()` | `get_settings()` | Reads `scheduler_timezone` from DB | WIRED | `cfg = get_settings()` then `cfg.get('scheduler_timezone', 'Europe/Istanbul')` at lines 1783-1784 |
| `app.py settings_page()` | `app.py _restart_scheduler_with_timezone()` | POST handler calls restart on timezone change | WIRED | `_restart_scheduler_with_timezone(tz)` at line 2809 in `elif tab == 'scheduler':` branch |
| `app.py ssh_exec_stream()` | `time.monotonic()` | 30s deadline for sudo prompt detection | WIRED | `prompt_deadline = time.monotonic() + 30` at line 1098; timeout check at line 1193 |

---

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| BUG-01 | 01-01-PLAN.md | `_running_jobs` dict all access points protected with `_job_lock` (race condition) | SATISFIED | 6 new lock sites verified in context; `grep -c "with _job_lock:"` = 8 |
| BUG-02 | 01-02-PLAN.md | SSH PTY / sudo prompt detection reliable across OS variants; 30s timeout | SATISFIED | `prompt_deadline`, `buf = b''` removed, 5 tests pass |
| BUG-03 | 01-02-PLAN.md | APScheduler started with explicit timezone; configurable via settings UI | SATISFIED | `SCHEDULER_TIMEZONES`, `_restart_scheduler_with_timezone`, settings.html dropdown |
| BUG-04 | 01-01-PLAN.md | `app.secret_key` persistent across restarts so sessions are not lost | SATISFIED | `_load_or_create_secret_key()`, `secret.key` in `.gitignore`, 5 tests pass |

No orphaned requirements. All 4 requirement IDs declared in plan frontmatter match REQUIREMENTS.md Phase 1 entries, all marked Complete.

---

### Anti-Patterns Found

| File | Pattern | Severity | Impact |
|------|---------|----------|--------|
| `tests/test_bug02_ssh.py` lines 49, 52, 55 | `pass` statements | Info | Intentional no-ops inside MockChannel helper class (`close`, `get_pty`, `exec_command`) — correct test infrastructure |

No blockers. No warnings.

---

### Human Verification Required

#### 1. Timezone change live behavior

**Test:** Log in as admin, navigate to Settings > Zamanlayici tab, change timezone from `Europe/Istanbul` to `UTC`, click Kaydet. Then navigate to the schedules list.
**Expected:** Flash message "Zamanlayici ayarlari kaydedildi." appears; scheduler restarts with UTC; existing enabled schedules are visible and still active.
**Why human:** Requires running app instance with scheduler; can't verify flash message or UI state programmatically.

#### 2. Session persistence across restart

**Test:** Log in, kill the Flask process, restart it, navigate to any protected page.
**Expected:** Session is preserved — no redirect to login page.
**Why human:** Requires a live running app and manual restart sequence.

#### 3. SSH sudo prompt split-buffer behavior on real host

**Test:** Configure a server with `become_method=sudo` and a correct become password, trigger a backup job, observe job log.
**Expected:** Job completes successfully without hanging or failing with "Sudo prompt not received".
**Why human:** MockChannel covers the logic path but real paramiko channel buffering behavior can only be confirmed with a real SSH target.

---

### Summary

All four bugs are fully implemented and verified:

**BUG-01** — All 6 previously unprotected `_running_jobs` read sites are now wrapped with `with _job_lock:`. The lock is held only for the dict operation, capturing a local variable before use outside the lock. `grep -c "with _job_lock:" app.py` = 8 (2 original in `start_job_thread` + 6 new).

**BUG-02** — `ssh_exec_stream` now sets `prompt_deadline = time.monotonic() + 30` after `exec_command`. The two `buf = b''` resets that caused split-prompt failures are removed. The timeout check fires in the `else` sleep branch when `actual_method in ('sudo', 'su')` and the prompt has not yet been received.

**BUG-03** — `init_scheduler()` reads `scheduler_timezone` from `get_settings()` with `Europe/Istanbul` as default. `_restart_scheduler_with_timezone(new_tz)` shuts down the running scheduler, creates a new one, and re-adds all enabled schedules. The settings page has a new Zamanlayici tab with a `<select name="scheduler_timezone">` dropdown backed by `SCHEDULER_TIMEZONES`.

**BUG-04** — `_load_or_create_secret_key()` reads from `BASE_DIR/secret.key` on startup; if the file is missing or empty, it generates a new 64-char hex key and writes it with `0o600` permissions. `app.secret_key` is assigned from this function rather than from an ephemeral `secrets.token_hex(32)` call. `secret.key` is in `.gitignore`.

The full test suite runs: **19 passed, 1 xfailed** (the xfail is `test_unprotected_access_can_fail`, a probabilistic race condition demonstration correctly marked `xfail`).

---

_Verified: 2026-03-17_
_Verifier: Claude (gsd-verifier)_
