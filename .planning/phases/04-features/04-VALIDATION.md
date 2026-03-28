---
phase: 4
slug: features
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-28
---

# Phase 4 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 8.x |
| **Config file** | pytest.ini |
| **Quick run command** | `pytest tests/test_pagination.py tests/test_audit.py tests/test_output_truncation.py -x` |
| **Full suite command** | `pytest tests/ -v` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_pagination.py tests/test_audit.py tests/test_output_truncation.py -x`
- **After every plan wave:** Run `pytest tests/ -v`
- **Before `/gsd:verify-work`:** Full suite must be green + manual verification (page navigation works, audit_log populated, output not corrupted)
- **Max feedback latency:** ~15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 4-01-01 | 01 | 0 | FEAT-01 | unit | `pytest tests/test_pagination.py::test_jobs_list_page_1 -xvs` | ❌ Wave 0 | ⬜ pending |
| 4-01-02 | 01 | 0 | FEAT-01 | integration | `pytest tests/test_pagination.py::test_pagination_nav_links -xvs` | ❌ Wave 0 | ⬜ pending |
| 4-02-01 | 02 | 0 | FEAT-02 | unit | `pytest tests/test_audit.py::test_audit_table_schema -xvs` | ❌ Wave 0 | ⬜ pending |
| 4-02-02 | 02 | 0 | FEAT-02 | integration | `pytest tests/test_audit.py::test_backup_trigger_logs_audit -xvs` | ❌ Wave 0 | ⬜ pending |
| 4-02-03 | 02 | 0 | FEAT-02 | integration | `pytest tests/test_audit.py::test_ansible_run_logs_audit -xvs` | ❌ Wave 0 | ⬜ pending |
| 4-03-01 | 03 | 0 | FEAT-03 | unit | `pytest tests/test_output_truncation.py::test_truncate_output -xvs` | ❌ Wave 0 | ⬜ pending |
| 4-03-02 | 03 | 0 | FEAT-03 | unit | `pytest tests/test_output_truncation.py::test_truncate_output_utf8_safety -xvs` | ❌ Wave 0 | ⬜ pending |
| 4-03-03 | 03 | 0 | FEAT-03 | integration | `pytest tests/test_output_truncation.py::test_job_output_capped -xvs` | ❌ Wave 0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_pagination.py` — Pagination offset/limit logic, URL building, page boundaries for FEAT-01
- [ ] `tests/test_audit.py` — Audit table schema, capture in job/ansible routes, query functions for FEAT-02
- [ ] `tests/test_output_truncation.py` — UTF-8 truncation, byte limit enforcement, marker appended for FEAT-03
- [ ] Fixture extension in `tests/conftest.py`: `app_with_audit_db()` — Initializes audit_log table
- [ ] Database migration in `db.py`: Add `CREATE TABLE audit_log` to `init_db()` function

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Pagination UI navigation renders correctly in browser | FEAT-01 | Visual rendering requires browser inspection | Load /jobs?page=1, verify prev/next links, check page count display |
| Audit log visible in application UI | FEAT-02 | UI rendering requires browser | Trigger a backup job, navigate to audit log view, verify username and timestamp |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
