# Conventions

## Language & Style

- **Python 3**, no type annotations anywhere in the codebase
- No linter config (no `.flake8`, `pyproject.toml`, or `setup.cfg`)
- Turkish comments and UI strings throughout — this is a Turkish-language project
- Mix of short and long functions; business logic is not separated from route handlers

## Naming

| Context | Pattern | Example |
|---------|---------|---------|
| Flask routes | `snake_case` verb+noun | `server_add`, `schedule_toggle`, `ansible_run_detail` |
| Internal helpers | `_leading_underscore` | `_run_install_rear`, `_do_backup`, `_append_log` |
| Constants | `UPPER_SNAKE_CASE` | `DB_PATH`, `BACKUP_ROOT`, `ANSIBLE_DIR` |
| DB columns | `snake_case` | `created_at`, `triggered_by`, `ssh_user` |
| Templates | `resource_action.html` | `server_form.html`, `ansible_run_detail.html` |
| Feature flags | `HAS_*` bool | `HAS_PARAMIKO`, `HAS_SCHEDULER`, `HAS_LDAP` |

## Import Pattern

Optional dependencies are wrapped in try/except at module top:

```python
try:
    import paramiko
    HAS_PARAMIKO = True
except ImportError:
    HAS_PARAMIKO = False
```

Guards used before calling: `if not HAS_PARAMIKO: flash(...); return`.

## Error Handling

- Bare `except Exception` is the dominant pattern — ~40+ occurrences
- No custom exception classes
- Errors surfaced to users via `flash(message, 'danger')`
- Background job errors logged via `_append_log(job_id, f"[HATA] ...")` and `_set_job_status(job_id, 'error')`
- SSH errors caught and written to job log, not re-raised

```python
# Typical pattern
try:
    result = ssh_exec_stream(server, cmd, log)
except Exception as e:
    log(f"[HATA] {e}")
    _set_job_status(job_id, 'error')
    return
```

## Logging

No Python `logging` module used. Two logging mechanisms:

1. **Flash messages** — user-facing feedback via `flash(text, level)` where level is `'success'`, `'danger'`, `'warning'`, `'info'`
2. **Job log callbacks** — background jobs use `log = lambda t: _append_log(job_id, t)`, then pass `log` to SSH helpers as `log_cb`

```python
log = lambda t: _append_log(job_id, t)
ssh_exec_stream(server, command, log_cb=log)
```

## Database Access

Raw `sqlite3`, no ORM. Per-request connection via Flask `g`:

```python
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db
```

Queries written inline in route handlers with `?` placeholders. No query builder.

## Template Patterns

- All templates extend `base.html`
- Dark-themed Bootstrap UI
- Form submissions via POST with `method="POST"` + CSRF token absent (no Flask-WTF)
- Real-time job output via JS polling (`/api/ansible/run-output/<rid>`, `/jobs/<jid>`)
- Turkish labels and messages throughout

## Background Jobs

Job functions named `_run_*` or `_do_*`, started via `start_job_thread()`:

```python
start_job_thread(_do_backup, job_id, server_dict, ...)
```

Jobs store state in both `_running_jobs` dict (in-memory, lost on restart) and `backup_jobs` DB table (persistent).

## Route Pattern

```python
@app.route('/servers/<int:sid>/action', methods=['GET', 'POST'])
@login_required
def server_action(sid):
    db = get_db()
    row = db.execute('SELECT * FROM servers WHERE id=?', (sid,)).fetchone()
    if not row:
        flash('Sunucu bulunamadı.', 'danger')
        return redirect(url_for('servers_list'))
    # ... logic ...
    return render_template('template.html', **ctx)
```

## Comments

- Docstrings used on some helper functions (Turkish)
- Section dividers with `# ─────────────` style separators
- Inline comments for complex logic (Turkish)
- No type hints or return type annotations
