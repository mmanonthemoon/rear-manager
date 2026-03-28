"""Tests for services/ansible.py and models/ansible.py — inventory generation, run execution, host CRUD."""
import pytest
from unittest.mock import patch, MagicMock

import services.ansible as ansible_service
from models import ansible as ansible_repo


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _make_linux_host(name='web1', hostname='10.0.0.1', become_method='none',
                     become_user='root', become_pass='', become_same=0):
    return ansible_repo.create_host(
        name=name,
        hostname=hostname,
        os_type='linux',
        connection_type='ssh',
        ssh_port=22,
        winrm_port=None,
        winrm_scheme=None,
        ansible_user='deploy',
        ansible_pass='secret',
        auth_type='password',
        ssh_key_path=None,
        win_domain=None,
        win_transport=None,
        become_method=become_method,
        become_user=become_user,
        become_pass=become_pass,
        become_same=become_same,
        vars_yaml='',
        notes='',
        active=1,
    )


def _make_windows_host(name='win1', hostname='10.0.0.2'):
    return ansible_repo.create_host(
        name=name,
        hostname=hostname,
        os_type='windows',
        connection_type='winrm',
        ssh_port=22,
        winrm_port=5985,
        winrm_scheme='http',
        ansible_user='Administrator',
        ansible_pass='winpass',
        auth_type='password',
        ssh_key_path=None,
        win_domain=None,
        win_transport='ntlm',
        become_method='none',
        become_user='',
        become_pass='',
        become_same=0,
        vars_yaml='',
        notes='',
        active=1,
    )


# ─────────────────────────────────────────────────────────────
# INVENTORY GENERATION TESTS
# ─────────────────────────────────────────────────────────────

def test_generate_inventory_linux_host(app_with_db, tmp_path, monkeypatch):
    """Linux SSH host → inventory contains ansible_connection: ssh and ansible_port: 22."""
    monkeypatch.setattr(ansible_service, 'ANSIBLE_INV_DIR', str(tmp_path))
    monkeypatch.setattr(ansible_service, 'ANSIBLE_GVARS_DIR', str(tmp_path))
    monkeypatch.setattr(ansible_service, 'ANSIBLE_HVARS_DIR', str(tmp_path))

    _make_linux_host(name='web1', hostname='10.0.0.1')

    result = ansible_service._generate_inventory()

    assert 'ansible_connection: ssh' in result
    assert 'ansible_port: 22' in result
    assert 'web1' in result


def test_generate_inventory_windows_host(app_with_db, tmp_path, monkeypatch):
    """Windows WinRM host → inventory contains ansible_connection: winrm and ansible_port: 5985."""
    monkeypatch.setattr(ansible_service, 'ANSIBLE_INV_DIR', str(tmp_path))
    monkeypatch.setattr(ansible_service, 'ANSIBLE_GVARS_DIR', str(tmp_path))
    monkeypatch.setattr(ansible_service, 'ANSIBLE_HVARS_DIR', str(tmp_path))

    _make_windows_host(name='win1', hostname='10.0.0.2')

    result = ansible_service._generate_inventory()

    assert 'ansible_connection: winrm' in result
    assert 'ansible_port: 5985' in result
    assert 'win1' in result


def test_generate_inventory_grouped_host(app_with_db, tmp_path, monkeypatch):
    """Grouped host → placed under children.groupname.hosts, not all.hosts directly."""
    monkeypatch.setattr(ansible_service, 'ANSIBLE_INV_DIR', str(tmp_path))
    monkeypatch.setattr(ansible_service, 'ANSIBLE_GVARS_DIR', str(tmp_path))
    monkeypatch.setattr(ansible_service, 'ANSIBLE_HVARS_DIR', str(tmp_path))

    hid = _make_linux_host(name='grouped-host', hostname='10.0.0.3')

    # create_group doesn't return the id, so we fetch it after creation
    ansible_repo.create_group('webservers', 'Web servers')
    import db as db_module
    conn = db_module.get_db()
    gid = conn.execute(
        "SELECT id FROM ansible_groups WHERE name='webservers'"
    ).fetchone()['id']
    conn.close()

    ansible_repo.set_host_groups(hid, [gid])

    result = ansible_service._generate_inventory()

    # Group name should appear as a children key
    assert 'webservers' in result
    # Host should appear in the result
    assert 'grouped-host' in result
    # children keyword must be present (host is under a group, not all.hosts)
    assert 'children' in result


def test_generate_inventory_become_vars(app_with_db, tmp_path, monkeypatch):
    """Host with become_method='sudo' → inventory includes become vars."""
    monkeypatch.setattr(ansible_service, 'ANSIBLE_INV_DIR', str(tmp_path))
    monkeypatch.setattr(ansible_service, 'ANSIBLE_GVARS_DIR', str(tmp_path))
    monkeypatch.setattr(ansible_service, 'ANSIBLE_HVARS_DIR', str(tmp_path))

    _make_linux_host(
        name='become-host',
        hostname='10.0.0.4',
        become_method='sudo',
        become_user='root',
        become_pass='rootpass',
        become_same=0,
    )

    result = ansible_service._generate_inventory()

    assert 'ansible_become: true' in result
    assert 'ansible_become_method: sudo' in result
    assert 'ansible_become_password: rootpass' in result


# ─────────────────────────────────────────────────────────────
# HOST CRUD TESTS
# ─────────────────────────────────────────────────────────────

def test_ansible_host_crud(app_with_db):
    """Create host → read by id → delete → read again returns None."""
    hid = ansible_repo.create_host(
        name='crud-test',
        hostname='192.168.1.1',
        os_type='linux',
        connection_type='ssh',
        ssh_port=22,
        winrm_port=None,
        winrm_scheme=None,
        ansible_user='admin',
        ansible_pass='pass',
        auth_type='password',
        ssh_key_path=None,
        win_domain=None,
        win_transport=None,
        become_method='none',
        become_user='root',
        become_pass='',
        become_same=0,
        vars_yaml='',
        notes='test',
        active=1,
    )
    assert hid is not None

    row = ansible_repo.get_host_by_id(hid)
    assert row is not None
    assert row['name'] == 'crud-test'

    ansible_repo.delete_host(hid)
    assert ansible_repo.get_host_by_id(hid) is None


# ─────────────────────────────────────────────────────────────
# ANSIBLE RUN EXECUTION TESTS
# ─────────────────────────────────────────────────────────────

def test_do_ansible_run_success(app_with_db):
    """subprocess.Popen returns exit_code 0 → run status is 'success' with exit_code 0."""
    run_id = ansible_repo.create_run(
        playbook_id=1,
        playbook_name='test.yml',
        inventory='',
        extra_vars='',
        limit_hosts='',
        tags_run='',
        triggered_by='test',
    )

    mock_proc = MagicMock()
    mock_proc.pid = 12345
    mock_proc.stdout = iter(['PLAY [all] ***\n', 'ok: [host1]\n'])
    mock_proc.wait.return_value = 0
    mock_proc.returncode = 0

    with patch.object(ansible_service, '_generate_inventory', return_value='---\nall: {}'), \
         patch('services.ansible.subprocess.Popen', return_value=mock_proc):
        ansible_service._do_ansible_run(run_id, '/path/test.yml', [])

    row = ansible_repo.get_run_by_id(run_id)
    assert row['status'] == 'success'
    assert row['exit_code'] == 0


def test_do_ansible_run_failure(app_with_db):
    """subprocess.Popen returns exit_code 2 → run status is 'failed' with exit_code 2."""
    run_id = ansible_repo.create_run(
        playbook_id=1,
        playbook_name='failing.yml',
        inventory='',
        extra_vars='',
        limit_hosts='',
        tags_run='',
        triggered_by='test',
    )

    mock_proc = MagicMock()
    mock_proc.pid = 22222
    mock_proc.stdout = iter(['PLAY [all] ***\n', 'fatal: [host1]\n'])
    mock_proc.wait.return_value = 2
    mock_proc.returncode = 2

    with patch.object(ansible_service, '_generate_inventory', return_value='---\nall: {}'), \
         patch('services.ansible.subprocess.Popen', return_value=mock_proc):
        ansible_service._do_ansible_run(run_id, '/path/failing.yml', [])

    row = ansible_repo.get_run_by_id(run_id)
    assert row['status'] == 'failed'
    assert row['exit_code'] == 2


def test_do_ansible_run_not_found(app_with_db):
    """subprocess.Popen raises FileNotFoundError → run status is 'failed' with exit_code -1."""
    run_id = ansible_repo.create_run(
        playbook_id=1,
        playbook_name='notfound.yml',
        inventory='',
        extra_vars='',
        limit_hosts='',
        tags_run='',
        triggered_by='test',
    )

    with patch.object(ansible_service, '_generate_inventory', return_value='---\nall: {}'), \
         patch('services.ansible.subprocess.Popen',
               side_effect=FileNotFoundError('ansible-playbook not found')):
        ansible_service._do_ansible_run(run_id, '/path/notfound.yml', [])

    row = ansible_repo.get_run_by_id(run_id)
    assert row['status'] == 'failed'
    assert row['exit_code'] == -1
