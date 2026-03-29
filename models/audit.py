"""Audit log repository — immutable record of user-triggered actions."""

from db import get_db


def log_action(username, action, resource_id=None, resource_type=None, details=''):
    """Insert an audit event. Call AFTER the resource is committed to DB."""
    conn = get_db()
    conn.execute(
        '''INSERT INTO audit_log(username, action, resource_id, resource_type, details)
           VALUES(?, ?, ?, ?, ?)''',
        (username, action, resource_id, resource_type, details)
    )
    conn.commit()
    conn.close()


def get_audit_log(limit=100, offset=0):
    """Return audit log rows ordered by most recent first."""
    conn = get_db()
    rows = conn.execute(
        '''SELECT * FROM audit_log ORDER BY created_at DESC LIMIT ? OFFSET ?''',
        (limit, offset)
    ).fetchall()
    conn.close()
    return rows
