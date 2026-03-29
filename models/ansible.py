"""Repository for all ansible_* tables."""
import datetime
from db import get_db


# ── Hosts ────────────────────────────────────────────────────

def get_hosts():
    conn = get_db()
    rows = conn.execute('SELECT * FROM ansible_hosts ORDER BY name').fetchall()
    conn.close()
    return rows


def get_hosts_active():
    conn = get_db()
    rows = conn.execute('SELECT * FROM ansible_hosts WHERE active=1 ORDER BY name').fetchall()
    conn.close()
    return rows


def get_host_by_id(hid):
    conn = get_db()
    row = conn.execute('SELECT * FROM ansible_hosts WHERE id=?', (hid,)).fetchone()
    conn.close()
    return row


def get_hosts_with_groups():
    """Returns hosts, groups, and host_group mapping for list views."""
    conn = get_db()
    hosts  = conn.execute('SELECT * FROM ansible_hosts ORDER BY name').fetchall()
    groups = conn.execute('SELECT * FROM ansible_groups ORDER BY name').fetchall()
    hg     = conn.execute('SELECT * FROM ansible_host_groups').fetchall()
    conn.close()
    return hosts, groups, hg


def get_hosts_active_with_groups():
    """Returns active hosts, groups, and host_group mapping for inventory generation."""
    conn = get_db()
    hosts  = conn.execute('SELECT * FROM ansible_hosts WHERE active=1 ORDER BY name').fetchall()
    groups = conn.execute('SELECT * FROM ansible_groups ORDER BY name').fetchall()
    hg     = conn.execute('SELECT * FROM ansible_host_groups').fetchall()
    conn.close()
    return hosts, groups, hg


def create_host(name, hostname, os_type, connection_type, ssh_port, winrm_port, winrm_scheme,
                ansible_user, ansible_pass, auth_type, ssh_key_path,
                win_domain, win_transport, become_method, become_user, become_pass,
                become_same, vars_yaml, notes, active):
    conn = get_db()
    c = conn.execute(
        '''INSERT INTO ansible_hosts(name,hostname,os_type,connection_type,ssh_port,
             winrm_port,winrm_scheme,ansible_user,ansible_pass,auth_type,ssh_key_path,
             win_domain,win_transport,become_method,become_user,become_pass,become_same,
             vars_yaml,notes,active)
           VALUES(:name,:hostname,:os_type,:connection_type,:ssh_port,
             :winrm_port,:winrm_scheme,:ansible_user,:ansible_pass,:auth_type,:ssh_key_path,
             :win_domain,:win_transport,:become_method,:become_user,:become_pass,:become_same,
             :vars_yaml,:notes,:active)''',
        {'name': name, 'hostname': hostname, 'os_type': os_type, 'connection_type': connection_type,
         'ssh_port': ssh_port, 'winrm_port': winrm_port, 'winrm_scheme': winrm_scheme,
         'ansible_user': ansible_user, 'ansible_pass': ansible_pass, 'auth_type': auth_type,
         'ssh_key_path': ssh_key_path, 'win_domain': win_domain, 'win_transport': win_transport,
         'become_method': become_method, 'become_user': become_user, 'become_pass': become_pass,
         'become_same': become_same, 'vars_yaml': vars_yaml, 'notes': notes, 'active': active}
    )
    new_id = c.lastrowid
    conn.commit()
    conn.close()
    return new_id


def update_host(hid, fields_dict):
    conn = get_db()
    conn.execute(
        '''UPDATE ansible_hosts SET
             name=:name, hostname=:hostname, os_type=:os_type,
             connection_type=:connection_type, ssh_port=:ssh_port,
             winrm_port=:winrm_port, winrm_scheme=:winrm_scheme,
             ansible_user=:ansible_user, ansible_pass=:ansible_pass,
             auth_type=:auth_type, ssh_key_path=:ssh_key_path,
             win_domain=:win_domain, win_transport=:win_transport,
             become_method=:become_method, become_user=:become_user,
             become_pass=:become_pass, become_same=:become_same,
             vars_yaml=:vars_yaml, notes=:notes, active=:active
           WHERE id=:id''',
        {**fields_dict, 'id': hid}
    )
    conn.commit()
    conn.close()


def delete_host(hid):
    conn = get_db()
    h = conn.execute('SELECT name FROM ansible_hosts WHERE id=?', (hid,)).fetchone()
    conn.execute('DELETE FROM ansible_host_groups WHERE host_id=?', (hid,))
    conn.execute('DELETE FROM ansible_hosts WHERE id=?', (hid,))
    conn.commit()
    conn.close()
    return h


def get_host_groups(hid):
    conn = get_db()
    rows = conn.execute(
        'SELECT group_id FROM ansible_host_groups WHERE host_id=?', (hid,)
    ).fetchall()
    conn.close()
    return [r['group_id'] for r in rows]


def set_host_groups(hid, group_ids):
    conn = get_db()
    conn.execute('DELETE FROM ansible_host_groups WHERE host_id=?', (hid,))
    for gid in group_ids:
        conn.execute(
            'INSERT OR IGNORE INTO ansible_host_groups(host_id,group_id) VALUES(?,?)',
            (hid, int(gid))
        )
    conn.commit()
    conn.close()


def check_host_name_exists(name):
    conn = get_db()
    row = conn.execute('SELECT id FROM ansible_hosts WHERE name=?', (name,)).fetchone()
    conn.close()
    return row is not None


def get_host_id_by_name(name):
    conn = get_db()
    row = conn.execute('SELECT id FROM ansible_hosts WHERE name=?', (name,)).fetchone()
    conn.close()
    return row['id'] if row else None


def bulk_create_hosts(hosts_rows):
    """Insert multiple hosts. hosts_rows: list of param dicts for INSERT.
    Returns (added, skipped, errors)."""
    conn = get_db()
    added = 0
    skipped = 0
    errors = []
    group_map = {g['name'].lower(): g['id'] for g in
                 conn.execute('SELECT * FROM ansible_groups ORDER BY name').fetchall()}

    for item in hosts_rows:
        name = item['name']
        if conn.execute('SELECT id FROM ansible_hosts WHERE name=?', (name,)).fetchone():
            errors.append(f"'{name}' zaten mevcut, atlandı.")
            skipped += 1
            continue
        conn.execute(
            '''INSERT INTO ansible_hosts(
                   name, hostname, os_type, connection_type,
                   ssh_port, winrm_port, winrm_scheme,
                   ansible_user, ansible_pass, auth_type,
                   become_method, become_user, become_pass, become_same,
                   notes, active)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)''',
            (item['name'], item['hostname'], item['os_type'], item['connection_type'],
             item['ssh_port'], item['winrm_port'], item['winrm_scheme'],
             item['ansible_user'], item['ansible_pass'], item['auth_type'],
             item['become_method'], item['become_user'], item['become_pass'], item['become_same'],
             item['notes'])
        )
        new_id = conn.execute('SELECT id FROM ansible_hosts WHERE name=?', (name,)).fetchone()['id']
        grp_name = item.get('group_name', '').lower()
        if grp_name and grp_name in group_map:
            conn.execute(
                'INSERT OR IGNORE INTO ansible_host_groups(host_id, group_id) VALUES(?,?)',
                (new_id, group_map[grp_name])
            )
        added += 1

    conn.commit()
    conn.close()
    return added, skipped, errors


def link_server_to_host(sid, hid):
    conn = get_db()
    conn.execute(
        "UPDATE servers SET ansible_host_id=?, updated_at=datetime('now','localtime') WHERE id=?",
        (hid, sid)
    )
    conn.commit()
    conn.close()


def unlink_server_host(sid):
    conn = get_db()
    conn.execute(
        "UPDATE servers SET ansible_host_id=NULL, updated_at=datetime('now','localtime') WHERE id=?",
        (sid,)
    )
    conn.commit()
    conn.close()


def get_host_by_server(sid):
    """Find the ansible host linked to a server."""
    conn = get_db()
    server = conn.execute('SELECT ansible_host_id FROM servers WHERE id=?', (sid,)).fetchone()
    if not server or not server['ansible_host_id']:
        conn.close()
        return None
    row = conn.execute('SELECT * FROM ansible_hosts WHERE id=?', (server['ansible_host_id'],)).fetchone()
    conn.close()
    return row


def create_host_from_server(server_dict, host_name):
    """Create an ansible host from a server dict. Returns new host id."""
    conn = get_db()
    c = conn.execute(
        '''INSERT INTO ansible_hosts(
               name, hostname, os_type, connection_type,
               ssh_port, ansible_user, ansible_pass, auth_type,
               ssh_key_path, become_method, become_user, become_pass,
               become_same, active, notes
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (
            host_name,
            server_dict['ip_address'],
            'linux',
            'ssh',
            server_dict['ssh_port'] or 22,
            server_dict['ssh_user'],
            server_dict['ssh_password'] or '',
            server_dict['ssh_auth'] or 'password',
            '',
            server_dict['become_method'] or 'none',
            server_dict['become_user'] or 'root',
            server_dict['become_password'] or '',
            1 if server_dict['become_same_pass'] else 0,
            1,
            f"ReaR sunucusundan otomatik oluşturuldu: {server_dict['label']}"
        )
    )
    new_host_id = c.lastrowid
    conn.execute(
        "UPDATE servers SET ansible_host_id=?, updated_at=datetime('now','localtime') WHERE id=?",
        (new_host_id, server_dict['id'])
    )
    conn.commit()
    conn.close()
    return new_host_id


def get_existing_ansible_host_for_server(ip_address, hostname):
    conn = get_db()
    row = conn.execute(
        'SELECT id, name FROM ansible_hosts WHERE hostname=? OR hostname=?',
        (ip_address, hostname)
    ).fetchone()
    conn.close()
    return row


def get_linked_host_info(ansible_host_id):
    conn = get_db()
    row = conn.execute(
        'SELECT id, name FROM ansible_hosts WHERE id=?', (ansible_host_id,)
    ).fetchone()
    conn.close()
    return row


# ── Groups ───────────────────────────────────────────────────

def get_groups():
    conn = get_db()
    rows = conn.execute('SELECT * FROM ansible_groups ORDER BY name').fetchall()
    conn.close()
    return rows


def get_group_host_counts(groups):
    conn = get_db()
    hcounts = {}
    for g in groups:
        cnt = conn.execute(
            'SELECT COUNT(*) FROM ansible_host_groups WHERE group_id=?', (g['id'],)
        ).fetchone()[0]
        hcounts[g['id']] = cnt
    conn.close()
    return hcounts


def create_group(name, description):
    conn = get_db()
    conn.execute(
        'INSERT INTO ansible_groups(name,description) VALUES(?,?)', (name, description)
    )
    conn.commit()
    conn.close()


def delete_group(gid):
    conn = get_db()
    conn.execute('DELETE FROM ansible_host_groups WHERE group_id=?', (gid,))
    conn.execute('DELETE FROM ansible_groups WHERE id=?', (gid,))
    conn.commit()
    conn.close()


def save_group_vars(gid, vars_yaml):
    conn = get_db()
    conn.execute('UPDATE ansible_groups SET vars_yaml=? WHERE id=?', (vars_yaml, gid))
    conn.commit()
    conn.close()


def add_host_to_group(host_id, group_id):
    conn = get_db()
    conn.execute(
        'INSERT OR IGNORE INTO ansible_host_groups(host_id, group_id) VALUES(?,?)',
        (host_id, group_id)
    )
    conn.commit()
    conn.close()


# ── Playbooks ────────────────────────────────────────────────

def get_playbooks():
    conn = get_db()
    rows = conn.execute('SELECT * FROM ansible_playbooks ORDER BY name').fetchall()
    conn.close()
    return rows


def get_playbook_by_id(pid):
    conn = get_db()
    row = conn.execute('SELECT * FROM ansible_playbooks WHERE id=?', (pid,)).fetchone()
    conn.close()
    return row


def get_playbook_last_run(pid):
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM ansible_runs WHERE playbook_id=? ORDER BY id DESC LIMIT 1", (pid,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_group_names():
    conn = get_db()
    rows = conn.execute('SELECT name FROM ansible_groups ORDER BY name').fetchall()
    conn.close()
    return rows


def get_host_names_active():
    conn = get_db()
    rows = conn.execute('SELECT name FROM ansible_hosts WHERE active=1 ORDER BY name').fetchall()
    conn.close()
    return rows


def create_playbook(name, description, content, tags):
    conn = get_db()
    c = conn.execute(
        'INSERT INTO ansible_playbooks(name, description, content, tags) VALUES(?,?,?,?)',
        (name, description, content, tags)
    )
    new_id = c.lastrowid
    conn.commit()
    conn.close()
    return new_id


def update_playbook(pid, name, description, content, tags):
    conn = get_db()
    conn.execute(
        '''UPDATE ansible_playbooks SET name=?, description=?, content=?, tags=?,
           updated_at=datetime('now','localtime') WHERE id=?''',
        (name, description, content, tags, pid)
    )
    conn.commit()
    conn.close()


def delete_playbook(pid):
    conn = get_db()
    pb = conn.execute('SELECT * FROM ansible_playbooks WHERE id=?', (pid,)).fetchone()
    conn.execute('DELETE FROM ansible_playbooks WHERE id=?', (pid,))
    conn.commit()
    conn.close()
    return pb


# ── Runs ─────────────────────────────────────────────────────

def get_runs(offset=0, limit=25):
    conn = get_db()
    rows = conn.execute(
        '''SELECT r.*, p.name as playbook_name
           FROM ansible_runs r
           LEFT JOIN ansible_playbooks p ON p.id = r.playbook_id
           ORDER BY r.id DESC LIMIT ? OFFSET ?''',
        (limit, offset)
    ).fetchall()
    total = conn.execute('SELECT COUNT(*) FROM ansible_runs').fetchone()[0]
    conn.close()
    return rows, total


def get_recent_runs(limit=15):
    conn = get_db()
    rows = conn.execute(
        'SELECT * FROM ansible_runs ORDER BY id DESC LIMIT ?', (limit,)
    ).fetchall()
    conn.close()
    return rows


def get_run_by_id(rid):
    conn = get_db()
    row = conn.execute('SELECT * FROM ansible_runs WHERE id=?', (rid,)).fetchone()
    conn.close()
    return row


def get_run_status(rid):
    conn = get_db()
    row = conn.execute(
        'SELECT status, exit_code, started_at, finished_at, '
        'length(output) as out_len FROM ansible_runs WHERE id=?', (rid,)
    ).fetchone()
    conn.close()
    return row


def get_run_output(rid, offset=0):
    conn = get_db()
    row = conn.execute(
        'SELECT substr(output,?) as chunk, status, finished_at FROM ansible_runs WHERE id=?',
        (offset + 1, rid)
    ).fetchone()
    conn.close()
    return row


def create_run(playbook_id, playbook_name, inventory, extra_vars, limit_hosts, tags_run, triggered_by):
    conn = get_db()
    c = conn.execute(
        '''INSERT INTO ansible_runs(playbook_id, playbook_name, inventory, extra_vars,
                                    limit_hosts, tags_run, status, triggered_by)
           VALUES(?,?,?,?,?,?,?,?)''',
        (playbook_id, playbook_name, inventory, extra_vars, limit_hosts, tags_run, 'pending', triggered_by)
    )
    run_id = c.lastrowid
    conn.commit()
    conn.close()
    return run_id


def update_run_status(rid, status, exit_code=None):
    conn = get_db()
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if status in ('success', 'failed', 'cancelled'):
        conn.execute(
            "UPDATE ansible_runs SET status=?, finished_at=?, exit_code=? WHERE id=?",
            (status, ts, exit_code, rid)
        )
    else:
        conn.execute("UPDATE ansible_runs SET status=? WHERE id=?", (status, rid))
    conn.commit()
    conn.close()


def set_run_started(rid):
    conn = get_db()
    conn.execute(
        "UPDATE ansible_runs SET started_at=? WHERE id=?",
        (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), rid)
    )
    conn.commit()
    conn.close()


def append_run_log(rid, text):
    """Append text to run output. Hard-caps total at 1 MB (FEAT-03)."""
    from utils import truncate_output
    conn = get_db()
    row = conn.execute(
        "SELECT output FROM ansible_runs WHERE id=?", (rid,)
    ).fetchone()
    existing = (row['output'] or '') if row else ''
    combined = existing + text + '\n'
    combined = truncate_output(combined, max_bytes=1_000_000)
    conn.execute(
        "UPDATE ansible_runs SET output=? WHERE id=?",
        (combined, rid)
    )
    conn.commit()
    conn.close()


def append_run_output_raw(rid, text):
    """Append raw text to run output (e.g. PID line)."""
    conn = get_db()
    conn.execute(
        "UPDATE ansible_runs SET output=output||? WHERE id=?",
        (text, rid)
    )
    conn.commit()
    conn.close()


def delete_run(rid):
    conn = get_db()
    conn.execute('DELETE FROM ansible_runs WHERE id=?', (rid,))
    conn.commit()
    conn.close()


# ── Roles ────────────────────────────────────────────────────

def get_roles():
    conn = get_db()
    rows = conn.execute(
        '''SELECT r.*,
                  (SELECT COUNT(*) FROM ansible_role_files f WHERE f.role_id=r.id) as file_count
           FROM ansible_roles r
           ORDER BY r.name'''
    ).fetchall()
    conn.close()
    return rows


def get_role_by_id(rid):
    conn = get_db()
    role  = conn.execute('SELECT * FROM ansible_roles WHERE id=?', (rid,)).fetchone()
    files = conn.execute(
        "SELECT * FROM ansible_role_files WHERE role_id=? ORDER BY section,filename", (rid,)
    ).fetchall()
    conn.close()
    return role, files


def create_role(name, description):
    conn = get_db()
    c = conn.execute(
        'INSERT INTO ansible_roles(name,description) VALUES(?,?)', (name, description)
    )
    role_id = c.lastrowid
    for section, fname, fcontent in [
        ('tasks',    'main.yml', f'---\n# Tasks for role: {name}\n'),
        ('handlers', 'main.yml', f'---\n# Handlers for role: {name}\n'),
        ('vars',     'main.yml', f'---\n# Variables for role: {name}\n'),
        ('defaults', 'main.yml', f'---\n# Default variables for role: {name}\n'),
        ('meta',     'main.yml', '---\ndependencies: []\n'),
    ]:
        conn.execute(
            'INSERT INTO ansible_role_files(role_id,section,filename,content) VALUES(?,?,?,?)',
            (role_id, section, fname, fcontent)
        )
    conn.commit()
    conn.close()
    return role_id


def get_role_files(rid):
    conn = get_db()
    rows = conn.execute(
        'SELECT * FROM ansible_role_files WHERE role_id=?', (rid,)
    ).fetchall()
    conn.close()
    return rows


def update_role_file(fid, role_id, content):
    conn = get_db()
    conn.execute(
        'UPDATE ansible_role_files SET content=? WHERE id=? AND role_id=?',
        (content, fid, role_id)
    )
    conn.commit()
    conn.close()


def create_role_file(role_id, section, filename, content):
    conn = get_db()
    conn.execute(
        '''INSERT INTO ansible_role_files(role_id,section,filename,content) VALUES(?,?,?,?)''',
        (role_id, section, filename, content)
    )
    conn.commit()
    conn.close()


def delete_role(rid):
    conn = get_db()
    role = conn.execute('SELECT name FROM ansible_roles WHERE id=?', (rid,)).fetchone()
    conn.execute('DELETE FROM ansible_role_files WHERE role_id=?', (rid,))
    conn.execute('DELETE FROM ansible_roles WHERE id=?', (rid,))
    conn.commit()
    conn.close()
    return role


def get_role_for_disk_sync(role_id):
    conn = get_db()
    role  = conn.execute('SELECT * FROM ansible_roles WHERE id=?', (role_id,)).fetchone()
    files = conn.execute('SELECT * FROM ansible_role_files WHERE role_id=?', (role_id,)).fetchall()
    conn.close()
    return role, files


# ── Dashboard stats ──────────────────────────────────────────

def get_host_count():
    conn = get_db()
    count = conn.execute('SELECT COUNT(*) FROM ansible_hosts WHERE active=1').fetchone()[0]
    conn.close()
    return count


def get_group_count():
    conn = get_db()
    count = conn.execute('SELECT COUNT(*) FROM ansible_groups').fetchone()[0]
    conn.close()
    return count


def get_playbook_count():
    conn = get_db()
    count = conn.execute('SELECT COUNT(*) FROM ansible_playbooks').fetchone()[0]
    conn.close()
    return count


def get_run_count():
    conn = get_db()
    count = conn.execute('SELECT COUNT(*) FROM ansible_runs').fetchone()[0]
    conn.close()
    return count


def get_dashboard_stats():
    conn = get_db()
    stats = {
        'total_hosts':     conn.execute('SELECT COUNT(*) FROM ansible_hosts WHERE active=1').fetchone()[0],
        'total_groups':    conn.execute('SELECT COUNT(*) FROM ansible_groups').fetchone()[0],
        'total_playbooks': conn.execute('SELECT COUNT(*) FROM ansible_playbooks').fetchone()[0],
        'total_roles':     conn.execute('SELECT COUNT(*) FROM ansible_roles').fetchone()[0],
        'total_runs':      conn.execute('SELECT COUNT(*) FROM ansible_runs').fetchone()[0],
        'success_runs':    conn.execute("SELECT COUNT(*) FROM ansible_runs WHERE status='success'").fetchone()[0],
        'failed_runs':     conn.execute("SELECT COUNT(*) FROM ansible_runs WHERE status='failed'").fetchone()[0],
        'running_runs':    conn.execute("SELECT COUNT(*) FROM ansible_runs WHERE status='running'").fetchone()[0],
    }
    conn.close()
    return stats
