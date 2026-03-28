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
