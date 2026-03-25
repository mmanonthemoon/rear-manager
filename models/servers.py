"""Repository for the servers table."""
from db import get_db


def get_all():
    conn = get_db()
    rows = conn.execute('SELECT * FROM servers ORDER BY label').fetchall()
    conn.close()
    return rows


def get_by_id(sid):
    conn = get_db()
    row = conn.execute('SELECT * FROM servers WHERE id=?', (sid,)).fetchone()
    conn.close()
    return row


def create(label, hostname, ip_address, ssh_port, ssh_user, ssh_auth, ssh_password,
           become_method, become_user, become_password, become_same_pass,
           exclude_dirs, notes):
    conn = get_db()
    cur = conn.execute(
        '''INSERT INTO servers(label, hostname, ip_address, ssh_port, ssh_user,
                               ssh_auth, ssh_password,
                               become_method, become_user, become_password, become_same_pass,
                               exclude_dirs, notes)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (label, hostname, ip_address, ssh_port, ssh_user, ssh_auth, ssh_password,
         become_method, become_user, become_password, become_same_pass,
         exclude_dirs, notes)
    )
    new_sid = cur.lastrowid
    conn.commit()
    row = conn.execute('SELECT * FROM servers WHERE id=?', (new_sid,)).fetchone()
    conn.close()
    return row


def update(sid, label, hostname, ip_address, ssh_port, ssh_user, ssh_auth, ssh_password,
           become_method, become_user, become_password, become_same_pass,
           exclude_dirs, notes):
    conn = get_db()
    conn.execute(
        '''UPDATE servers SET label=?, hostname=?, ip_address=?, ssh_port=?,
           ssh_user=?, ssh_auth=?, ssh_password=?,
           become_method=?, become_user=?, become_password=?, become_same_pass=?,
           exclude_dirs=?, notes=?,
           updated_at=datetime('now','localtime')
           WHERE id=?''',
        (label, hostname, ip_address, ssh_port, ssh_user, ssh_auth, ssh_password,
         become_method, become_user, become_password, become_same_pass,
         exclude_dirs, notes, sid)
    )
    conn.commit()
    conn.close()


def update_field(sid, **kwargs):
    conn = get_db()
    for k, v in kwargs.items():
        conn.execute(f"UPDATE servers SET {k}=?, updated_at=datetime('now','localtime') WHERE id=?", (v, sid))
    conn.commit()
    conn.close()


def update_rear_installed(sid, os_type):
    conn = get_db()
    conn.execute(
        "UPDATE servers SET rear_installed=1, os_type=?, updated_at=datetime('now','localtime') WHERE id=?",
        (os_type, sid)
    )
    conn.commit()
    conn.close()


def update_rear_configured(sid):
    conn = get_db()
    conn.execute(
        "UPDATE servers SET rear_configured=1, updated_at=datetime('now','localtime') WHERE id=?",
        (sid,)
    )
    conn.commit()
    conn.close()


def update_exclude_dirs(sid, exclude_dirs):
    conn = get_db()
    conn.execute("UPDATE servers SET exclude_dirs=? WHERE id=?", (exclude_dirs, sid))
    conn.commit()
    conn.close()


def delete(sid):
    conn = get_db()
    conn.execute('DELETE FROM schedules WHERE server_id=?', (sid,))
    conn.execute('DELETE FROM backup_jobs WHERE server_id=?', (sid,))
    conn.execute('DELETE FROM servers WHERE id=?', (sid,))
    conn.commit()
    conn.close()


def get_server_count():
    conn = get_db()
    row = conn.execute('SELECT COUNT(*) FROM servers').fetchone()
    conn.close()
    return row[0]


def get_dashboard_stats():
    conn = get_db()
    stats = {
        'total_servers':      conn.execute('SELECT COUNT(*) FROM servers').fetchone()[0],
        'installed_servers':  conn.execute('SELECT COUNT(*) FROM servers WHERE rear_installed=1').fetchone()[0],
        'configured_servers': conn.execute('SELECT COUNT(*) FROM servers WHERE rear_configured=1').fetchone()[0],
    }
    conn.close()
    return stats


def get_ansible_host_info(ansible_host_id):
    conn = get_db()
    row = conn.execute(
        'SELECT id, name FROM ansible_hosts WHERE id=?', (ansible_host_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def link_ansible_host(sid, ansible_host_id):
    conn = get_db()
    conn.execute(
        "UPDATE servers SET ansible_host_id=?, updated_at=datetime('now','localtime') WHERE id=?",
        (ansible_host_id, sid)
    )
    conn.commit()
    conn.close()


def unlink_ansible_host(sid):
    conn = get_db()
    conn.execute(
        "UPDATE servers SET ansible_host_id=NULL, updated_at=datetime('now','localtime') WHERE id=?",
        (sid,)
    )
    conn.commit()
    conn.close()


def check_exists_by_ip_or_hostname(ip, hostname):
    conn = get_db()
    row = conn.execute(
        'SELECT id FROM servers WHERE ip_address=? OR (hostname=? AND hostname != "")',
        (ip, hostname)
    ).fetchone()
    conn.close()
    return row


def bulk_create(servers_list):
    """Insert multiple servers in a single transaction. servers_list is a list of tuples.
    Each tuple: (label, hostname, ip, port, ssh_user, ssh_auth, ssh_pass,
                 bmethod, buser, bpass, bsame, notes)
    Returns (added_count, skipped_count, errors)."""
    conn = get_db()
    added = 0
    skipped = 0
    errors = []
    for item in servers_list:
        (label, hostname, ip, port, ssh_user, ssh_auth, ssh_pass,
         bmethod, buser, bpass, bsame, notes) = item
        exists = conn.execute(
            'SELECT id FROM servers WHERE ip_address=? OR (hostname=? AND hostname != "")',
            (ip, hostname)
        ).fetchone()
        if exists:
            errors.append(f"{ip} / {hostname} zaten mevcut, atlandı.")
            skipped += 1
            continue
        conn.execute(
            '''INSERT INTO servers(label, hostname, ip_address, ssh_port, ssh_user,
                                   ssh_auth, ssh_password,
                                   become_method, become_user, become_password, become_same_pass,
                                   notes)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
            (label, hostname, ip, port, ssh_user, ssh_auth, ssh_pass,
             bmethod, buser, bpass, bsame, notes)
        )
        added += 1
    conn.commit()
    conn.close()
    return added, skipped, errors


def find_existing_ansible_host(ip_address, hostname):
    conn = get_db()
    row = conn.execute(
        'SELECT id, name FROM ansible_hosts WHERE hostname=? OR hostname=?',
        (ip_address, hostname)
    ).fetchone()
    conn.close()
    return row


def check_ansible_name_taken(name):
    conn = get_db()
    row = conn.execute('SELECT name FROM ansible_hosts WHERE name=?', (name,)).fetchone()
    conn.close()
    return row is not None
