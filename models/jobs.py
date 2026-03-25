"""Repository for the backup_jobs table."""
import datetime
from db import get_db


def get_all(limit=None):
    conn = get_db()
    query = '''
        SELECT j.*, s.label as server_label, s.hostname
        FROM backup_jobs j JOIN servers s ON s.id=j.server_id
        WHERE 1=1
    '''
    params = []
    if limit is not None:
        query += ' ORDER BY j.id DESC LIMIT ?'
        params.append(limit)
    else:
        query += ' ORDER BY j.id DESC LIMIT 300'
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return rows


def get_all_filtered(status_filter=None, type_filter=None, server_filter=None):
    conn = get_db()
    query = '''
        SELECT j.*, s.label as server_label, s.hostname
        FROM backup_jobs j JOIN servers s ON s.id=j.server_id
        WHERE 1=1
    '''
    params = []
    if status_filter:
        query += ' AND j.status=?'
        params.append(status_filter)
    if type_filter:
        query += ' AND j.job_type=?'
        params.append(type_filter)
    if server_filter:
        query += ' AND j.server_id=?'
        params.append(server_filter)
    query += ' ORDER BY j.id DESC LIMIT 300'
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return rows


def get_by_id(jid):
    conn = get_db()
    row = conn.execute(
        '''SELECT j.*, s.label as server_label, s.hostname, s.ip_address
           FROM backup_jobs j JOIN servers s ON s.id=j.server_id WHERE j.id=?''',
        (jid,)
    ).fetchone()
    conn.close()
    return row


def get_by_server(sid):
    conn = get_db()
    rows = conn.execute(
        'SELECT * FROM backup_jobs WHERE server_id=? ORDER BY id DESC LIMIT 20',
        (sid,)
    ).fetchall()
    conn.close()
    return rows


def get_log(jid):
    conn = get_db()
    row = conn.execute(
        'SELECT log_output, status, finished_at FROM backup_jobs WHERE id=?', (jid,)
    ).fetchone()
    conn.close()
    return row


def get_running_job_info(jid):
    conn = get_db()
    row = conn.execute(
        'SELECT j.id, j.job_type, j.started_at, s.label FROM backup_jobs j '
        'JOIN servers s ON s.id=j.server_id WHERE j.id=?', (jid,)
    ).fetchone()
    conn.close()
    return row


def get_server_id(jid):
    conn = get_db()
    row = conn.execute('SELECT server_id FROM backup_jobs WHERE id=?', (jid,)).fetchone()
    conn.close()
    return row


def create(server_id, job_type, triggered_by='manual', schedule_id=None):
    conn = get_db()
    c = conn.execute(
        "INSERT INTO backup_jobs(server_id, job_type, status, triggered_by, schedule_id) "
        "VALUES(?,?,?,?,?)",
        (server_id, job_type, 'pending', triggered_by, schedule_id)
    )
    job_id = c.lastrowid
    conn.commit()
    conn.close()
    return job_id


def update_status(jid, status, extra=None):
    conn = get_db()
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if status in ('success', 'failed', 'cancelled'):
        conn.execute(
            "UPDATE backup_jobs SET status=?, finished_at=? WHERE id=?",
            (status, ts, jid)
        )
    else:
        conn.execute("UPDATE backup_jobs SET status=? WHERE id=?", (status, jid))
    if extra:
        for k, v in extra.items():
            conn.execute(f"UPDATE backup_jobs SET {k}=? WHERE id=?", (v, jid))
    conn.commit()
    conn.close()


def set_started(jid):
    conn = get_db()
    conn.execute(
        "UPDATE backup_jobs SET started_at=? WHERE id=?",
        (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), jid)
    )
    conn.commit()
    conn.close()


def append_log(jid, text):
    """Append text to job log_output. Trims to last 500KB if over 2MB."""
    conn = get_db()
    row = conn.execute(
        "SELECT length(log_output) FROM backup_jobs WHERE id=?", (jid,)
    ).fetchone()
    current_size = row[0] if row and row[0] else 0

    if current_size > 2_000_000:
        conn.execute(
            "UPDATE backup_jobs SET log_output = "
            "'[... önceki loglar kırpıldı ...]\n' || substr(log_output, -500000) || ? "
            "WHERE id=?",
            (text + '\n', jid)
        )
    else:
        conn.execute(
            "UPDATE backup_jobs SET log_output = log_output || ? WHERE id=?",
            (text + '\n', jid)
        )
    conn.commit()
    conn.close()


def delete(jid):
    conn = get_db()
    conn.execute('DELETE FROM backup_jobs WHERE id=?', (jid,))
    conn.commit()
    conn.close()


def get_running_count():
    conn = get_db()
    row = conn.execute("SELECT COUNT(*) FROM backup_jobs WHERE status='running'").fetchone()
    conn.close()
    return row[0]


def get_recent(limit=12):
    conn = get_db()
    rows = conn.execute(
        '''SELECT j.*, s.label as server_label
           FROM backup_jobs j JOIN servers s ON s.id=j.server_id
           ORDER BY j.id DESC LIMIT ?''',
        (limit,)
    ).fetchall()
    conn.close()
    return rows


def get_stats():
    conn = get_db()
    stats = {
        'total_backups':   conn.execute("SELECT COUNT(*) FROM backup_jobs WHERE job_type='backup'").fetchone()[0],
        'success_backups': conn.execute("SELECT COUNT(*) FROM backup_jobs WHERE job_type='backup' AND status='success'").fetchone()[0],
        'failed_backups':  conn.execute("SELECT COUNT(*) FROM backup_jobs WHERE job_type='backup' AND status='failed'").fetchone()[0],
    }
    conn.close()
    return stats


def get_servers_list():
    conn = get_db()
    rows = conn.execute('SELECT id, label FROM servers ORDER BY label').fetchall()
    conn.close()
    return rows
