# Testing

## Framework

**None.** There is no test framework configured in this project.

- No `pytest`, `unittest`, or any test runner
- No test files (`test_*.py`, `*_test.py`) anywhere in the codebase
- No test dependencies in `requirements.txt`
- No CI configuration (no `.github/workflows/`, no `Makefile` test target)

## Manual Testing Endpoints

The application has two built-in "test" routes that serve as manual smoke tests:

| Route | Function | What it tests |
|-------|----------|---------------|
| `POST /servers/<sid>/test` | `server_test()` | SSH connectivity to a server |
| `POST /api/ansible/ping-host` | `api_ansible_ping_host()` | Ansible `ping` module against a host |

These are runtime checks via the UI, not automated tests.

## High-Risk Untested Areas

| Area | Risk | Location |
|------|------|----------|
| SSH PTY prompt detection | High — password/sudo prompts detected via string matching | `ssh_exec_stream()` app.py:1049 |
| Job threading & concurrency | High — `_running_jobs` dict accessed from multiple threads | app.py:1388–1413 |
| DB migrations | High — `ADD COLUMN` migrations with no rollback | `_migrate_db()` app.py:406 |
| ReaR config generation | Medium — generated config must match ReaR syntax | `generate_rear_config()` app.py:1299 |
| Ansible inventory generation | Medium — YAML must be valid Ansible inventory | `_generate_inventory()` app.py:3042 |
| Cron expression parsing | Medium — used for scheduler + UI display | `_cron_describe()` app.py:84 |
| `_safe_dirname()` sanitization | Medium — hostname→directory name conversion | app.py:130 |
| AD/LDAP authentication | Low (optional) — `authenticate_ad()` | app.py:664 |

## Testing Recommendations

To add tests, the natural starting point would be:

```
pip install pytest pytest-flask
```

Priority areas to test first:
1. `generate_rear_config()` — pure function, easy to unit test
2. `_safe_dirname()` — pure function with edge cases
3. `_cron_describe()` — pure function
4. `_generate_inventory()` — pure function returning YAML string
5. Auth routes — `login_required` decorator behavior
6. DB schema migrations — verify `_migrate_db()` is idempotent

Integration tests (SSH, Ansible) would require mock infrastructure or test containers.
