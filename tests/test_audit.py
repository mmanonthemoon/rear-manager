"""Tests for audit log table schema and route-level capture."""
import pytest


def test_audit_table_schema(app_with_db):
    """audit_log table exists and has required columns after init_db."""
    import db as db_module
    conn = db_module.get_db()
    rows = conn.execute("PRAGMA table_info(audit_log)").fetchall()
    conn.close()
    col_names = [r['name'] for r in rows]
    assert 'id' in col_names
    assert 'username' in col_names
    assert 'action' in col_names
    assert 'resource_id' in col_names
    assert 'resource_type' in col_names
    assert 'details' in col_names
    assert 'created_at' in col_names


def test_log_action_inserts_row(app_with_db):
    """log_action inserts a row into audit_log with correct fields."""
    from models import audit as audit_repo
    audit_repo.log_action(
        username='testuser',
        action='backup_job_started',
        resource_id=42,
        resource_type='backup_job',
        details='Server ID 1'
    )
    rows = audit_repo.get_audit_log(limit=10)
    assert len(rows) >= 1
    row = rows[0]
    assert row['username'] == 'testuser'
    assert row['action'] == 'backup_job_started'
    assert row['resource_id'] == 42
    assert row['resource_type'] == 'backup_job'


def test_get_audit_log_returns_list(app_with_db):
    """get_audit_log returns a list (possibly empty)."""
    from models import audit as audit_repo
    rows = audit_repo.get_audit_log()
    assert isinstance(rows, list)


def test_backup_trigger_logs_audit(app_with_db):
    """Placeholder — full integration test added in Task 2 after route capture."""
    # This test verifies audit_repo.log_action works when called from route context
    from models import audit as audit_repo
    import db as db_module
    audit_repo.log_action(username='admin', action='backup_job_started',
                          resource_id=1, resource_type='backup_job', details='integration')
    conn = db_module.get_db()
    row = conn.execute(
        "SELECT * FROM audit_log WHERE action='backup_job_started' AND username='admin'"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row['username'] == 'admin'


def test_ansible_run_logs_audit(app_with_db):
    """Placeholder — verifies audit_repo.log_action works for ansible_run_started action."""
    from models import audit as audit_repo
    import db as db_module
    audit_repo.log_action(username='admin', action='ansible_run_started',
                          resource_id=5, resource_type='ansible_run', details='playbook test')
    conn = db_module.get_db()
    row = conn.execute(
        "SELECT * FROM audit_log WHERE action='ansible_run_started' AND resource_id=5"
    ).fetchone()
    conn.close()
    assert row is not None
    assert row['resource_type'] == 'ansible_run'
