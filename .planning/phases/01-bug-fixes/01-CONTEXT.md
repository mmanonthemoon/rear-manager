# Phase 1: Bug Fixes - Context

**Gathered:** 2026-03-17
**Status:** Ready for planning

<domain>
## Phase Boundary

Eliminate four specific bugs in the running application: race condition on `_running_jobs`, SSH sudo prompt reliability on RHEL/Ubuntu/Debian, APScheduler timezone drift, and session loss on restart. No new UI features, no new capabilities — fixes only.

</domain>

<decisions>
## Implementation Decisions

### BUG-01: _running_jobs race condition
- Wrap ALL reads and writes of `_running_jobs` with `_job_lock`
- 6 unprotected read sites: lines 1902, 2253, 2654, 2671, 2684, 2977 in app.py
- Lock is held only for dict lookups (microseconds) — direct locking, not snapshot copy

### BUG-02: SSH sudo prompt reliability
- Primary issue: sudo hangs on RHEL/Debian because the custom `SUDO_PASS_PROMPT:` prefix isn't being received/detected reliably
- Fix the prompt injection to work reliably across RHEL, Ubuntu, Debian
- Add a timeout (30s) on prompt detection — if no prompt is received, mark job as failed with a clear error: "Sudo prompt not received — check become password or sudoers config"
- No need to handle password-expired prompts or other edge cases (air-gapped environment, security not a priority)

### BUG-03: APScheduler timezone drift
- Timezone should be configurable via the existing app settings page, not hardcoded
- Default value: `Europe/Istanbul` (existing hardcoded value)
- UI: dropdown of common timezones (not free-text) — validate against known pytz timezone strings
- On save: restart/reschedule the APScheduler with the new timezone
- Reading the timezone from settings on `init_scheduler()` startup

### BUG-04: app.secret_key persistence
- Generate once, persist to a file (`secret.key`) in `BASE_DIR` (next to app.py)
- On startup: read from file if it exists; generate and save if it doesn't
- If file is deleted, a new key is generated and existing sessions are lost — this is acceptable
- No env var, no DB storage — file is simplest for this deployment model

### Security note
- User confirmed: security hardening is NOT a priority — this tool runs in an air-gapped, internet-free environment
- Do not add CSRF, rate limiting, SSH host key validation, or password encryption as part of this phase

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Bug locations
- `app.py` line 77 — `app.secret_key = secrets.token_hex(32)` (BUG-04)
- `app.py` lines 79–80 — `_running_jobs` dict and `_job_lock` definition
- `app.py` lines 1695–1700 — `start_job_thread()` with existing lock usage (BUG-01 reference)
- `app.py` lines 1049–1100 — `ssh_exec_stream()` sudo/su prompt detection (BUG-02)
- `app.py` line 1745 — `BackgroundScheduler(timezone='Europe/Istanbul', ...)` (BUG-03)
- `.planning/codebase/CONCERNS.md` — Known bugs table with exact locations

### Requirements
- `.planning/REQUIREMENTS.md` — BUG-01 through BUG-04 acceptance criteria

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `get_settings()` function — already reads from DB settings table; use this to read the timezone value
- `_job_lock` threading.Lock — already defined at module level, just needs consistent use
- Settings page (`/settings` route) — already exists with form fields; add timezone dropdown here

### Established Patterns
- Settings stored in DB `settings` table, read via `get_settings()` — follow this for timezone config
- Background job status updated via `_set_job_status()` — use this for the timeout/failure path in BUG-02
- `_append_log(job_id, text)` — use for writing the "Sudo prompt not received" error message

### Integration Points
- `init_scheduler()` — reads timezone; needs to pull from `get_settings()` instead of hardcoded string
- `app.py` top-level startup — secret key file read happens here, before `app = Flask(__name__)`
- All 6 unprotected `_running_jobs` reads are in route handlers and API endpoints in app.py

</code_context>

<specifics>
## Specific Ideas

- For the timezone dropdown, include at a minimum: Europe/Istanbul, UTC, Europe/London, Europe/Berlin, Europe/Moscow, Asia/Dubai, America/New_York, America/Los_Angeles, Asia/Tokyo — typical enterprise deployment zones
- The `secret.key` file approach matches how other tools (e.g. Gitea, Forgejo) handle this — familiar pattern for sysadmins

</specifics>

<deferred>
## Deferred Ideas

- CSRF protection — Phase 2+ or security milestone (out of scope: air-gapped environment)
- SSH host key verification policy — security milestone
- Brute-force protection on login — security milestone
- SSH password encryption in DB — security milestone

</deferred>

---

*Phase: 01-bug-fixes*
*Context gathered: 2026-03-17*
