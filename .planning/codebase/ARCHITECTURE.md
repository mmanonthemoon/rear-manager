# Architecture

## Pattern

**Monolithic Flask MVC** — single `app.py` (4,242 lines) with server-side rendering via Jinja2 templates. No frontend framework. No separate service layer or API layer beyond a handful of `/api/*` JSON endpoints.

## Layers

```
Browser
  └── Flask Routes (app.py:1806–4242)
        ├── Auth / Session management
        ├── Business Logic (inline in route handlers)
        ├── SSH Layer (Paramiko) — remote command execution
        ├── Ansible Layer — inventory generation + subprocess calls
        ├── Background Jobs — threading + in-memory registry
        ├── Scheduler — APScheduler (cron-based)
        └── Data Layer — SQLite via sqlite3 (no ORM)
```

## Two Major Subsystems

### 1. ReaR Backup Management
- Server CRUD with SSH credentials (`servers` table)
- SSH key pair generation (`~/.ssh/rear_manager_rsa`)
- ReaR install/configure/run via SSH (`_run_install_rear`, `_run_configure_rear`, `_do_backup`)
- Job tracking with real-time log streaming (`backup_jobs` table + `_running_jobs` dict)
- Cron-based scheduling (`schedules` table + APScheduler)
- NFS target configuration (per-server or global)

### 2. Ansible Automation
- Ansible host/group management (`ansible_hosts`, `ansible_groups`, `ansible_host_groups` tables)
- Dynamic inventory generation from DB → YAML (`_generate_inventory`, `app.py:3042`)
- Playbook/role CRUD with disk sync (`ansible_playbooks`, `ansible_roles`, `ansible_role_files` tables)
- Playbook execution via `ansible-playbook` subprocess (`_do_ansible_run`, `app.py:3262`)
- Run tracking with live output tailing (`ansible_runs` table)

## Entry Points

| Path | Description |
|------|-------------|
| `app.py` (bottom) | `app.run()` or WSGI |
| `/login` | Auth entry point |
| `/` | Dashboard |
| `/servers` | ReaR server management |
| `/ansible/` | Ansible dashboard |

## Data Flow — Backup Job

```
POST /servers/<sid>/backup
  → create_job() → inserts backup_jobs row
  → start_job_thread(_do_backup, job_id, ...)
      → SSH connect (Paramiko)
      → stream output via _append_log()
      → _set_job_status() on complete
  → GET /jobs/<jid> polls for status
```

## Data Flow — Ansible Run

```
POST /ansible/playbooks/<pid>/run
  → inserts ansible_runs row
  → _generate_inventory() → writes YAML to ansible/inventories/
  → subprocess: ansible-playbook ... (streaming)
  → _append_run_log() + _set_run_status()
  → GET /api/ansible/run-status/<rid> polls
```

## Key Abstractions

| Function | Location | Purpose |
|----------|----------|---------|
| `build_ssh_client()` | app.py:1026 | Create Paramiko SSH client with key/password auth |
| `ssh_exec_stream()` | app.py:1049 | Execute command with PTY + log streaming |
| `_wrap_become_cmd()` | app.py:990 | Wrap command with sudo/su privilege escalation |
| `generate_rear_config()` | app.py:1299 | Generate `/etc/rear/local.conf` content |
| `_generate_inventory()` | app.py:3042 | Build Ansible YAML inventory from DB |
| `get_db()` | app.py:173 | Per-request SQLite connection (stored in `g`) |
| `init_db()` | app.py:180 | Schema creation with 12 tables |
| `_migrate_db()` | app.py:406 | ADD COLUMN migrations (no rollback) |

## Cross-Cutting Concerns

- **Authentication**: `login_required` + `admin_required` decorators (app.py:746, 773)
- **Localization**: Turkish UI via Jinja2 template strings; `_cron_describe()` produces Turkish cron text
- **Graceful degradation**: Optional imports (`paramiko`, `apscheduler`, `ldap3`, `werkzeug`) with `HAS_*` flags
- **Job state**: In-memory `_running_jobs` dict (lost on restart) + persistent `backup_jobs` table
- **Secrets**: `app.secret_key` generated fresh each start (`secrets.token_hex(32)`) — sessions invalidated on restart
