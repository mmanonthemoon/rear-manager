# Phase 4: Features - Research

**Researched:** 2026-03-28
**Domain:** Pagination, Audit Logging, Output Size Management
**Confidence:** HIGH

## Summary

Phase 4 implements three features essential for production readiness: pagination to handle growing datasets, audit logging to track user actions, and output size limits to prevent database bloat. The project already has a modularized codebase with a repository layer (models/), service layer (services/), and route handlers (routes/), plus pytest test infrastructure. The features require:

1. **Pagination (FEAT-01):** Adding OFFSET/LIMIT to existing repository functions and rendering pagination UI in templates
2. **Audit Log (FEAT-02):** Capturing `session['username']` (already available in routes) at trigger time, storing in new `audit_log` table
3. **Output Truncation (FEAT-03):** Adding pre-insert validation in backup job and Ansible run creation paths with 1 MB cap

**Primary recommendation:** Implement as three focused subtasks — pagination infrastructure first (enables list navigation), audit table + route capture second (integration point exists), output truncation last (protects against regression).

## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| FEAT-01 | Jobs, servers, and ansible_runs lists display 25 records/page with next/previous navigation | Pagination layer functions, template navigation components |
| FEAT-02 | Audit log shows which user triggered backup/Ansible actions and when | New audit_log table schema, session['username'] capture at trigger points |
| FEAT-03 | Output stored in DB capped at 1 MB; older content truncated before save | Pre-insert validation in job creation + Ansible run creation paths |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Flask | 2.x | Web framework (already in use) | HTTP routing, templates, session management |
| Flask-SQLAlchemy | 3.x | ORM (optional) | For pagination helpers if moving to async, not strictly needed |
| SQLite3 | 3.x | Database (already in use) | Embedded, no external dependencies, suitable for single-admin use |
| pytest | 8.x | Testing framework (already in place) | Test validation, mocking, fixtures established |
| Jinja2 | 3.x | Templating (already in use) | Template filters, macros for pagination UI |

**Installation (no new packages needed):**
Existing stack already contains Flask, SQLite3, pytest, Jinja2. No additional dependencies required.

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| Flask-Pagination | N/A | Pagination helper | Optional if building custom pagination logic; project templates can use manual URL construction |

**Note:** Current codebase uses manual query construction with repository layer. No ORM in use. Pagination implemented via OFFSET/LIMIT SQL without external helpers.

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Manual OFFSET/LIMIT pagination | flask-sqlalchemy.Pagination class | Would require ORM refactor (out of scope); manual approach simpler for current codebase |
| In-process audit log capture | Message queue (Celery/Redis) | Single-admin use case; in-process write to audit_log table sufficient |
| Output truncation at DB level | Archive to external storage | Complexity; 1 MB cap with truncation meets requirement |

## Architecture Patterns

### Pagination Pattern: Repository Layer Extension

**What:** Add offset/limit parameters to existing repository functions (`get_all()`, `get_runs()`, etc.). Return both data and total count.

**When to use:** Every list endpoint that displays records (jobs, servers, ansible_runs).

**Example:**
```python
# models/jobs.py (existing get_all_filtered — modify to support pagination)
def get_all_filtered(status_filter=None, type_filter=None, server_filter=None, offset=0, limit=25):
    conn = get_db()
    query = '''
        SELECT j.*, s.label as server_label, s.hostname
        FROM backup_jobs j JOIN servers s ON s.id=j.server_id
        WHERE 1=1
    '''
    params = []
    if status_filter:
        query += ' AND j.status=?'
        params.append(status_filter)
    if type_filter:
        query += ' AND j.job_type=?'
        params.append(type_filter)
    if server_filter:
        query += ' AND j.server_id=?'
        params.append(server_filter)
    query += ' ORDER BY j.id DESC LIMIT ? OFFSET ?'
    params.extend([limit, offset])

    rows = conn.execute(query, params).fetchall()

    # Get total count (without LIMIT/OFFSET)
    count_query = '''
        SELECT COUNT(*) FROM backup_jobs j WHERE 1=1
    '''
    count_params = []
    if status_filter:
        count_query += ' AND j.status=?'
        count_params.append(status_filter)
    if type_filter:
        count_query += ' AND j.job_type=?'
        count_params.append(type_filter)
    if server_filter:
        count_query += ' AND j.server_id=?'
        count_params.append(server_filter)

    total = conn.execute(count_query, count_params).fetchone()[0]
    conn.close()
    return rows, total
```

**Route handler pattern:**
```python
# routes/jobs.py
@jobs_bp.route('/jobs')
@login_required
def jobs_list():
    page = request.args.get('page', 1, type=int)
    if page < 1:
        page = 1
    offset = (page - 1) * 25
    limit = 25

    status_filter = request.args.get('status', '')
    type_filter = request.args.get('type', '')
    server_filter = request.args.get('server', '')

    jobs, total = job_repo.get_all_filtered(status_filter, type_filter, server_filter, offset, limit)

    total_pages = (total + limit - 1) // limit  # Ceil division

    return render_template('jobs.html',
                         jobs=jobs,
                         servers=job_repo.get_servers_list(),
                         current_page=page,
                         total_pages=total_pages,
                         total=total,
                         status_filter=status_filter,
                         type_filter=type_filter,
                         server_filter=server_filter,
                         running_job_ids=set(job_service.get_running_job_ids()))
```

**Template pagination component:**
```html
<!-- In templates/jobs.html -->
<div class="pagination">
    {% if current_page > 1 %}
        <a href="{{ url_for('jobs.jobs_list', page=1, status=status_filter, type=type_filter, server=server_filter) }}" class="btn btn-sm">« Başa</a>
        <a href="{{ url_for('jobs.jobs_list', page=current_page-1, status=status_filter, type=type_filter, server=server_filter) }}" class="btn btn-sm">‹ Önceki</a>
    {% endif %}

    <span class="text-muted">Sayfa {{ current_page }} / {{ total_pages }} ({{ total }} kayıt)</span>

    {% if current_page < total_pages %}
        <a href="{{ url_for('jobs.jobs_list', page=current_page+1, status=status_filter, type=type_filter, server=server_filter) }}" class="btn btn-sm">Sonraki ›</a>
        <a href="{{ url_for('jobs.jobs_list', page=total_pages, status=status_filter, type=type_filter, server=server_filter) }}" class="btn btn-sm">Sona »</a>
    {% endif %}
</div>
```

### Audit Log Pattern: Session Capture + Table Storage

**What:** New `audit_log` table captures user, action, and timestamp. Populated at job/playbook trigger points.

**When to use:** Whenever a backup job or Ansible playbook is triggered (routes/jobs.py route handlers, routes/ansible.py route handlers).

**Database schema:**
```sql
CREATE TABLE IF NOT EXISTS audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER,
    username     TEXT NOT NULL,
    action       TEXT NOT NULL,  -- 'backup_job_started', 'ansible_run_started'
    resource_id  INTEGER,        -- job_id or run_id
    resource_type TEXT,          -- 'backup_job', 'ansible_run'
    details      TEXT DEFAULT '', -- JSON with extra context if needed
    created_at   TEXT DEFAULT (datetime('now','localtime'))
);
```

**Capture pattern (in route handler):**
```python
# routes/jobs.py — in a route that triggers a backup
def some_backup_trigger_route():
    # ... existing job creation code ...
    job_id = job_service.create_job(...)

    # Capture audit event
    username = session.get('username', 'anonymous')
    from models import audit as audit_repo
    audit_repo.log_action(
        username=username,
        action='backup_job_started',
        resource_id=job_id,
        resource_type='backup_job',
        details=f'Server: {server_id}'
    )

    job_service.start_job_thread(...)
    # ... rest of handler ...
```

**Repository function:**
```python
# models/audit.py
def log_action(username, action, resource_id, resource_type, details=''):
    conn = get_db()
    conn.execute('''
        INSERT INTO audit_log(username, action, resource_id, resource_type, details)
        VALUES(?, ?, ?, ?, ?)
    ''', (username, action, resource_id, resource_type, details))
    conn.commit()
    conn.close()

def get_audit_log(limit=100, offset=0):
    conn = get_db()
    rows = conn.execute('''
        SELECT * FROM audit_log
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
    ''', (limit, offset)).fetchall()
    conn.close()
    return rows
```

### Output Truncation Pattern: Pre-Insert Validation

**What:** Before saving `backup_jobs.log_output` or `ansible_runs.output`, check size. If > 1 MB, truncate trailing content with warning marker.

**When to use:** Job completion handlers (rear.py functions), Ansible run log append functions (ansible.py).

**Example helper:**
```python
# utils.py (add to existing file)
def truncate_output(text, max_bytes=1_000_000):
    """Truncate text to max_bytes, appending marker if truncated."""
    if not text:
        return text

    text_bytes = text.encode('utf-8')
    if len(text_bytes) <= max_bytes:
        return text

    # Truncate at max_bytes, ensuring no split UTF-8 sequences
    truncated = text_bytes[:max_bytes].decode('utf-8', errors='ignore')
    return truncated + '\n\n[... çıkış 1 MB sınırında kesildi ...]'
```

**Usage at job completion:**
```python
# In services/rear.py or wherever job output is saved
def _run_backup(...):
    # ... run actual backup ...
    # ... collect output ...

    output = truncate_output(output)  # Ensure under 1 MB
    job_repo.update_job_output(job_id, output)
```

**Usage at Ansible run completion:**
```python
# In services/ansible.py — _append_run_log function
def _append_run_log(run_id, text):
    # Get existing log
    existing = _get_run_output(run_id)  # Custom helper
    combined = (existing or '') + text

    # Truncate before saving
    combined = truncate_output(combined)

    # Save
    conn = get_db()
    conn.execute('UPDATE ansible_runs SET output=? WHERE id=?', (combined, run_id))
    conn.commit()
    conn.close()
```

### Recommended Project Structure

```
models/
├── __init__.py
├── jobs.py             # Add pagination: get_all_filtered(offset, limit)
├── servers.py          # Add pagination: get_all(offset, limit)
├── ansible.py          # Add pagination: get_runs(offset, limit)
├── audit.py            # NEW: log_action(), get_audit_log()
└── ...

routes/
├── jobs.py             # Modify list handler to capture page param + session username
├── servers.py          # Modify list handler to capture page param
├── ansible.py          # Modify run list + playbook run handler to capture audit
└── ...

services/
├── rear.py             # Add output truncation before job_repo.update_job_output()
├── ansible.py          # Add output truncation in _append_run_log()
└── ...

templates/
├── jobs.html           # Add pagination nav component
├── servers.html        # Add pagination nav component
├── ansible_runs.html   # Add pagination nav component
└── ...

utils.py                # Add truncate_output() helper

db.py                   # Add CREATE TABLE FOR audit_log in init_db()
```

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Pagination logic | Custom offset calculation, URL building, page validation | Repository layer + template macros | Off-by-one errors in offset math are common; SQLite LIMIT/OFFSET is standard and battle-tested |
| Audit table schema | Storing only timestamp/username in separate fields | Structured schema with user_id lookup + action enum-like table | Makes audit queries filterable and queryable; enables future compliance reporting |
| Output truncation | Regex truncation at character boundaries | UTF-8 aware byte-level truncation with `decode('utf-8', errors='ignore')` | UTF-8 split sequences cause corruption; standard library handles correctly |
| Pagination URL construction | Manual string concat in templates | Jinja2 `url_for()` with all filters preserved | Error-prone; Jinja2's `url_for()` is designed for this |
| Session capture | Reading request object in service layer | Capture in route handler only; pass to service as parameter | Services should not depend on Flask context; routes are the boundary |

**Key insight:** The project's repository layer pattern already enables clean pagination — functions return data, services orchestrate, routes build URLs. This structure scales to new features without refactoring existing code.

## Common Pitfalls

### Pitfall 1: Pagination Offset Calculation Error
**What goes wrong:** `offset = (page - 1) * limit` fails when page is 0 or negative, or when limit is not validated.

**Why it happens:** Developers assume `request.args.get('page', 1)` is always positive, but user input is unvalidated.

**How to avoid:** Validate page >= 1 before calculating offset. Handle edge cases (page 0, negative page, page beyond total).

**Warning signs:** Users report "Page 0 shows last page's data" or "Negative page parameter causes crash."

**Prevention code:**
```python
page = request.args.get('page', 1, type=int)
if page < 1:
    page = 1
offset = (page - 1) * limit
# Validate offset doesn't exceed total (handled by DB; no rows returned if offset > count)
```

### Pitfall 2: Audit Log Captures Before Action Completes
**What goes wrong:** Audit table records an action (e.g., 'backup_job_started') but the action then fails before completion. Log shows action was triggered, but job table shows it failed/was never created.

**Why it happens:** Logging before transaction completion, or in wrong handler order.

**How to avoid:** Log audit entry AFTER resource creation is committed. For async jobs (background threads), log immediately after thread starts (since job record already exists in pending state).

**Warning signs:** Audit log shows action completed, but job table doesn't match (missing job_id, or different timestamp).

**Prevention code:**
```python
# CORRECT: Create job, commit, then log
job_id = job_service.create_job(...)  # Commits immediately
audit_repo.log_action(username=session.get('username'), action='backup_job_started',
                      resource_id=job_id, resource_type='backup_job')

# WRONG: Logging before creation
audit_repo.log_action(...)  # Premature
job_id = job_service.create_job(...)  # Fails → audit orphaned
```

### Pitfall 3: UTF-8 Truncation Splits Multi-Byte Character
**What goes wrong:** Output is truncated at byte boundary, leaving incomplete UTF-8 sequence. When re-read from DB or displayed, causes "mojibake" or display errors.

**Why it happens:** Simple byte-level slicing `text[:1000000]` doesn't respect multi-byte UTF-8 boundaries.

**How to avoid:** Use Python's `.decode('utf-8', errors='ignore')` after byte-level truncation, or truncate at known-safe boundaries.

**Warning signs:** Truncated output renders as garbled text or fails JSON serialization.

**Prevention code:**
```python
# WRONG: Character-level truncation loses bytes
text = text[:1000000]

# CORRECT: Byte-level with UTF-8 safety
text_bytes = text.encode('utf-8')
if len(text_bytes) > 1_000_000:
    truncated = text_bytes[:1_000_000].decode('utf-8', errors='ignore')
else:
    truncated = text
```

### Pitfall 4: Page Parameter Bypass in Filtered Pagination
**What goes wrong:** User filters jobs (status=success, type=backup), navigates to page 2, but URL loses filter parameters. Page 2 shows unfiltered data instead.

**Why it happens:** Pagination links built without preserving query string filters.

**How to avoid:** Pass all existing filter params to `url_for()` when building pagination links.

**Warning signs:** Filter + pagination combination breaks; filters reset when navigating pages.

**Prevention code:**
```html
<!-- WRONG: Pagination link loses filters -->
<a href="{{ url_for('jobs.jobs_list', page=2) }}">Next</a>

<!-- CORRECT: Preserve all filters -->
<a href="{{ url_for('jobs.jobs_list', page=2, status=status_filter, type=type_filter, server=server_filter) }}">Next</a>
```

### Pitfall 5: Session['username'] Not Set or Stale
**What goes wrong:** Audit log records `username='None'` or shows stale username from previous session.

**Why it happens:** Session not initialized properly, or username key not set during login.

**How to avoid:** Check login handler sets `session['username']`. For API endpoints without session, use alternative auth (API keys, etc.).

**Warning signs:** Audit log shows null/empty username, or username from different user than expected.

**Prevention code:**
```python
# In login handler (routes/auth.py)
username = ...  # validated
session['username'] = username
session.permanent = True  # Persist across restarts

# In job trigger route
username = session.get('username', 'anonymous')  # Fallback for safety
audit_repo.log_action(username=username, ...)
```

## Code Examples

Verified patterns from project codebase:

### Pagination: Repository Function Extension

```python
# Source: models/jobs.py — modify existing get_all_filtered()
def get_all_filtered(status_filter=None, type_filter=None, server_filter=None, offset=0, limit=25):
    """Fetch filtered jobs with pagination. Returns (rows, total_count)."""
    conn = get_db()

    # Build WHERE clause
    query_base = '''
        SELECT j.*, s.label as server_label, s.hostname
        FROM backup_jobs j JOIN servers s ON s.id=j.server_id
        WHERE 1=1
    '''
    params = []

    if status_filter:
        query_base += ' AND j.status=?'
        params.append(status_filter)
    if type_filter:
        query_base += ' AND j.job_type=?'
        params.append(type_filter)
    if server_filter:
        query_base += ' AND j.server_id=?'
        params.append(server_filter)

    # Fetch paginated results
    data_query = query_base + ' ORDER BY j.id DESC LIMIT ? OFFSET ?'
    data_params = params + [limit, offset]
    rows = conn.execute(data_query, data_params).fetchall()

    # Fetch total count (without LIMIT/OFFSET)
    count_query = 'SELECT COUNT(*) FROM backup_jobs j WHERE 1=1'
    count_params = []
    if status_filter:
        count_query += ' AND j.status=?'
        count_params.append(status_filter)
    if type_filter:
        count_query += ' AND j.job_type=?'
        count_params.append(type_filter)
    if server_filter:
        count_query += ' AND j.server_id=?'
        count_params.append(server_filter)

    total = conn.execute(count_query, count_params).fetchone()[0]
    conn.close()

    return rows, total
```

### Audit Logging: Route Handler Capture

```python
# Source: routes/jobs.py — in a route that triggers a job
from models import audit as audit_repo
from flask import session

@jobs_bp.route('/jobs/run/<int:sid>', methods=['POST'])
@login_required
def trigger_backup_job(sid):
    """Trigger a backup and log to audit table."""
    server = server_repo.get_by_id(sid)
    if not server:
        flash('Server not found', 'danger')
        return redirect(url_for('servers.servers_list'))

    # Create the job
    job_id = job_service.create_job(sid, 'backup', triggered_by=session.get('username', 'manual'))

    # Log audit event AFTER job is created
    username = session.get('username', 'anonymous')
    audit_repo.log_action(
        username=username,
        action='backup_job_started',
        resource_id=job_id,
        resource_type='backup_job',
        details=f'Server ID {sid} ({server["label"]})'
    )

    # Start background thread
    job_service.start_job_thread(rear_service._run_backup, job_id, dict(server))

    flash(f'Backup job #{job_id} started', 'info')
    return redirect(url_for('jobs.job_detail', jid=job_id))
```

### Output Truncation: Pre-Save Validation

```python
# Source: utils.py — add new helper
def truncate_output(text, max_bytes=1_000_000):
    """Truncate text to max_bytes, appending marker if truncated.

    Handles UTF-8 safely to avoid split multi-byte sequences.
    """
    if not text:
        return text

    text_bytes = text.encode('utf-8')
    if len(text_bytes) <= max_bytes:
        return text

    # Truncate at max_bytes, then decode safely (dropping incomplete sequences)
    truncated_bytes = text_bytes[:max_bytes]
    truncated = truncated_bytes.decode('utf-8', errors='ignore')

    return truncated + '\n\n[... çıkış 1 MB sınırında kesildi ...]'
```

```python
# Source: services/rear.py — in _run_backup or _run_mkbackup
from utils import truncate_output

def _run_backup(job_id, server_dict, schedule_id=None):
    """Run backup on server. Truncate output before saving."""
    # ... SSH connection, ReaR execution, output collection ...

    output = ...  # Collected output (may be large)

    # Truncate before saving
    output = truncate_output(output, max_bytes=1_000_000)

    # Save to job
    job_repo.update_job_output(job_id, output)
    # ... rest of handler ...
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Load all records into memory | Use LIMIT/OFFSET at SQL level | SQLite best practices | Scales to large datasets without memory bloat |
| Manual pagination string building | Jinja2 `url_for()` with query params | Flask conventions | Less error-prone, maintainable |
| Unstructured audit (grep logs) | Structured audit_log table | v1 audit requirement | Queryable, compliance-ready, auditable |
| No output size management | Pre-insert truncation with UTF-8 safety | v1 output management | Prevents DB growth, avoids corruption |

**Deprecated/outdated:**
- **Manual list views without pagination:** Old approach loaded 300 records (see models/jobs.py LIMIT 300 comment). With audit + output growth, pagination becomes essential.

## Open Questions

1. **Audit table user_id vs. username:**
   - What we know: Current code uses `session['username']` as string, no user_id foreign key
   - What's unclear: Should audit_log.username be TEXT (denormalized) or reference users.id?
   - Recommendation: Store TEXT username (denormalized) for audit immutability — if user is deleted, audit record preserves original username. Add user_id INDEX for queries if needed later.

2. **Pagination page size (25 vs. customizable):**
   - What we know: Requirement specifies 25 records/page
   - What's unclear: Should page size be user-configurable? Stored in settings table?
   - Recommendation: Hardcode 25 for Phase 4. If settings-driven pagination needed, defer to Phase 5+ (scope creep).

3. **Audit log retention policy:**
   - What we know: Requirement is to log user actions; no retention policy specified
   - What's unclear: Should old audit records be pruned? Monthly archive?
   - Recommendation: No pruning in Phase 4. If storage becomes an issue, implement in later phase.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.x |
| Config file | pytest.ini |
| Quick run command | `pytest tests/ -v -k "not (smoke\|slow)" -x` |
| Full suite command | `pytest tests/ -v` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| FEAT-01 | Jobs list shows page 1 with 25 records, page param in URL | unit | `pytest tests/test_pagination.py::test_jobs_list_page_1 -xvs` | ❌ Wave 0 |
| FEAT-01 | Pagination nav shows correct page count and next/previous links | integration | `pytest tests/test_pagination.py::test_pagination_nav_links -xvs` | ❌ Wave 0 |
| FEAT-02 | Audit log table created with username, action, resource_id columns | unit | `pytest tests/test_audit.py::test_audit_table_schema -xvs` | ❌ Wave 0 |
| FEAT-02 | Backup job trigger captures username in audit_log | integration | `pytest tests/test_audit.py::test_backup_trigger_logs_audit -xvs` | ❌ Wave 0 |
| FEAT-02 | Ansible playbook run trigger captures username in audit_log | integration | `pytest tests/test_audit.py::test_ansible_run_logs_audit -xvs` | ❌ Wave 0 |
| FEAT-03 | Output > 1 MB is truncated before job save | unit | `pytest tests/test_output_truncation.py::test_truncate_output -xvs` | ❌ Wave 0 |
| FEAT-03 | UTF-8 characters are not corrupted during truncation | unit | `pytest tests/test_output_truncation.py::test_truncate_output_utf8_safety -xvs` | ❌ Wave 0 |
| FEAT-03 | Job output stored in DB respects 1 MB limit | integration | `pytest tests/test_output_truncation.py::test_job_output_capped -xvs` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/test_pagination.py tests/test_audit.py tests/test_output_truncation.py -x`
- **Per wave merge:** Full pytest suite `pytest tests/ -v`
- **Phase gate:** Full suite green + manual verification (page navigation works, audit_log populated, output not corrupted) before `/gsd:verify-work`

### Wave 0 Gaps

Existing test infrastructure is solid (conftest.py with fixtures: `app_client`, `app_with_db`, `app_context`). Tests needed:

- [ ] `tests/test_pagination.py` — Pagination offset/limit logic, URL building, page boundaries
- [ ] `tests/test_audit.py` — Audit table schema, capture in job/ansible routes, query functions
- [ ] `tests/test_output_truncation.py` — UTF-8 truncation, byte limit enforcement, marker appended
- [ ] Fixture extension in conftest.py: `app_with_audit_db()` — Initializes audit_log table
- [ ] Database migration in `db.py`: Add `CREATE TABLE audit_log` to `init_db()` function

## Sources

### Primary (HIGH confidence)
- **Project codebase:** models/{jobs,servers,ansible}.py — existing pagination-ready structure with repository pattern
- **Flask documentation:** Session handling (`session['username']`), `url_for()` with query params
- **SQLite documentation:** LIMIT/OFFSET syntax, PRAGMA table_info, ALTER TABLE for schema evolution
- **Python stdlib:** `str.encode('utf-8')`, `bytes.decode('utf-8', errors='ignore')` for UTF-8 safety

### Secondary (MEDIUM confidence)
- **Existing test suite (Phase 3):** conftest.py fixtures demonstrate app_client, app_with_db patterns for integration tests
- **Project decisions (STATE.md):** Repository layer functions are plain functions; services own business logic; routes capture user context

### Tertiary (LOW confidence)
- None — all critical claims verified against codebase or official library docs

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — Flask, SQLite, pytest already in use; no new external dependencies
- Architecture: HIGH — Repository pagination pattern proven by existing code; audit table straightforward SQL
- Pitfalls: MEDIUM — Common pagination/audit issues documented in industry; UTF-8 truncation verified against Python docs
- Implementation: HIGH — Patterns align with existing codebase structure

**Research date:** 2026-03-28
**Valid until:** 2026-04-28 (stable domain; unlikely major changes in pagination/audit practices)
