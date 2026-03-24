# Phase 2: Refactoring - Research

**Researched:** 2026-03-24
**Domain:** Flask application layering, Python exception hierarchy, repository pattern, SQLite in Python
**Confidence:** HIGH (all findings based on direct code inspection of app.py + standard Python/Flask patterns)

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| REF-01 | `app.py` split into routes / services / models layers; no cross-layer access | Module boundary map + Flask Blueprint pattern below |
| REF-02 | All DB queries moved to repository layer; no inline SQL strings in routes or services | 177+ DB `execute()` calls catalogued; repository pattern documented below |
| REF-03 | ~49 bare `except Exception` blocks replaced with typed exception handling | All 49 sites located; exception taxonomy and replacement strategy documented below |

</phase_requirements>

---

## Summary

`app.py` is a 4,329-line Flask monolith containing every concern in one file: DB schema and migrations, authentication logic, SSH operations, ReaR orchestration, Ansible orchestration, scheduler management, and all HTTP route handlers. The current code has ~260 raw `conn.execute()` calls scattered across route handlers, service functions, and background threads, and 49 bare `except Exception` blocks that swallow all error details.

The refactor decomposes this into three clean layers: **routes** (thin HTTP handlers — validate input, call services, render responses), **services** (SSH, ReaR, Ansible, scheduler, auth business logic), and **models/repository** (all DB access behind typed query methods). No new behavior is introduced; every URL endpoint must return the same response as before.

The critical planning concern is scope management. A 4,329-line monolith cannot be safely refactored in a single plan — the risk of regression is too high and the changeset is unrevewable. The planner MUST split this phase into multiple sequential plans, each targeting one module boundary and verifiable independently. The recommended split is six plans: foundation scaffolding, then one plan per domain area.

**Primary recommendation:** Use Flask Blueprints for route registration, plain Python modules (not classes) for services, and a simple repository module per DB entity. Migrate domain-by-domain, never cross-cutting. Each plan ends with a smoke test that all routes return HTTP 200/30x (not 500).

---

## Codebase Inventory

This is the factual map of what exists in app.py and where it lives. Planners must treat line numbers as approximate — they shift as code is moved.

### Current File Structure (4,329 lines)

| Lines (approx) | Section | Contents |
|----------------|---------|----------|
| 1–90 | Imports + constants | All module-level constants, optional imports |
| 92–172 | App init + helpers | `_load_or_create_secret_key`, `_cron_describe`, `_safe_dirname`, template filters |
| 202–453 | Database | `get_db`, `init_db` (schema DDL + seed data), `_migrate_db` |
| 593–652 | Settings helpers | `get_settings`, `save_setting`, `_get_local_ip` |
| 656–664 | NFS helper | `get_nfs_target` |
| 667–773 | Auth logic | `_get_user_by_username`, `authenticate_local`, `authenticate_ad` |
| 775–809 | Decorators | `login_required`, `admin_required` |
| 815–880 | Offline pkg mgmt | `get_offline_pkg_status`, `get_ubuntu_codename_via_ssh` |
| 882–1054 | SSH install | `ssh_install_offline_ubuntu` |
| 1007–1053 | SSH become helpers | `_get_become_password`, `_wrap_become_cmd` |
| 1055–1074 | SSH client builder | `build_ssh_client` |
| 1078–1210 | SSH stream exec | `ssh_exec_stream` |
| 1213–1335 | SSH utilities | `ssh_test_connection`, `ssh_get_os_info`, `ssh_upload_file` |
| 1337–1420 | ReaR config gen | `generate_rear_config` |
| 1426–1466 | Job log helpers | `_append_log`, `_set_job_status` |
| 1469–1657 | ReaR job runners | `_run_install_rear`, `_run_configure_rear` |
| 1659–1753 | Backup job runner | `_do_backup`, `start_job_thread`, `create_job` |
| 1755–1860 | Scheduler | `_scheduler_run_backup`, `init_scheduler`, `_restart_scheduler_with_timezone`, `_add_scheduler_job`, `_remove_scheduler_job`, `get_next_run` |
| 1863–2780 | Flask routes: core | Auth, dashboard, servers, schedules, jobs, settings, users, API endpoints |
| 3057–3108 | Flask routes: API | `/api/status`, `/api/schedules-status`, `/api/offline-packages` |
| 3109–3428 | Ansible service | `_ansible_check`, `_ansible_version`, `_generate_inventory`, `_dict_to_yaml`, `_sync_playbook_to_disk`, `_sync_role_to_disk`, `_append_run_log`, `_set_run_status`, `_do_ansible_run` |
| 3431–4314 | Flask routes: Ansible | All `/ansible/*` routes + `/api/ansible/*` routes |
| 4315–4329 | Entry point | `if __name__ == '__main__':` block |

### Route Count by Domain

| Domain | Route count |
|--------|-------------|
| Auth (login/logout) | 2 |
| Dashboard | 1 |
| Servers | 12 |
| Schedules | 4 |
| Jobs | 5 |
| Settings + SSH key mgmt | 5 |
| Users | 5 |
| API (status/schedules/packages) | 3 |
| Ansible hosts/groups/playbooks/runs/roles | 22 |
| API Ansible | 3 |
| **Total** | **62** |

### DB Access Distribution

- ~260 raw `conn.execute()` calls across the file
- Pattern is always: `conn = get_db(); conn.execute(...); conn.close()`
- No connection pooling or context manager — each call opens and closes its own connection
- SQLite WAL mode is enabled on each `get_db()` call (line 205)
- DB tables: `users`, `servers`, `schedules`, `backup_jobs`, `settings`, `ansible_groups`, `ansible_hosts`, `ansible_host_groups`, `ansible_playbooks`, `ansible_runs`, `ansible_roles`, `ansible_role_files`

### Exception Audit

49 total `except Exception` sites. Breakdown by character:

| Category | Count | Example sites |
|----------|-------|---------------|
| Silent swallow (no log, no return value change) | ~18 | Lines 152, 195, 848, 960, 964, 1826, 1847 |
| Log + continue (logs but no typed exception) | ~16 | Lines 771, 931, 958, 1204, 1271, 1838 |
| Log + return error response | ~10 | Lines 2234, 2430, 2866, 2892, 3363, 3414 |
| Cleanup-only (in `finally`-equivalent blocks) | ~5 | Lines 3778, 3900, 4163, 4181, 4314 |

---

## Standard Stack

No new library dependencies are needed for this refactor. The existing stack is sufficient.

### Core (existing, no additions)
| Library | Version constraint | Purpose |
|---------|-------------------|---------|
| Flask | >=2.3.0 | HTTP framework — Blueprints are built-in |
| paramiko | >=3.0.0 | SSH client |
| apscheduler | >=3.10.0 | Background scheduler |
| werkzeug | >=2.3.0 | Password hashing |
| sqlite3 | stdlib | DB access |
| pytest | (from tests/) | Regression testing |

### No new dependencies required
The refactor is purely structural. Do not add SQLAlchemy, Flask-SQLAlchemy, marshmallow, or any ORM. The repository pattern will wrap raw sqlite3 calls — no ORM, no migration framework. The app runs air-gapped; adding new dependencies creates an offline packaging burden.

---

## Architecture Patterns

### Recommended Project Structure

```
rear-manager/
├── app.py                  # Entry point: creates Flask app, registers Blueprints, calls init_db/init_scheduler
├── config.py               # Constants (BASE_DIR, DB_PATH, BACKUP_ROOT, ANSIBLE_DIR, etc.)
├── db.py                   # get_db() only — connection factory
├── models/
│   ├── __init__.py
│   ├── users.py            # Repository: all SQL for users table
│   ├── servers.py          # Repository: all SQL for servers table
│   ├── schedules.py        # Repository: all SQL for schedules table
│   ├── jobs.py             # Repository: all SQL for backup_jobs table
│   ├── settings.py         # Repository: all SQL for settings table
│   └── ansible.py          # Repository: all SQL for ansible_* tables
├── services/
│   ├── __init__.py
│   ├── auth.py             # authenticate_local, authenticate_ad, _get_user_by_username
│   ├── ssh.py              # build_ssh_client, ssh_exec_stream, ssh_test_connection, ssh_get_os_info, ssh_upload_file, _wrap_become_cmd, _get_become_password
│   ├── rear.py             # generate_rear_config, _run_install_rear, _run_configure_rear, _do_backup, get_offline_pkg_status, get_ubuntu_codename_via_ssh, ssh_install_offline_ubuntu
│   ├── jobs.py             # create_job, start_job_thread, _append_log, _set_job_status
│   ├── scheduler.py        # init_scheduler, _restart_scheduler_with_timezone, _add_scheduler_job, _remove_scheduler_job, get_next_run, _scheduler_run_backup
│   └── ansible.py          # _ansible_check, _ansible_version, _generate_inventory, _do_ansible_run, _append_run_log, _set_run_status, _sync_playbook_to_disk, _sync_role_to_disk
├── routes/
│   ├── __init__.py
│   ├── auth.py             # Blueprint: /login, /logout
│   ├── dashboard.py        # Blueprint: /
│   ├── servers.py          # Blueprint: /servers/*
│   ├── schedules.py        # Blueprint: /schedules/*
│   ├── jobs.py             # Blueprint: /jobs/*
│   ├── settings.py         # Blueprint: /settings/*
│   ├── users.py            # Blueprint: /users/*
│   ├── api.py              # Blueprint: /api/status, /api/schedules-status, /api/offline-packages
│   └── ansible.py          # Blueprint: /ansible/*, /api/ansible/*
├── tests/
│   ├── conftest.py         # (existing, extend with app factory fixture)
│   ├── test_bug01_lock.py  # (existing)
│   ├── test_bug02_ssh.py   # (existing)
│   ├── test_bug03_scheduler.py  # (existing)
│   ├── test_bug04_secret.py     # (existing)
│   └── test_smoke_routes.py    # NEW: verify all 62 routes return non-500
└── ...
```

### Pattern 1: Flask Blueprint Registration

**What:** Each route module defines a `Blueprint` and registers it in `app.py`. The `app` object itself never lives inside a route module.

**When to use:** Every route module in routes/

**Example:**
```python
# routes/servers.py
from flask import Blueprint, render_template, redirect, url_for, flash, request, session
from services import ssh as ssh_service
from services import rear as rear_service
from services.jobs import create_job, start_job_thread
from models import servers as server_repo
from models import settings as settings_repo
from routes.auth import login_required

servers_bp = Blueprint('servers', __name__)

@servers_bp.route('/servers')
@login_required
def servers_list():
    servers = server_repo.get_all()
    return render_template('servers.html', servers=servers)
```

```python
# app.py (after refactor)
from flask import Flask
from db import init_db
from services.scheduler import init_scheduler
from routes.auth import auth_bp
from routes.servers import servers_bp
# ... etc

def create_app():
    app = Flask(__name__)
    app.secret_key = _load_or_create_secret_key()
    app.register_blueprint(auth_bp)
    app.register_blueprint(servers_bp)
    # ... register all blueprints
    return app

app = create_app()
```

### Pattern 2: Repository Functions (NOT classes)

**What:** Each model file exports plain functions that take a db connection or open their own. No ORM objects, no Active Record pattern, no class instances.

**When to use:** Every SQL call in the codebase.

**Rationale:** The existing code uses `get_db()` directly everywhere. The refactor wraps this in named functions without changing the underlying sqlite3 calls. Adding a class hierarchy here is unnecessary complexity for a single-admin tool.

**Example:**
```python
# models/servers.py
from db import get_db

def get_all():
    conn = get_db()
    rows = conn.execute('SELECT * FROM servers ORDER BY label').fetchall()
    conn.close()
    return rows

def get_by_id(sid):
    conn = get_db()
    row = conn.execute('SELECT * FROM servers WHERE id=?', (sid,)).fetchone()
    conn.close()
    return dict(row) if row else None

def create(label, hostname, ip_address, ssh_port, ssh_user, ssh_auth,
           ssh_password, become_method, become_user, become_password,
           become_same_pass, exclude_dirs, notes):
    conn = get_db()
    cur = conn.execute('''
        INSERT INTO servers(label, hostname, ip_address, ssh_port, ssh_user,
                            ssh_auth, ssh_password, become_method, become_user,
                            become_password, become_same_pass, exclude_dirs, notes)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
    ''', (label, hostname, ip_address, ssh_port, ssh_user, ssh_auth,
          ssh_password, become_method, become_user, become_password,
          become_same_pass, exclude_dirs, notes))
    new_id = cur.lastrowid
    conn.commit()
    conn.close()
    return new_id

def update(sid, **kwargs):
    # builds SET clause from kwargs
    ...

def delete(sid):
    conn = get_db()
    conn.execute('DELETE FROM servers WHERE id=?', (sid,))
    conn.commit()
    conn.close()
```

### Pattern 3: Typed Exception Handling

**What:** Replace `except Exception` with specific exception types. Where `Exception` is genuinely needed (catching all errors in a background thread), log with `traceback` and re-raise or return a typed error code.

**When to use:** Every exception handler in the codebase.

**Exception taxonomy for this codebase:**

```python
# services/ssh.py — typed exceptions to define
class SSHConnectionError(Exception):
    """Raised when paramiko.connect() fails."""

class SSHCommandError(Exception):
    """Raised when a remote command returns non-zero exit code."""

class SSHAuthenticationError(SSHConnectionError):
    """Raised when SSH authentication fails."""

class SSHTimeoutError(SSHConnectionError):
    """Raised when sudo/su prompt times out."""
```

```python
# services/rear.py — typed exceptions to define
class ReaRInstallError(Exception):
    """Raised when ReaR installation fails on the remote host."""

class ReaRConfigError(Exception):
    """Raised when ReaR configuration cannot be applied."""
```

```python
# services/ansible.py — typed exceptions to define
class AnsibleNotInstalledError(Exception):
    """Raised when ansible binary is not found."""

class AnsibleRunError(Exception):
    """Raised when ansible-playbook exits non-zero."""
```

**Replacement strategy by category:**

1. **Silent swallow** (`except Exception: pass`) — Replace with specific exception type. If swallowing is truly correct (e.g. `_add_scheduler_job` removing a non-existent job), use `except JobLookupError: pass` (APScheduler's actual exception type).

2. **Log + continue** — Replace with typed catch + structured log:
   ```python
   # Before
   except Exception as e:
       app.logger.error(f"Something failed: {e}")

   # After
   except paramiko.AuthenticationException as e:
       app.logger.error("SSH auth failed for %s: %s", server['hostname'], e)
       raise SSHAuthenticationError(str(e)) from e
   except paramiko.SSHException as e:
       app.logger.error("SSH connection failed for %s: %s", server['hostname'], e)
       raise SSHConnectionError(str(e)) from e
   except OSError as e:
       app.logger.error("Network error connecting to %s: %s", server['hostname'], e)
       raise SSHConnectionError(str(e)) from e
   ```

3. **Background thread catch-all** — Background threads (`_do_backup`, `_do_ansible_run`, `_run_install_rear`) must catch broadly to prevent thread death, but must log with `traceback`:
   ```python
   # Acceptable broad catch in background threads only
   except Exception as e:
       app.logger.error("Unexpected error in job %d: %s", job_id, traceback.format_exc())
       _set_job_status(job_id, 'failed')
   ```

4. **Route handler catch-all** — Replace with specific types; fall back to 500 with meaningful message:
   ```python
   # Before
   except Exception as e:
       return jsonify({'ok': False, 'msg': str(e)})

   # After
   except SSHConnectionError as e:
       return jsonify({'ok': False, 'msg': f'SSH connection failed: {e}'})
   except SSHAuthenticationError as e:
       return jsonify({'ok': False, 'msg': f'SSH authentication failed: {e}'})
   ```

### Anti-Patterns to Avoid

- **Circular imports:** Services must not import from routes. Models must not import from services. Routes import from services and models only. `app.py` imports from routes only.
- **Passing the Flask `app` object into services:** Services should use `current_app` from Flask context if they need logging, or accept a logger parameter. Do NOT pass `app` as a parameter.
- **Moving `init_db` and `_migrate_db` to a model file and calling it from routes:** Keep DB initialization in a dedicated `db.py`; call it only from `app.py` startup.
- **Creating a single `models/__init__.py` that re-exports everything:** Keep models per-entity. Route handlers import from `models.servers`, `models.jobs`, etc. — never from `models` directly.
- **Using Flask's `g` object for DB connections in the repository layer:** The current `get_db()` opens/closes per call. Keep this pattern — it's safe for SQLite with WAL mode and avoids threading complexity. Do not switch to `g`-based connection pooling in this refactor.
- **Renaming functions during the move:** When moving a function from app.py to a new module, keep the function name identical. Renaming during a refactor creates merge conflicts and makes regression detection harder.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Blueprint URL prefix management | Custom URL dispatcher | Flask Blueprint `url_prefix` | Built-in, zero overhead |
| Scheduler job not found handling | Custom job existence check | APScheduler `JobLookupError` | APScheduler raises this specific exception — catch it directly |
| Paramiko exception types | Custom SSH error detection | `paramiko.AuthenticationException`, `paramiko.SSHException`, `socket.timeout`, `OSError` | Paramiko already has a typed exception hierarchy |
| Subprocess error handling | Custom process error classes | `subprocess.TimeoutExpired`, `subprocess.CalledProcessError` | Already in stdlib |

**Key insight:** The entire refactor uses zero new libraries. Every pattern here is either stdlib Python, Flask built-ins, or typed exceptions from already-imported libraries.

---

## Common Pitfalls

### Pitfall 1: Circular Import Between Routes and Services
**What goes wrong:** A service imports a function from a route module (or from `app.py` directly), creating an `ImportError` at startup.
**Why it happens:** Background service functions currently call `app.logger` directly — they reference the global `app` object. After the split, `app` lives only in `app.py`.
**How to avoid:** In service modules, use `from flask import current_app` and call `current_app.logger.error(...)` instead. `current_app` is a proxy that resolves at request/app context time.
**Warning signs:** `ImportError: cannot import name 'app' from 'app'` at startup.

### Pitfall 2: Background Threads Lose Flask App Context
**What goes wrong:** Background threads (APScheduler callbacks, job threads) call `current_app` and get `RuntimeError: Working outside of application context`.
**Why it happens:** Flask's `current_app` proxy only works inside an application context. Background threads have no context unless explicitly pushed.
**How to avoid:** In `start_job_thread` and `_do_ansible_run`, push an app context before running:
```python
def _thread_wrapper(app, fn, *args, **kwargs):
    with app.app_context():
        fn(*args, **kwargs)
```
Or pass the logger explicitly rather than using `current_app.logger`.
**Warning signs:** `RuntimeError: Working outside of application context` in background thread logs.

### Pitfall 3: Moving Globals Without Tracking All Access Sites
**What goes wrong:** `_running_jobs`, `_job_lock`, `_scheduler`, `_ansible_running`, `_ansible_run_lock` are module-level globals. If they are split across multiple modules, import ordering creates stale references.
**Why it happens:** Python module globals are per-module, not per-process namespace. After the refactor, `from services.jobs import _running_jobs` creates a copy of the reference at import time.
**How to avoid:** Keep all mutable globals in their owning service module. Never use `from module import _mutable_global` — always use `import services.jobs; services.jobs._running_jobs`. Better: wrap all mutable global access in functions.
**Warning signs:** Job status checks see empty `_running_jobs` even though jobs are running.

### Pitfall 4: SQL in Services After REF-02
**What goes wrong:** When moving functions to service modules, the developer also copies the SQL calls rather than extracting them to the repository layer.
**Why it happens:** It feels faster to move function as-is; the SQL extraction is a separate step.
**How to avoid:** The discipline is: a service function may call a repository function, but MAY NOT call `get_db()` or `conn.execute()` directly. Code review / grep for `get_db()` in `services/` should return zero results.
**Warning signs:** `grep -r "get_db()" services/` returns any output.

### Pitfall 5: Breaking Route Endpoint Names
**What goes wrong:** After Blueprint registration, `url_for('servers_list')` breaks because Blueprint endpoints are namespaced as `url_for('servers.servers_list')`.
**Why it happens:** Flask Blueprints prefix endpoint names with the blueprint name by default.
**How to avoid:** Two options: (a) use the full namespaced name everywhere in templates and redirects, or (b) set `Blueprint('servers', __name__, url_prefix='')` with no prefix and leave endpoint names unchanged by not changing function names. Option (b) is safer for this refactor since no URL changes are needed.
**Warning signs:** `BuildError: Could not build url for endpoint 'servers_list'` in templates or route handlers.

### Pitfall 6: Ansible Routes Have Large Private Helpers
**What goes wrong:** `_save_ansible_host` (line 3682) and `_save_playbook` (line 3854) are large private helpers called by multiple routes. Moving them incorrectly breaks multiple endpoints.
**Why it happens:** These helpers are 60-100 lines each and touch both DB and filesystem. They sit between service and route layers.
**How to avoid:** Move `_save_ansible_host` and `_save_playbook` to `services/ansible.py` (not models), since they contain validation and filesystem sync logic, not just DB queries.

---

## Code Examples

### Blueprint Registration Pattern
```python
# app.py (after refactor) — source: Flask docs, Blueprint registration
from routes.auth import auth_bp
from routes.servers import servers_bp
from routes.schedules import schedules_bp
from routes.jobs import jobs_bp
from routes.settings import settings_bp
from routes.users import users_bp
from routes.api import api_bp
from routes.dashboard import dashboard_bp
from routes.ansible import ansible_bp

app.register_blueprint(auth_bp)
app.register_blueprint(servers_bp)
app.register_blueprint(schedules_bp)
app.register_blueprint(jobs_bp)
app.register_blueprint(settings_bp)
app.register_blueprint(users_bp)
app.register_blueprint(api_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(ansible_bp)
```

### APScheduler JobLookupError (replaces bare except)
```python
# services/scheduler.py
from apscheduler.jobstores.base import JobLookupError

def _remove_scheduler_job(schedule_id):
    if not _scheduler:
        return
    try:
        _scheduler.remove_job(f'sched_{schedule_id}')
    except JobLookupError:
        pass  # Job was already removed — this is expected
```

### Paramiko Typed Exception Hierarchy
```python
# services/ssh.py — typed catches replacing bare except Exception
import paramiko
import socket

try:
    client = build_ssh_client(server)
except paramiko.AuthenticationException as e:
    raise SSHAuthenticationError(f"Auth failed for {server['hostname']}: {e}") from e
except paramiko.SSHException as e:
    raise SSHConnectionError(f"SSH error for {server['hostname']}: {e}") from e
except (socket.timeout, OSError) as e:
    raise SSHConnectionError(f"Network error for {server['hostname']}: {e}") from e
```

### App Context for Background Threads
```python
# services/jobs.py
import threading

def start_job_thread(target_fn, job_id, *args):
    from flask import current_app
    app = current_app._get_current_object()  # get real app, not proxy

    def _wrapper():
        with app.app_context():
            target_fn(job_id, *args)

    t = threading.Thread(target=_wrapper, daemon=True)
    t.start()
    return t
```

---

## Recommended Plan Splitting Strategy

This is the most important output for the planner. The 4,329-line monolith must be migrated incrementally. Each plan moves one domain and ends with a passing smoke test.

### Plan 1: Foundation (scaffolding, no behavior change)
- Create directory structure: `models/`, `services/`, `routes/`, `config.py`, `db.py`
- Move constants from app.py to `config.py`
- Move `get_db()` to `db.py`
- Move `init_db()` and `_migrate_db()` to `db.py`
- `app.py` imports from `config` and `db`; behavior identical
- Smoke test: `python app.py` starts without error; all routes return non-500

### Plan 2: Models/Repository Layer
- Create all repository modules: `models/users.py`, `models/servers.py`, `models/schedules.py`, `models/jobs.py`, `models/settings.py`, `models/ansible.py`
- Move all SQL `conn.execute()` calls from app.py into the corresponding repository modules
- Update all callers in app.py to use repository functions
- `app.py` must have zero `conn.execute()` calls after this plan
- Smoke test: all routes return non-500; `grep "conn.execute\|get_db()" app.py` returns zero results

### Plan 3: Services Layer — SSH + ReaR + Jobs
- Create `services/ssh.py`, `services/rear.py`, `services/jobs.py`
- Move SSH functions, ReaR runners, job management functions
- Replace SSH bare `except Exception` with typed paramiko/socket exceptions
- Services call repository functions from models/; services do NOT call `get_db()` directly
- Smoke test: server install/configure/backup routes work end-to-end (manual test in air-gapped env acceptable)

### Plan 4: Services Layer — Auth + Scheduler + Ansible
- Create `services/auth.py`, `services/scheduler.py`, `services/ansible.py`
- Move auth functions, scheduler management, Ansible orchestration
- Fix scheduler background thread app context issue
- Smoke test: login works; scheduler fires jobs correctly; ansible routes work

### Plan 5: Routes Layer
- Create all Blueprint modules in `routes/`
- Move route handler functions from app.py into corresponding Blueprint modules
- Register all Blueprints in app.py
- Fix any `url_for()` endpoint name breakage from Blueprint namespacing
- Smoke test: all 62 routes return correct HTTP status codes (automated with `test_smoke_routes.py`)

### Plan 6: Exception Handling (REF-03)
- Audit all remaining `except Exception` blocks (any that survived Plans 3-5)
- Replace with typed exceptions per the taxonomy defined above
- Ensure background threads retain broad catch with `traceback.format_exc()` logging
- Verification: `grep -n "except Exception:" services/ routes/ models/ | grep -v "# broad-catch"` returns zero
- Smoke test: full pytest suite passes

---

## State of the Art

| Old Approach | Current Approach | Impact on This Refactor |
|--------------|------------------|------------------------|
| Single `app.py` monolith | Blueprints + service modules | Standard Flask pattern since 2.0; no behavioral change |
| `except Exception` catch-all | Typed exceptions from paramiko, APScheduler, subprocess | Paramiko 3.x has documented exception hierarchy — use it |
| Direct `conn.execute()` in routes | Repository functions | Pure structural — same SQL, named functions |
| Global `app` reference in threads | `current_app._get_current_object()` | Required for Flask 2.3+ app factory compatibility |

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (already installed and configured) |
| Config file | `pytest.ini` — `testpaths = tests` |
| Quick run command | `cd /home/ubuntu/workspace/rear-manager && python -m pytest tests/ -x -q` |
| Full suite command | `cd /home/ubuntu/workspace/rear-manager && python -m pytest tests/ -v` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| REF-01 | No layer crosses into another's responsibility | static/grep | `grep -r "conn.execute\|get_db()" routes/ services/` returns 0 | ❌ Wave 0 |
| REF-02 | No inline SQL strings in routes or services | static/grep | `grep -rn "\.execute(" routes/ services/` returns 0 | ❌ Wave 0 |
| REF-03 | No bare `except Exception` in routes/services/models | static/grep | `grep -rn "except Exception" routes/ services/ models/` returns 0 lines without approval comment | ❌ Wave 0 |
| REF-01 (runtime) | All 62 routes return non-500 responses | smoke | `python -m pytest tests/test_smoke_routes.py -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/ -x -q` (existing suite + any new smoke tests)
- **Per plan merge:** Full pytest suite + grep-based layer boundary checks
- **Phase gate:** Full suite green + grep checks clean before `/gsd:verify-work`

### Wave 0 Gaps (must be created before implementation plans)
- [ ] `tests/test_smoke_routes.py` — smoke test that creates a test Flask client and hits all 62 routes, asserting HTTP status is not 500. Requires an in-memory SQLite DB fixture.
- [ ] `tests/conftest.py` extension — add `app_client` fixture that creates the Flask test client with an in-memory DB (needed for smoke tests after Blueprint refactor)

---

## Open Questions

1. **`login_required` and `admin_required` decorator placement**
   - What we know: these decorators currently live in `app.py` and are used by routes
   - What's unclear: should they live in `routes/auth.py` or in a standalone `decorators.py`?
   - Recommendation: place in `routes/auth.py` since they are HTTP-layer concerns; import from there in other route modules

2. **Template filter and `jinja_env.globals` registration**
   - What we know: `calc_duration_filter`, `_cron_describe`, `_safe_dirname` are registered on the Flask app instance at lines 156, 172, 175
   - What's unclear: after Blueprint refactor, where do these registrations happen?
   - Recommendation: keep them in `app.py` (or a `utils.py` imported by `app.py`), registered on the app object before Blueprint registration

3. **`_ansible_running` and `_ansible_run_lock` globals in ansible service**
   - What we know: these are module-level globals in the current app.py (lines 3310-3311)
   - What's unclear: thread safety during module split
   - Recommendation: place in `services/ansible.py`; never import the variable directly — always access via `services.ansible._ansible_running`

---

## Sources

### Primary (HIGH confidence)
- Direct inspection of `/home/ubuntu/workspace/rear-manager/app.py` (4,329 lines) — all line numbers and function names
- Flask documentation (Flask Blueprints, `current_app`, `app_context`) — standard patterns, confidence HIGH based on training knowledge (Flask stable since 2.0)
- Python stdlib `sqlite3`, `threading`, `subprocess` — standard exception hierarchies

### Secondary (MEDIUM confidence)
- Paramiko exception hierarchy (`paramiko.AuthenticationException`, `paramiko.SSHException`) — verified against paramiko 3.x source structure; these exception classes exist in paramiko >= 1.x
- APScheduler `JobLookupError` — exists in apscheduler.jobstores.base since APScheduler 3.x

### Tertiary (LOW confidence)
- None — all findings from direct code inspection or well-established library APIs

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — no new libraries, existing requirements.txt is sufficient
- Architecture patterns: HIGH — derived from direct code inspection + standard Flask patterns
- Pitfalls: HIGH — each pitfall identified from specific code constructs observed in app.py
- Exception taxonomy: MEDIUM — paramiko/APScheduler exception names verified against known APIs, but not freshly fetched from Context7

**Research date:** 2026-03-24
**Valid until:** 2026-06-24 (stable domain — Flask/sqlite3/paramiko APIs are stable)
