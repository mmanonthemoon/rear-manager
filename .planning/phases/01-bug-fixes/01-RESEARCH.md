# Phase 1: Bug Fixes - Research

**Researched:** 2026-03-17
**Domain:** Python threading, SSH PTY/sudo prompt detection, APScheduler timezone, Flask session persistence
**Confidence:** HIGH (all findings based on direct code inspection + verified library knowledge)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**BUG-01: _running_jobs race condition**
- Wrap ALL reads and writes of `_running_jobs` with `_job_lock`
- 6 unprotected read sites: lines 1902, 2253, 2654, 2671, 2684, 2977 in app.py
- Lock is held only for dict lookups (microseconds) — direct locking, not snapshot copy

**BUG-02: SSH sudo prompt reliability**
- Primary issue: sudo hangs on RHEL/Debian because the custom `SUDO_PASS_PROMPT:` prefix isn't being received/detected reliably
- Fix the prompt injection to work reliably across RHEL, Ubuntu, Debian
- Add a timeout (30s) on prompt detection — if no prompt is received, mark job as failed with a clear error: "Sudo prompt not received — check become password or sudoers config"
- No need to handle password-expired prompts or other edge cases (air-gapped environment, security not a priority)

**BUG-03: APScheduler timezone drift**
- Timezone should be configurable via the existing app settings page, not hardcoded
- Default value: `Europe/Istanbul` (existing hardcoded value)
- UI: dropdown of common timezones (not free-text) — validate against known pytz timezone strings
- On save: restart/reschedule the APScheduler with the new timezone
- Reading the timezone from settings on `init_scheduler()` startup

**BUG-04: app.secret_key persistence**
- Generate once, persist to a file (`secret.key`) in `BASE_DIR` (next to app.py)
- On startup: read from file if it exists; generate and save if it doesn't
- If file is deleted, a new key is generated and existing sessions are lost — this is acceptable
- No env var, no DB storage — file is simplest for this deployment model

**Security note**
- User confirmed: security hardening is NOT a priority — this tool runs in an air-gapped, internet-free environment
- Do not add CSRF, rate limiting, SSH host key validation, or password encryption as part of this phase

### Claude's Discretion

None explicitly stated for this phase — all four bugs have locked implementation decisions.

### Deferred Ideas (OUT OF SCOPE)

- CSRF protection — Phase 2+ or security milestone
- SSH host key verification policy — security milestone
- Brute-force protection on login — security milestone
- SSH password encryption in DB — security milestone
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| BUG-01 | `_running_jobs` dict must be protected with `_job_lock` at ALL access points (race condition) | 6 unprotected read sites identified; `with _job_lock:` pattern already established in `start_job_thread()` |
| BUG-02 | SSH PTY / sudo prompt detection must work reliably across RHEL, Ubuntu, Debian | Root cause identified: `chan.recv_ready()` polling can miss the prompt window; timeout path missing entirely |
| BUG-03 | APScheduler must use explicit configurable timezone (no drift) | `init_scheduler()` at line 1745 has hardcoded `'Europe/Istanbul'`; pytz confirmed installed (v2024.1); `save_setting()`/`get_settings()` pattern ready |
| BUG-04 | `app.secret_key` must persist across restarts (no session loss on redeploy) | Line 77 generates a new key on every startup; `BASE_DIR` constant at line 52 is the right location for `secret.key` file |
</phase_requirements>

---

## Summary

This phase fixes four isolated bugs in a 4,242-line monolithic Flask application (`app.py`). No new features, no architectural changes. Each bug has a clearly bounded fix scope.

**BUG-01** is a threading race condition: `_running_jobs` (a plain `dict`) is accessed from multiple route handler threads without holding `_job_lock` at 6 specific sites. The fix is mechanical — wrap each unprotected access in `with _job_lock:`. The correct pattern already exists in `start_job_thread()` (lines 1695–1701).

**BUG-02** is a PTY/SSH timing problem. The sudo prompt `SUDO_PASS_PROMPT: ` is injected correctly via `sudo -p`, but the recv loop uses `chan.recv_ready()` polling which can miss a prompt that arrives between poll cycles. Additionally there is no timeout — if the prompt never arrives the job hangs forever. The fix combines a select-based or deadline-based recv strategy with a 30s timeout watchdog.

**BUG-03** is straightforward: `BackgroundScheduler(timezone='Europe/Istanbul')` is hardcoded. The fix reads the timezone from the settings DB (using the existing `get_settings()` + `save_setting()` pattern), adds a timezone dropdown to the settings page, and restarts the scheduler on settings save.

**BUG-04** is also straightforward: `app.secret_key = secrets.token_hex(32)` on line 77 regenerates a new key on every process start, invalidating all user sessions. The fix reads from `BASE_DIR/secret.key` (or creates it) before Flask initializes.

**Primary recommendation:** Fix all four bugs in a single plan wave (they are independent, non-overlapping changes in app.py).

---

## Standard Stack

### Core (already installed — no new dependencies needed)

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python `threading.Lock` | stdlib | Mutual exclusion for `_running_jobs` | Already used — `_job_lock` defined at line 80 |
| `paramiko` | 3.0+ | SSH PTY channel I/O | Already the SSH layer; `chan.recv_ready()`, `chan.recv()`, `chan.exit_status_ready()` |
| `apscheduler` | 3.10+ | Background cron scheduler | Already the scheduler; `BackgroundScheduler`, `CronTrigger` |
| `pytz` | 2024.1 | Timezone objects for APScheduler | Already installed on this system; APScheduler 3.x accepts pytz zone strings directly |
| Python `secrets` | stdlib | Secret key generation | Already imported at line 13 |

### No New Dependencies

All four bug fixes use libraries that are already imported and available. The only new import needed is `select` (stdlib) for the BUG-02 timeout — it is part of the Python standard library and requires no installation.

**Version verification:** pytz 2024.1 confirmed installed. APScheduler 3.10+ confirmed in requirements.txt.

---

## Architecture Patterns

### BUG-01: Threading Lock Pattern

The correct pattern is already established in `start_job_thread()`:

```python
# Source: app.py lines 1695–1701 (established pattern)
with _job_lock:
    _running_jobs[job_id] = t   # write — protected
...
with _job_lock:
    _running_jobs.pop(job_id, None)  # write — protected
```

The 6 unprotected read sites all need the same treatment:

```python
# Pattern for simple len() read (line 1902)
with _job_lock:
    running_count = len(_running_jobs)

# Pattern for .keys() snapshot (lines 2253, 2654, 2977)
with _job_lock:
    running_job_ids = set(_running_jobs.keys())

# Pattern for membership test (lines 2671, 2684)
with _job_lock:
    is_running = jid in _running_jobs
```

**Key insight:** The lock must be acquired and released atomically around the read operation itself. Acquiring outside and passing the dict reference in is wrong — the dict can mutate between acquisition and use.

### BUG-02: SSH PTY Recv with Timeout

The current loop polls `chan.recv_ready()` (spin-wait with `time.sleep(0.05)`). On RHEL/Debian, sudo may write the prompt in a burst that completes before the next `recv_ready()` check, OR the recv buffer may split the prompt across multiple `recv()` calls causing the prefix match to fail against a partial buffer.

Two issues to fix:

1. **Accumulation problem**: The `buf` is reset to `b''` after sending the password (`buf = b''` at lines 1101, 1110). If the prompt spans two `recv()` calls, the partial match fails. The fix is to NOT reset `buf` before detecting the prompt — instead only clear it after the password is sent.

2. **Timeout problem**: No deadline exists. Add a `prompt_deadline` timestamp and check it in the loop. If the deadline passes before `pass_sent`, call `_set_job_status(job_id, 'failed')` and log via `_append_log()`.

```python
# Pattern for timeout (new logic in the recv loop)
import time

PROMPT_TIMEOUT = 30  # seconds
prompt_deadline = time.monotonic() + PROMPT_TIMEOUT

while True:
    if chan.recv_ready():
        data = chan.recv(8192)
        ...
        # check for SUDO_PROMPT in accumulated buf_lower
        if SUDO_PROMPT in buf_lower and not pass_sent:
            chan.sendall((bpass + '\n').encode('utf-8'))
            pass_sent = True
            # Do NOT reset buf here — let output continue accumulating
            continue
    elif chan.exit_status_ready():
        ...
    else:
        # Timeout check for sudo prompt
        if actual_method == 'sudo' and not pass_sent:
            if time.monotonic() > prompt_deadline:
                chan.close(); client.close()
                return 1, "Sudo prompt not received — check become password or sudoers config"
        time.sleep(0.05)
```

**OS variation detail**: RHEL uses `/etc/sudoers` with `requiretty` by default in older versions (RHEL 7), which causes sudo to refuse PTY sessions. However, since this tool uses `chan.get_pty()` (line 1066), a PTY IS allocated — RHEL's `requiretty` is satisfied. The real RHEL/Debian difference is that the prompt string may be preceded by a PAM message or MOTD noise that fills the buffer before `SUDO_PASS_PROMPT: ` appears. The fix is to check `buf_lower` (the FULL accumulated buffer), not just the latest chunk.

### BUG-03: APScheduler Timezone via Settings

```python
# Pattern: read timezone from settings on init (replace line 1745)
def init_scheduler():
    global _scheduler
    if not HAS_SCHEDULER:
        return
    cfg = get_settings()
    tz = cfg.get('scheduler_timezone', 'Europe/Istanbul')
    _scheduler = BackgroundScheduler(timezone=tz, daemon=True)
    _scheduler.start()
    ...
```

For settings save + scheduler restart:

```python
# Pattern: on settings POST for 'scheduler' tab
def _restart_scheduler_with_timezone(new_tz):
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = BackgroundScheduler(timezone=new_tz, daemon=True)
    _scheduler.start()
    # Re-add all active schedules
    conn = get_db()
    schedules = conn.execute('SELECT * FROM schedules WHERE enabled=1').fetchall()
    conn.close()
    for sched in schedules:
        _add_scheduler_job(sched['id'], sched['cron_minute'], sched['cron_hour'],
                           sched['cron_dom'], sched['cron_month'], sched['cron_dow'])
```

APScheduler 3.x `BackgroundScheduler` accepts a plain timezone string (e.g., `'Europe/Istanbul'`) and resolves it via `pytz.timezone()` internally. No need to pass a `pytz.timezone` object explicitly.

### BUG-04: Persistent Secret Key

```python
# Source: pattern replaces app.py line 77
# Runs BEFORE app = Flask(__name__)
SECRET_KEY_FILE = os.path.join(BASE_DIR, 'secret.key')

def _load_or_create_secret_key():
    if os.path.exists(SECRET_KEY_FILE):
        with open(SECRET_KEY_FILE, 'r') as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    with open(SECRET_KEY_FILE, 'w') as f:
        f.write(key)
    return key

app = Flask(__name__)
app.secret_key = _load_or_create_secret_key()
```

**Critical ordering**: The `_load_or_create_secret_key()` call must happen after `BASE_DIR` is defined (line 52) and after `app = Flask(__name__)` is constructed (line 76). Currently line 77 sets `app.secret_key` immediately after Flask construction — replace that single line.

### Existing Integration Points (Reuse These)

| Function | Location | How BUG Uses It |
|----------|----------|-----------------|
| `_append_log(job_id, text)` | app.py:1388 | BUG-02: write timeout error message |
| `_set_job_status(job_id, status)` | app.py:1414 | BUG-02: mark job as failed on timeout |
| `get_settings()` | app.py:564 | BUG-03: read `scheduler_timezone` key |
| `save_setting(key, value)` | app.py:571 | BUG-03: persist timezone on settings save |
| `_add_scheduler_job(...)` | app.py:1760 | BUG-03: re-add jobs after scheduler restart |

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Timezone validation | Custom string parser | `pytz.timezone(tz_string)` raises `UnknownTimeZoneError` | pytz handles all edge cases; dropdown constrains input anyway |
| Thread-safe dict | Custom class | `threading.Lock` + `with` statement | Standard Python pattern; GIL provides some safety but not atomicity for compound operations |
| SSH select/timeout | Custom socket polling | `chan.settimeout(N)` or `select.select()` | Paramiko channels support settimeout directly |
| Secret key generation | UUID or hash of hostname | `secrets.token_hex(32)` | Already used; cryptographically random; correct tool |

---

## Common Pitfalls

### Pitfall 1: Lock Scope Too Wide (BUG-01)
**What goes wrong:** Holding `_job_lock` across a DB query or SSH call causes other threads to starve waiting for the lock.
**Why it happens:** Over-correcting — wrapping everything "to be safe."
**How to avoid:** Lock only the dict operation itself. Capture the value into a local variable, release the lock, then use the local variable.
**Warning signs:** Lock held for more than ~1ms; logging shows job threads queuing up.

### Pitfall 2: buf Reset Before Full Prompt Received (BUG-02)
**What goes wrong:** Current code resets `buf = b''` after `pass_sent = True`. If the prompt arrived split across two recv() calls, the first partial match fails, and the second recv() overwrites the buffer — the prompt is never seen.
**Why it happens:** The original author assumed the full prompt arrives in one `recv()` call.
**How to avoid:** Don't reset `buf` to `b''` during normal prompt detection. Only clear noise after sending the password, and consider not clearing at all — let the filter logic at the bottom strip prompt lines from output.

### Pitfall 3: Scheduler Restart Loses Active Jobs (BUG-03)
**What goes wrong:** `_scheduler.shutdown()` + new `BackgroundScheduler()` removes all scheduled jobs from memory. If `_add_scheduler_job` is not called for each active schedule, backups stop firing.
**Why it happens:** APScheduler's `BackgroundScheduler` stores jobs in memory (default jobstore). On shutdown, all jobs are lost.
**How to avoid:** After creating the new scheduler instance, re-query `schedules WHERE enabled=1` and call `_add_scheduler_job()` for each row.

### Pitfall 4: secret.key Permissions (BUG-04)
**What goes wrong:** The file is created world-readable, exposing the Flask session signing key.
**Why it happens:** Default `open()` uses umask, which may allow group/other read.
**How to avoid:** After writing, call `os.chmod(SECRET_KEY_FILE, 0o600)`. Given the air-gapped deployment model and the note that security is not a priority, this is still good hygiene.

### Pitfall 5: APScheduler Timezone String Typos (BUG-03)
**What goes wrong:** Saving `'Europe/Istanbull'` (typo) crashes the next `init_scheduler()` call with `UnknownTimeZoneError`, leaving the scheduler unstarted.
**Why it happens:** Free-text entry. The fix prevents this by using a dropdown, but the settings save handler should also validate: call `pytz.timezone(tz_string)` and catch `UnknownTimeZoneError` before persisting.

---

## Code Examples

### BUG-01: Protecting a Simple Read
```python
# Line 1902 — before
'running_jobs': len(_running_jobs),

# Line 1902 — after
with _job_lock:
    running_jobs_count = len(_running_jobs)
# ... then use running_jobs_count in the stats dict
```

### BUG-01: Protecting a keys() Snapshot
```python
# Line 2253 — before
running_job_ids = set(_running_jobs.keys())

# Line 2253 — after
with _job_lock:
    running_job_ids = set(_running_jobs.keys())
```

### BUG-01: Protecting a Membership Test
```python
# Line 2671 — before
is_running=jid in _running_jobs

# Line 2671 — after
with _job_lock:
    is_running = jid in _running_jobs
# ... pass is_running to render_template
```

### BUG-02: Timeout in recv Loop
```python
import time
# At top of ssh_exec_stream, after chan.exec_command(wrapped_cmd):
prompt_deadline = time.monotonic() + 30  # 30s timeout

# In the else branch (no data, no exit status):
else:
    if actual_method == 'sudo' and not pass_sent:
        if time.monotonic() > prompt_deadline:
            msg = "Sudo prompt not received — check become password or sudoers config"
            chan.close(); client.close()
            return 1, msg
    time.sleep(0.05)
```

### BUG-03: Timezone Dropdown (Python list for template)
```python
# Common enterprise timezones for the dropdown
SCHEDULER_TIMEZONES = [
    'UTC',
    'Europe/London',
    'Europe/Berlin',
    'Europe/Istanbul',
    'Europe/Moscow',
    'Asia/Dubai',
    'Asia/Tokyo',
    'America/New_York',
    'America/Los_Angeles',
]
# Pass to render_template('settings.html', ..., scheduler_timezones=SCHEDULER_TIMEZONES)
```

### BUG-04: Secret Key File Load
```python
SECRET_KEY_FILE = os.path.join(BASE_DIR, 'secret.key')

def _load_or_create_secret_key():
    if os.path.exists(SECRET_KEY_FILE):
        with open(SECRET_KEY_FILE, 'r') as f:
            key = f.read().strip()
        if key:
            return key
    key = secrets.token_hex(32)
    with open(SECRET_KEY_FILE, 'w') as f:
        f.write(key)
    os.chmod(SECRET_KEY_FILE, 0o600)
    return key
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `app.secret_key = secrets.token_hex(32)` at startup | Read from persistent file | This phase | Sessions survive restart |
| `BackgroundScheduler(timezone='Europe/Istanbul')` hardcoded | Read from DB settings | This phase | Timezone configurable without code change |
| `_running_jobs` read without lock | All 6 read sites wrapped with `_job_lock` | This phase | Eliminates "dict changed size during iteration" errors |
| No timeout on sudo prompt detection | 30s deadline, fail with clear message | This phase | Jobs no longer hang indefinitely |

---

## Open Questions

1. **Scheduler shutdown during active jobs (BUG-03)**
   - What we know: `_scheduler.shutdown(wait=False)` does not interrupt running jobs (they run in their own threads, not in APScheduler threads). APScheduler only triggers job start — it does not own the execution thread.
   - What's unclear: If a job fires between `shutdown()` and the new `_scheduler.start()`, it will be missed. This window is ~milliseconds and only matters if a schedule fires exactly during a settings save.
   - Recommendation: Accept the race (it is extremely unlikely and the consequence is one missed trigger). Do not add locking here.

2. **RHEL-specific sudo behavior with requiretty (BUG-02)**
   - What we know: RHEL 7 has `Defaults requiretty` in `/etc/sudoers`, which requires a real TTY. Paramiko `get_pty()` satisfies this requirement.
   - What's unclear: Whether some RHEL 8+ hardened configurations use `Defaults !requiretty` or a different PAM module that changes the prompt format.
   - Recommendation: The current custom prompt `SUDO_PASS_PROMPT: ` approach is correct. The fix (accumulate full buffer before matching, add timeout) covers the detected failure mode. No need to handle further RHEL edge cases per user decision.

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (not yet installed — Wave 0 gap) |
| Config file | None — needs `pytest.ini` or `pyproject.toml` |
| Quick run command | `python3 -m pytest tests/ -x -q` |
| Full suite command | `python3 -m pytest tests/ -v` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| BUG-01 | `_running_jobs` reads are lock-protected (no race) | unit | `python3 -m pytest tests/test_bug01_lock.py -x` | Wave 0 |
| BUG-01 | Concurrent threads do not produce "dict changed size" errors | unit (threading) | `python3 -m pytest tests/test_bug01_lock.py::test_concurrent_access -x` | Wave 0 |
| BUG-02 | Sudo prompt detection works when prompt arrives in buffer | unit (mock channel) | `python3 -m pytest tests/test_bug02_ssh.py::test_prompt_detected -x` | Wave 0 |
| BUG-02 | Timeout fires after 30s if no prompt received | unit (mock channel) | `python3 -m pytest tests/test_bug02_ssh.py::test_prompt_timeout -x` | Wave 0 |
| BUG-03 | `init_scheduler()` reads timezone from DB, not hardcoded | unit (mock DB) | `python3 -m pytest tests/test_bug03_scheduler.py::test_timezone_from_db -x` | Wave 0 |
| BUG-03 | Settings save with new timezone restarts scheduler correctly | unit | `python3 -m pytest tests/test_bug03_scheduler.py::test_scheduler_restart -x` | Wave 0 |
| BUG-04 | Secret key is read from file if file exists | unit | `python3 -m pytest tests/test_bug04_secret.py::test_reads_existing_key -x` | Wave 0 |
| BUG-04 | Secret key is generated and persisted if file does not exist | unit | `python3 -m pytest tests/test_bug04_secret.py::test_creates_key_file -x` | Wave 0 |
| BUG-04 | Secret key is stable across two consecutive calls | unit | `python3 -m pytest tests/test_bug04_secret.py::test_key_stable -x` | Wave 0 |

### Sampling Rate
- **Per task commit:** `python3 -m pytest tests/test_bug0{1,2,3,4}_*.py -x -q`
- **Per wave merge:** `python3 -m pytest tests/ -v`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_bug01_lock.py` — threading lock coverage for BUG-01
- [ ] `tests/test_bug02_ssh.py` — mock paramiko channel for BUG-02 prompt + timeout
- [ ] `tests/test_bug03_scheduler.py` — mock APScheduler + get_settings for BUG-03
- [ ] `tests/test_bug04_secret.py` — tmpdir-based secret key file tests for BUG-04
- [ ] `tests/conftest.py` — shared fixtures (tmp SQLite DB, mock server dict)
- [ ] Framework install: `pip3 install pytest` — not detected in environment

---

## Sources

### Primary (HIGH confidence)
- Direct code inspection: `app.py` lines 52, 77, 79–80, 990–1023, 1049–1172, 1388–1428, 1686–1701, 1740–1757, 2253, 2654, 2671, 2684, 2977 — exact bug locations confirmed
- Python stdlib `threading` documentation — `Lock` and `with` statement semantics (HIGH, stable since Python 2.7)
- pytz 2024.1 confirmed installed; `pytz.common_timezones` verified to include all required zones

### Secondary (MEDIUM confidence)
- APScheduler 3.x documentation — `BackgroundScheduler(timezone=str)` accepts pytz-compatible string directly; jobs lost on shutdown confirmed by in-memory default jobstore behavior
- Paramiko `Channel` documentation — `recv_ready()`, `recv()`, `exit_status_ready()`, `settimeout()` API confirmed stable in 3.x

### Tertiary (LOW confidence — but corroborated by code inspection)
- RHEL 7 `Defaults requiretty` behavior — confirmed this is satisfied by `chan.get_pty()` call at line 1066; no additional RHEL-specific handling needed per user decision

---

## Metadata

**Confidence breakdown:**
- BUG-01 fix: HIGH — mechanical, well-understood threading pattern; existing lock in codebase
- BUG-02 fix: HIGH — root cause confirmed by code inspection; timeout pattern is stdlib
- BUG-03 fix: HIGH — APScheduler API is well-documented; pytz confirmed available
- BUG-04 fix: HIGH — trivial file I/O; pattern confirmed by code inspection
- Test strategy: MEDIUM — pytest not yet installed; test file structure is standard

**Research date:** 2026-03-17
**Valid until:** 2026-04-17 (stable domain — APScheduler 3.x, paramiko 3.x, pytz API do not change frequently)
