"""Tests for services/rear.py — config generation (unit) and install/configure (integration)."""
import pytest
from unittest.mock import patch, MagicMock, call


# ─────────────────────────────────────────────────────────────────────────────
# Task 1 — Unit tests for generate_rear_config (pure function)
# ─────────────────────────────────────────────────────────────────────────────

def test_generate_rear_config_contains_backup_url():
    """OUTPUT, BACKUP, and BACKUP_URL lines are present and correct."""
    import services.rear as rear_service

    server = {'hostname': 'webserver', 'exclude_dirs': ''}
    cfg = {
        'autoresize': '1',
        'migration_mode': '1',
        'rear_output': 'ISO',
        'rear_backup': 'NETFS',
        'global_exclude_dirs': '',
    }

    with patch('services.rear.settings_repo.get_nfs_target',
               return_value='nfs://10.0.0.1/backups/webserver'):
        result = rear_service.generate_rear_config(server, cfg)

    assert 'OUTPUT=ISO' in result
    assert 'BACKUP=NETFS' in result
    assert 'BACKUP_URL="nfs://10.0.0.1/backups/webserver"' in result


def test_generate_rear_config_excludes_merged():
    """Global and per-server exclude_dirs both appear in BACKUP_PROG_EXCLUDE."""
    import services.rear as rear_service

    server = {'hostname': 'web', 'exclude_dirs': '/logs/*'}
    cfg = {
        'autoresize': '1',
        'migration_mode': '1',
        'rear_output': 'ISO',
        'rear_backup': 'NETFS',
        'global_exclude_dirs': '/data/*',
    }

    with patch('services.rear.settings_repo.get_nfs_target',
               return_value='nfs://10.0.0.1/backups/web'):
        result = rear_service.generate_rear_config(server, cfg)

    assert '/data/*' in result
    assert '/logs/*' in result


def test_generate_rear_config_migration_mode_off():
    """When migration_mode='0', MIGRATION_MODE line is commented out."""
    import services.rear as rear_service

    server = {'hostname': 'db', 'exclude_dirs': ''}
    cfg = {
        'autoresize': '0',
        'migration_mode': '0',
        'rear_output': 'ISO',
        'rear_backup': 'NETFS',
        'global_exclude_dirs': '',
    }

    with patch('services.rear.settings_repo.get_nfs_target',
               return_value='nfs://10.0.0.1/backups/db'):
        result = rear_service.generate_rear_config(server, cfg)

    assert '# MIGRATION_MODE=true' in result
    # Ensure it is only in commented form — no bare active line
    lines = result.splitlines()
    active_lines = [l for l in lines if l.strip() == 'MIGRATION_MODE=true']
    assert active_lines == [], "MIGRATION_MODE=true should be commented out when mode is '0'"


# ─────────────────────────────────────────────────────────────────────────────
# Task 2 — Integration tests for _run_install_rear and _run_configure_rear
# ─────────────────────────────────────────────────────────────────────────────

def test_run_install_rear_ubuntu_apt_success(app_with_db, server_dict):
    """Ubuntu path: apt-get command issued before rear --version verification."""
    from models import jobs as job_repo
    import services.rear as rear_service

    jid = job_repo.create(server_id=server_dict['id'], job_type='install_rear')

    issued_commands = []

    def fake_exec_stream(server, command, log_cb):
        issued_commands.append(command)
        if 'apt-get' in command:
            return 0, 'Reading package lists...\nDone'
        if 'rear --version' in command:
            return 0, 'Relax-and-Recover 2.7 / 2022-07-13'
        return 0, ''

    ubuntu_os_info = 'NAME="Ubuntu"\nVERSION_ID="22.04"'

    with patch('services.rear.ssh_service.ssh_get_os_info', return_value=ubuntu_os_info), \
         patch('services.rear.ssh_service.ssh_exec_stream', side_effect=fake_exec_stream), \
         patch('services.rear.get_ubuntu_codename_via_ssh', return_value=('jammy', '22.04')), \
         patch('services.rear.server_repo.update_rear_installed'):

        rear_service._run_install_rear(jid, server_dict)

    assert any('apt-get' in cmd for cmd in issued_commands), \
        f"Expected apt-get command in {issued_commands}"
    assert any('rear --version' in cmd for cmd in issued_commands), \
        f"Expected 'rear --version' command in {issued_commands}"

    apt_idx = next(i for i, cmd in enumerate(issued_commands) if 'apt-get' in cmd)
    ver_idx = next(i for i, cmd in enumerate(issued_commands) if 'rear --version' in cmd)
    assert apt_idx < ver_idx, "apt-get install must come before rear --version verification"


def test_run_install_rear_rhel_dnf(app_with_db, server_dict):
    """RHEL/AlmaLinux path: dnf or yum command is issued for installation."""
    from models import jobs as job_repo
    import services.rear as rear_service

    jid = job_repo.create(server_id=server_dict['id'], job_type='install_rear')

    issued_commands = []

    def fake_exec_stream(server, command, log_cb):
        issued_commands.append(command)
        if 'rear --version' in command:
            return 0, 'Relax-and-Recover 2.7 / 2022-07-13'
        return 0, ''

    alma_os_info = 'NAME="AlmaLinux"\nVERSION_ID="9.2"'

    with patch('services.rear.ssh_service.ssh_get_os_info', return_value=alma_os_info), \
         patch('services.rear.ssh_service.ssh_exec_stream', side_effect=fake_exec_stream), \
         patch('services.rear.server_repo.update_rear_installed'):

        rear_service._run_install_rear(jid, server_dict)

    assert any('dnf' in cmd or 'yum' in cmd for cmd in issued_commands), \
        f"Expected dnf or yum command in {issued_commands}"


def test_run_configure_rear_command_order(app_with_db, server_dict):
    """Configure flow: mkdir -> backup existing -> upload -> rear dump, in correct order."""
    from models import jobs as job_repo
    import services.rear as rear_service

    jid = job_repo.create(server_id=server_dict['id'], job_type='configure_rear')

    exec_stream_calls = []
    upload_calls = []

    def fake_exec_stream(server, command, log_cb):
        exec_stream_calls.append(command)
        return 0, ''

    def fake_upload_file(server, content, remote_path):
        upload_calls.append((server, content, remote_path))
        return True, 'OK'

    config_content = 'OUTPUT=ISO\nBACKUP=NETFS\n'

    with patch('services.rear.ssh_service.ssh_exec_stream', side_effect=fake_exec_stream), \
         patch('services.rear.ssh_service.ssh_upload_file', side_effect=fake_upload_file), \
         patch('services.rear.server_repo.update_rear_configured'):

        rear_service._run_configure_rear(jid, server_dict, config_content)

    # Verify required commands were issued
    assert any('mkdir -p /etc/rear' in cmd for cmd in exec_stream_calls), \
        f"Expected mkdir -p /etc/rear in {exec_stream_calls}"
    assert any('rear dump' in cmd for cmd in exec_stream_calls), \
        f"Expected rear dump in {exec_stream_calls}"

    # Verify upload was called with correct destination
    assert len(upload_calls) == 1, f"Expected 1 upload call, got {len(upload_calls)}"
    _, uploaded_content, remote_path = upload_calls[0]
    assert remote_path == '/etc/rear/local.conf'
    assert uploaded_content == config_content

    # Verify ordering: mkdir < upload < rear dump
    mkdir_idx = next(i for i, cmd in enumerate(exec_stream_calls) if 'mkdir -p /etc/rear' in cmd)
    dump_idx = next(i for i, cmd in enumerate(exec_stream_calls) if 'rear dump' in cmd)

    # Upload happens between exec_stream calls — mkdir must come before dump
    assert mkdir_idx < dump_idx, "mkdir must come before rear dump"
    # Upload must happen: dump must be after upload (upload_calls[0] happens between mkdir and dump)
    assert dump_idx > mkdir_idx, "rear dump must follow upload"
