---
phase: 1
slug: bug-fixes
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-17
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (not yet installed — Wave 0 gap) |
| **Config file** | None — Wave 0 creates `pytest.ini` or `pyproject.toml` |
| **Quick run command** | `python3 -m pytest tests/ -x -q` |
| **Full suite command** | `python3 -m pytest tests/ -v` |
| **Estimated runtime** | ~10 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python3 -m pytest tests/ -x -q`
- **After every plan wave:** Run `python3 -m pytest tests/ -v`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| Wave0-infra | 01 | 0 | BUG-01..04 | setup | `python3 -m pytest tests/ -x -q` | ❌ W0 | ⬜ pending |
| BUG-01-lock | 01 | 1 | BUG-01 | unit | `python3 -m pytest tests/test_bug01_lock.py -x` | ❌ W0 | ⬜ pending |
| BUG-01-concurrent | 01 | 1 | BUG-01 | unit (threading) | `python3 -m pytest tests/test_bug01_lock.py::test_concurrent_access -x` | ❌ W0 | ⬜ pending |
| BUG-02-prompt | 01 | 1 | BUG-02 | unit (mock channel) | `python3 -m pytest tests/test_bug02_ssh.py::test_prompt_detected -x` | ❌ W0 | ⬜ pending |
| BUG-02-timeout | 01 | 1 | BUG-02 | unit (mock channel) | `python3 -m pytest tests/test_bug02_ssh.py::test_prompt_timeout -x` | ❌ W0 | ⬜ pending |
| BUG-03-tz-db | 01 | 1 | BUG-03 | unit (mock DB) | `python3 -m pytest tests/test_bug03_scheduler.py::test_timezone_from_db -x` | ❌ W0 | ⬜ pending |
| BUG-03-restart | 01 | 1 | BUG-03 | unit | `python3 -m pytest tests/test_bug03_scheduler.py::test_scheduler_restart -x` | ❌ W0 | ⬜ pending |
| BUG-04-reads | 01 | 1 | BUG-04 | unit | `python3 -m pytest tests/test_bug04_secret.py::test_reads_existing_key -x` | ❌ W0 | ⬜ pending |
| BUG-04-creates | 01 | 1 | BUG-04 | unit | `python3 -m pytest tests/test_bug04_secret.py::test_creates_key_file -x` | ❌ W0 | ⬜ pending |
| BUG-04-stable | 01 | 1 | BUG-04 | unit | `python3 -m pytest tests/test_bug04_secret.py::test_key_stable -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/conftest.py` — shared fixtures (tmp SQLite DB, mock server dict)
- [ ] `tests/test_bug01_lock.py` — threading lock coverage for BUG-01
- [ ] `tests/test_bug02_ssh.py` — mock paramiko channel for BUG-02 prompt + timeout
- [ ] `tests/test_bug03_scheduler.py` — mock APScheduler + get_settings for BUG-03
- [ ] `tests/test_bug04_secret.py` — tmpdir-based secret key file tests for BUG-04
- [ ] Framework install: `pip3 install pytest` — not detected in environment

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| RHEL PAM sudo prompt timing | BUG-02 | Requires live RHEL host with sudo | SSH to RHEL target, run a backup job, verify no hang |
| Scheduler fires after restart | BUG-03 | Requires wall-clock wait | Restart app, wait for scheduled time, confirm job ran |
| Session survives restart | BUG-04 | Requires browser session | Log in, restart app, refresh browser, confirm no re-login |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
