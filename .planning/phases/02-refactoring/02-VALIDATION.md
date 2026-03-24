---
phase: 2
slug: refactoring
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-24
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (already installed and configured) |
| **Config file** | `pytest.ini` — `testpaths = tests` |
| **Quick run command** | `cd /home/ubuntu/workspace/rear-manager && python -m pytest tests/ -x -q` |
| **Full suite command** | `cd /home/ubuntu/workspace/rear-manager && python -m pytest tests/ -v` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/ -x -q`
- **After every plan wave:** Run `python -m pytest tests/ -v` + grep-based layer boundary checks
- **Before `/gsd:verify-work`:** Full suite must be green + grep checks clean
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 2-01-01 | 01 | 0 | REF-01 | smoke | `python -m pytest tests/test_smoke_routes.py -x -q` | ❌ W0 | ⬜ pending |
| 2-01-02 | 01 | 0 | REF-02 | static | `grep -rn "\.execute(" routes/ services/ \| wc -l` → 0 | ❌ W0 | ⬜ pending |
| 2-01-03 | 01 | 0 | REF-03 | static | `grep -rn "except Exception" routes/ services/ models/ \| wc -l` → 0 | ❌ W0 | ⬜ pending |
| 2-02-01 | 02 | 1 | REF-02 | static | `grep -rn "\.execute(" models/` → only in models/ | ❌ W0 | ⬜ pending |
| 2-03-01 | 03 | 2 | REF-01 | static | `grep -rn "conn.execute\|get_db()" services/` → 0 lines | ❌ W0 | ⬜ pending |
| 2-04-01 | 04 | 3 | REF-01 | static | `grep -rn "conn.execute\|get_db()" routes/` → 0 lines | ❌ W0 | ⬜ pending |
| 2-05-01 | 05 | 4 | REF-01 | smoke | `python -m pytest tests/test_smoke_routes.py -v` all 62 routes non-500 | ❌ W0 | ⬜ pending |
| 2-06-01 | 06 | 5 | REF-03 | static | `grep -rn "except Exception" routes/ services/ models/ \| wc -l` → 0 | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_smoke_routes.py` — smoke test that creates a Flask test client and hits all 62 routes, asserting HTTP status is not 500. Requires an in-memory SQLite DB fixture.
- [ ] `tests/conftest.py` — extend with `app_client` fixture that creates the Flask test client with an in-memory DB (needed for smoke tests after Blueprint refactor)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| All 62 routes return same response as before refactor | REF-01 | Behavioral equivalence requires comparison with pre-refactor baseline | Run smoke suite before and after each plan wave; diff route responses manually if a test fails |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
