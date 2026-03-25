"""Repository for the schedules table."""
from db import get_db


def get_by_server(sid):
    conn = get_db()
    rows = conn.execute(
        'SELECT * FROM schedules WHERE server_id=? ORDER BY id DESC', (sid,)
    ).fetchall()
    conn.close()
    return rows


def get_by_id(scid):
    conn = get_db()
    row = conn.execute('SELECT * FROM schedules WHERE id=?', (scid,)).fetchone()
    conn.close()
    return row


def get_all_enabled():
    conn = get_db()
    rows = conn.execute('SELECT * FROM schedules WHERE enabled=1').fetchall()
    conn.close()
    return rows


def create(server_id, backup_type, minute, hour, dom, month, dow):
    conn = get_db()
    c = conn.execute(
        '''INSERT INTO schedules(server_id, backup_type, cron_minute, cron_hour,
                                 cron_dom, cron_month, cron_dow, enabled)
           VALUES(?,?,?,?,?,?,?,1)''',
        (server_id, backup_type, minute, hour, dom, month, dow)
    )
    sched_id = c.lastrowid
    conn.commit()
    conn.close()
    return sched_id


def toggle(scid):
    conn = get_db()
    sched = conn.execute('SELECT * FROM schedules WHERE id=?', (scid,)).fetchone()
    if not sched:
        conn.close()
        return None
    new_state = 0 if sched['enabled'] else 1
    conn.execute('UPDATE schedules SET enabled=? WHERE id=?', (new_state, scid))
    conn.commit()
    conn.close()
    return sched, new_state


def delete(scid):
    conn = get_db()
    sched = conn.execute('SELECT * FROM schedules WHERE id=?', (scid,)).fetchone()
    if not sched:
        conn.close()
        return None
    conn.execute('DELETE FROM schedules WHERE id=?', (scid,))
    conn.commit()
    conn.close()
    return sched


def get_count():
    conn = get_db()
    row = conn.execute("SELECT COUNT(*) FROM schedules WHERE enabled=1").fetchone()
    conn.close()
    return row[0]


def update_last_run(scid, last_run, last_status):
    conn = get_db()
    conn.execute(
        "UPDATE schedules SET last_run=?, last_status=? WHERE id=?",
        (last_run, last_status, scid)
    )
    conn.commit()
    conn.close()
