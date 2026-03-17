# Concerns

## Tech Debt

### Monolithic Structure
- `app.py` is 4,242 lines — routes, business logic, SSH, DB, scheduling all in one file
- No separation of concerns; impossible to test individual layers in isolation
- All SQL queries are inline strings scattered across route handlers

### Error Handling
- ~48 bare `except Exception` clauses that silently swallow errors
- No structured error reporting or centralized error handler
- Background job failures only visible if user navigates to job detail page

### Session Security
- `app.secret_key = secrets.token_hex(32)` — regenerated on every restart
- All user sessions invalidated on application restart (no persistent session store)

## Security Issues

### Critical
- **Plaintext passwords in DB**: SSH passwords stored unencrypted in `servers` table
- **No CSRF protection**: No Flask-WTF or equivalent; all POST forms vulnerable
- **SSH host key verification disabled**: `client.set_missing_host_key_policy(paramiko.AutoAddPolicy())` in `build_ssh_client()` — susceptible to MITM
- **No rate limiting on login**: `/login` endpoint has no brute-force protection

### High
- **Subprocess injection risk**: `ansible-playbook` called via `subprocess` with user-controlled parameters; needs careful review of `extra_args` handling in `_do_ansible_run()`
- **SQL injection surface**: Raw string queries with `?` placeholders used correctly for values, but dynamic column/table names (if any) would be unsafe
- **No audit logging**: No record of who ran which backup or Ansible playbook

### Medium
- **Become password in DB**: `become_password` stored plaintext alongside SSH credentials
- **SSH key at fixed path**: `~/.ssh/rear_manager_rsa` — if compromised, all managed servers are at risk

## Known Bugs / Fragile Areas

| Issue | Location | Notes |
|-------|----------|-------|
| APScheduler timezone drift | `init_scheduler()` app.py:1740 | No explicit timezone set |
| Race condition on `_running_jobs` | app.py:1388–1413 | Dict accessed from multiple threads with `_job_lock` only in some paths |
| Ansible hostname truncation | `_safe_dirname()` app.py:130 | Previous bug with special chars — fixed in recent commit |
| SSH PTY prompt detection | `ssh_exec_stream()` app.py:1049 | Password/sudo detection via string matching is fragile across OS variants |
| `app.secret_key` regenerated on restart | app.py top | All sessions lost on restart |
| No DB transaction rollback | Throughout | Multi-step operations not wrapped in transactions |

## Performance

- **Single-threaded SQLite**: All DB writes contend on one file; concurrent jobs will serialize
- **Unbounded log storage**: `backup_jobs.output` stores full job output in DB with no truncation limit
- **No pagination on history**: Jobs/runs list pages load all records (no LIMIT in queries shown in templates)
- **Blocking SSH I/O**: Long-running SSH commands block their thread for the full duration
- **No DB indexes**: Frequent lookups by `server_id`, `schedule_id` have no indexes beyond PKs

## Scaling Limits

- **SQLite**: Not suitable for concurrent multi-user write load; fine for single-admin use
- **Thread-per-job**: Each backup/ansible run spawns a new thread — no thread pool or queue
- **In-memory job state**: `_running_jobs` dict is not shared across processes; won't work behind multi-worker WSGI

## At-Risk Dependencies

| Dependency | Risk |
|------------|------|
| `paramiko` | Mature but not always promptly patched for SSH CVEs |
| `apscheduler` | Not designed for distributed/multi-process use |
| `ldap3` | Optional; AD auth untested in CI |
| Flask dev server | `app.run()` used — not suitable for production load |

## Missing Features

- No backup verification (does the backup actually restore?)
- No backup encryption at rest
- No MFA / 2FA support
- No audit log of administrative actions
- No disaster recovery documentation
- No email/webhook alerting on backup failure (beyond UI status)
- No pagination on long lists (jobs, servers, ansible runs)
