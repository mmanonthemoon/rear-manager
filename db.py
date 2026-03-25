import os
import sqlite3
import hashlib

from config import (
    DB_PATH, BASE_DIR, BUILTIN_ADMIN, BACKUP_ROOT, KEY_PATH,
    ANSIBLE_DIR, ANSIBLE_PLAYS_DIR, ANSIBLE_ROLES_DIR,
    ANSIBLE_FILES_DIR, ANSIBLE_INV_DIR, ANSIBLE_HVARS_DIR, ANSIBLE_GVARS_DIR,
)

try:
    from werkzeug.security import generate_password_hash, check_password_hash
    HAS_WERKZEUG = True
except ImportError:
    HAS_WERKZEUG = False
    def generate_password_hash(pw):
        return hashlib.sha256(pw.encode()).hexdigest()
    def check_password_hash(h, pw):
        return h == hashlib.sha256(pw.encode()).hexdigest()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()
    c.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT,
            full_name     TEXT DEFAULT '',
            role          TEXT NOT NULL DEFAULT 'user',
            auth_type     TEXT NOT NULL DEFAULT 'local',
            is_builtin    INTEGER NOT NULL DEFAULT 0,
            active        INTEGER NOT NULL DEFAULT 1,
            created_at    TEXT DEFAULT (datetime('now','localtime')),
            last_login    TEXT
        );

        CREATE TABLE IF NOT EXISTS servers (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            label             TEXT NOT NULL,
            hostname          TEXT NOT NULL,
            ip_address        TEXT NOT NULL,
            ssh_port          INTEGER DEFAULT 22,
            ssh_user          TEXT NOT NULL DEFAULT 'root',
            ssh_auth          TEXT NOT NULL DEFAULT 'password',
            ssh_password      TEXT,
            -- Become / privilege escalation
            become_method     TEXT NOT NULL DEFAULT 'none',
            become_user       TEXT NOT NULL DEFAULT 'root',
            become_password   TEXT DEFAULT '',
            become_same_pass  INTEGER NOT NULL DEFAULT 1,
            --
            rear_installed    INTEGER DEFAULT 0,
            rear_configured   INTEGER DEFAULT 0,
            os_type           TEXT,
            exclude_dirs      TEXT DEFAULT '',
            notes             TEXT DEFAULT '',
            ansible_host_id   INTEGER DEFAULT NULL,
            created_at        TEXT DEFAULT (datetime('now','localtime')),
            updated_at        TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY(ansible_host_id) REFERENCES ansible_hosts(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS schedules (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id    INTEGER NOT NULL,
            backup_type  TEXT DEFAULT 'mkbackup',
            cron_minute  TEXT DEFAULT '0',
            cron_hour    TEXT DEFAULT '2',
            cron_dom     TEXT DEFAULT '*',
            cron_month   TEXT DEFAULT '*',
            cron_dow     TEXT DEFAULT '*',
            enabled      INTEGER DEFAULT 1,
            last_run     TEXT,
            last_status  TEXT DEFAULT '',
            next_run     TEXT,
            created_at   TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY(server_id) REFERENCES servers(id)
        );

        CREATE TABLE IF NOT EXISTS backup_jobs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            server_id   INTEGER NOT NULL,
            schedule_id INTEGER,
            job_type    TEXT NOT NULL DEFAULT 'backup',
            status      TEXT NOT NULL DEFAULT 'pending',
            started_at  TEXT,
            finished_at TEXT,
            log_output  TEXT DEFAULT '',
            backup_size TEXT,
            iso_path    TEXT,
            triggered_by TEXT DEFAULT 'manual',
            FOREIGN KEY(server_id) REFERENCES servers(id)
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        -- ANSIBLE
        CREATE TABLE IF NOT EXISTS ansible_groups (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            description TEXT DEFAULT '',
            parent_id   INTEGER DEFAULT NULL,
            vars_yaml   TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now','localtime')),
            FOREIGN KEY(parent_id) REFERENCES ansible_groups(id)
        );

        CREATE TABLE IF NOT EXISTS ansible_hosts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL UNIQUE,
            hostname        TEXT NOT NULL,
            os_type         TEXT NOT NULL DEFAULT 'linux',
            -- Baglanti
            connection_type TEXT NOT NULL DEFAULT 'ssh',
            ssh_port        INTEGER DEFAULT 22,
            winrm_port      INTEGER DEFAULT 5985,
            winrm_scheme    TEXT DEFAULT 'http',
            -- Kimlik dogrulama
            ansible_user    TEXT DEFAULT '',
            ansible_pass    TEXT DEFAULT '',
            auth_type       TEXT DEFAULT 'password',
            ssh_key_path    TEXT DEFAULT '',
            -- Windows domain
            win_domain      TEXT DEFAULT '',
            win_transport   TEXT DEFAULT 'ntlm',
            -- Linux become
            become_method   TEXT DEFAULT 'none',
            become_user     TEXT DEFAULT 'root',
            become_pass     TEXT DEFAULT '',
            become_same     INTEGER DEFAULT 1,
            -- Ekstra
            vars_yaml       TEXT DEFAULT '',
            notes           TEXT DEFAULT '',
            active          INTEGER DEFAULT 1,
            created_at      TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS ansible_host_groups (
            host_id   INTEGER NOT NULL,
            group_id  INTEGER NOT NULL,
            PRIMARY KEY (host_id, group_id),
            FOREIGN KEY(host_id)  REFERENCES ansible_hosts(id),
            FOREIGN KEY(group_id) REFERENCES ansible_groups(id)
        );

        CREATE TABLE IF NOT EXISTS ansible_playbooks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            description TEXT DEFAULT '',
            content     TEXT NOT NULL DEFAULT '',
            tags        TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now','localtime')),
            updated_at  TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS ansible_runs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            playbook_id  INTEGER,
            playbook_name TEXT NOT NULL,
            inventory    TEXT NOT NULL DEFAULT 'all',
            extra_vars   TEXT DEFAULT '',
            limit_hosts  TEXT DEFAULT '',
            tags_run     TEXT DEFAULT '',
            status       TEXT NOT NULL DEFAULT 'pending',
            started_at   TEXT,
            finished_at  TEXT,
            output       TEXT DEFAULT '',
            exit_code    INTEGER,
            triggered_by TEXT DEFAULT 'manual',
            FOREIGN KEY(playbook_id) REFERENCES ansible_playbooks(id)
        );

        CREATE TABLE IF NOT EXISTS ansible_roles (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            description TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        );

        CREATE TABLE IF NOT EXISTS ansible_role_files (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            role_id   INTEGER NOT NULL,
            section   TEXT NOT NULL DEFAULT 'tasks',
            filename  TEXT NOT NULL DEFAULT 'main.yml',
            content   TEXT DEFAULT '',
            UNIQUE(role_id, section, filename),
            FOREIGN KEY(role_id) REFERENCES ansible_roles(id)
        );
    ''')

    # Built-in admin hesabi
    existing = c.execute(
        "SELECT id FROM users WHERE username=? AND is_builtin=1",
        (BUILTIN_ADMIN,)
    ).fetchone()
    if not existing:
        c.execute('''
            INSERT INTO users(username, password_hash, full_name, role, auth_type, is_builtin, active)
            VALUES(?,?,?,?,?,?,?)
        ''', (
            BUILTIN_ADMIN,
            generate_password_hash('admin123'),
            'Sistem Yoneticisi', 'admin', 'local', 1, 1
        ))

    # Varsayilan ayarlar
    defaults = {
        'central_ip':          _get_local_ip(),
        'nfs_export_path':     BACKUP_ROOT,
        'rear_output':         'ISO',
        'rear_backup':         'NETFS',
        'autoresize':          '1',
        'migration_mode':      '1',
        'global_exclude_dirs': '/data/cache/*\n/var/tmp/*',
        'ssh_key_path':        KEY_PATH,
        'ad_enabled':          '0',
        'ad_server':           '',
        'ad_port':             '389',
        'ad_domain':           '',
        'ad_base_dn':          '',
        'ad_bind_user':        '',
        'ad_bind_password':    '',
        'ad_user_filter':      '(sAMAccountName={username})',
        'ad_admin_group':      'ReaR-Admins',
        'ad_user_group':       'ReaR-Users',
        'retention_days':      '30',
        'session_timeout':     '480',
    }
    for k, v in defaults.items():
        c.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)', (k, v))

    conn.commit()
    conn.close()

    # Mevcut kurulumlar icin migration (yeni sutunlar)
    _migrate_db()


def _migrate_db():
    """Mevcut DB'ye eksik sutunlari ekler (idempotent)."""
    conn = get_db()
    existing_cols = [row[1] for row in conn.execute("PRAGMA table_info(servers)").fetchall()]
    migrations = [
        ("become_method",    "TEXT NOT NULL DEFAULT 'none'"),
        ("become_user",      "TEXT NOT NULL DEFAULT 'root'"),
        ("become_password",  "TEXT DEFAULT ''"),
        ("become_same_pass", "INTEGER NOT NULL DEFAULT 1"),
        ("ansible_host_id",  "INTEGER DEFAULT NULL"),
    ]
    for col, defn in migrations:
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE servers ADD COLUMN {col} {defn}")
    conn.commit()
    conn.close()

    _init_ansible_workspace()


def _init_ansible_workspace():
    """Ansible calisma dizinini ve temel dosyalari olusturur."""
    for d in [ANSIBLE_DIR, ANSIBLE_PLAYS_DIR, ANSIBLE_ROLES_DIR,
              ANSIBLE_FILES_DIR, ANSIBLE_INV_DIR,
              ANSIBLE_HVARS_DIR, ANSIBLE_GVARS_DIR]:
        os.makedirs(d, exist_ok=True)

    # ansible.cfg
    cfg_path = os.path.join(ANSIBLE_DIR, 'ansible.cfg')
    if not os.path.isfile(cfg_path):
        with open(cfg_path, 'w') as f:
            f.write(f"""[defaults]
inventory       = {ANSIBLE_INV_DIR}/hosts.yml
roles_path      = {ANSIBLE_ROLES_DIR}
host_key_checking = False
stdout_callback = default
callbacks_enabled = timer
retry_files_enabled = False
gathering = smart
fact_caching = memory
forks = 10
timeout = 30

[privilege_escalation]
become       = False
become_method = sudo
become_user  = root

[ssh_connection]
pipelining = True
ssh_args = -o ControlMaster=auto -o ControlPersist=60s -o StrictHostKeyChecking=no

[winrm]
""")

    # Ornek Linux playbook
    ex_linux = os.path.join(ANSIBLE_PLAYS_DIR, 'example-linux.yml')
    if not os.path.isfile(ex_linux):
        with open(ex_linux, 'w') as f:
            f.write("""---
# Ornek Linux Playbook
- name: Linux Sunucu Temel Kontroller
  hosts: linux
  become: yes
  gather_facts: yes

  tasks:
    - name: Sistem bilgisi al
      debug:
        msg: "{{ ansible_hostname }} - {{ ansible_distribution }} {{ ansible_distribution_version }}"

    - name: Disk kullanimi
      command: df -h /
      register: disk_info
      changed_when: false

    - name: Disk bilgisi goster
      debug:
        var: disk_info.stdout_lines

    - name: Servisleri kontrol et
      service_facts:

    - name: Bellek bilgisi
      debug:
        msg: "Toplam RAM: {{ ansible_memtotal_mb }} MB | Bos: {{ ansible_memfree_mb }} MB"
""")

    # Ornek Windows playbook
    ex_win = os.path.join(ANSIBLE_PLAYS_DIR, 'example-windows.yml')
    if not os.path.isfile(ex_win):
        with open(ex_win, 'w') as f:
            f.write("""---
# Ornek Windows Playbook
- name: Windows Sunucu Temel Kontroller
  hosts: windows
  gather_facts: yes

  tasks:
    - name: Sistem bilgisi
      debug:
        msg: "{{ ansible_hostname }} - {{ ansible_os_name }} {{ ansible_os_version }}"

    - name: Disk kullanimi
      win_disk_facts:

    - name: Disk bilgisi goster
      debug:
        var: ansible_disks

    - name: Servis listesi (calisan)
      win_service_info:
      register: svc_info

    - name: Calisan servis sayisi
      debug:
        msg: "Calisan servis sayisi: {{ svc_info.services | selectattr('state', 'eq', 'running') | list | length }}"

    - name: WinRM baglanti testi
      win_ping:
""")

    # group_vars/linux.yml
    gv_linux = os.path.join(ANSIBLE_GVARS_DIR, 'linux.yml')
    if not os.path.isfile(gv_linux):
        with open(gv_linux, 'w') as f:
            f.write("---\n# Linux grubu varsayilan degiskenler\nansible_connection: ssh\n")

    # group_vars/windows.yml
    gv_win = os.path.join(ANSIBLE_GVARS_DIR, 'windows.yml')
    if not os.path.isfile(gv_win):
        with open(gv_win, 'w') as f:
            f.write("""---
# Windows grubu varsayilan degiskenler
ansible_connection: winrm
ansible_winrm_server_cert_validation: ignore
ansible_winrm_operation_timeout_sec: 60
ansible_winrm_read_timeout_sec: 70
""")

    # common role
    common_tasks = os.path.join(ANSIBLE_ROLES_DIR, 'common', 'tasks')
    os.makedirs(common_tasks, exist_ok=True)
    common_main = os.path.join(common_tasks, 'main.yml')
    if not os.path.isfile(common_main):
        with open(common_main, 'w') as f:
            f.write("---\n# Common role tasks\n")

    for sub in ['handlers', 'templates', 'files', 'vars', 'defaults', 'meta']:
        os.makedirs(os.path.join(ANSIBLE_ROLES_DIR, 'common', sub), exist_ok=True)

    # .gitignore
    gitignore = os.path.join(ANSIBLE_DIR, '.gitignore')
    if not os.path.isfile(gitignore):
        with open(gitignore, 'w') as f:
            f.write("*.retry\n*.log\n__pycache__/\n")


def _get_local_ip():
    """Sunucunun yerel IP adresini doner."""
    import socket
    import subprocess as _sp
    try:
        r = _sp.run(['hostname', '-I'], capture_output=True, text=True, timeout=2)
        ips = r.stdout.strip().split()
        for ip in ips:
            if not ip.startswith('127.') and not ip.startswith('::1'):
                return ip
    except Exception:
        pass

    try:
        hostname = socket.gethostname()
        infos = socket.getaddrinfo(hostname, None, socket.AF_INET)
        for info in infos:
            ip = info[4][0]
            if not ip.startswith('127.'):
                return ip
    except Exception:
        pass

    try:
        r = _sp.run(['ip', 'route', 'show', 'default'],
                    capture_output=True, text=True, timeout=2)
        for part in r.stdout.split():
            if part.count('.') == 3 and not part.startswith('0.'):
                try:
                    socket.inet_aton(part)
                    if not part.startswith('127.'):
                        return part
                except Exception:
                    pass
    except Exception:
        pass

    return '127.0.0.1'
