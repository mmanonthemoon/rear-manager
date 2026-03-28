# Phase 3: Testing - Research

**Researched:** 2026-03-28
**Domain:** pytest unit/integration testing for Flask + paramiko (SSH) + subprocess (Ansible)
**Confidence:** HIGH

---

## Summary

Phase 3 adds automated test coverage for three critical workflows: SSH execution, ReaR install/configure, and Ansible host/playbook execution. The groundwork is already strong — pytest 9.0.2 is installed, `pytest.ini` is configured, `tests/` contains 55 passing tests across five files, and the Phase 2 refactor created clean service boundaries that tests can target without touching Flask routes at all.

The central challenge is that all three workflows ultimately call external processes or network targets: `paramiko.SSHClient.connect()` for SSH, `subprocess.Popen(['ansible-playbook', ...])` for Ansible. Neither is available in CI without a live server. The established pattern from `test_bug02_ssh.py` solves this: a hand-rolled `MockChannel` class plus `patch.object(ssh_module, 'build_ssh_client', return_value=mock_client)`. This same pattern scales directly to ReaR and Ansible tests — the services layer already has clear injection points.

The Ansible service (`services/ansible.py`) wraps `subprocess.Popen` in `_do_ansible_run()`. This function can be tested by patching `subprocess.Popen` at the module level with `unittest.mock.patch('services.ansible.subprocess.Popen', ...)`. Inventory generation (`_generate_inventory()`) has no external dependencies beyond the DB and filesystem, so it can be tested with a real in-memory fixture DB and a `tmp_path`.

**Primary recommendation:** Use `unittest.mock` (stdlib) exclusively — no third-party mock library needed. Patch at the exact import boundary used by each service module. Follow the existing `MockChannel` pattern for SSH-dependent tests; follow `patch('services.ansible.subprocess.Popen')` for Ansible tests.

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|-----------------|
| TEST-01 | SSH connection and command execution services must have unit tests; suite runs without a real server | `MockChannel` + `patch.object(ssh_module, 'build_ssh_client')` pattern already proven in `test_bug02_ssh.py`; extend for `ssh_upload_file`, `ssh_test_connection`, `ssh_get_os_info` |
| TEST-02 | ReaR install and configure flows must have integration tests verifying SSH commands issued in correct order | `_run_install_rear()` and `_run_configure_rear()` in `services/rear.py` call `ssh_service.ssh_exec_stream` sequentially; mock `build_ssh_client` and capture `ssh_exec_stream` calls to assert command order |
| TEST-03 | Ansible host registration, inventory generation, and playbook execution must have test coverage | `_generate_inventory()` tested with DB fixture + `tmp_path`; `_do_ansible_run()` tested by patching `subprocess.Popen`; host CRUD tested via `models/ansible.py` repository functions directly |
</phase_requirements>

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| pytest | 9.0.2 (installed) | Test runner, fixtures, parametrize | Already in use; `pytest.ini` configured |
| unittest.mock | stdlib (Python 3.12) | `MagicMock`, `patch`, `patch.object` | Already used in all existing tests; no extra install |
| tempfile / tmp_path | stdlib / pytest builtin | Isolated DB files, filesystem fixtures | Already used in `conftest.py` `app_client` fixture |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pytest.mark.parametrize | pytest builtin | Run same test across OS variants, command variants | Parametrize OS detection tests (ubuntu/debian/rhel/suse) |
| pytest fixtures (conftest.py) | pytest builtin | Shared server dicts, mock SSH clients, DB clients | All tests needing a server dict or app client |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| unittest.mock | pytest-mock | pytest-mock is a thin wrapper; adds a dependency without benefit when stdlib mock is already used everywhere |
| Custom MockChannel | paramiko mock transport | Paramiko has no official mock transport; MockChannel is proven and already exists |

**Installation:** No new packages required. All tools are present.

**Version verification:** `pytest 9.0.2` confirmed via `python3 -m pytest --version`. `paramiko 4.0.0` confirmed via `python3 -c "import paramiko; print(paramiko.__version__)"`.

---

## Architecture Patterns

### Recommended Project Structure
```
tests/
├── conftest.py              # existing — add new fixtures here (mock_ssh_client, db_fixture, ansible_dirs)
├── test_bug01_lock.py       # existing — no changes
├── test_bug02_ssh.py        # existing — no changes
├── test_bug03_scheduler.py  # existing — no changes
├── test_bug04_secret.py     # existing — no changes
├── test_smoke_routes.py     # existing — no changes
├── test_ssh_service.py      # NEW — TEST-01: ssh_upload_file, ssh_test_connection, ssh_get_os_info
├── test_rear_service.py     # NEW — TEST-02: _run_install_rear, _run_configure_rear, generate_rear_config
└── test_ansible_service.py  # NEW — TEST-03: _generate_inventory, _do_ansible_run, host/playbook repo
```

### Pattern 1: Patching build_ssh_client for SSH and ReaR tests

**What:** Replace `build_ssh_client` with a `MagicMock` that returns a controlled `MockChannel`. All SSH logic under test runs against the mock channel without a real network connection.

**When to use:** Any test of a function that internally calls `ssh_service.build_ssh_client()` or `ssh_service.ssh_exec_stream()`.

**Example (from existing test_bug02_ssh.py):**
```python
# Source: tests/test_bug02_ssh.py (existing, proven)
import services.ssh as ssh_module
from unittest.mock import MagicMock, patch

def _build_mock_client(mock_channel):
    mock_transport = MagicMock()
    mock_transport.open_session.return_value = mock_channel
    mock_client = MagicMock()
    mock_client.get_transport.return_value = mock_transport
    return mock_client

def _call_ssh_exec(mock_channel, server, command='echo hi'):
    mock_client = _build_mock_client(mock_channel)
    with patch.object(ssh_module, 'build_ssh_client', return_value=mock_client):
        return ssh_module.ssh_exec_stream(server, command, log_cb=lambda x: None)
```

### Pattern 2: Tracking SSH command sequences for ReaR integration tests

**What:** For `_run_install_rear()` and `_run_configure_rear()`, the test must assert that the correct SSH commands were issued in the correct order. Capture calls to `ssh_exec_stream` using `side_effect` to record command arguments.

**When to use:** TEST-02 — verifying ReaR install and configure command sequences.

**Example:**
```python
# Source: derived from existing MockChannel pattern + unittest.mock side_effect
import services.rear as rear_service
import services.ssh as ssh_service
from unittest.mock import patch, MagicMock

def test_install_rear_ubuntu_apt_success():
    issued_commands = []

    def fake_exec_stream(server, command, log_cb):
        issued_commands.append(command)
        if 'apt-get' in command:
            return 0, 'Reading package lists...'
        if 'rear --version' in command:
            return 0, 'Relax-and-Recover 2.7'
        return 0, ''

    def fake_get_os_info(server):
        return 'NAME="Ubuntu"\nVERSION_ID="22.04"'

    with patch.object(ssh_service, 'ssh_exec_stream', side_effect=fake_exec_stream), \
         patch.object(ssh_service, 'ssh_get_os_info', side_effect=fake_get_os_info), \
         patch.object(ssh_service, 'build_ssh_client', return_value=MagicMock()):
        # _run_install_rear requires job_id and DB; use app_client fixture for DB setup
        ...
    # Assert apt-get command was first, rear --version was last
    assert any('apt-get' in cmd for cmd in issued_commands)
    assert any('rear --version' in cmd for cmd in issued_commands)
    apt_idx = next(i for i, c in enumerate(issued_commands) if 'apt-get' in c)
    ver_idx = next(i for i, c in enumerate(issued_commands) if 'rear --version' in c)
    assert apt_idx < ver_idx
```

### Pattern 3: Patching subprocess.Popen for Ansible tests

**What:** `_do_ansible_run()` calls `subprocess.Popen(['ansible-playbook', ...])`. Patch at the exact import name in the module under test.

**When to use:** TEST-03 — testing Ansible playbook execution without a real ansible-playbook binary.

**Example:**
```python
# Source: derived from services/ansible.py structure
import services.ansible as ansible_service
from unittest.mock import patch, MagicMock
import io

def test_do_ansible_run_success(app_client):
    mock_proc = MagicMock()
    mock_proc.pid = 99999
    mock_proc.stdout = iter(['PLAY [all]\n', 'ok: [host1]\n'])
    mock_proc.returncode = 0
    mock_proc.wait.return_value = 0

    with patch('services.ansible.subprocess.Popen', return_value=mock_proc), \
         patch.object(ansible_service, '_generate_inventory', return_value='---\nall: {}'):
        # Call _do_ansible_run(run_id, playbook_path, extra_args) with a real run_id from DB
        ...
```

### Pattern 4: Testing inventory generation with a DB fixture

**What:** `_generate_inventory()` queries `ansible_repo.get_hosts_active_with_groups()` and writes a `hosts.yml` file. Test by seeding the DB via repository functions and using `tmp_path` to redirect filesystem writes.

**When to use:** TEST-03 — verifying inventory YAML structure for Linux SSH and Windows WinRM hosts.

**Example:**
```python
# Source: derived from services/ansible.py _generate_inventory()
import services.ansible as ansible_service
from models import ansible as ansible_repo

def test_generate_inventory_linux_host(app_client, tmp_path, monkeypatch):
    # Seed a Linux host
    hid = ansible_repo.create_host(
        name='web1', hostname='10.0.0.1', os_type='linux',
        connection_type='ssh', ssh_port=22, ansible_user='deploy',
        ansible_pass='secret', auth_type='password',
        become_method='sudo', become_user='root', become_pass='',
        become_same=1, active=1,
        # ... other required fields
    )
    # Redirect inventory write to tmp_path
    monkeypatch.setattr('config.ANSIBLE_INV_DIR', str(tmp_path))
    monkeypatch.setattr('config.ANSIBLE_GVARS_DIR', str(tmp_path))
    monkeypatch.setattr('config.ANSIBLE_HVARS_DIR', str(tmp_path))

    inv_str = ansible_service._generate_inventory()
    assert 'web1' in inv_str
    assert 'ansible_connection: ssh' in inv_str
```

### Anti-Patterns to Avoid

- **Patching at the wrong import name:** If `services/rear.py` does `from services import ssh as ssh_service`, you must patch `services.rear.ssh_service.build_ssh_client`, not `services.ssh.build_ssh_client`. The correct target is the name as it exists in the module being tested.
- **Testing background threads directly:** `_run_install_rear()` and `_do_ansible_run()` are designed to run inside `start_job_thread()` which pushes a Flask app context. Call them directly within a `with app.app_context():` block in tests, or use the `app_client` fixture which sets up the app.
- **Calling `start_ansible_run()` in tests:** This spawns a real background thread. Test `_do_ansible_run()` directly instead — it contains all the logic.
- **Testing `_run_install_rear` without a real job_id in DB:** These functions call `job_repo.set_started(job_id)` and `job_repo.update_status(job_id, ...)`. The job_id must exist in the DB fixture or the test will fail with a DB integrity error.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Capturing log output | Custom log accumulator | `log_cb=lambda x: None` or `log_cb=captured.append` passed directly | Services already accept `log_cb` parameter |
| Fake subprocess output | Custom Popen subclass | `MagicMock()` with `stdout = iter([...])` | `MagicMock` handles attribute access automatically |
| Fake SSH channel | New mock class | Extend or reuse existing `MockChannel` from `test_bug02_ssh.py` | It already handles `recv_ready`, `recv`, `exit_status_ready`, `sendall` |
| DB setup for tests | Raw SQL inserts | `db_module.init_db()` + repository create functions | Ensures schema is always current |
| Filesystem isolation | `os.makedirs` + `shutil.rmtree` | `tmp_path` (pytest builtin) + `monkeypatch.setattr` for config paths | Automatic cleanup, no manual teardown |

**Key insight:** The service layer was explicitly designed in Phase 2 for testability — services receive server dicts (not DB rows), call `log_cb` for output, and use typed exceptions. There is nothing to refactor before writing tests.

---

## Common Pitfalls

### Pitfall 1: Flask app context missing in service tests

**What goes wrong:** `services/rear.py`, `services/ssh.py`, and `services/ansible.py` call `current_app.logger.*`. Any test that exercises these services outside a request context will get `RuntimeError: Working outside of application context`.

**Why it happens:** Flask's `current_app` proxy requires an active app context.

**How to avoid:** Wrap service calls in `with app_module.app.app_context():` in the test body, or use the `app_client` fixture from `conftest.py` which sets `app.config['TESTING'] = True` but does NOT automatically push an app context. Push it explicitly.

**Warning signs:** `RuntimeError: Working outside of application context` in test output.

### Pitfall 2: `_run_install_rear` and `_run_configure_rear` need a job record in DB

**What goes wrong:** Both functions call `job_repo.set_started(job_id)` immediately. If `job_id` does not exist in the `backup_jobs` table, the UPDATE is a no-op but later `job_repo.update_status()` may also silently fail, making it impossible to assert final job state.

**Why it happens:** The functions assume the caller has already created the job record.

**How to avoid:** Create a job record via `job_repo.create(...)` in the fixture before calling the function. Use the `app_client` fixture which initializes the DB schema.

**Warning signs:** Assertions on job status always pass (because the UPDATE touched 0 rows and status never actually changed).

### Pitfall 3: Patching at wrong module reference

**What goes wrong:** `patch('services.ssh.build_ssh_client')` has no effect when `services/rear.py` imports SSH as `from services import ssh as ssh_service` and calls `ssh_service.build_ssh_client(...)`.

**Why it happens:** Python's import system: the name `ssh_service` in `rear.py` is a reference to the module object; patching `services.ssh.build_ssh_client` replaces the function on the module object, which IS the same object, so this specific case works. However, if rear.py had done `from services.ssh import build_ssh_client`, patching `services.ssh.build_ssh_client` would NOT affect the already-imported name in rear.py.

**How to avoid:** Always verify the import form in the module under test. For `from services import ssh as ssh_service`, patch `services.ssh.build_ssh_client` (patching the module attribute). For `from services.ssh import build_ssh_client`, patch `services.rear.build_ssh_client`.

**Warning signs:** Mock is never called (`mock.assert_called()` fails) even though the function should have been called.

### Pitfall 4: ansible-playbook not installed in test environment

**What goes wrong:** `_do_ansible_run()` calls `subprocess.Popen(['ansible-playbook', ...])`. If the mock is not applied, this raises `FileNotFoundError`. Confirmed: `ansible-playbook` is NOT installed in the current environment.

**Why it happens:** ansible-playbook is a system-level tool, not a Python package.

**How to avoid:** Always patch `services.ansible.subprocess.Popen` before calling `_do_ansible_run()`. Never call `start_ansible_run()` in tests.

**Warning signs:** `FileNotFoundError: [Errno 2] No such file or directory: 'ansible-playbook'` in test output.

### Pitfall 5: `_generate_inventory()` writes to real filesystem paths from config

**What goes wrong:** `_generate_inventory()` writes `hosts.yml` to `ANSIBLE_INV_DIR` and `group_vars`/`host_vars` files to `ANSIBLE_GVARS_DIR`/`ANSIBLE_HVARS_DIR`. In tests, these paths point to the real `ansible/` directory.

**Why it happens:** Config paths are module-level constants imported from `config.py`.

**How to avoid:** Use `monkeypatch.setattr('config.ANSIBLE_INV_DIR', str(tmp_path))` (and similar for other dirs) before calling `_generate_inventory()`. Also patch inside `services.ansible` module: `monkeypatch.setattr(ansible_service, 'ANSIBLE_INV_DIR', str(tmp_path))` since the function uses the locally-imported names.

**Warning signs:** `hosts.yml` modified in the real `ansible/inventory/` dir during test runs; `PermissionError` if the dir doesn't exist.

---

## Code Examples

Verified patterns from existing codebase:

### Reusable server dict fixture (add to conftest.py)
```python
# Source: derived from tests/test_bug02_ssh.py _make_server()
import pytest

@pytest.fixture
def server_dict():
    """Minimal server dict for SSH service tests."""
    return {
        'id': 1,
        'label': 'test-server',
        'hostname': 'test.local',
        'ip_address': '127.0.0.1',
        'ssh_port': '22',
        'ssh_user': 'testuser',
        'ssh_auth': 'password',
        'ssh_password': 'ssh_pass',
        'become_method': 'sudo',
        'become_password': 'become_pass',
        'become_same_pass': '0',
        'become_user': 'root',
    }
```

### MockChannel for file upload test (ssh_upload_file uses SFTP + exec)
```python
# Source: derived from tests/test_bug02_ssh.py MockChannel pattern
from unittest.mock import MagicMock, patch
import services.ssh as ssh_module

def test_ssh_upload_file_no_become(server_dict):
    server = {**server_dict, 'become_method': 'none'}

    mock_sftp = MagicMock()
    mock_sftp_file = MagicMock()
    mock_sftp.open.return_value.__enter__ = lambda s: mock_sftp_file
    mock_sftp.open.return_value.__exit__ = MagicMock(return_value=False)

    mock_client = MagicMock()
    mock_client.open_sftp.return_value = mock_sftp

    with patch.object(ssh_module, 'build_ssh_client', return_value=mock_client):
        ok, msg = ssh_module.ssh_upload_file(server, 'content here', '/etc/rear/local.conf')

    assert ok is True
    mock_sftp.open.assert_called_once_with('/etc/rear/local.conf', 'w')
```

### generate_rear_config pure function test (no mocking needed)
```python
# Source: services/rear.py generate_rear_config() — pure function after patching helpers
import services.rear as rear_service
from unittest.mock import patch

def test_generate_rear_config_contains_backup_url():
    server = {'hostname': 'webserver', 'exclude_dirs': ''}
    cfg = {
        'autoresize': '1',
        'migration_mode': '1',
        'rear_output': 'ISO',
        'rear_backup': 'NETFS',
        'global_exclude_dirs': '/data/*',
    }
    with patch('services.rear.settings_repo.get_nfs_target', return_value='nfs://10.0.0.1/backups/webserver'):
        config_text = rear_service.generate_rear_config(server, cfg)

    assert 'BACKUP_URL="nfs://10.0.0.1/backups/webserver"' in config_text
    assert 'OUTPUT=ISO' in config_text
    assert 'BACKUP=NETFS' in config_text
    assert '/data/*' in config_text
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Monolithic app.py (untestable) | Services layer with clean injection points | Phase 2 (2026-03-28) | Services can now be tested without Flask routing |
| No SSH mocking convention | MockChannel class in test_bug02_ssh.py | Phase 1 (2026-03-17) | Proven pattern to reuse for Phase 3 |

---

## Open Questions

1. **Flask app context for service tests**
   - What we know: All service modules call `current_app.logger`; the `app_client` fixture creates a test client but does not automatically push an app context.
   - What's unclear: Whether tests calling services directly (not via HTTP) need `with app.app_context():` or whether `app_client` fixture alone provides it via the test client's implicit context.
   - Recommendation: Explicitly push `app.app_context()` in test functions that call services directly. Add a shared `app_context` fixture to `conftest.py`.

2. **Job record creation for `_run_install_rear` / `_run_configure_rear` tests**
   - What we know: Both functions take a `job_id` and expect it to exist in the DB.
   - What's unclear: The exact signature of `job_repo.create()` / `models.jobs.create()` — need to verify field names.
   - Recommendation: Read `models/jobs.py` during planning to confirm the create function signature. Add a `test_job` fixture to `conftest.py`.

3. **`_generate_inventory()` uses yaml.dump — PyYAML may or may not be installed**
   - What we know: `services/ansible.py` has a fallback `_dict_to_yaml()` if PyYAML is not importable.
   - What's unclear: Whether PyYAML is installed in the current environment.
   - Recommendation: Confirm with `python3 -c "import yaml; print(yaml.__version__)"` during planning. Tests should work either way since the fallback path is also production code.

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 9.0.2 |
| Config file | `/home/ubuntu/workspace/rear-manager/pytest.ini` (exists) |
| Quick run command | `python3 -m pytest tests/ -q` |
| Full suite command | `python3 -m pytest tests/ -v` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| TEST-01 | SSH upload file (no become) | unit | `python3 -m pytest tests/test_ssh_service.py::test_ssh_upload_file_no_become -x` | ❌ Wave 0 |
| TEST-01 | SSH upload file (with become/mv) | unit | `python3 -m pytest tests/test_ssh_service.py::test_ssh_upload_file_with_become -x` | ❌ Wave 0 |
| TEST-01 | SSH test connection — success | unit | `python3 -m pytest tests/test_ssh_service.py::test_ssh_test_connection_success -x` | ❌ Wave 0 |
| TEST-01 | SSH test connection — auth failure | unit | `python3 -m pytest tests/test_ssh_service.py::test_ssh_test_connection_auth_failure -x` | ❌ Wave 0 |
| TEST-01 | SSH test connection — become failure | unit | `python3 -m pytest tests/test_ssh_service.py::test_ssh_test_connection_become_failure -x` | ❌ Wave 0 |
| TEST-01 | SSH get OS info — success | unit | `python3 -m pytest tests/test_ssh_service.py::test_ssh_get_os_info -x` | ❌ Wave 0 |
| TEST-02 | ReaR config generation — ubuntu output/netfs | unit | `python3 -m pytest tests/test_rear_service.py::test_generate_rear_config_ubuntu -x` | ❌ Wave 0 |
| TEST-02 | ReaR config generation — exclude dirs merged | unit | `python3 -m pytest tests/test_rear_service.py::test_generate_rear_config_excludes -x` | ❌ Wave 0 |
| TEST-02 | ReaR install — ubuntu apt-get success path | integration | `python3 -m pytest tests/test_rear_service.py::test_run_install_rear_ubuntu_apt_success -x` | ❌ Wave 0 |
| TEST-02 | ReaR install — RHEL/dnf path | integration | `python3 -m pytest tests/test_rear_service.py::test_run_install_rear_rhel -x` | ❌ Wave 0 |
| TEST-02 | ReaR install — apt fails, offline fallback | integration | `python3 -m pytest tests/test_rear_service.py::test_run_install_rear_ubuntu_offline_fallback -x` | ❌ Wave 0 |
| TEST-02 | ReaR configure — commands in correct order | integration | `python3 -m pytest tests/test_rear_service.py::test_run_configure_rear_command_order -x` | ❌ Wave 0 |
| TEST-03 | Ansible inventory — Linux SSH host | unit | `python3 -m pytest tests/test_ansible_service.py::test_generate_inventory_linux_host -x` | ❌ Wave 0 |
| TEST-03 | Ansible inventory — Windows WinRM host | unit | `python3 -m pytest tests/test_ansible_service.py::test_generate_inventory_windows_host -x` | ❌ Wave 0 |
| TEST-03 | Ansible inventory — grouped host | unit | `python3 -m pytest tests/test_ansible_service.py::test_generate_inventory_grouped_host -x` | ❌ Wave 0 |
| TEST-03 | Ansible inventory — become vars included | unit | `python3 -m pytest tests/test_ansible_service.py::test_generate_inventory_become_vars -x` | ❌ Wave 0 |
| TEST-03 | Ansible run — playbook success (exit 0) | integration | `python3 -m pytest tests/test_ansible_service.py::test_do_ansible_run_success -x` | ❌ Wave 0 |
| TEST-03 | Ansible run — playbook failure (exit 2) | integration | `python3 -m pytest tests/test_ansible_service.py::test_do_ansible_run_failure -x` | ❌ Wave 0 |
| TEST-03 | Ansible run — ansible-playbook not found | integration | `python3 -m pytest tests/test_ansible_service.py::test_do_ansible_run_not_found -x` | ❌ Wave 0 |
| TEST-03 | Ansible host CRUD — create/read/update | unit | `python3 -m pytest tests/test_ansible_service.py::test_ansible_host_crud -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `python3 -m pytest tests/ -q`
- **Per wave merge:** `python3 -m pytest tests/ -v`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_ssh_service.py` — covers TEST-01
- [ ] `tests/test_rear_service.py` — covers TEST-02
- [ ] `tests/test_ansible_service.py` — covers TEST-03
- [ ] Update `tests/conftest.py` — add `server_dict`, `app_context`, and `test_job` shared fixtures

*(Existing test infrastructure: pytest.ini, conftest.py, __init__.py already present — no framework install needed)*

---

## Sources

### Primary (HIGH confidence)
- `/home/ubuntu/workspace/rear-manager/tests/test_bug02_ssh.py` — MockChannel and patch.object patterns verified in 55 passing tests
- `/home/ubuntu/workspace/rear-manager/services/ssh.py` — exact function signatures and exception types
- `/home/ubuntu/workspace/rear-manager/services/rear.py` — exact call sequence for install and configure flows
- `/home/ubuntu/workspace/rear-manager/services/ansible.py` — subprocess.Popen usage and _generate_inventory() structure
- `/home/ubuntu/workspace/rear-manager/tests/conftest.py` — existing fixture patterns

### Secondary (MEDIUM confidence)
- Python 3.12 `unittest.mock` stdlib documentation — patch() and patch.object() behavior is stable

### Tertiary (LOW confidence)
- None — all findings verified against actual source code

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — pytest 9.0.2 and unittest.mock confirmed installed and in use
- Architecture: HIGH — patterns extracted directly from 55 passing existing tests and Phase 2 service code
- Pitfalls: HIGH — identified from actual code structure (app context, DB preconditions, import paths, missing ansible binary)

**Research date:** 2026-03-28
**Valid until:** 2026-04-28 (stable Python test tooling)
