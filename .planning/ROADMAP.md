# Roadmap: ReaR Manager

## Overview

This milestone stabilizes and improves the existing ReaR Manager codebase. Four phases flow in dependency order: first eliminate known bugs in the running system, then modularize the monolithic `app.py` to create clean seams, then write tests against those seams, then deliver missing user-facing features on top of the hardened foundation.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Bug Fixes** - Eliminate race conditions, SSH reliability gaps, and session instability (completed 2026-03-17)
- [ ] **Phase 2: Refactoring** - Decompose `app.py` into routes/services/models with structured error handling
- [x] **Phase 3: Testing** - Add unit and integration test coverage for SSH, ReaR, and Ansible workflows (completed 2026-03-28)
- [ ] **Phase 4: Features** - Ship pagination, audit log, and output size limits

## Phase Details

### Phase 1: Bug Fixes
**Goal**: The running application handles concurrency, OS variation, and restarts without data corruption or session loss
**Depends on**: Nothing (first phase)
**Requirements**: BUG-01, BUG-02, BUG-03, BUG-04
**Success Criteria** (what must be TRUE):
  1. Concurrent backup jobs complete without race conditions — no "dict changed size during iteration" errors appear in logs
  2. SSH sudo prompts are detected and handled correctly on RHEL, Ubuntu, and Debian targets
  3. Scheduled jobs fire at the correct time after an application restart, with no drift accumulating over days
  4. A user's session remains active across application restarts (no forced re-login on redeploy)
**Plans:** 2/2 plans complete

Plans:
- [x] 01-01-PLAN.md — Test infrastructure + BUG-01 race condition fix + BUG-04 persistent secret key
- [x] 01-02-PLAN.md — BUG-02 SSH sudo prompt timeout + BUG-03 configurable scheduler timezone

### Phase 2: Refactoring
**Goal**: The codebase is split into isolated layers (routes / services / models) with structured error handling, while all existing behavior is preserved
**Depends on**: Phase 1
**Requirements**: REF-01, REF-02, REF-03
**Success Criteria** (what must be TRUE):
  1. All Flask routes live in a routes layer; all SSH/ReaR/Ansible logic lives in a services layer; all DB access lives in a models/repository layer — no layer crosses into another's responsibility
  2. Every DB query uses the repository layer; no inline SQL strings appear in route handlers or service functions
  3. All bare `except Exception` blocks are replaced with typed exception handling that logs the specific error and returns a meaningful response
  4. Every existing URL endpoint returns the same response as before the refactor (no regressions)
**Plans:** 4/6 plans executed

Plans:
- [ ] 02-01-PLAN.md — Foundation scaffolding: config.py, db.py, package directories, smoke test infrastructure
- [ ] 02-02-PLAN.md — Models/repository layer: extract all ~260 SQL calls to models/*.py
- [ ] 02-03-PLAN.md — Services layer part 1: SSH, ReaR, and jobs services with typed SSH exceptions
- [ ] 02-04-PLAN.md — Services layer part 2: auth, scheduler, and Ansible services
- [ ] 02-05-PLAN.md — Routes layer: Flask Blueprints for all 62 routes + url_for() migration
- [ ] 02-06-PLAN.md — Exception handling: replace all ~49 bare except Exception blocks

### Phase 3: Testing
**Goal**: Critical application workflows are covered by automated tests that can be run without a live SSH target
**Depends on**: Phase 2
**Requirements**: TEST-01, TEST-02, TEST-03
**Success Criteria** (what must be TRUE):
  1. SSH connection and remote command execution can be tested with a mock transport — the test suite runs without a real server
  2. ReaR install and configuration flows have integration tests that verify the correct SSH commands are issued in the correct order
  3. Ansible host registration, inventory generation, and playbook execution flows have test coverage that catches regressions
  4. Running the test suite from the project root produces a pass/fail result with no manual setup required
**Plans:** 3/3 plans complete

Plans:
- [ ] 03-01-PLAN.md — Shared test fixtures + SSH service unit tests (TEST-01)
- [ ] 03-02-PLAN.md — ReaR config generation + install/configure integration tests (TEST-02)
- [ ] 03-03-PLAN.md — Ansible inventory, host CRUD, and playbook execution tests (TEST-03)

### Phase 4: Features
**Goal**: Users can navigate large data sets without performance degradation, every action is auditable, and runaway log output cannot fill the database
**Depends on**: Phase 3
**Requirements**: FEAT-01, FEAT-02, FEAT-03
**Success Criteria** (what must be TRUE):
  1. The jobs list, servers list, and ansible runs list each display 25 records per page with working next/previous navigation
  2. After running a backup or Ansible playbook, the audit log shows which user triggered the action and when
  3. Backup job output and Ansible run output stored in the database are capped at 1 MB — older content is truncated before saving when the limit is exceeded
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3 -> 4

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Bug Fixes | 2/2 | Complete   | 2026-03-17 |
| 2. Refactoring | 4/6 | In Progress|  |
| 3. Testing | 3/3 | Complete   | 2026-03-28 |
| 4. Features | 0/TBD | Not started | - |
