#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ReaR Manager v2.0 - Merkezi ReaR Yedekleme Yönetim Paneli
Özellikler:
  - Çoklu hedef sunucu yönetimi (SSH)
  - Ayrı/merkezi NFS sunucusu seçeneği
  - Per-server ve global hariç tutma dizinleri
  - Zamanlanmış yedekleme (APScheduler)
  - Local + Active Directory kimlik doğrulama
  - Silinemeyen built-in admin hesabı
"""

import os, sqlite3, threading, time, json, datetime, socket, shlex, re
import subprocess, traceback, hashlib, secrets, functools
from flask import (Flask, render_template, request, redirect,
                   url_for, flash, jsonify, session, g)

try:
    import paramiko
    HAS_PARAMIKO = True
except ImportError:
    HAS_PARAMIKO = False

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    HAS_SCHEDULER = True
except ImportError:
    HAS_SCHEDULER = False

try:
    from ldap3 import Server as LdapServer, Connection as LdapConn, ALL, NTLM, SIMPLE
    from ldap3.core.exceptions import LDAPException
    HAS_LDAP = True
except ImportError:
    HAS_LDAP = False

try:
    from werkzeug.security import generate_password_hash, check_password_hash
    HAS_WERKZEUG = True
except ImportError:
    HAS_WERKZEUG = False
    def generate_password_hash(pw):
        return hashlib.sha256(pw.encode()).hexdigest()
    def check_password_hash(h, pw):
        return h == hashlib.sha256(pw.encode()).hexdigest()

# ─────────────────────────────────────────────────────────────
# SABITLER VE UYGULAMA
# ─────────────────────────────────────────────────────────────
BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
DB_PATH     = os.path.join(BASE_DIR, 'rear_manager.db')
BACKUP_ROOT = '/srv/rear-backups'
KEY_PATH    = os.path.expanduser('~/.ssh/rear_manager_rsa')
BUILTIN_ADMIN   = 'admin'
OFFLINE_PKG_DIR = os.path.join(BASE_DIR, 'offline-packages')

# Ubuntu codename → sürüm numarası eşleşmesi
UBUNTU_CODENAMES = {
    'focal':  '20.04',
    'jammy':  '22.04',
    'noble':  '24.04',
    'plucky': '25.04',
}

# ── Ansible sabitleri ──────────────────────────────────────────
ANSIBLE_DIR       = os.path.join(BASE_DIR, 'ansible')
ANSIBLE_PLAYS_DIR = os.path.join(ANSIBLE_DIR, 'playbooks')
ANSIBLE_ROLES_DIR = os.path.join(ANSIBLE_DIR, 'roles')
ANSIBLE_FILES_DIR = os.path.join(ANSIBLE_DIR, 'files')
ANSIBLE_INV_DIR   = os.path.join(ANSIBLE_DIR, 'inventories')
ANSIBLE_HVARS_DIR = os.path.join(ANSIBLE_DIR, 'host_vars')
ANSIBLE_GVARS_DIR = os.path.join(ANSIBLE_DIR, 'group_vars')

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)

_running_jobs = {}
_job_lock     = threading.Lock()
_scheduler    = None


def _cron_describe(minute, hour, dom, month, dow):
    """Cron ifadesini insan okunabilir Türkçe metne çevirir."""
    try:
        m  = str(minute or '*').strip()
        h  = str(hour or '*').strip()
        d  = str(dom or '*').strip()
        mo = str(month or '*').strip()
        dw = str(dow or '*').strip()

        gun_adlari = {
            '0': 'Pazar', '1': 'Pazartesi', '2': 'Salı', '3': 'Çarşamba',
            '4': 'Perşembe', '5': 'Cuma', '6': 'Cumartesi', '7': 'Pazar',
            '1-5': 'Hft içi', '0-4': 'Pzt-Per', '0,6': 'Hft sonu', '6,0': 'Hft sonu',
        }
        ay_adlari = {
            '1': 'Oca', '2': 'Şub', '3': 'Mar', '4': 'Nis',
            '5': 'May', '6': 'Haz', '7': 'Tem', '8': 'Ağu',
            '9': 'Eyl', '10': 'Eki', '11': 'Kas', '12': 'Ara',
        }

        # Her X saatte bir: "0 */N * * *"
        if h.startswith('*/') and m == '0' and d == '*' and mo == '*' and dw == '*':
            return f'Her {h[2:]} saatte'
        # Her X dakikada bir: "*/N * * * *"
        if m.startswith('*/') and h == '*' and d == '*' and mo == '*' and dw == '*':
            return f'Her {m[2:]} dakikada'
        # Sabit saat — her gün
        if m.isdigit() and h.isdigit() and d == '*' and mo == '*' and dw == '*':
            return f'Her gün {h.zfill(2)}:{m.zfill(2)}'
        # Sabit saat — belirli haftanın günü
        if m.isdigit() and h.isdigit() and d == '*' and mo == '*' and dw in gun_adlari:
            return f'Her {gun_adlari[dw]} {h.zfill(2)}:{m.zfill(2)}'
        # Sabit saat — ayın belirli günü
        if m.isdigit() and h.isdigit() and d.isdigit() and mo == '*' and dw == '*':
            return f'Her ay {d}. gün {h.zfill(2)}:{m.zfill(2)}'
        # Sabit saat — belirli ay ve gün
        if m.isdigit() and h.isdigit() and d.isdigit() and mo in ay_adlari and dw == '*':
            return f'Her yıl {ay_adlari[mo]} {d}. gün {h.zfill(2)}:{m.zfill(2)}'
        return f'{m} {h} {d} {mo} {dw}'
    except Exception:
        return ''


app.jinja_env.globals['_cron_describe'] = _cron_describe


@app.template_filter('calc_duration')
def calc_duration_filter(started_at, finished_at):
    """İki tarih string'i arasındaki süreyi insan okunabilir formatta döner."""
    if not started_at or not finished_at:
        return '-'
    try:
        fmt = '%Y-%m-%d %H:%M:%S'
        start = datetime.datetime.strptime(str(started_at)[:19], fmt)
        end   = datetime.datetime.strptime(str(finished_at)[:19], fmt)
        secs  = int((end - start).total_seconds())
        if secs < 0:
            return '-'
        if secs < 60:
            return f'{secs}s'
        elif secs < 3600:
            return f'{secs // 60}m {secs % 60}s'
        else:
            h = secs // 3600
            m = (secs % 3600) // 60
            return f'{h}h {m}m'
    except Exception:
        return '-'


# ─────────────────────────────────────────────────────────────
# VERİTABANI
# ─────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
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

        -- ═══════════════ ANSIBLE ═══════════════
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
            -- Bağlantı
            connection_type TEXT NOT NULL DEFAULT 'ssh',
            ssh_port        INTEGER DEFAULT 22,
            winrm_port      INTEGER DEFAULT 5985,
            winrm_scheme    TEXT DEFAULT 'http',
            -- Kimlik doğrulama
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

    # ── Built-in admin hesabı ──────────────────────────────
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
            'Sistem Yöneticisi', 'admin', 'local', 1, 1
        ))

    # ── Varsayılan ayarlar ────────────────────────────────
    defaults = {
        'central_ip':          _get_local_ip(),
        # NFS ayarları
        'nfs_mode':            'central',       # 'central' | 'separate'
        'nfs_server_ip':       '',              # ayrı NFS sunucusu
        'nfs_export_path':     BACKUP_ROOT,
        'nfs_options':         'rw,sync,no_subtree_check,no_root_squash',
        # ReaR varsayılanları
        'rear_output':         'ISO',
        'rear_backup':         'NETFS',
        'autoresize':          '1',
        'migration_mode':      '1',
        'global_exclude_dirs': '/data/cache/*\n/var/tmp/*',
        # SSH
        'ssh_key_path':        KEY_PATH,
        # AD ayarları
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
        # Diğer
        'retention_days':      '30',
        'session_timeout':     '480',  # dakika
    }
    for k, v in defaults.items():
        c.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)', (k, v))

    conn.commit()
    conn.close()

    # ── Mevcut kurulumlar için migration (yeni sütunlar) ──────
    _migrate_db()


def _migrate_db():
    """Mevcut DB'ye eksik sütunları ekler (idempotent)."""
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
    """Ansible çalışma dizinini ve temel dosyaları oluşturur."""
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

    # Örnek Linux playbook
    ex_linux = os.path.join(ANSIBLE_PLAYS_DIR, 'example-linux.yml')
    if not os.path.isfile(ex_linux):
        with open(ex_linux, 'w') as f:
            f.write("""---
# Örnek Linux Playbook
- name: Linux Sunucu Temel Kontroller
  hosts: linux
  become: yes
  gather_facts: yes

  tasks:
    - name: Sistem bilgisi al
      debug:
        msg: "{{ ansible_hostname }} - {{ ansible_distribution }} {{ ansible_distribution_version }}"

    - name: Disk kullanımı
      command: df -h /
      register: disk_info
      changed_when: false

    - name: Disk bilgisi göster
      debug:
        var: disk_info.stdout_lines

    - name: Servisleri kontrol et
      service_facts:

    - name: Bellek bilgisi
      debug:
        msg: "Toplam RAM: {{ ansible_memtotal_mb }} MB | Boş: {{ ansible_memfree_mb }} MB"
""")

    # Örnek Windows playbook
    ex_win = os.path.join(ANSIBLE_PLAYS_DIR, 'example-windows.yml')
    if not os.path.isfile(ex_win):
        with open(ex_win, 'w') as f:
            f.write("""---
# Örnek Windows Playbook
- name: Windows Sunucu Temel Kontroller
  hosts: windows
  gather_facts: yes

  tasks:
    - name: Sistem bilgisi
      debug:
        msg: "{{ ansible_hostname }} - {{ ansible_os_name }} {{ ansible_os_version }}"

    - name: Disk kullanımı
      win_disk_facts:

    - name: Disk bilgisi göster
      debug:
        var: ansible_disks

    - name: Servis listesi (çalışan)
      win_service_info:
      register: svc_info

    - name: Çalışan servis sayısı
      debug:
        msg: "Çalışan servis sayısı: {{ svc_info.services | selectattr('state', 'eq', 'running') | list | length }}"

    - name: WinRM bağlantı testi
      win_ping:
""")

    # group_vars/linux.yml
    gv_linux = os.path.join(ANSIBLE_GVARS_DIR, 'linux.yml')
    if not os.path.isfile(gv_linux):
        with open(gv_linux, 'w') as f:
            f.write("---\n# Linux grubu varsayılan değişkenleri\nansible_connection: ssh\n")

    # group_vars/windows.yml
    gv_win = os.path.join(ANSIBLE_GVARS_DIR, 'windows.yml')
    if not os.path.isfile(gv_win):
        with open(gv_win, 'w') as f:
            f.write("""---
# Windows grubu varsayılan değişkenleri
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


def get_settings():
    conn = get_db()
    rows = conn.execute('SELECT key, value FROM settings').fetchall()
    conn.close()
    return {r['key']: r['value'] for r in rows}


def save_setting(key, value):
    conn = get_db()
    conn.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', (key, value))
    conn.commit()
    conn.close()


def _get_local_ip():
    """
    Sunucunun yerel IP adresini döner.
    Offline ortam için güvenli: dışarıya bağlantı denemez.
    """
    # 1. Önce hostname -I ile dene (Linux'ta güvenilir)
    try:
        import subprocess as _sp
        r = _sp.run(['hostname', '-I'], capture_output=True, text=True, timeout=2)
        ips = r.stdout.strip().split()
        # Loopback olmayan ilk IP'yi seç
        for ip in ips:
            if not ip.startswith('127.') and not ip.startswith('::1'):
                return ip
    except Exception:
        pass

    # 2. socket.getaddrinfo ile kendi hostname'imizi çöz
    try:
        hostname = socket.gethostname()
        infos = socket.getaddrinfo(hostname, None, socket.AF_INET)
        for info in infos:
            ip = info[4][0]
            if not ip.startswith('127.'):
                return ip
    except Exception:
        pass

    # 3. Tüm ağ arayüzlerini tara
    try:
        import subprocess as _sp
        r = _sp.run(['ip', 'route', 'show', 'default'],
                    capture_output=True, text=True, timeout=2)
        # "default via X.X.X.X dev eth0 src Y.Y.Y.Y" → src IP'si
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


def get_nfs_target(hostname):
    """
    Yapılandırmaya göre NFS backup URL'ini döner.
    nfs_mode='central'  → nfs://<central_ip><export_path>/<hostname>
    nfs_mode='separate' → nfs://<nfs_server_ip><export_path>/<hostname>
    """
    cfg = get_settings()
    mode = cfg.get('nfs_mode', 'central')
    path = cfg.get('nfs_export_path', BACKUP_ROOT)
    if mode == 'separate' and cfg.get('nfs_server_ip', '').strip():
        ip = cfg['nfs_server_ip'].strip()
    else:
        ip = cfg.get('central_ip', _get_local_ip())
    return f"nfs://{ip}{path}/{hostname}"


# ─────────────────────────────────────────────────────────────
# KİMLİK DOĞRULAMA
# ─────────────────────────────────────────────────────────────
def _get_user_by_username(username):
    conn = get_db()
    row = conn.execute(
        'SELECT * FROM users WHERE username=? COLLATE NOCASE', (username,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def authenticate_local(username, password):
    """Yerel kullanıcı doğrulama. (ok, user_dict, msg)"""
    user = _get_user_by_username(username)
    if not user:
        return False, None, 'Kullanıcı bulunamadı'
    if user['auth_type'] != 'local':
        return False, None, 'Bu kullanıcı için yerel giriş desteklenmiyor'
    if not user['active']:
        return False, None, 'Hesap pasif'
    if not check_password_hash(user['password_hash'] or '', password):
        return False, None, 'Hatalı şifre'
    return True, user, 'OK'


def authenticate_ad(username, password):
    """
    Active Directory LDAP doğrulama.
    Bind → kullanıcıyı ara → grup üyeliğini kontrol et → rol belirle.
    (ok, role, full_name, msg)
    """
    if not HAS_LDAP:
        return False, None, None, 'ldap3 modülü kurulu değil'

    cfg = get_settings()
    if cfg.get('ad_enabled') != '1':
        return False, None, None, 'AD kimlik doğrulama etkin değil'

    ad_server   = cfg.get('ad_server', '').strip()
    ad_port     = int(cfg.get('ad_port', 389))
    ad_domain   = cfg.get('ad_domain', '').strip()
    ad_base_dn  = cfg.get('ad_base_dn', '').strip()
    bind_user   = cfg.get('ad_bind_user', '').strip()
    bind_pass   = cfg.get('ad_bind_password', '')
    user_filter = cfg.get('ad_user_filter', '(sAMAccountName={username})')
    admin_grp   = cfg.get('ad_admin_group', 'ReaR-Admins').strip()
    user_grp    = cfg.get('ad_user_group', 'ReaR-Users').strip()

    if not ad_server or not ad_domain:
        return False, None, None, 'AD yapılandırması eksik'

    try:
        srv = LdapServer(ad_server, port=ad_port, get_info=ALL, connect_timeout=5)

        # Bind kullanıcısı ile bağlan
        bind_dn = f"{bind_user}@{ad_domain}" if bind_user else f"{username}@{ad_domain}"
        bind_pw = bind_pass if bind_user else password

        conn_bind = LdapConn(srv, user=bind_dn, password=bind_pw, auto_bind=True)

        # Kullanıcıyı ara
        search_filter = user_filter.replace('{username}', username)
        conn_bind.search(
            search_base=ad_base_dn,
            search_filter=search_filter,
            attributes=['distinguishedName', 'displayName', 'memberOf', 'sAMAccountName']
        )

        if not conn_bind.entries:
            conn_bind.unbind()
            return False, None, None, 'Kullanıcı AD\'de bulunamadı'

        entry     = conn_bind.entries[0]
        user_dn   = str(entry.distinguishedName)
        full_name = str(entry.displayName) if entry.displayName else username
        member_of = [str(g) for g in entry.memberOf] if entry.memberOf else []
        conn_bind.unbind()

        # Kullanıcı adına bind (şifre doğrulama)
        user_upn = f"{username}@{ad_domain}"
        try:
            conn_user = LdapConn(srv, user=user_upn, password=password, auto_bind=True)
            conn_user.unbind()
        except LDAPException:
            return False, None, None, 'AD şifre doğrulama başarısız'

        # Grup üyeliği → rol
        role = None
        for grp_dn in member_of:
            cn = grp_dn.split(',')[0].replace('CN=', '').replace('cn=', '')
            if cn.lower() == admin_grp.lower():
                role = 'admin'
                break
            if cn.lower() == user_grp.lower():
                role = 'user'

        if role is None:
            return False, None, None, f'Kullanıcı yetkili bir AD grubunda değil ({admin_grp} / {user_grp})'

        return True, role, full_name, 'OK'

    except LDAPException as e:
        return False, None, None, f'LDAP bağlantı hatası: {str(e)}'
    except Exception as e:
        return False, None, None, f'Hata: {str(e)}'


def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            # AJAX isteği ise JSON döndür, normal istek ise redirect
            if (request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                    or request.accept_mimetypes.best == 'application/json'
                    or request.path.startswith('/api/')):
                return jsonify({'ok': False, 'msg': 'Oturum süresi doldu, sayfayı yenileyin.'}), 401
            return redirect(url_for('login', next=request.url))
        # Session timeout kontrolü
        cfg = get_settings()
        timeout_min = int(cfg.get('session_timeout', 480))
        last_active = session.get('last_active', 0)
        if time.time() - last_active > timeout_min * 60:
            session.clear()
            if (request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                    or request.accept_mimetypes.best == 'application/json'
                    or request.path.startswith('/api/')):
                return jsonify({'ok': False, 'msg': 'Oturum süresi doldu, sayfayı yenileyin.'}), 401
            flash('Oturum süresi doldu.', 'warning')
            return redirect(url_for('login'))
        session['last_active'] = time.time()
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if session.get('user_role') != 'admin':
            flash('Bu işlem için yönetici yetkisi gerekli.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return login_required(decorated)


# ─────────────────────────────────────────────────────────────
# OFFLİNE UBUNTU PAKET YÖNETİMİ
# ─────────────────────────────────────────────────────────────
def get_offline_pkg_status():
    """
    offline-packages/ dizinindeki mevcut paket setlerini döner.
    {codename: {'count': N, 'size': 'XM', 'meta': {...}, 'ready': True/False}}
    """
    result = {}
    os.makedirs(OFFLINE_PKG_DIR, exist_ok=True)

    for codename in UBUNTU_CODENAMES:
        pkg_dir = os.path.join(OFFLINE_PKG_DIR, codename)
        if not os.path.isdir(pkg_dir):
            result[codename] = {'ready': False, 'count': 0, 'size': '0', 'meta': {}}
            continue

        debs = [f for f in os.listdir(pkg_dir) if f.endswith('.deb')]
        if not debs:
            result[codename] = {'ready': False, 'count': 0, 'size': '0', 'meta': {}}
            continue

        # Toplam boyut
        total_bytes = sum(
            os.path.getsize(os.path.join(pkg_dir, f)) for f in debs
        )
        size_mb = f"{total_bytes / 1024 / 1024:.1f} MB"

        # meta.json varsa oku
        meta = {}
        meta_path = os.path.join(pkg_dir, 'meta.json')
        if os.path.isfile(meta_path):
            try:
                import json as _json
                with open(meta_path) as mf:
                    meta = _json.load(mf)
            except Exception:
                pass

        result[codename] = {
            'ready':  True,
            'count':  len(debs),
            'size':   size_mb,
            'meta':   meta,
            'path':   pkg_dir,
        }

    return result


def get_ubuntu_codename_via_ssh(server):
    """
    SSH ile hedef sunucunun Ubuntu codename'ini alır.
    Döner: (codename_str | None, version_str | None)
    """
    try:
        client = build_ssh_client(server)
        _, stdout, _ = client.exec_command(
            'lsb_release -cs 2>/dev/null; lsb_release -rs 2>/dev/null',
            timeout=10
        )
        out = stdout.read().decode().strip().split('\n')
        client.close()
        codename = out[0].strip().lower() if out else None
        version  = out[1].strip() if len(out) > 1 else None
        return codename, version
    except Exception:
        return None, None


def ssh_install_offline_ubuntu(server_dict, job_id):
    """
    Offline Ubuntu kurulumu:
    1. SSH ile codename tespit et
    2. offline-packages/<codename>/ dizinindeki .deb'leri tar.gz yap
    3. Hedef sunucuya SFTP ile gönder
    4. become ile: dpkg -i (iki pass) + apt-get install -f (yerel çözümleme)
    5. Geçici dosyaları temizle

    Döner: (success: bool, message: str)
    """
    log = lambda t: _append_log(job_id, t)

    # ── Codename tespit ─────────────────────────────────────
    log("► Ubuntu sürümü tespit ediliyor...")
    codename, version = get_ubuntu_codename_via_ssh(server_dict)

    if not codename:
        return False, "Ubuntu sürümü tespit edilemedi."

    log(f"► Tespit edildi: Ubuntu {version or '?'} ({codename})")

    # ── Offline paket var mı? ───────────────────────────────
    pkg_dir = os.path.join(OFFLINE_PKG_DIR, codename)
    debs = []
    if os.path.isdir(pkg_dir):
        debs = [f for f in os.listdir(pkg_dir) if f.endswith('.deb')]

    if not debs:
        msg = (
            f"Ubuntu '{codename}' için offline paket bulunamadı: {pkg_dir}\n"
            f"Lütfen internet erişimi olan bir makinede önce "
            f"'prepare_offline_packages.sh' betiğini çalıştırın."
        )
        return False, msg

    log(f"► {len(debs)} adet .deb paketi bulundu ({pkg_dir})")

    # ── Paketleri tar.gz'e sıkıştır ────────────────────────
    import tarfile, tempfile
    tmp_tar = tempfile.mktemp(suffix='.tar.gz', prefix='rear_pkgs_')
    log(f"► Paketler arşivleniyor ({len(debs)} dosya)...")

    try:
        with tarfile.open(tmp_tar, 'w:gz') as tar:
            for deb in sorted(debs):
                tar.add(os.path.join(pkg_dir, deb), arcname=deb)
        tar_size_mb = os.path.getsize(tmp_tar) / 1024 / 1024
        log(f"► Arşiv boyutu: {tar_size_mb:.1f} MB")
    except Exception as e:
        return False, f"Arşivleme hatası: {e}"

    # ── Hedef sunucuya gönder ───────────────────────────────
    remote_tmp_dir = f"/tmp/.rear_pkgs_{secrets.token_hex(4)}"
    remote_tar     = f"{remote_tmp_dir}.tar.gz"

    log(f"► Paketler hedef sunucuya kopyalanıyor...")
    log(f"  Hedef: {server_dict['ip_address']}:{remote_tar}")

    try:
        client = build_ssh_client(server_dict)
        sftp   = client.open_sftp()

        # İlerleme callback
        total = os.path.getsize(tmp_tar)
        sent  = [0]
        def progress(transferred, total_size):
            pct = int(transferred / total_size * 100)
            if pct % 20 == 0 or transferred == total_size:
                mb = transferred / 1024 / 1024
                log(f"  ↑ {mb:.1f} MB / {total_size/1024/1024:.1f} MB ({pct}%)")

        sftp.put(tmp_tar, remote_tar, callback=progress)
        sftp.close()
        client.close()
        log("► Kopyalama tamamlandı ✓")
    except Exception as e:
        try: os.unlink(tmp_tar)
        except Exception: pass
        return False, f"SFTP gönderme hatası: {e}"
    finally:
        try: os.unlink(tmp_tar)
        except Exception: pass

    # ── Hedefte: aç + kur + temizle ────────────────────────
    log("► Paketler açılıyor ve kuruluyor...")
    log("  (dpkg -i ile offline kurulum — internet gerekmez)")

    # Tek komut bloğu: mkdir, tar xz, dpkg, dpkg (2. pass), temizlik
    install_script = f"""
set -e
mkdir -p {remote_tmp_dir}
cd {remote_tmp_dir}
echo "[1/4] Arşiv açılıyor..."
tar xzf {remote_tar} -C {remote_tmp_dir}/
echo "[2/4] dpkg ile kuruluyor (1. geçiş)..."
dpkg -i {remote_tmp_dir}/*.deb 2>&1 || true
echo "[3/4] dpkg ikinci geçiş (bağımlılık sırası)..."
dpkg -i {remote_tmp_dir}/*.deb 2>&1 || true
echo "[4/4] Bağımlılıklar düzeltiliyor..."
DEBIAN_FRONTEND=noninteractive apt-get install -f -y --no-install-recommends 2>&1 || true
echo "Temizleniyor..."
rm -rf {remote_tmp_dir} {remote_tar}
echo "KURULUM_TAMAM"
"""

    ec, out = ssh_exec_stream(server_dict, install_script.strip(), log)

    # KURULUM_TAMAM kontrolü (dpkg exit code'u güvenilmez olabilir)
    if 'KURULUM_TAMAM' in out:
        return True, "Offline kurulum başarılı."
    elif ec == 0:
        return True, "Offline kurulum tamamlandı."
    else:
        # dpkg -i bazı hatalara rağmen 0 dışı dönebilir; rear kuruldu mu kontrol et
        ec2, ver = ssh_exec_stream(server_dict, 'rear --version 2>/dev/null', lambda x: None)
        if ec2 == 0 and 'Relax-and-Recover' in ver:
            log(f"► ReaR kurulmuş: {ver.strip()}")
            return True, f"ReaR kuruldu (uyarılarla): {ver.strip()}"
        return False, f"Kurulum başarısız (kod: {ec})."


# ─────────────────────────────────────────────────────────────
def _get_become_password(server):
    """
    Become şifresini döner.
    become_same_pass=1 → SSH şifresi kullanılır
    become_same_pass=0 → become_password alanı kullanılır
    """
    same = server.get('become_same_pass', 1)
    if str(same) == '1':
        return server.get('ssh_password', '') or ''
    return server.get('become_password', '') or ''


def _wrap_become_cmd(server, command):
    """
    Komutu become yöntemine göre sarar.
    - none  : komut olduğu gibi çalışır
    - sudo  : sudo -H -u <user> bash -c '...'  (şifre PTY prompt ile gönderilir)
    - su    : su - <user> -c '...'             (şifre PTY prompt ile gönderilir)
    Döner: (wrapped_command, method, become_pass)
    """
    method = server.get('become_method', 'none')
    if method == 'none':
        return command, 'none', ''

    buser = (server.get('become_user', 'root') or 'root').strip()
    bpass = _get_become_password(server)

    if method == 'sudo':
        # PTY prompt yöntemi: sudo şifre isteyince prompt yakala, şifre gönder
        # -p 'SUDO_PASS_PROMPT: ' → sabit prompt metni ile yakalaması kolay
        # -H : HOME=/root,  -u : hedef kullanıcı
        if bpass:
            wrapped = (
                f"sudo -p 'SUDO_PASS_PROMPT: ' -H -u {buser} "
                f"bash -c {shlex.quote(command)}"
            )
        else:
            # NOPASSWD sudoers — şifre gönderme
            wrapped = f"sudo -H -u {buser} bash -c {shlex.quote(command)}"
        return wrapped, 'sudo', bpass

    elif method == 'su':
        wrapped = f"su - {buser} -c {shlex.quote(command)}"
        return wrapped, 'su', bpass

    return command, 'none', ''


def build_ssh_client(server):
    if not HAS_PARAMIKO:
        raise RuntimeError(
            "paramiko modülü kurulu değil. 'pip install paramiko' komutunu çalıştırın."
        )
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = dict(
        hostname=server['ip_address'],
        port=int(server['ssh_port']),
        username=server['ssh_user'],
        timeout=30,
    )
    if server['ssh_auth'] == 'key':
        kp = get_settings().get('ssh_key_path', KEY_PATH)
        kwargs['key_filename'] = kp
    else:
        kwargs['password'] = server['ssh_password']
    client.connect(**kwargs)
    return client



def ssh_exec_stream(server, command, log_cb):
    """
    SSH ile komut çalıştırır, PTY üzerinden çıktıyı satır satır log_cb'ye yollar.
    become (sudo/su) için PTY prompt beklenir ve şifre gönderilir.

    sudo: 'SUDO_PASS_PROMPT: ' sabit prompt metni ile şifre yakalaması güvenilir.
    su  : 'password:', 'parola:' vb. prompt ile şifre gönderilir.
    """
    wrapped_cmd, actual_method, bpass = _wrap_become_cmd(server, command)

    output_lines = []
    exit_code    = -1

    try:
        client    = build_ssh_client(server)
        transport = client.get_transport()
        chan      = transport.open_session()
        chan.get_pty(term='vt100', width=220, height=50)
        chan.exec_command(wrapped_cmd)

        buf           = b''
        pass_sent     = False
        pass_attempts = 0
        MAX_ATTEMPTS  = 3

        # Prompt desenleri (küçük harf karşılaştırması için)
        SUDO_PROMPT = b'sudo_pass_prompt: '
        SU_PROMPTS  = (
            b'password:', b'parola:',
            'şifre:'.encode('utf-8'),
            b'mot de passe:', b'passwort:',
            b'password for',
        )

        while True:
            if chan.recv_ready():
                data = chan.recv(8192)
                if not data:
                    break
                buf += data

                buf_lower = buf.lower()

                # ── Sudo şifre promptu ──────────────────────────────
                if actual_method == 'sudo' and not pass_sent and pass_attempts < MAX_ATTEMPTS:
                    if SUDO_PROMPT in buf_lower:
                        if bpass:
                            chan.sendall((bpass + '\n').encode('utf-8'))
                        else:
                            chan.sendall(b'\n')
                        pass_sent     = True
                        pass_attempts += 1
                        buf = b''
                        continue

                # ── Su şifre promptu ────────────────────────────────
                if actual_method == 'su' and not pass_sent and pass_attempts < MAX_ATTEMPTS:
                    if any(p in buf_lower for p in SU_PROMPTS):
                        chan.sendall((bpass + '\n').encode('utf-8'))
                        pass_sent     = True
                        pass_attempts += 1
                        buf = b''
                        continue

                # ── Yanlış şifre: tekrar prompt geldi ──────────────
                if actual_method in ('sudo','su') and pass_sent and pass_attempts < MAX_ATTEMPTS:
                    check = buf_lower
                    wrong = (b'sorry' in check or b'incorrect' in check or
                             b'authentication failure' in check or
                             b'3 incorrect' in check)
                    if wrong:
                        output_lines.append('[HATA] Become şifresi yanlış!')
                        log_cb('[HATA] Become şifresi yanlış! Lütfen sunucu ayarlarını kontrol edin.')
                        chan.close(); client.close()
                        return 1, '\n'.join(output_lines)

                # ── Satır satır çıktıyı işle ────────────────────────
                while b'\n' in buf:
                    line, buf = buf.split(b'\n', 1)
                    decoded = line.decode('utf-8', errors='replace').rstrip('\r')

                    # sudo/su gürültüsünü filtrele
                    if actual_method in ('sudo','su'):
                        dl = decoded.lower()
                        if (decoded.strip() == '' or
                            'sudo_pass_prompt' in dl or
                            'sudo:' in dl and 'password' in dl or
                            any(p.decode('utf-8','replace') in dl for p in SU_PROMPTS)):
                            continue

                    output_lines.append(decoded)
                    log_cb(decoded)

            elif chan.exit_status_ready():
                # Kanaldaki kalan veriyi boşalt
                while chan.recv_ready():
                    buf += chan.recv(8192)
                if buf:
                    for ln in buf.decode('utf-8', errors='replace').split('\n'):
                        ln = ln.rstrip('\r')
                        if not ln.strip():
                            continue
                        dl = ln.lower()
                        if actual_method in ('sudo','su') and (
                            'sudo_pass_prompt' in dl or
                            any(p.decode('utf-8','replace') in dl for p in SU_PROMPTS)
                        ):
                            continue
                        output_lines.append(ln)
                        log_cb(ln)
                exit_code = chan.recv_exit_status()
                break
            else:
                time.sleep(0.05)

        client.close()

    except Exception as e:
        msg = f"[SSH HATA] {str(e)}"
        output_lines.append(msg)
        log_cb(msg)
        exit_code = -1

    return exit_code, '\n'.join(output_lines)


def ssh_test_connection(server):
    """
    Bağlantı testi: SSH bağlantısı + become testi.
    Döner: (ok: bool, mesaj: str)
    """
    try:
        # 1. Temel SSH bağlantısı
        client = build_ssh_client(server)
        _, stdout, stderr = client.exec_command('id && uname -r', timeout=10)
        id_out   = stdout.read().decode().strip()
        err_out  = stderr.read().decode().strip()
        client.close()

        if not id_out:
            return False, f"SSH bağlandı ancak komut çalışmadı.\nHata: {err_out}"

        method = server.get('become_method', 'none')
        if method == 'none':
            return True, f"SSH OK\n{id_out}"

        # 2. Become testi
        buser = (server.get('become_user', 'root') or 'root').strip()
        bpass = _get_become_password(server)

        # Become öncesi hangi kullanıcı olduğunu göster
        ssh_user = server.get('ssh_user', '?')

        ec, out = ssh_exec_stream(server, 'id && whoami', lambda x: None)
        actual_lines = [ln.strip() for ln in out.strip().split('\n') if ln.strip()]
        actual_user  = actual_lines[-1] if actual_lines else ''

        if ec == 0 and (actual_user == buser or f'uid=0({buser})' in out or f'({buser})' in out):
            return True, (
                f"SSH OK — {ssh_user} → become({method}) → {buser} ✓\n"
                f"SSH kullanıcı: {id_out.split(chr(10))[0]}\n"
                f"Become sonrası: {actual_lines[0] if actual_lines else '?'}"
            )
        else:
            # Hata nedenini tespit et
            hint = ""
            if 'yanlış' in out.lower() or 'incorrect' in out.lower() or 'sorry' in out.lower():
                hint = f"\nNeden: Şifre yanlış. 'Become şifresi' alanını kontrol edin."
                if server.get('become_same_pass', 1) == 1:
                    hint += f"\n(Şu an SSH şifresi kullanılıyor: become_same_pass=1)"
            elif 'not in the sudoers' in out.lower() or 'is not allowed' in out.lower():
                hint = f"\nNeden: {ssh_user} sudoers'da yok.\nÇözüm: Hedef sunucuda → sudo visudo → '{ssh_user} ALL=(ALL) NOPASSWD: ALL'"
            elif 'command not found' in out.lower():
                hint = f"\nNeden: sudo/su bulunamadı."
            elif not bpass:
                hint = f"\nNeden: Şifre boş. Sunucu ayarlarında şifre girin."

            return False, (
                f"SSH OK ancak become başarısız!\n"
                f"SSH kullanıcı: {ssh_user}, Become: {method} → {buser}\n"
                f"Beklenen: {buser} | Dönen: {actual_user!r}\n"
                f"Çıkış kodu: {ec}{hint}"
            )

    except Exception as e:
        return False, str(e)


def ssh_get_os_info(server):
    """OS bilgisini alır. Become gerekebilir (genellikle gerekmez ama tutarlılık için)."""
    try:
        client = build_ssh_client(server)
        _, stdout, _ = client.exec_command(
            'cat /etc/os-release 2>/dev/null | head -5', timeout=10
        )
        out = stdout.read().decode().strip()
        client.close()
        return out
    except Exception:
        return ''


def ssh_upload_file(server, content, remote_path):
    """
    Dosyayı uzak sunucuya yazar.
    Become gerekiyorsa:
      1) /tmp'ye normal kullanıcı ile yaz (SFTP)
      2) become ile mv + chmod
    """
    import io, tempfile, posixpath

    method = server.get('become_method', 'none')

    try:
        client = build_ssh_client(server)
        sftp   = client.open_sftp()

        if method == 'none':
            # Doğrudan yaz
            with sftp.open(remote_path, 'w') as f:
                f.write(content)
            sftp.close()
            client.close()
            return True, 'OK'
        else:
            # Önce /tmp'ye yaz
            tmp_path = f"/tmp/.rear_upload_{secrets.token_hex(6)}"
            with sftp.open(tmp_path, 'w') as f:
                f.write(content)
            sftp.close()
            client.close()

            # Sonra become ile taşı
            mv_cmd = (
                f"mv -f {shlex.quote(tmp_path)} {shlex.quote(remote_path)} && "
                f"chmod 600 {shlex.quote(remote_path)}"
            )
            ec, out = ssh_exec_stream(server, mv_cmd, lambda x: None)
            if ec == 0:
                return True, 'OK'
            else:
                return False, f"mv başarısız (kod {ec}): {out}"

    except Exception as e:
        return False, str(e)


# ─────────────────────────────────────────────────────────────
# REAR YAPILANDIRMA ÜRETECİ
# ─────────────────────────────────────────────────────────────
def generate_rear_config(server, cfg, extra_server_exclude=''):
    """
    ReaR local.conf içeriğini üretir.
    server: dict
    cfg: settings dict
    extra_server_exclude: sunucuya özel ek hariç dizinler (multiline str)
    """
    backup_url  = get_nfs_target(server['hostname'])
    autoresize  = cfg.get('autoresize', '1')
    migration   = cfg.get('migration_mode', '1')
    output_type = cfg.get('rear_output', 'ISO')
    backup_type = cfg.get('rear_backup', 'NETFS')

    # Hariç tutulacak dizinleri birleştir
    global_excl = cfg.get('global_exclude_dirs', '')
    server_excl = server.get('exclude_dirs', '') or ''
    if extra_server_exclude:
        server_excl = (server_excl + '\n' + extra_server_exclude).strip()

    all_excludes = []
    for src in [global_excl, server_excl]:
        for line in src.splitlines():
            line = line.strip()
            if line and not line.startswith('#'):
                all_excludes.append(line)

    lines = [
        "# ReaR Yapılandırması - ReaR Manager v2.0 tarafından oluşturuldu",
        f"# Sunucu  : {server['hostname']}",
        f"# Tarih   : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"# NFS Mod : {cfg.get('nfs_mode','central')}",
        "",
        f"OUTPUT={output_type}",
        f"BACKUP={backup_type}",
        f'BACKUP_URL="{backup_url}"',
        "",
        "# ── Farklı donanım / disk boyutu ───────────────────────",
    ]

    if migration == '1':
        lines.append("MIGRATION_MODE=true")
    else:
        lines.append("# MIGRATION_MODE=true")

    if autoresize == '1':
        lines += [
            'AUTORESIZE_PARTITIONS=("true")',
            'AUTORESIZE_EXCLUDE_PARTITIONS=()',
            "AUTOSHRINK_DISK_SIZE_LIMIT_PERCENTAGE=80",
            "AUTOINCREASE_DISK_SIZE_THRESHOLD_PERCENTAGE=10",
        ]

    lines += [
        "",
        "# ── Ağ ────────────────────────────────────────────────",
        "USE_DHCLIENT=yes",
        'NETWORKING_PREPARATION_COMMANDS=("ip link set dev eth0 up" "dhclient eth0")',
        "",
        "# ── Hariç tutulan yollar ───────────────────────────────",
        "BACKUP_PROG_EXCLUDE=(",
        "    '${BACKUP_PROG_EXCLUDE[@]}'",
        "    '/tmp/*'",
        "    '/var/tmp/*'",
        "    '/proc/*'",
        "    '/sys/*'",
        "    '/dev/*'",
        "    '/run/*'",
    ]

    for excl in all_excludes:
        lines.append(f"    '{excl}'")

    lines += [
        ")",
        "",
        "# ── ISO / Kurtarma ayarları ────────────────────────────",
        "OUTPUT_URL=''",
        "ISO_DEFAULT=automatic",
        "",
        "# ── Loglama ────────────────────────────────────────────",
        "KEEP_BUILD_DIR=no",
        "REAR_PROGNAME=rear",
    ]

    return '\n'.join(lines) + '\n'


# ─────────────────────────────────────────────────────────────
# ARKA PLAN İŞ YÖNETİMİ
# ─────────────────────────────────────────────────────────────
def _append_log(job_id, text):
    """Backup job log'una satır ekle. 2 MB limitini aşarsa eski satırları kırpar."""
    conn = get_db()
    # Mevcut boyutu kontrol et
    row = conn.execute(
        "SELECT length(log_output) FROM backup_jobs WHERE id=?", (job_id,)
    ).fetchone()
    current_size = row[0] if row and row[0] else 0

    if current_size > 2_000_000:  # 2 MB
        # Son 500 KB'ı tut, eskisini at
        conn.execute(
            "UPDATE backup_jobs SET log_output = "
            "'[... önceki loglar kırpıldı ...]\n' || substr(log_output, -500000) || ? "
            "WHERE id=?",
            (text + '\n', job_id)
        )
    else:
        conn.execute(
            "UPDATE backup_jobs SET log_output = log_output || ? WHERE id=?",
            (text + '\n', job_id)
        )
    conn.commit()
    conn.close()


def _set_job_status(job_id, status, extra=None):
    conn = get_db()
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if status in ('success', 'failed', 'cancelled'):
        conn.execute(
            "UPDATE backup_jobs SET status=?, finished_at=? WHERE id=?",
            (status, ts, job_id)
        )
    else:
        conn.execute("UPDATE backup_jobs SET status=? WHERE id=?", (status, job_id))
    if extra:
        for k, v in extra.items():
            conn.execute(f"UPDATE backup_jobs SET {k}=? WHERE id=?", (v, job_id))
    conn.commit()
    conn.close()


def _run_install_rear(job_id, server_dict):
    log = lambda t: _append_log(job_id, t)
    _set_job_status(job_id, 'running')
    conn = get_db()
    conn.execute("UPDATE backup_jobs SET started_at=? WHERE id=?",
                 (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), job_id))
    conn.commit(); conn.close()

    log("=== ReaR Kurulumu Başlıyor ===")
    log("► OS bilgisi alınıyor...")
    os_info = ssh_get_os_info(server_dict)
    log(os_info)

    os_lower  = os_info.lower()
    is_ubuntu = 'ubuntu' in os_lower
    is_debian = 'debian' in os_lower and not is_ubuntu
    is_redhat = any(x in os_lower for x in ['rhel','centos','almalinux','rocky','fedora'])
    is_suse   = any(x in os_lower for x in ['suse', 'sles'])

    installed = False   # kurulum başarılı mı?

    # ── UBUNTU ─────────────────────────────────────────────────────────────────
    if is_ubuntu:
        codename, version = get_ubuntu_codename_via_ssh(server_dict)
        log(f"► Hedef: Ubuntu {version or '?'} ({codename or 'bilinmiyor'})")
        log("")

        # ── 1. ADIM: apt-get ile dene (internet varsa hızlı çözüm) ──────────
        log("► [1/2] apt-get ile kurulum deneniyor...")
        apt_cmd = (
            'export DEBIAN_FRONTEND=noninteractive && '
            'apt-get update -q 2>&1 | tail -3 && '
            'apt-get install -y rear nfs-common genisoimage xorriso '
            'syslinux syslinux-common isolinux 2>&1'
        )
        ec_apt, _ = ssh_exec_stream(server_dict, apt_cmd, log)

        if ec_apt == 0:
            log("► apt-get kurulum başarılı ✓")
            installed = True
        else:
            log(f"► apt-get başarısız (kod: {ec_apt}) — offline pakete geçiliyor...")
            log("")

            # ── 2. ADIM: offline paket ───────────────────────────────────────
            log("► [2/2] Offline paket kurulumu deneniyor...")
            pkg_status = get_offline_pkg_status()
            has_offline = (
                codename and
                codename in pkg_status and
                pkg_status[codename].get('ready', False)
            )

            if has_offline:
                pkg_info = pkg_status[codename]
                log(f"► Offline paket seti hazır: {pkg_info['count']} paket, {pkg_info['size']}")
                ok, msg = ssh_install_offline_ubuntu(server_dict, job_id)
                if ok:
                    log(f"► {msg}")
                    installed = True
                else:
                    log(f"[HATA] Offline kurulum başarısız: {msg}")
            else:
                if codename:
                    log(f"[HATA] Ubuntu '{codename}' için offline paket paketi bulunamadı.")
                    log(f"       Beklenen konum: {os.path.join(OFFLINE_PKG_DIR, codename)}/")
                else:
                    log("[HATA] Ubuntu codename tespit edilemedi.")
                log("")
                log("ÇÖZÜM: İnternet bağlantısı olan bir Ubuntu makinesinde şunu çalıştırın:")
                log(f"  sudo bash prepare_offline_packages.sh")
                log(f"Sonra dosyaları bu sunucuya kopyalayın:")
                log(f"  rsync -avz /opt/rear-manager/offline-packages/ \\")
                log(f"      root@<bu_sunucu>:/opt/rear-manager/offline-packages/")

        if not installed:
            _set_job_status(job_id, 'failed')
            return

    # ── DİĞER DAĞITIMLAR ──────────────────────────────────────────────────────
    elif is_debian:
        log("► Debian tespit edildi — apt-get ile kurulum...")
        ec, _ = ssh_exec_stream(server_dict, (
            'export DEBIAN_FRONTEND=noninteractive && '
            'apt-get update -q && '
            'apt-get install -y rear nfs-common genisoimage xorriso syslinux syslinux-common'
        ), log)
        if ec != 0:
            log(f"[HATA] Kurulum başarısız (kod: {ec})")
            _set_job_status(job_id, 'failed'); return

    elif is_redhat:
        log("► RHEL/CentOS/Alma/Rocky tespit edildi — dnf ile kurulum...")
        ec, _ = ssh_exec_stream(server_dict, (
            'dnf install -y epel-release 2>/dev/null || true; '
            'dnf install -y rear nfs-utils genisoimage syslinux'
        ), log)
        if ec != 0:
            log(f"[HATA] Kurulum başarısız (kod: {ec})")
            _set_job_status(job_id, 'failed'); return

    elif is_suse:
        log("► SUSE tespit edildi — zypper ile kurulum...")
        ec, _ = ssh_exec_stream(server_dict, 'zypper install -y rear nfs-client genisoimage syslinux', log)
        if ec != 0:
            log(f"[HATA] Kurulum başarısız (kod: {ec})")
            _set_job_status(job_id, 'failed'); return

    else:
        log("[UYARI] Bilinmeyen OS — apt-get ile deneniyor...")
        ec, _ = ssh_exec_stream(server_dict, (
            'export DEBIAN_FRONTEND=noninteractive && '
            'apt-get update -q && '
            'apt-get install -y rear nfs-common genisoimage xorriso || '
            '(dnf install -y epel-release 2>/dev/null; dnf install -y rear nfs-utils genisoimage)'
        ), log)
        if ec != 0:
            log(f"[HATA] Kurulum başarısız (kod: {ec})")
            _set_job_status(job_id, 'failed'); return

    # ── Sürüm doğrulama ───────────────────────────────────────────────────────
    log("")
    log("► ReaR sürümü doğrulanıyor...")
    _, ver = ssh_exec_stream(server_dict, 'rear --version 2>/dev/null', log)
    ver_str = ver.strip()
    if 'Relax-and-Recover' not in ver_str:
        log("[HATA] ReaR kurulu görünmüyor — 'rear --version' çalışmadı.")
        _set_job_status(job_id, 'failed'); return

    log(f"► ReaR Versiyonu: {ver_str}")
    log("")

    conn = get_db()
    conn.execute(
        "UPDATE servers SET rear_installed=1, os_type=?, updated_at=datetime('now','localtime') WHERE id=?",
        (os_info.split('\n')[0][:200], server_dict['id'])
    )
    conn.commit(); conn.close()

    log("=== ReaR Kurulumu Tamamlandı ✓ ===")
    _set_job_status(job_id, 'success')


def _run_configure_rear(job_id, server_dict, rear_config_content):
    log = lambda t: _append_log(job_id, t)
    _set_job_status(job_id, 'running')
    conn = get_db()
    conn.execute("UPDATE backup_jobs SET started_at=? WHERE id=?",
                 (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), job_id))
    conn.commit(); conn.close()

    log("=== ReaR Yapılandırması Başlıyor ===")
    ssh_exec_stream(server_dict, 'mkdir -p /etc/rear', log)
    ssh_exec_stream(server_dict,
        'test -f /etc/rear/local.conf && '
        'cp /etc/rear/local.conf /etc/rear/local.conf.bak && '
        'echo "Eski config yedeklendi" || true', log)

    log("► Yapılandırma dosyası yazılıyor...")
    ok, msg = ssh_upload_file(server_dict, rear_config_content, '/etc/rear/local.conf')
    if not ok:
        log(f"[HATA] {msg}")
        _set_job_status(job_id, 'failed'); return

    log("► Doğrulanıyor...")
    ssh_exec_stream(server_dict, 'rear dump 2>&1 | head -20', log)

    conn = get_db()
    conn.execute(
        "UPDATE servers SET rear_configured=1, updated_at=datetime('now','localtime') WHERE id=?",
        (server_dict['id'],)
    )
    conn.commit(); conn.close()

    log("=== Yapılandırma Tamamlandı ✓ ===")
    _set_job_status(job_id, 'success')


def _do_backup(job_id, server_dict, backup_cmd='mkbackup', triggered_by='manual', schedule_id=None):
    log = lambda t: _append_log(job_id, t)
    _set_job_status(job_id, 'running')
    conn = get_db()
    conn.execute("UPDATE backup_jobs SET started_at=? WHERE id=?",
                 (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), job_id))
    conn.commit(); conn.close()

    log(f"=== ReaR {'Yedekleme' if backup_cmd == 'mkbackup' else 'ISO Oluşturma'} Başlıyor ===")
    log(f"► Tetikleyen  : {triggered_by}")
    log(f"► Sunucu      : {server_dict['hostname']} ({server_dict['ip_address']})")
    log(f"► Başlangıç   : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log("")

    cfg = get_settings()
    nfs_ip = cfg.get('nfs_server_ip') if cfg.get('nfs_mode') == 'separate' else cfg.get('central_ip')
    log(f"► NFS Sunucusu: {nfs_ip} ({cfg.get('nfs_mode','central')} mod)")
    log(f"► NFS Yol     : {cfg.get('nfs_export_path', BACKUP_ROOT)}/{server_dict['hostname']}")
    log("")

    hostname   = server_dict['hostname']
    backup_dir = os.path.join(BACKUP_ROOT, hostname)

    # NFS hedef dizinini rear çalışmadan önce oluştur
    try:
        os.makedirs(backup_dir, exist_ok=True)
        os.chmod(backup_dir, 0o755)
        log(f"► NFS dizini hazırlandı: {backup_dir}")
    except OSError as e:
        log(f"[UYARI] NFS dizini oluşturulamadı: {e}")

    log(f"► rear -v {backup_cmd} çalıştırılıyor (bu uzun sürebilir)...")
    log("─" * 60)
    ec, _ = ssh_exec_stream(server_dict, f'rear -v {backup_cmd} 2>&1', log)
    log("─" * 60)
    status     = 'success' if ec == 0 else 'failed'

    size_str = '-'
    if ec == 0:
        if os.path.isdir(backup_dir):
            try:
                r = subprocess.run(['du', '-sh', backup_dir], capture_output=True, text=True)
                size_str = r.stdout.split()[0]
            except Exception:
                pass
        log(f"► Yedek boyutu: {size_str}")
        log("=== Tamamlandı ✓ ===")
    else:
        log(f"[HATA] İşlem başarısız (kod: {ec})")

    _set_job_status(job_id, status, {
        'backup_size': size_str,
        'iso_path': f"{backup_dir}/*.iso"
    })

    # Zamanlayıcı kaydını güncelle
    if schedule_id:
        conn = get_db()
        conn.execute(
            "UPDATE schedules SET last_run=?, last_status=? WHERE id=?",
            (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), status, schedule_id)
        )
        conn.commit(); conn.close()


def start_job_thread(target_fn, job_id, *args):
    def wrapper():
        try:
            target_fn(job_id, *args)
        except Exception:
            err = traceback.format_exc()
            _append_log(job_id, f"[BEKLENMEYEN HATA]\n{err}")
            _set_job_status(job_id, 'failed')
        finally:
            with _job_lock:
                _running_jobs.pop(job_id, None)

    t = threading.Thread(target=wrapper, daemon=True, name=f"job-{job_id}")
    with _job_lock:
        _running_jobs[job_id] = t
    t.start()
    return t


def create_job(server_id, job_type, triggered_by='manual', schedule_id=None):
    conn = get_db()
    c = conn.execute(
        "INSERT INTO backup_jobs(server_id, job_type, status, triggered_by, schedule_id) "
        "VALUES(?,?,?,?,?)",
        (server_id, job_type, 'pending', triggered_by, schedule_id)
    )
    job_id = c.lastrowid
    conn.commit(); conn.close()
    return job_id


# ─────────────────────────────────────────────────────────────
# ZAMANLAYICI (APScheduler)
# ─────────────────────────────────────────────────────────────
def _scheduler_run_backup(schedule_id):
    """APScheduler tarafından çağrılır."""
    conn = get_db()
    sched = conn.execute('SELECT * FROM schedules WHERE id=?', (schedule_id,)).fetchone()
    if not sched or not sched['enabled']:
        conn.close()
        return
    server = conn.execute('SELECT * FROM servers WHERE id=?', (sched['server_id'],)).fetchone()
    conn.close()
    if not server:
        return

    job_id = create_job(server['id'], 'backup', triggered_by='scheduler', schedule_id=schedule_id)
    start_job_thread(_do_backup, job_id, dict(server),
                     sched['backup_type'] or 'mkbackup', 'scheduler', schedule_id)


def init_scheduler():
    global _scheduler
    if not HAS_SCHEDULER:
        return

    _scheduler = BackgroundScheduler(timezone='Europe/Istanbul', daemon=True)
    _scheduler.start()

    # Mevcut aktif zamanlamaları yükle
    conn = get_db()
    schedules = conn.execute('SELECT * FROM schedules WHERE enabled=1').fetchall()
    conn.close()

    for sched in schedules:
        _add_scheduler_job(sched['id'],
                           sched['cron_minute'], sched['cron_hour'],
                           sched['cron_dom'],    sched['cron_month'],
                           sched['cron_dow'])


def _add_scheduler_job(schedule_id, minute, hour, dom, month, dow):
    if not _scheduler:
        return
    job_id_str = f'sched_{schedule_id}'
    try:
        _scheduler.remove_job(job_id_str)
    except Exception:
        pass
    try:
        _scheduler.add_job(
            _scheduler_run_backup,
            CronTrigger(minute=minute, hour=hour, day=dom,
                        month=month, day_of_week=dow),
            args=[schedule_id],
            id=job_id_str,
            replace_existing=True,
            misfire_grace_time=300
        )
    except Exception as e:
        app.logger.error(f"Zamanlayıcı eklenemedi (sched {schedule_id}): {e}")


def _remove_scheduler_job(schedule_id):
    if not _scheduler:
        return
    try:
        _scheduler.remove_job(f'sched_{schedule_id}')
    except Exception:
        pass


def get_next_run(schedule_id):
    if not _scheduler:
        return None
    try:
        job = _scheduler.get_job(f'sched_{schedule_id}')
        if job and job.next_run_time:
            return job.next_run_time.strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────
# FLASK ROTALARI — KİMLİK DOĞRULAMA
# ─────────────────────────────────────────────────────────────
@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))

    cfg = get_settings()
    ad_enabled = cfg.get('ad_enabled') == '1'
    error = None

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        auth_method = request.form.get('auth_method', 'local')

        if auth_method == 'ad' and ad_enabled:
            ok, role, full_name, msg = authenticate_ad(username, password)
            if ok:
                # AD kullanıcısını DB'ye kaydet / güncelle
                conn = get_db()
                user = conn.execute(
                    "SELECT * FROM users WHERE username=? COLLATE NOCASE AND auth_type='ad'",
                    (username,)
                ).fetchone()
                if user:
                    conn.execute(
                        "UPDATE users SET role=?, full_name=?, last_login=datetime('now','localtime'), active=1 WHERE id=?",
                        (role, full_name or username, user['id'])
                    )
                    user_id = user['id']
                else:
                    c = conn.execute(
                        "INSERT INTO users(username, full_name, role, auth_type, is_builtin, active, last_login) "
                        "VALUES(?,?,?,?,0,1,datetime('now','localtime'))",
                        (username, full_name or username, role, 'ad')
                    )
                    user_id = c.lastrowid
                conn.commit(); conn.close()

                session['user_id']       = user_id
                session['username']      = username
                session['user_role']     = role
                session['full_name']     = full_name or username
                session['last_active']   = time.time()
                return redirect(request.args.get('next') or url_for('dashboard'))
            else:
                error = f'AD Giriş Hatası: {msg}'

        else:  # local
            ok, user, msg = authenticate_local(username, password)
            if ok:
                conn = get_db()
                conn.execute(
                    "UPDATE users SET last_login=datetime('now','localtime') WHERE id=?",
                    (user['id'],)
                )
                conn.commit(); conn.close()

                session['user_id']     = user['id']
                session['username']    = user['username']
                session['user_role']   = user['role']
                session['full_name']   = user.get('full_name') or user['username']
                session['last_active'] = time.time()
                return redirect(request.args.get('next') or url_for('dashboard'))
            else:
                error = f'Giriş Hatası: {msg}'

    return render_template('login.html', ad_enabled=ad_enabled, error=error)


@app.route('/logout')
def logout():
    session.clear()
    flash('Oturum kapatıldı.', 'info')
    return redirect(url_for('login'))


# ─────────────────────────────────────────────────────────────
# FLASK ROTALARI — DASHBOARD
# ─────────────────────────────────────────────────────────────
@app.route('/')
@login_required
def dashboard():
    conn = get_db()
    servers = conn.execute('SELECT * FROM servers ORDER BY label').fetchall()
    jobs    = conn.execute('''
        SELECT j.*, s.label as server_label
        FROM backup_jobs j JOIN servers s ON s.id=j.server_id
        ORDER BY j.id DESC LIMIT 12
    ''').fetchall()
    stats = {
        'total_servers':      conn.execute('SELECT COUNT(*) FROM servers').fetchone()[0],
        'installed_servers':  conn.execute('SELECT COUNT(*) FROM servers WHERE rear_installed=1').fetchone()[0],
        'configured_servers': conn.execute('SELECT COUNT(*) FROM servers WHERE rear_configured=1').fetchone()[0],
        'total_backups':      conn.execute("SELECT COUNT(*) FROM backup_jobs WHERE job_type='backup'").fetchone()[0],
        'success_backups':    conn.execute("SELECT COUNT(*) FROM backup_jobs WHERE job_type='backup' AND status='success'").fetchone()[0],
        'failed_backups':     conn.execute("SELECT COUNT(*) FROM backup_jobs WHERE job_type='backup' AND status='failed'").fetchone()[0],
        'running_jobs':       len(_running_jobs),
        'active_schedules':   conn.execute("SELECT COUNT(*) FROM schedules WHERE enabled=1").fetchone()[0],
    }
    conn.close()

    backup_info = {}
    for s in servers:
        d = os.path.join(BACKUP_ROOT, s['hostname'])
        if os.path.isdir(d):
            try:
                r = subprocess.run(['du', '-sh', d], capture_output=True, text=True)
                backup_info[s['id']] = r.stdout.split()[0]
            except Exception:
                backup_info[s['id']] = '?'
        else:
            backup_info[s['id']] = '-'

    return render_template('dashboard.html', servers=servers, jobs=jobs,
                           stats=stats, backup_info=backup_info)


# ─────────────────────────────────────────────────────────────
# SUNUCU YÖNETİMİ
# ─────────────────────────────────────────────────────────────
@app.route('/servers')
@login_required
def servers_list():
    conn = get_db()
    servers = conn.execute('SELECT * FROM servers ORDER BY label').fetchall()
    # Ansible bağlantı durumu
    ansible_map = {}
    for s in servers:
        if s['ansible_host_id']:
            ah = conn.execute(
                'SELECT id, name FROM ansible_hosts WHERE id=?',
                (s['ansible_host_id'],)
            ).fetchone()
            ansible_map[s['id']] = dict(ah) if ah else None
        else:
            ansible_map[s['id']] = None
    conn.close()
    return render_template('servers.html', servers=servers, ansible_map=ansible_map)


@app.route('/servers/add', methods=['GET', 'POST'])
@login_required
def server_add():
    if request.method == 'POST':
        d = request.form
        label      = d.get('label', '').strip()
        hostname   = d.get('hostname', '').strip()
        ip_address = d.get('ip_address', '').strip()
        ssh_user   = d.get('ssh_user', '').strip()

        if not label or not hostname or not ip_address or not ssh_user:
            flash('Zorunlu alanlar eksik: Ad, Hostname, IP Adresi ve SSH Kullanıcısı gereklidir.', 'danger')
            cfg = get_settings()
            return render_template('server_form.html', server=dict(d), title='Sunucu Ekle', cfg=cfg), 400

        try:
            ssh_port = int(d.get('ssh_port', 22) or 22)
        except (ValueError, TypeError):
            ssh_port = 22

        conn = get_db()
        conn.execute('''
            INSERT INTO servers(label, hostname, ip_address, ssh_port, ssh_user,
                                ssh_auth, ssh_password,
                                become_method, become_user, become_password, become_same_pass,
                                exclude_dirs, notes)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (
            label, hostname, ip_address,
            ssh_port, ssh_user,
            d.get('ssh_auth', 'password'), d.get('ssh_password', ''),
            d.get('become_method', 'none'),
            d.get('become_user', 'root'),
            d.get('become_password', ''),
            1 if d.get('become_same_pass') else 0,
            d.get('exclude_dirs', ''), d.get('notes', '')
        ))
        conn.commit(); conn.close()
        flash(f'Sunucu "{label}" eklendi.', 'success')
        return redirect(url_for('servers_list'))
    cfg = get_settings()
    return render_template('server_form.html', server=None, title='Sunucu Ekle', cfg=cfg)


@app.route('/servers/<int:sid>/edit', methods=['GET', 'POST'])
@login_required
def server_edit(sid):
    conn = get_db()
    server = conn.execute('SELECT * FROM servers WHERE id=?', (sid,)).fetchone()
    conn.close()
    if not server:
        flash('Sunucu bulunamadı.', 'danger')
        return redirect(url_for('servers_list'))
    if request.method == 'POST':
        d = request.form
        label      = d.get('label', '').strip()
        hostname   = d.get('hostname', '').strip()
        ip_address = d.get('ip_address', '').strip()
        ssh_user   = d.get('ssh_user', '').strip()

        if not label or not hostname or not ip_address or not ssh_user:
            flash('Zorunlu alanlar eksik: Ad, Hostname, IP Adresi ve SSH Kullanıcısı gereklidir.', 'danger')
            cfg = get_settings()
            return render_template('server_form.html', server={**dict(server), **dict(d)},
                                   title='Sunucu Düzenle', cfg=cfg), 400

        try:
            ssh_port = int(d.get('ssh_port', 22) or 22)
        except (ValueError, TypeError):
            ssh_port = 22

        conn = get_db()
        conn.execute('''
            UPDATE servers SET label=?, hostname=?, ip_address=?, ssh_port=?,
            ssh_user=?, ssh_auth=?, ssh_password=?,
            become_method=?, become_user=?, become_password=?, become_same_pass=?,
            exclude_dirs=?, notes=?,
            updated_at=datetime('now','localtime')
            WHERE id=?
        ''', (
            label, hostname, ip_address,
            ssh_port, ssh_user,
            d.get('ssh_auth', 'password'), d.get('ssh_password', ''),
            d.get('become_method', 'none'),
            d.get('become_user', 'root'),
            d.get('become_password', ''),
            1 if d.get('become_same_pass') else 0,
            d.get('exclude_dirs', ''), d.get('notes', ''), sid
        ))
        conn.commit(); conn.close()
        flash('Sunucu güncellendi.', 'success')
        return redirect(url_for('server_detail', sid=sid))
    cfg = get_settings()
    return render_template('server_form.html', server=dict(server),
                           title='Sunucu Düzenle', cfg=cfg)


@app.route('/servers/<int:sid>/delete', methods=['POST'])
@login_required
def server_delete(sid):
    conn = get_db()
    conn.execute('DELETE FROM schedules WHERE server_id=?', (sid,))
    conn.execute('DELETE FROM backup_jobs WHERE server_id=?', (sid,))
    conn.execute('DELETE FROM servers WHERE id=?', (sid,))
    conn.commit(); conn.close()
    flash('Sunucu silindi.', 'success')
    return redirect(url_for('servers_list'))


@app.route('/servers/bulk-add', methods=['GET', 'POST'])
@login_required
def server_bulk_add():
    """
    Toplu sunucu ekleme.
    Her satır bir sunucu; alanlar sekme veya virgülle ayrılır.
    Format (zorunlu → opsiyonel):
      label | hostname | ip | [port] | [ssh_user] | [auth:password/key] |
      [ssh_password] | [become:none/sudo/su] | [become_user] |
      [become_same_pass:1/0] | [become_password] | [notes]

    Ayrıca CSV yükleme de desteklenir (aynı sütun sırası, başlık satırı opsiyonel).
    """
    if request.method == 'GET':
        return render_template('server_bulk.html')

    # Metin mi yoksa dosya mı?
    raw_text = ''
    uploaded = request.files.get('csv_file')
    if uploaded and uploaded.filename:
        raw_text = uploaded.read().decode('utf-8', errors='replace')
    else:
        raw_text = request.form.get('bulk_text', '')

    if not raw_text.strip():
        flash('Veri girilmedi.', 'warning')
        return redirect(url_for('server_bulk_add'))

    # Varsayılan değerler — form'dan alınabilir
    def_ssh_user    = request.form.get('def_ssh_user', 'ubuntu').strip() or 'ubuntu'
    def_auth        = request.form.get('def_auth', 'password')
    def_ssh_pass    = request.form.get('def_ssh_password', '')
    def_become      = request.form.get('def_become_method', 'sudo')
    def_become_user = request.form.get('def_become_user', 'root').strip() or 'root'
    def_same_pass   = 1 if request.form.get('def_become_same_pass', '1') == '1' else 0
    def_become_pass = request.form.get('def_become_password', '')

    added = 0
    skipped = 0
    errors = []

    conn = get_db()
    for lineno, raw_line in enumerate(raw_text.splitlines(), start=1):
        line = raw_line.strip()
        # Boş satır veya yorum
        if not line or line.startswith('#'):
            continue

        # Separator: virgül veya sekme; ikisini de destekle
        sep = '\t' if '\t' in line else ','
        parts = [p.strip() for p in line.split(sep)]

        # Başlık satırı kontrolü (label veya "label" kelimesiyle başlıyorsa atla)
        if parts[0].lower() in ('label', 'etiket', '#label'):
            continue

        if len(parts) < 3:
            errors.append(f"Satır {lineno}: Yetersiz alan (en az 3 gerekli: label, hostname, ip). → '{line}'")
            skipped += 1
            continue

        try:
            label      = parts[0] or f"server-{lineno}"
            hostname   = parts[1]
            ip         = parts[2]
            port       = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 22
            ssh_user   = parts[4] if len(parts) > 4 and parts[4] else def_ssh_user
            ssh_auth   = parts[5] if len(parts) > 5 and parts[5] in ('password','key') else def_auth
            ssh_pass   = parts[6] if len(parts) > 6 and parts[6] else def_ssh_pass
            bmethod    = parts[7] if len(parts) > 7 and parts[7] in ('none','sudo','su') else def_become
            buser      = parts[8] if len(parts) > 8 and parts[8] else def_become_user
            bsame_raw  = parts[9] if len(parts) > 9 else str(def_same_pass)
            bsame      = 1 if bsame_raw in ('1','true','evet','yes') else 0
            bpass      = parts[10] if len(parts) > 10 and parts[10] else def_become_pass
            notes      = parts[11] if len(parts) > 11 else ''

            # IP kontrolü
            if not ip:
                errors.append(f"Satır {lineno}: IP boş. → '{line}'")
                skipped += 1
                continue

            # Zaten var mı?
            exists = conn.execute(
                'SELECT id FROM servers WHERE ip_address=? OR (hostname=? AND hostname != "")',
                (ip, hostname)
            ).fetchone()
            if exists:
                errors.append(f"Satır {lineno}: {ip} / {hostname} zaten mevcut, atlandı.")
                skipped += 1
                continue

            conn.execute('''
                INSERT INTO servers(label, hostname, ip_address, ssh_port, ssh_user,
                                    ssh_auth, ssh_password,
                                    become_method, become_user, become_password, become_same_pass,
                                    notes)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ''', (
                label, hostname, ip, port, ssh_user,
                ssh_auth, ssh_pass,
                bmethod, buser, bpass, bsame,
                notes
            ))
            added += 1

        except Exception as e:
            errors.append(f"Satır {lineno}: {str(e)} → '{line}'")
            skipped += 1

    conn.commit(); conn.close()

    if added:
        flash(f'✓ {added} sunucu eklendi.{f"  {skipped} satır atlandı." if skipped else ""}', 'success')
    else:
        flash(f'Hiç sunucu eklenmedi. {skipped} satır atlandı.', 'warning')

    if errors:
        for e in errors[:10]:   # İlk 10 hata
            flash(e, 'warning')
        if len(errors) > 10:
            flash(f'... ve {len(errors)-10} hata daha.', 'warning')

    return redirect(url_for('servers_list'))


@app.route('/servers/<int:sid>')
@login_required
def server_detail(sid):
    conn = get_db()
    server = conn.execute('SELECT * FROM servers WHERE id=?', (sid,)).fetchone()
    if not server:
        conn.close()
        flash('Sunucu bulunamadı.', 'danger')
        return redirect(url_for('servers_list'))
    jobs = conn.execute(
        'SELECT * FROM backup_jobs WHERE server_id=? ORDER BY id DESC LIMIT 20',
        (sid,)
    ).fetchall()
    schedules = conn.execute(
        'SELECT * FROM schedules WHERE server_id=? ORDER BY id DESC',
        (sid,)
    ).fetchall()

    # Ansible bağlantısı
    ansible_host = None
    if server['ansible_host_id']:
        ah = conn.execute(
            'SELECT * FROM ansible_hosts WHERE id=?', (server['ansible_host_id'],)
        ).fetchone()
        if ah:
            ansible_host = dict(ah)

    # Bağlanabilecek mevcut Ansible hostları (henüz bağlı olmayanlar)
    # IP veya hostname eşleşmesi öneri için
    all_ansible_hosts = conn.execute(
        'SELECT id, name, hostname FROM ansible_hosts ORDER BY name'
    ).fetchall()

    conn.close()

    backup_dir = os.path.join(BACKUP_ROOT, server['hostname'])
    backup_files = []
    if os.path.isdir(backup_dir):
        for fname in sorted(os.listdir(backup_dir), reverse=True):
            fpath = os.path.join(backup_dir, fname)
            try:
                st = os.stat(fpath)
                if os.path.isdir(fpath):
                    try:
                        r = subprocess.run(['du', '-sb', fpath], capture_output=True, text=True, timeout=10)
                        size_bytes = int(r.stdout.split()[0]) if r.returncode == 0 and r.stdout.strip() else 0
                    except Exception:
                        size_bytes = 0
                    size_mb = size_bytes / 1024 / 1024
                else:
                    size_mb = st.st_size / 1024 / 1024
                mtime = datetime.datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M')
                backup_files.append({'name': fname, 'size': f"{size_mb:.1f} MB", 'mtime': mtime})
            except Exception:
                pass

    sched_next = {}
    for s in schedules:
        sched_next[s['id']] = get_next_run(s['id'])

    cfg = get_settings()
    running_job_ids = set(_running_jobs.keys())

    return render_template('server_detail.html',
                           server=dict(server), jobs=jobs,
                           schedules=schedules, sched_next=sched_next,
                           backup_files=backup_files,
                           running_job_ids=running_job_ids,
                           cfg=cfg,
                           ansible_host=ansible_host,
                           all_ansible_hosts=all_ansible_hosts)


# ─────────────────────────────────────────────────────────────
# ANSİBLE BAĞLANTI YÖNETİMİ
# ─────────────────────────────────────────────────────────────
@app.route('/servers/<int:sid>/ansible-auto-add', methods=['POST'])
@login_required
def server_ansible_auto_add(sid):
    """
    Sunucuyu Ansible host olarak otomatik oluşturur ve bağlar.
    SSH bilgilerini sunucudan kopyalar.
    Eğer aynı IP/hostname ile zaten bir Ansible host varsa onu bağlar.
    """
    conn = get_db()
    server = conn.execute('SELECT * FROM servers WHERE id=?', (sid,)).fetchone()
    if not server:
        conn.close()
        flash('Sunucu bulunamadı.', 'danger')
        return redirect(url_for('servers_list'))

    server = dict(server)

    # Zaten bağlı mı?
    if server['ansible_host_id']:
        ah = conn.execute(
            'SELECT id, name FROM ansible_hosts WHERE id=?', (server['ansible_host_id'],)
        ).fetchone()
        if ah:
            conn.close()
            flash(f'Bu sunucu zaten "{ah["name"]}" Ansible hostuna bağlı.', 'info')
            return redirect(url_for('server_detail', sid=sid))

    # Aynı IP veya hostname ile mevcut Ansible host var mı?
    existing = conn.execute(
        'SELECT id, name FROM ansible_hosts WHERE hostname=? OR hostname=?',
        (server['ip_address'], server['hostname'])
    ).fetchone()

    if existing:
        # Mevcut hosta bağla
        conn.execute(
            'UPDATE servers SET ansible_host_id=?, updated_at=datetime(\'now\',\'localtime\') WHERE id=?',
            (existing['id'], sid)
        )
        conn.commit(); conn.close()
        flash(f'Mevcut Ansible hostu "{existing["name"]}" ile bağlandı.', 'success')
        return redirect(url_for('server_detail', sid=sid))

    # Yeni Ansible host oluştur
    host_name = server['hostname'].split('.')[0]  # kısa isim

    # İsim çakışması varsa suffix ekle
    taken = conn.execute(
        'SELECT name FROM ansible_hosts WHERE name=?', (host_name,)
    ).fetchone()
    if taken:
        host_name = f"{host_name}-rear"

    try:
        c = conn.execute('''
            INSERT INTO ansible_hosts(
                name, hostname, os_type, connection_type,
                ssh_port, ansible_user, ansible_pass, auth_type,
                ssh_key_path, become_method, become_user, become_pass,
                become_same, active, notes
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ''', (
            host_name,
            server['ip_address'],          # IP adresini kullan
            'linux',                        # ReaR = Linux
            'ssh',
            server['ssh_port'] or 22,
            server['ssh_user'],
            server['ssh_password'] or '',
            server['ssh_auth'] or 'password',
            '',                             # key_path — gerekirse sonra güncellenir
            server['become_method'] or 'none',
            server['become_user'] or 'root',
            server['become_password'] or '',
            1 if server['become_same_pass'] else 0,
            1,
            f"ReaR sunucusundan otomatik oluşturuldu: {server['label']}"
        ))
        new_host_id = c.lastrowid

        # Sunucuya bağla
        conn.execute(
            'UPDATE servers SET ansible_host_id=?, updated_at=datetime(\'now\',\'localtime\') WHERE id=?',
            (new_host_id, sid)
        )
        conn.commit(); conn.close()

        # Inventory'i güncelle
        _generate_inventory()

        flash(f'✓ Ansible hostu "{host_name}" oluşturuldu ve bağlandı. '
              f'Gerekirse Ansible → Hostlar sayfasından düzenleyebilirsiniz.', 'success')

    except Exception as e:
        conn.close()
        flash(f'Ansible host oluşturma hatası: {e}', 'danger')

    return redirect(url_for('server_detail', sid=sid))


@app.route('/servers/<int:sid>/ansible-link', methods=['POST'])
@login_required
def server_ansible_link(sid):
    """Mevcut bir Ansible hostunu bu sunucuya bağlar."""
    ansible_host_id = request.form.get('ansible_host_id', type=int)
    conn = get_db()
    server = conn.execute('SELECT id FROM servers WHERE id=?', (sid,)).fetchone()
    if not server:
        conn.close()
        flash('Sunucu bulunamadı.', 'danger')
        return redirect(url_for('servers_list'))

    if ansible_host_id:
        ah = conn.execute('SELECT name FROM ansible_hosts WHERE id=?', (ansible_host_id,)).fetchone()
        if ah:
            conn.execute(
                'UPDATE servers SET ansible_host_id=?, updated_at=datetime(\'now\',\'localtime\') WHERE id=?',
                (ansible_host_id, sid)
            )
            conn.commit(); conn.close()
            flash(f'"{ah["name"]}" Ansible hostuna bağlandı.', 'success')
        else:
            conn.close()
            flash('Seçilen Ansible hostu bulunamadı.', 'danger')
    else:
        conn.close()
        flash('Geçerli bir Ansible hostu seçin.', 'warning')

    return redirect(url_for('server_detail', sid=sid))


@app.route('/servers/<int:sid>/ansible-unlink', methods=['POST'])
@login_required
def server_ansible_unlink(sid):
    """Ansible host bağlantısını kaldırır (Ansible hostu silmez)."""
    conn = get_db()
    conn.execute(
        'UPDATE servers SET ansible_host_id=NULL, updated_at=datetime(\'now\',\'localtime\') WHERE id=?',
        (sid,)
    )
    conn.commit(); conn.close()
    flash('Ansible host bağlantısı kaldırıldı.', 'info')
    return redirect(url_for('server_detail', sid=sid))


# ─────────────────────────────────────────────────────────────
# SSH BAĞLANTI TESTİ
# ─────────────────────────────────────────────────────────────
@app.route('/servers/<int:sid>/test', methods=['POST'])
@login_required
def server_test(sid):
    conn = get_db()
    server = conn.execute('SELECT * FROM servers WHERE id=?', (sid,)).fetchone()
    conn.close()
    if not server:
        return jsonify({'ok': False, 'msg': 'Sunucu bulunamadı'})
    ok, msg = ssh_test_connection(dict(server))
    return jsonify({'ok': ok, 'msg': msg})


# ─────────────────────────────────────────────────────────────
# REAR KURULUM / YAPILANDIRMA / YEDEKLEME
# ─────────────────────────────────────────────────────────────
@app.route('/servers/<int:sid>/install', methods=['POST'])
@login_required
def server_install_rear(sid):
    conn = get_db()
    server = conn.execute('SELECT * FROM servers WHERE id=?', (sid,)).fetchone()
    conn.close()
    if not server:
        flash('Sunucu bulunamadı.', 'danger')
        return redirect(url_for('servers_list'))
    job_id = create_job(sid, 'install')
    start_job_thread(_run_install_rear, job_id, dict(server))
    flash(f'ReaR kurulumu başlatıldı. İş #{job_id}', 'info')
    return redirect(url_for('job_detail', jid=job_id))


@app.route('/servers/<int:sid>/configure', methods=['GET', 'POST'])
@login_required
def server_configure(sid):
    conn = get_db()
    server = conn.execute('SELECT * FROM servers WHERE id=?', (sid,)).fetchone()
    conn.close()
    if not server:
        flash('Sunucu bulunamadı.', 'danger')
        return redirect(url_for('servers_list'))
    settings = get_settings()

    if request.method == 'POST':
        cfg = dict(settings)
        cfg['autoresize']    = request.form.get('autoresize', '0')
        cfg['migration_mode']= request.form.get('migration_mode', '0')
        cfg['rear_output']   = request.form.get('rear_output', 'ISO')
        cfg['rear_backup']   = request.form.get('rear_backup', 'NETFS')
        cfg['central_ip']    = request.form.get('central_ip', settings.get('central_ip', ''))
        cfg['nfs_mode']      = request.form.get('nfs_mode', 'central')
        cfg['nfs_server_ip'] = request.form.get('nfs_server_ip', '')

        # Sunucuya özel hariç dizinleri kaydet
        server_excl = request.form.get('server_exclude_dirs', '')
        conn2 = get_db()
        conn2.execute("UPDATE servers SET exclude_dirs=? WHERE id=?", (server_excl, sid))
        conn2.commit(); conn2.close()

        srv_dict = dict(server)
        srv_dict['exclude_dirs'] = server_excl
        content  = generate_rear_config(srv_dict, cfg)

        job_id = create_job(sid, 'configure')
        start_job_thread(_run_configure_rear, job_id, srv_dict, content)
        flash(f'Yapılandırma gönderildi. İş #{job_id}', 'info')
        return redirect(url_for('job_detail', jid=job_id))

    srv_dict = dict(server)
    preview  = generate_rear_config(srv_dict, settings)
    nfs_url  = get_nfs_target(server['hostname'])
    return render_template('configure.html', server=srv_dict,
                           settings=settings, preview=preview, nfs_url=nfs_url)


@app.route('/servers/<int:sid>/backup', methods=['POST'])
@login_required
def server_backup(sid):
    conn = get_db()
    server = conn.execute('SELECT * FROM servers WHERE id=?', (sid,)).fetchone()
    conn.close()
    if not server:
        flash('Sunucu bulunamadı.', 'danger')
        return redirect(url_for('servers_list'))

    btype  = request.form.get('backup_type', 'mkbackup')
    job_id = create_job(sid, 'backup', triggered_by='manual')
    start_job_thread(_do_backup, job_id, dict(server), btype, 'manual', None)
    flash(f'Yedekleme başlatıldı. İş #{job_id}', 'info')
    return redirect(url_for('job_detail', jid=job_id))


# ─────────────────────────────────────────────────────────────
# ZAMANLAMA YÖNETİMİ
# ─────────────────────────────────────────────────────────────
@app.route('/servers/<int:sid>/schedules/add', methods=['POST'])
@login_required
def schedule_add(sid):
    conn = get_db()
    server = conn.execute('SELECT * FROM servers WHERE id=?', (sid,)).fetchone()
    if not server:
        conn.close()
        flash('Sunucu bulunamadı.', 'danger')
        return redirect(url_for('servers_list'))

    d = request.form
    c = conn.execute('''
        INSERT INTO schedules(server_id, backup_type, cron_minute, cron_hour,
                              cron_dom, cron_month, cron_dow, enabled)
        VALUES(?,?,?,?,?,?,?,1)
    ''', (
        sid,
        d.get('backup_type', 'mkbackup'),
        d.get('cron_minute', '0'),
        d.get('cron_hour', '2'),
        d.get('cron_dom', '*'),
        d.get('cron_month', '*'),
        d.get('cron_dow', '*'),
    ))
    sched_id = c.lastrowid
    conn.commit(); conn.close()

    _add_scheduler_job(sched_id,
                       d.get('cron_minute', '0'), d.get('cron_hour', '2'),
                       d.get('cron_dom', '*'), d.get('cron_month', '*'),
                       d.get('cron_dow', '*'))

    flash(f'Zamanlama #{sched_id} oluşturuldu.', 'success')
    return redirect(url_for('server_detail', sid=sid))


@app.route('/schedules/<int:scid>/toggle', methods=['POST'])
@login_required
def schedule_toggle(scid):
    conn = get_db()
    sched = conn.execute('SELECT * FROM schedules WHERE id=?', (scid,)).fetchone()
    if not sched:
        conn.close()
        return jsonify({'ok': False})
    new_state = 0 if sched['enabled'] else 1
    conn.execute('UPDATE schedules SET enabled=? WHERE id=?', (new_state, scid))
    conn.commit()
    sid = sched['server_id']
    conn.close()

    if new_state:
        _add_scheduler_job(scid, sched['cron_minute'], sched['cron_hour'],
                           sched['cron_dom'], sched['cron_month'], sched['cron_dow'])
    else:
        _remove_scheduler_job(scid)

    flash(f'Zamanlama #{scid} {"aktif" if new_state else "devre dışı"} edildi.', 'success')
    return redirect(url_for('server_detail', sid=sid))


@app.route('/schedules/<int:scid>/delete', methods=['POST'])
@login_required
def schedule_delete(scid):
    conn = get_db()
    sched = conn.execute('SELECT * FROM schedules WHERE id=?', (scid,)).fetchone()
    if not sched:
        conn.close()
        flash('Zamanlama bulunamadı.', 'danger')
        return redirect(url_for('servers_list'))
    sid = sched['server_id']
    conn.execute('DELETE FROM schedules WHERE id=?', (scid,))
    conn.commit(); conn.close()
    _remove_scheduler_job(scid)
    flash(f'Zamanlama #{scid} silindi.', 'success')
    return redirect(url_for('server_detail', sid=sid))


@app.route('/schedules/<int:scid>/run-now', methods=['POST'])
@login_required
def schedule_run_now(scid):
    conn = get_db()
    sched  = conn.execute('SELECT * FROM schedules WHERE id=?', (scid,)).fetchone()
    server = conn.execute('SELECT * FROM servers WHERE id=?', (sched['server_id'],)).fetchone() if sched else None
    conn.close()
    if not sched or not server:
        flash('Bulunamadı.', 'danger')
        return redirect(url_for('servers_list'))
    job_id = create_job(server['id'], 'backup', triggered_by='manual-schedule', schedule_id=scid)
    start_job_thread(_do_backup, job_id, dict(server),
                     sched['backup_type'] or 'mkbackup', 'manual-schedule', scid)
    flash(f'Zamanlama #{scid} hemen çalıştırıldı. İş #{job_id}', 'info')
    return redirect(url_for('job_detail', jid=job_id))


# ─────────────────────────────────────────────────────────────
# İŞ YÖNETİMİ
# ─────────────────────────────────────────────────────────────
@app.route('/jobs')
@login_required
def jobs_list():
    conn = get_db()
    # Filtreleme
    status_filter = request.args.get('status', '')
    type_filter   = request.args.get('type', '')
    server_filter = request.args.get('server', '')

    query = '''
        SELECT j.*, s.label as server_label, s.hostname
        FROM backup_jobs j JOIN servers s ON s.id=j.server_id
        WHERE 1=1
    '''
    params = []
    if status_filter:
        query += ' AND j.status=?'; params.append(status_filter)
    if type_filter:
        query += ' AND j.job_type=?'; params.append(type_filter)
    if server_filter:
        query += ' AND j.server_id=?'; params.append(server_filter)

    query += ' ORDER BY j.id DESC LIMIT 300'
    jobs    = conn.execute(query, params).fetchall()
    servers = conn.execute('SELECT id, label FROM servers ORDER BY label').fetchall()
    conn.close()
    return render_template('jobs.html', jobs=jobs, servers=servers,
                           status_filter=status_filter, type_filter=type_filter,
                           server_filter=server_filter,
                           running_job_ids=set(_running_jobs.keys()))


@app.route('/jobs/<int:jid>')
@login_required
def job_detail(jid):
    conn = get_db()
    job = conn.execute('''
        SELECT j.*, s.label as server_label, s.hostname, s.ip_address
        FROM backup_jobs j JOIN servers s ON s.id=j.server_id WHERE j.id=?
    ''', (jid,)).fetchone()
    conn.close()
    if not job:
        flash('İş bulunamadı.', 'danger')
        return redirect(url_for('jobs_list'))
    return render_template('job_detail.html',
                           job=dict(job),
                           is_running=jid in _running_jobs)


@app.route('/jobs/<int:jid>/log')
@login_required
def job_log_api(jid):
    conn = get_db()
    row = conn.execute('SELECT log_output, status, finished_at FROM backup_jobs WHERE id=?', (jid,)).fetchone()
    conn.close()
    if not row:
        return jsonify({'log': '', 'status': 'notfound'})
    return jsonify({'log': row['log_output'] or '', 'status': row['status'],
                    'finished_at': row['finished_at'] or '',
                    'running': jid in _running_jobs})


@app.route('/jobs/<int:jid>/cancel', methods=['POST'])
@login_required
def job_cancel(jid):
    _set_job_status(jid, 'cancelled')
    flash(f'İş #{jid} iptal edildi.', 'warning')
    return redirect(url_for('job_detail', jid=jid))


@app.route('/jobs/<int:jid>/delete', methods=['POST'])
@login_required
def job_delete(jid):
    conn = get_db()
    job = conn.execute('SELECT server_id FROM backup_jobs WHERE id=?', (jid,)).fetchone()
    conn.execute('DELETE FROM backup_jobs WHERE id=?', (jid,))
    conn.commit(); conn.close()
    flash('İş silindi.', 'success')
    if job:
        return redirect(url_for('server_detail', sid=job['server_id']))
    return redirect(url_for('jobs_list'))


# ─────────────────────────────────────────────────────────────
# AYARLAR
# ─────────────────────────────────────────────────────────────
@app.route('/settings', methods=['GET', 'POST'])
@login_required
@admin_required
def settings_page():
    if request.method == 'POST':
        tab = request.form.get('tab', 'general')
        conn = get_db()

        if tab == 'general':
            keys = ['central_ip', 'nfs_mode', 'nfs_server_ip', 'nfs_export_path',
                    'nfs_options', 'rear_output', 'rear_backup',
                    'ssh_key_path', 'retention_days', 'session_timeout',
                    'autoresize', 'migration_mode', 'global_exclude_dirs']
        elif tab == 'ad':
            keys = ['ad_enabled', 'ad_server', 'ad_port', 'ad_domain',
                    'ad_base_dn', 'ad_bind_user', 'ad_bind_password',
                    'ad_user_filter', 'ad_admin_group', 'ad_user_group']
        else:
            keys = []

        for k in keys:
            v = request.form.get(k, '')
            conn.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', (k, v))
        conn.commit(); conn.close()
        flash('Ayarlar kaydedildi.', 'success')
        return redirect(url_for('settings_page', tab=tab))

    settings = get_settings()
    active_tab = request.args.get('tab', 'general')

    du_info = ''
    if os.path.isdir(BACKUP_ROOT):
        try:
            r = subprocess.run(['df', '-h', BACKUP_ROOT], capture_output=True, text=True)
            du_info = r.stdout
        except Exception:
            pass

    # Offline paket durumu
    offline_pkg_status = get_offline_pkg_status()

    return render_template('settings.html', settings=settings,
                           du_info=du_info, active_tab=active_tab,
                           has_scheduler=HAS_SCHEDULER, has_ldap=HAS_LDAP,
                           offline_pkg_status=offline_pkg_status,
                           ubuntu_codenames=UBUNTU_CODENAMES,
                           offline_pkg_dir=OFFLINE_PKG_DIR)


@app.route('/settings/setup-nfs', methods=['POST'])
@login_required
@admin_required
def setup_nfs():
    """
    Merkezi sunucuya NFS server kur ve export oluştur.
    - Ubuntu/Debian : nfs-kernel-server  → servis: nfs-kernel-server veya nfs-server
    - RHEL/CentOS   : nfs-utils          → servis: nfs-server
    - SUSE          : nfs-kernel-server  → servis: nfsserver
    """
    settings    = get_settings()
    export_path = settings.get('nfs_export_path', BACKUP_ROOT)
    nfs_opts    = settings.get('nfs_options', 'rw,sync,no_subtree_check,no_root_squash')
    msgs        = []
    errors      = []

    def run(cmd, check=True):
        """Komut çalıştır, stdout+stderr döner."""
        r = subprocess.run(cmd, capture_output=True, text=True)
        if check and r.returncode != 0:
            raise RuntimeError(
                f"Komut başarısız: {' '.join(cmd)}\n"
                f"stdout: {r.stdout.strip()}\n"
                f"stderr: {r.stderr.strip()}"
            )
        return r

    try:
        # ── 1. Dizin oluştur ──────────────────────────────────────────────
        os.makedirs(export_path, exist_ok=True)
        os.chmod(export_path, 0o777)
        msgs.append(f"✓ Dizin hazır: {export_path}")

        # ── 2. NFS server paketini kur ────────────────────────────────────
        is_debian_like = os.path.exists('/etc/debian_version')
        is_redhat_like = os.path.exists('/etc/redhat-release') or os.path.exists('/etc/centos-release')
        is_suse_like   = False
        if os.path.exists('/etc/os-release'):
            try:
                with open('/etc/os-release') as _f:
                    is_suse_like = 'suse' in _f.read().lower()
            except Exception:
                pass

        if is_debian_like:
            r = run(['apt-get', 'install', '-y', 'nfs-kernel-server'], check=False)
            if r.returncode != 0:
                raise RuntimeError(
                    f"nfs-kernel-server kurulamadı.\n"
                    f"Hata: {r.stderr.strip() or r.stdout.strip()}\n"
                    f"İpucu: 'apt-get update' çalıştırın veya ağ bağlantısını kontrol edin."
                )
        elif is_redhat_like:
            r = run(['dnf', 'install', '-y', 'nfs-utils'], check=False)
            if r.returncode != 0:
                r = run(['yum', 'install', '-y', 'nfs-utils'], check=False)
                if r.returncode != 0:
                    raise RuntimeError(f"nfs-utils kurulamadı: {r.stderr.strip()}")
        else:
            r = run(['zypper', 'install', '-y', 'nfs-kernel-server'], check=False)
            if r.returncode != 0:
                raise RuntimeError(f"nfs-kernel-server kurulamadı: {r.stderr.strip()}")

        msgs.append("✓ NFS server paketi kuruldu")

        # ── 3. /etc/exports güncelle ──────────────────────────────────────
        export_line = f"{export_path}\t*({nfs_opts})\n"

        try:
            if os.path.exists('/etc/exports'):
                with open('/etc/exports') as _f:
                    existing = _f.read()
            else:
                existing = ''
        except Exception as e:
            raise RuntimeError(f"/etc/exports okunamadı: {e}")

        if export_path not in existing:
            try:
                with open('/etc/exports', 'a') as f:
                    f.write(export_line)
                msgs.append(f"✓ /etc/exports güncellendi: {export_line.strip()}")
            except PermissionError:
                raise RuntimeError(
                    "/etc/exports yazma izni yok.\n"
                    "Uygulama root olarak çalışmıyor olabilir.\n"
                    "Kontrol: systemctl show rear-manager | grep User"
                )
            except Exception as e:
                raise RuntimeError(f"/etc/exports yazılamadı: {e}")
        else:
            # Mevcut satırı güncelle (farklı seçenekler olabilir)
            lines = existing.splitlines(keepends=True)
            new_lines = []
            updated = False
            for ln in lines:
                if ln.strip().startswith(export_path):
                    new_lines.append(export_line)
                    updated = True
                else:
                    new_lines.append(ln)
            if updated:
                with open('/etc/exports', 'w') as f:
                    f.writelines(new_lines)
                msgs.append(f"✓ /etc/exports satırı güncellendi: {export_line.strip()}")
            else:
                msgs.append("ℹ /etc/exports zaten doğru yapılandırılmış")

        # ── 4. exportfs -ra ───────────────────────────────────────────────
        r = run(['exportfs', '-ra'], check=False)
        if r.returncode != 0:
            # exportfs yoksa veya nfsd henüz başlamadıysa → servis başlatıldıktan sonra tekrar çalışacak
            errors.append(f"exportfs -ra uyarısı: {r.stderr.strip() or r.stdout.strip()}")
        else:
            msgs.append("✓ exportfs -ra çalıştırıldı")

        # ── 5. NFS servisini başlat ───────────────────────────────────────
        # Servis adını tespit et (dağıtıma göre farklı)
        nfs_service = None
        for svc in ('nfs-kernel-server', 'nfs-server', 'nfsserver', 'nfs'):
            r = subprocess.run(['systemctl', 'list-unit-files', f'{svc}.service'],
                               capture_output=True, text=True)
            if svc in r.stdout and 'not-found' not in r.stdout:
                nfs_service = svc
                break

        if not nfs_service:
            raise RuntimeError(
                "NFS servis adı bulunamadı. Paket kurulumu tamamlanmamış olabilir.\n"
                "Kontrol: systemctl list-unit-files | grep nfs"
            )

        r = run(['systemctl', 'enable', '--now', nfs_service], check=False)
        if r.returncode != 0:
            # restart dene
            r = run(['systemctl', 'restart', nfs_service], check=False)
            if r.returncode != 0:
                raise RuntimeError(
                    f"NFS servisi ({nfs_service}) başlatılamadı.\n"
                    f"Hata: {r.stderr.strip()}\n"
                    f"Kontrol: journalctl -u {nfs_service} -n 20"
                )

        msgs.append(f"✓ NFS servisi aktif: {nfs_service}")

        # exportfs -ra'yı servis başlatıldıktan sonra tekrar çalıştır
        subprocess.run(['exportfs', '-ra'], capture_output=True)
        subprocess.run(['exportfs', '-v'], capture_output=True)  # sessizce

        # ── 6. Firewall (sadece firewalld) ────────────────────────────────
        r_fwcmd = subprocess.run(['which', 'firewall-cmd'], capture_output=True)
        if r_fwcmd.returncode == 0:
            r_active = subprocess.run(
                ['firewall-cmd', '--state'], capture_output=True, text=True
            )
            if r_active.returncode == 0 and 'running' in r_active.stdout.lower():
                subprocess.run(['firewall-cmd', '--permanent', '--add-service=nfs'],
                               capture_output=True)
                subprocess.run(['firewall-cmd', '--permanent', '--add-service=mountd'],
                               capture_output=True)
                subprocess.run(['firewall-cmd', '--permanent', '--add-service=rpc-bind'],
                               capture_output=True)
                subprocess.run(['firewall-cmd', '--reload'], capture_output=True)
                msgs.append("✓ Firewall (firewalld): NFS kuralları eklendi")

        # ── 7. Export doğrulama ───────────────────────────────────────────
        r_show = subprocess.run(['showmount', '-e', 'localhost'],
                                capture_output=True, text=True)
        if r_show.returncode == 0 and export_path in r_show.stdout:
            msgs.append(f"✓ Export doğrulandı: {r_show.stdout.strip()}")
        else:
            errors.append(
                f"Export doğrulanamadı (showmount çıktısı: {r_show.stdout.strip() or r_show.stderr.strip()})\n"
                f"Sunucuyu yeniden başlatarak tekrar deneyin: systemctl restart {nfs_service}"
            )

        result_msg = ' | '.join(msgs)
        if errors:
            result_msg += ' | ⚠ Uyarı: ' + ' | '.join(errors)
        flash(result_msg, 'success' if not errors else 'warning')

    except Exception as e:
        flash(f'NFS Kurulum Hatası: {str(e)}', 'danger')

    return redirect(url_for('settings_page'))


@app.route('/settings/generate-key', methods=['POST'])
@login_required
@admin_required
def generate_ssh_key():
    key_path = get_settings().get('ssh_key_path', KEY_PATH)
    os.makedirs(os.path.dirname(os.path.abspath(key_path)), exist_ok=True)
    try:
        subprocess.run(['ssh-keygen', '-t', 'rsa', '-b', '4096', '-f', key_path,
                        '-N', '', '-C', 'rear-manager'], check=True, capture_output=True)
        pub = open(f"{key_path}.pub").read().strip()
        flash(f'SSH anahtarı oluşturuldu. Public key: {pub[:60]}...', 'success')
    except Exception as e:
        flash(f'Hata: {str(e)}', 'danger')
    return redirect(url_for('settings_page'))


@app.route('/settings/copy-key/<int:sid>', methods=['POST'])
@login_required
def copy_ssh_key(sid):
    conn = get_db()
    server = conn.execute('SELECT * FROM servers WHERE id=?', (sid,)).fetchone()
    conn.close()
    if not server:
        return jsonify({'ok': False, 'msg': 'Sunucu bulunamadı'})
    kp = get_settings().get('ssh_key_path', KEY_PATH)
    pub_path = f"{kp}.pub"
    if not os.path.exists(pub_path):
        return jsonify({'ok': False, 'msg': 'Public key dosyası bulunamadı'})
    try:
        pub_key = open(pub_path).read().strip()
        cmd = (f"mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
               f"echo '{pub_key}' >> ~/.ssh/authorized_keys && "
               f"chmod 600 ~/.ssh/authorized_keys && echo OK")
        ec, out = ssh_exec_stream(dict(server), cmd, lambda x: None)
        if ec == 0:
            return jsonify({'ok': True, 'msg': 'Public key kopyalandı.'})
        return jsonify({'ok': False, 'msg': out})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})


@app.route('/settings/test-ad', methods=['POST'])
@login_required
@admin_required
def test_ad():
    username = request.form.get('test_username', '').strip()
    password = request.form.get('test_password', '')
    if not username or not password:
        return jsonify({'ok': False, 'msg': 'Kullanıcı adı ve şifre gerekli'})
    ok, role, full_name, msg = authenticate_ad(username, password)
    return jsonify({'ok': ok, 'role': role, 'full_name': full_name, 'msg': msg})


# ─────────────────────────────────────────────────────────────
# KULLANICI YÖNETİMİ
# ─────────────────────────────────────────────────────────────
@app.route('/users')
@login_required
@admin_required
def users_list():
    conn = get_db()
    users = conn.execute('SELECT * FROM users ORDER BY username').fetchall()
    conn.close()
    return render_template('users.html', users=users)


@app.route('/users/add', methods=['GET', 'POST'])
@login_required
@admin_required
def user_add():
    if request.method == 'POST':
        d = request.form
        uname = d['username'].strip()
        if not uname:
            flash('Kullanıcı adı boş olamaz.', 'danger')
            return redirect(url_for('user_add'))

        conn = get_db()
        existing = conn.execute('SELECT id FROM users WHERE username=? COLLATE NOCASE', (uname,)).fetchone()
        if existing:
            conn.close()
            flash('Bu kullanıcı adı zaten mevcut.', 'danger')
            return redirect(url_for('user_add'))

        pw_hash = None
        if d.get('auth_type', 'local') == 'local':
            pw = d.get('password', '')
            if not pw:
                conn.close()
                flash('Yerel hesap için şifre gerekli.', 'danger')
                return redirect(url_for('user_add'))
            pw_hash = generate_password_hash(pw)

        conn.execute('''
            INSERT INTO users(username, password_hash, full_name, role, auth_type, is_builtin, active)
            VALUES(?,?,?,?,?,0,1)
        ''', (uname, pw_hash, d.get('full_name', ''), d.get('role', 'user'), d.get('auth_type', 'local')))
        conn.commit(); conn.close()
        flash(f'Kullanıcı "{uname}" eklendi.', 'success')
        return redirect(url_for('users_list'))
    return render_template('user_form.html', user=None, title='Kullanıcı Ekle')


@app.route('/users/<int:uid>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def user_edit(uid):
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
    conn.close()
    if not user:
        flash('Kullanıcı bulunamadı.', 'danger')
        return redirect(url_for('users_list'))

    if request.method == 'POST':
        d = request.form
        conn = get_db()
        pw_hash = user['password_hash']
        new_pw = d.get('password', '').strip()
        if new_pw:
            pw_hash = generate_password_hash(new_pw)

        conn.execute('''
            UPDATE users SET full_name=?, role=?, active=?, password_hash=? WHERE id=?
        ''', (
            d.get('full_name', ''),
            d.get('role', 'user') if not user['is_builtin'] else 'admin',
            1 if d.get('active') else 0,
            pw_hash, uid
        ))
        conn.commit(); conn.close()
        flash('Kullanıcı güncellendi.', 'success')
        return redirect(url_for('users_list'))

    return render_template('user_form.html', user=dict(user), title='Kullanıcı Düzenle')


@app.route('/users/<int:uid>/delete', methods=['POST'])
@login_required
@admin_required
def user_delete(uid):
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
    if not user:
        conn.close()
        flash('Kullanıcı bulunamadı.', 'danger')
        return redirect(url_for('users_list'))
    if user['is_builtin']:
        conn.close()
        flash('Yerleşik admin hesabı silinemez!', 'danger')
        return redirect(url_for('users_list'))
    if user['id'] == session.get('user_id'):
        conn.close()
        flash('Kendi hesabınızı silemezsiniz!', 'danger')
        return redirect(url_for('users_list'))
    conn.execute('DELETE FROM users WHERE id=?', (uid,))
    conn.commit(); conn.close()
    flash('Kullanıcı silindi.', 'success')
    return redirect(url_for('users_list'))


@app.route('/users/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        old_pw  = request.form.get('old_password', '')
        new_pw  = request.form.get('new_password', '')
        new_pw2 = request.form.get('new_password2', '')

        if not new_pw:
            flash('Yeni şifre boş olamaz.', 'danger')
            return redirect(url_for('change_password'))

        if new_pw != new_pw2:
            flash('Yeni şifreler eşleşmiyor.', 'danger')
            return redirect(url_for('change_password'))

        conn = get_db()
        user = conn.execute('SELECT * FROM users WHERE id=?', (session['user_id'],)).fetchone()
        conn.close()

        if not user or user['auth_type'] != 'local':
            flash('Bu işlem sadece yerel hesaplar için geçerlidir.', 'danger')
            return redirect(url_for('dashboard'))

        if not check_password_hash(user['password_hash'] or '', old_pw):
            flash('Mevcut şifre hatalı.', 'danger')
            return redirect(url_for('change_password'))

        conn = get_db()
        conn.execute('UPDATE users SET password_hash=? WHERE id=?',
                     (generate_password_hash(new_pw), session['user_id']))
        conn.commit(); conn.close()
        flash('Şifre değiştirildi.', 'success')
        return redirect(url_for('dashboard'))

    return render_template('change_password.html')


# ─────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────
@app.route('/api/status')
@login_required
def api_status():
    conn = get_db()
    running = []
    for jid in list(_running_jobs.keys()):
        row = conn.execute(
            'SELECT j.id, j.job_type, j.started_at, s.label FROM backup_jobs j '
            'JOIN servers s ON s.id=j.server_id WHERE j.id=?', (jid,)
        ).fetchone()
        if row:
            running.append(dict(row))
    conn.close()
    return jsonify({'running': running, 'count': len(running)})


@app.route('/api/schedules-status')
@login_required
def api_schedules_status():
    if not _scheduler:
        return jsonify({'jobs': []})
    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append({
            'id': job.id,
            'next_run': job.next_run_time.strftime('%Y-%m-%d %H:%M:%S') if job.next_run_time else None
        })
    return jsonify({'jobs': jobs})


@app.route('/api/offline-packages')
@login_required
def api_offline_packages():
    """Offline paket durumunu JSON olarak döner."""
    return jsonify({
        'status':     get_offline_pkg_status(),
        'base_dir':   OFFLINE_PKG_DIR,
        'codenames':  UBUNTU_CODENAMES,
    })


# ═════════════════════════════════════════════════════════════
# ██████████████████ ANSIBLE MODÜLÜ ███████████████████████████
# ═════════════════════════════════════════════════════════════

# ─── Çalışan Ansible işleri ──────────────────────────────────
_ansible_running = {}   # run_id → thread
_ansible_lock    = threading.Lock()


def _ansible_check() -> bool:
    """Ansible kurulu mu kontrol eder."""
    try:
        r = subprocess.run(['ansible', '--version'],
                           capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def _ansible_version() -> str:
    try:
        r = subprocess.run(['ansible', '--version'],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.split('\n')[0].strip()
    except Exception:
        return 'Kurulu değil'


# ─── Inventory üreteci ───────────────────────────────────────
def _generate_inventory() -> str:
    """
    DB'deki host ve gruplardan YAML inventory üretir.
    Host değişkenleri (şifre, become, port vb.) doğrudan inventory YAML'ına
    host_vars anahtarı olarak yazılır; Ansible bunları host başına okur.
    """
    conn = get_db()
    hosts  = conn.execute('SELECT * FROM ansible_hosts WHERE active=1 ORDER BY name').fetchall()
    groups = conn.execute('SELECT * FROM ansible_groups ORDER BY name').fetchall()
    hg     = conn.execute('SELECT * FROM ansible_host_groups').fetchall()
    conn.close()

    # host_id → [group_id, ...] mapping
    host_groups: dict = {}
    for row in hg:
        host_groups.setdefault(row['host_id'], []).append(row['group_id'])

    group_map = {g['id']: g['name'] for g in groups}

    # Gruplara atanmış host id'leri
    grouped_host_ids = set(row['host_id'] for row in hg)

    # ── Host değişkenlerini hesapla ─────────────────────────────
    def build_hvars(h) -> dict:
        hvars: dict = {'ansible_host': h['hostname']}

        if h['os_type'] == 'windows':
            hvars['ansible_connection']      = 'winrm'
            hvars['ansible_port']            = int(h['winrm_port'] or 5985)
            hvars['ansible_winrm_scheme']    = h['winrm_scheme'] or 'http'
            hvars['ansible_winrm_transport'] = h['win_transport'] or 'ntlm'
            hvars['ansible_winrm_server_cert_validation'] = 'ignore'
            hvars['ansible_user']     = h['ansible_user']
            hvars['ansible_password'] = h['ansible_pass']
            if h['win_domain']:
                hvars['ansible_winrm_kerberos_delegation'] = False
        else:  # linux / ssh
            hvars['ansible_connection'] = 'ssh'
            hvars['ansible_port']       = int(h['ssh_port'] or 22)
            hvars['ansible_user']       = h['ansible_user']
            if h['auth_type'] == 'key' and h['ssh_key_path']:
                hvars['ansible_ssh_private_key_file'] = h['ssh_key_path']
            else:
                hvars['ansible_password'] = h['ansible_pass']

            # become (boş veya 'none' değilse)
            bm = h['become_method'] or 'none'
            if bm != 'none':
                hvars['ansible_become']        = True
                hvars['ansible_become_method'] = bm
                hvars['ansible_become_user']   = h['become_user'] or 'root'
                # become şifresi: same_pass=1 → ssh şifresi, 0 → özel şifre
                if int(h['become_same'] or 0) == 1:
                    bp = h['ansible_pass']
                else:
                    bp = h['become_pass'] or ''
                if bp:   # boş şifre yazma — NOPASSWD durumu için
                    hvars['ansible_become_password'] = bp

        return hvars

    # ── Inventory YAML yapısını kur ─────────────────────────────
    # Yapı: all → hosts (host_vars inline) + children (gruplar)
    inv_hosts = {}   # all → hosts → {hostname: {vars}}
    inv_children = {}  # all → children → {grpname: {hosts: {...}}}

    for h in hosts:
        hname = h['name']
        hvars = build_hvars(h)
        gids  = host_groups.get(h['id'], [])

        if gids:
            # Gruba(lara) ekle — her grup altında host değişkenleri
            for gid in gids:
                gname = group_map.get(gid)
                if gname:
                    inv_children.setdefault(gname, {'hosts': {}})
                    inv_children[gname]['hosts'][hname] = hvars
        else:
            # Grupsuz → all.hosts altına
            inv_hosts[hname] = hvars

    inv: dict = {'all': {}}
    if inv_hosts:
        inv['all']['hosts'] = inv_hosts
    if inv_children:
        inv['all']['children'] = inv_children

    # ── group_vars dosyaları ────────────────────────────────────
    for g in groups:
        if g['vars_yaml']:
            gv_path = os.path.join(ANSIBLE_GVARS_DIR, f"{g['name']}.yml")
            try:
                with open(gv_path, 'w') as f:
                    f.write(f"---\n{g['vars_yaml']}\n")
            except Exception:
                pass

    # ── host_vars dosyaları (ek özel değişkenler) ───────────────
    for h in hosts:
        if h['vars_yaml']:
            hv_path = os.path.join(ANSIBLE_HVARS_DIR, f"{h['name']}.yml")
            try:
                with open(hv_path, 'w') as f:
                    f.write(f"---\n{h['vars_yaml']}\n")
            except Exception:
                pass

    # ── YAML'a dönüştür ─────────────────────────────────────────
    try:
        import yaml as _yaml
        inv_str = _yaml.dump(inv, default_flow_style=False, allow_unicode=True)
    except ImportError:
        inv_str = _dict_to_yaml(inv)

    # hosts.yml'e yaz
    inv_path = os.path.join(ANSIBLE_INV_DIR, 'hosts.yml')
    with open(inv_path, 'w') as f:
        f.write(inv_str)

    return inv_str


def _dict_to_yaml(d, indent=0) -> str:
    """Minimal PyYAML bağımsız YAML yazıcı (temel tipler için)."""
    lines = []
    pad = '  ' * indent
    for k, v in d.items():
        if v is None:
            lines.append(f"{pad}{k}:")
        elif isinstance(v, dict):
            lines.append(f"{pad}{k}:")
            lines.append(_dict_to_yaml(v, indent + 1))
        elif isinstance(v, list):
            lines.append(f"{pad}{k}:")
            for item in v:
                lines.append(f"{pad}  - {item}")
        elif isinstance(v, bool):
            lines.append(f"{pad}{k}: {'true' if v else 'false'}")
        elif isinstance(v, (int, float)):
            lines.append(f"{pad}{k}: {v}")
        else:
            # String — escape gerekiyorsa tırnak
            sv = str(v)
            if any(c in sv for c in [':', '#', '{', '}', '[', ']', '&', '*', '?', '|', '-']):
                sv = f'"{sv.replace(chr(34), chr(92)+chr(34))}"'
            lines.append(f"{pad}{k}: {sv}")
    return '\n'.join(lines)


def _sync_playbook_to_disk(pb: dict):
    """Playbook içeriğini diske yazar."""
    safe_name = re.sub(r'[^\w\-]', '_', pb['name']) + '.yml'
    path = os.path.join(ANSIBLE_PLAYS_DIR, safe_name)
    with open(path, 'w') as f:
        f.write(pb['content'])
    return path


def _sync_role_to_disk(role_id: int):
    """Rol dosyalarını diske yazar."""
    conn = get_db()
    role  = conn.execute('SELECT * FROM ansible_roles WHERE id=?', (role_id,)).fetchone()
    files = conn.execute('SELECT * FROM ansible_role_files WHERE role_id=?', (role_id,)).fetchall()
    conn.close()
    if not role:
        return

    rname = role['name']
    for section in ['tasks','handlers','templates','files','vars','defaults','meta']:
        os.makedirs(os.path.join(ANSIBLE_ROLES_DIR, rname, section), exist_ok=True)

    for rf in files:
        sec_dir = os.path.join(ANSIBLE_ROLES_DIR, rname, rf['section'])
        os.makedirs(sec_dir, exist_ok=True)
        fpath = os.path.join(sec_dir, rf['filename'])
        with open(fpath, 'w') as f:
            f.write(rf['content'] or '')


# ─── Ansible çalıştırma ──────────────────────────────────────
_ansible_run_logs: dict = {}   # run_id → deque of log lines
_ansible_run_lock = threading.Lock()

def _append_run_log(run_id, text):
    """Ansible run log'una satır ekle. 2 MB limitini aşarsa eski satırları kırpar."""
    conn = get_db()
    row = conn.execute(
        "SELECT length(output) FROM ansible_runs WHERE id=?", (run_id,)
    ).fetchone()
    current_size = row[0] if row and row[0] else 0

    if current_size > 2_000_000:
        conn.execute(
            "UPDATE ansible_runs SET output = "
            "'[... önceki loglar kırpıldı ...]\n' || substr(output, -500000) || ? "
            "WHERE id=?",
            (text + '\n', run_id)
        )
    else:
        conn.execute(
            "UPDATE ansible_runs SET output = output || ? WHERE id=?",
            (text + '\n', run_id)
        )
    conn.commit(); conn.close()


def _set_run_status(run_id, status, exit_code=None):
    conn = get_db()
    ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if status in ('success', 'failed', 'cancelled'):
        conn.execute(
            "UPDATE ansible_runs SET status=?, finished_at=?, exit_code=? WHERE id=?",
            (status, ts, exit_code, run_id)
        )
    else:
        conn.execute("UPDATE ansible_runs SET status=? WHERE id=?", (status, run_id))
    conn.commit(); conn.close()


def _do_ansible_run(run_id, playbook_path, extra_args: list):
    """Arka planda ansible-playbook çalıştırır."""
    log = lambda t: _append_run_log(run_id, t)
    _set_run_status(run_id, 'running')
    conn = get_db()
    conn.execute("UPDATE ansible_runs SET started_at=? WHERE id=?",
                 (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'), run_id))
    conn.commit(); conn.close()

    # Inventory üret
    log("► Inventory üretiliyor...")
    try:
        _generate_inventory()
        log("► Inventory hazır ✓")
    except Exception as e:
        log(f"[HATA] Inventory üretme hatası: {e}")
        _set_run_status(run_id, 'failed', -1)
        return

    cmd = [
        'ansible-playbook',
        '-i', os.path.join(ANSIBLE_INV_DIR, 'hosts.yml'),
        playbook_path,
    ] + extra_args

    log(f"► Komut: {' '.join(cmd)}")
    log("─" * 60)

    env = os.environ.copy()
    env['ANSIBLE_FORCE_COLOR']       = '0'
    env['ANSIBLE_NOCOLOR']           = '1'
    env['ANSIBLE_HOST_KEY_CHECKING'] = 'False'
    env.setdefault('HOME', os.path.expanduser('~'))

    # ansible.cfg için ANSIBLE_CONFIG
    env['ANSIBLE_CONFIG'] = os.path.join(ANSIBLE_DIR, 'ansible.cfg')

    exit_code = -1
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            cwd=ANSIBLE_DIR,
            bufsize=1,
        )

        # Run kaydına pid sakla
        conn = get_db()
        conn.execute("UPDATE ansible_runs SET output=output||? WHERE id=?",
                     (f"[PID: {proc.pid}]\n", run_id))
        conn.commit(); conn.close()

        with _ansible_run_lock:
            _ansible_running[run_id] = proc

        for line in proc.stdout:
            line = line.rstrip()
            log(line)

        proc.wait()
        exit_code = proc.returncode

    except Exception as e:
        log(f"[HATA] {e}")
        exit_code = -1
    finally:
        with _ansible_run_lock:
            _ansible_running.pop(run_id, None)

    log("─" * 60)
    if exit_code == 0:
        log("✓ Playbook başarıyla tamamlandı.")
        _set_run_status(run_id, 'success', 0)
    else:
        log(f"✗ Playbook başarısız (çıkış kodu: {exit_code})")
        _set_run_status(run_id, 'failed', exit_code)


# ─── Ansible Rotaları ────────────────────────────────────────
@app.route('/ansible/')
@login_required
def ansible_dashboard():
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
    recent_runs = conn.execute('''
        SELECT * FROM ansible_runs ORDER BY id DESC LIMIT 15
    ''').fetchall()
    conn.close()
    ansible_ok  = _ansible_check()
    ansible_ver = _ansible_version() if ansible_ok else 'Kurulu değil'
    return render_template('ansible_dashboard.html',
                           stats=stats, recent_runs=recent_runs,
                           ansible_ok=ansible_ok, ansible_ver=ansible_ver)


# ── Hosts ────────────────────────────────────────────────────
@app.route('/ansible/hosts')
@login_required
def ansible_hosts():
    conn = get_db()
    hosts  = conn.execute('SELECT * FROM ansible_hosts ORDER BY name').fetchall()
    groups = conn.execute('SELECT * FROM ansible_groups ORDER BY name').fetchall()
    hg     = conn.execute('SELECT * FROM ansible_host_groups').fetchall()
    conn.close()
    hg_map = {}
    for row in hg:
        hg_map.setdefault(row['host_id'], []).append(row['group_id'])
    group_map = {g['id']: g['name'] for g in groups}
    return render_template('ansible_hosts.html', hosts=hosts, groups=groups,
                           hg_map=hg_map, group_map=group_map)


@app.route('/ansible/hosts/add', methods=['GET', 'POST'])
@login_required
def ansible_host_add():
    conn = get_db()
    groups = conn.execute('SELECT * FROM ansible_groups ORDER BY name').fetchall()
    conn.close()
    if request.method == 'POST':
        return _save_ansible_host(None)
    settings = get_settings()
    return render_template('ansible_host_form.html', host=None,
                           groups=groups, title='Host Ekle', settings=settings)


@app.route('/ansible/hosts/bulk-add', methods=['GET', 'POST'])
@login_required
def ansible_host_bulk_add():
    """
    Ansible host toplu ekleme.

    Metin formatı (virgül veya sekme ayraçlı, # ile yorum):
      Linux:
        name, hostname_veya_ip, linux, [ssh_port], [user], [pass],
        [become:sudo/su/none], [become_user], [become_same:1/0], [grup_adı], [notlar]

      Windows:
        name, hostname_veya_ip, windows, [winrm_port], [user], [pass],
        [transport:ntlm/basic/kerberos], [domain], [grup_adı], [notlar]

    os_type sütunu atlanırsa varsayılan değer kullanılır.
    """
    conn = get_db()
    groups = conn.execute('SELECT * FROM ansible_groups ORDER BY name').fetchall()
    group_map = {g['name'].lower(): g['id'] for g in groups}

    if request.method == 'GET':
        conn.close()
        return render_template('ansible_host_bulk.html', groups=groups)

    # ── POST: Metin veya CSV dosyası ──────────────────────────────
    raw_text = ''
    uploaded = request.files.get('csv_file')
    if uploaded and uploaded.filename:
        raw_text = uploaded.read().decode('utf-8', errors='replace')
    else:
        raw_text = request.form.get('bulk_text', '')

    if not raw_text.strip():
        flash('Veri girilmedi.', 'warning')
        conn.close()
        return redirect(url_for('ansible_host_bulk_add'))

    # Varsayılan değerler
    def_os          = request.form.get('def_os_type', 'linux')
    def_user        = request.form.get('def_user', 'ubuntu').strip() or 'ubuntu'
    def_pass        = request.form.get('def_pass', '')
    def_ssh_port    = int(request.form.get('def_ssh_port', '22') or 22)
    def_winrm_port  = int(request.form.get('def_winrm_port', '5985') or 5985)
    def_become      = request.form.get('def_become', 'sudo')
    def_become_user = request.form.get('def_become_user', 'root').strip() or 'root'
    def_become_same = 1 if request.form.get('def_become_same', '1') == '1' else 0
    def_transport   = request.form.get('def_transport', 'ntlm')
    def_group       = request.form.get('def_group', '').strip()

    added   = 0
    skipped = 0
    errors  = []

    for lineno, raw_line in enumerate(raw_text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue

        sep   = '\t' if '\t' in line else ','
        parts = [p.strip() for p in line.split(sep)]

        # Başlık satırı
        if parts[0].lower() in ('name', 'ad', '#name', 'hostname'):
            continue

        if len(parts) < 2:
            errors.append(f"Satır {lineno}: En az 2 alan gerekli (name, hostname). → '{line}'")
            skipped += 1
            continue

        try:
            name     = parts[0]
            hostname = parts[1]
            if not name or not hostname:
                errors.append(f"Satır {lineno}: Name veya hostname boş.")
                skipped += 1
                continue

            # 3. sütun: os_type (linux/windows) — yoksa varsayılan
            os_type = parts[2].lower() if len(parts) > 2 and parts[2].lower() in ('linux','windows') else def_os

            # Zaten var mı?
            if conn.execute('SELECT id FROM ansible_hosts WHERE name=?', (name,)).fetchone():
                errors.append(f"Satır {lineno}: '{name}' zaten mevcut, atlandı.")
                skipped += 1
                continue

            if os_type == 'windows':
                # Windows: name, host, windows, [port], [user], [pass], [transport], [domain], [group], [notes]
                winrm_port = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else def_winrm_port
                user       = parts[4] if len(parts) > 4 and parts[4] else def_user
                passwd     = parts[5] if len(parts) > 5 and parts[5] else def_pass
                transport  = parts[6].lower() if len(parts) > 6 and parts[6] in ('ntlm','basic','kerberos') else def_transport
                domain     = parts[7] if len(parts) > 7 else ''
                grp_name   = parts[8].lower() if len(parts) > 8 and parts[8] else def_group.lower()
                notes      = parts[9] if len(parts) > 9 else ''

                conn.execute('''
                    INSERT INTO ansible_hosts(
                        name, hostname, os_type, connection_type,
                        ssh_port, winrm_port, winrm_scheme,
                        ansible_user, ansible_pass, auth_type,
                        win_transport, win_domain,
                        become_method, become_user, become_pass, become_same,
                        notes, active)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
                ''', (
                    name, hostname, 'windows', 'winrm',
                    22, winrm_port, 'http',
                    user, passwd, 'password',
                    transport, domain,
                    'none', '', '', 0,
                    notes
                ))

            else:
                # Linux: name, host, linux, [port], [user], [pass], [become], [become_user], [become_same], [group], [notes]
                ssh_port     = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else def_ssh_port
                user         = parts[4] if len(parts) > 4 and parts[4] else def_user
                passwd       = parts[5] if len(parts) > 5 and parts[5] else def_pass
                become       = parts[6].lower() if len(parts) > 6 and parts[6] in ('sudo','su','none') else def_become
                become_user  = parts[7] if len(parts) > 7 and parts[7] else def_become_user
                bsame_raw    = parts[8] if len(parts) > 8 else str(def_become_same)
                become_same  = 1 if bsame_raw in ('1','true','evet','yes') else 0
                grp_name     = parts[9].lower() if len(parts) > 9 and parts[9] else def_group.lower()
                notes        = parts[10] if len(parts) > 10 else ''

                conn.execute('''
                    INSERT INTO ansible_hosts(
                        name, hostname, os_type, connection_type,
                        ssh_port, winrm_port, winrm_scheme,
                        ansible_user, ansible_pass, auth_type,
                        become_method, become_user, become_pass, become_same,
                        notes, active)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
                ''', (
                    name, hostname, 'linux', 'ssh',
                    ssh_port, 5985, 'http',
                    user, passwd, 'password',
                    become, become_user, '', become_same,
                    notes
                ))

            new_id = conn.execute('SELECT id FROM ansible_hosts WHERE name=?', (name,)).fetchone()['id']

            # Gruba ekle
            if grp_name and grp_name in group_map:
                conn.execute(
                    'INSERT OR IGNORE INTO ansible_host_groups(host_id, group_id) VALUES(?,?)',
                    (new_id, group_map[grp_name])
                )

            added += 1

        except Exception as e:
            errors.append(f"Satır {lineno}: {str(e)} → '{line}'")
            skipped += 1

    conn.commit()
    conn.close()

    if added:
        flash(f'✓ {added} host eklendi.' + (f'  {skipped} satır atlandı.' if skipped else ''), 'success')
    else:
        flash(f'Hiç host eklenmedi. {skipped} satır atlandı.', 'warning')

    for e in errors[:10]:
        flash(e, 'warning')
    if len(errors) > 10:
        flash(f'... ve {len(errors) - 10} hata daha.', 'warning')

    return redirect(url_for('ansible_hosts'))


@app.route('/ansible/hosts/<int:hid>/edit', methods=['GET', 'POST'])
@login_required
def ansible_host_edit(hid):
    conn = get_db()
    host   = conn.execute('SELECT * FROM ansible_hosts WHERE id=?', (hid,)).fetchone()
    groups = conn.execute('SELECT * FROM ansible_groups ORDER BY name').fetchall()
    sel_groups = [r['group_id'] for r in
                  conn.execute('SELECT group_id FROM ansible_host_groups WHERE host_id=?',
                               (hid,)).fetchall()]
    conn.close()
    if not host:
        flash('Host bulunamadı.', 'danger')
        return redirect(url_for('ansible_hosts'))
    if request.method == 'POST':
        return _save_ansible_host(hid)
    settings = get_settings()
    return render_template('ansible_host_form.html', host=dict(host),
                           groups=groups, sel_groups=sel_groups,
                           title='Host Düzenle', settings=settings)


def _save_ansible_host(hid):
    d = request.form
    conn = get_db()
    fields = {
        'name':           d.get('name', '').strip(),
        'hostname':       d.get('hostname', '').strip(),
        'os_type':        d.get('os_type', 'linux'),
        'connection_type':d.get('connection_type', 'ssh'),
        'ssh_port':       int(d.get('ssh_port') or 22),
        'winrm_port':     int(d.get('winrm_port') or 5985),
        'winrm_scheme':   d.get('winrm_scheme', 'http'),
        'ansible_user':   d.get('ansible_user', ''),
        'ansible_pass':   d.get('ansible_pass', ''),
        'auth_type':      d.get('auth_type', 'password'),
        'ssh_key_path':   d.get('ssh_key_path', ''),
        'win_domain':     d.get('win_domain', ''),
        'win_transport':  d.get('win_transport', 'ntlm'),
        'become_method':  d.get('become_method', 'none'),
        'become_user':    d.get('become_user', 'root'),
        'become_pass':    d.get('become_pass', ''),
        'become_same':    1 if d.get('become_same') else 0,
        'vars_yaml':      d.get('vars_yaml', ''),
        'notes':          d.get('notes', ''),
        'active':         1 if d.get('active', '1') != '0' else 0,
    }
    sel_groups = request.form.getlist('group_ids')

    if hid:
        conn.execute('''
            UPDATE ansible_hosts SET
              name=:name, hostname=:hostname, os_type=:os_type,
              connection_type=:connection_type, ssh_port=:ssh_port,
              winrm_port=:winrm_port, winrm_scheme=:winrm_scheme,
              ansible_user=:ansible_user, ansible_pass=:ansible_pass,
              auth_type=:auth_type, ssh_key_path=:ssh_key_path,
              win_domain=:win_domain, win_transport=:win_transport,
              become_method=:become_method, become_user=:become_user,
              become_pass=:become_pass, become_same=:become_same,
              vars_yaml=:vars_yaml, notes=:notes, active=:active
            WHERE id=:id
        ''', {**fields, 'id': hid})
        conn.execute('DELETE FROM ansible_host_groups WHERE host_id=?', (hid,))
        for gid in sel_groups:
            conn.execute('INSERT OR IGNORE INTO ansible_host_groups(host_id,group_id) VALUES(?,?)',
                         (hid, int(gid)))
        conn.commit(); conn.close()
        flash(f'Host "{fields["name"]}" güncellendi.', 'success')
        return redirect(url_for('ansible_host_edit', hid=hid))
    else:
        c = conn.execute('''
            INSERT INTO ansible_hosts(name,hostname,os_type,connection_type,ssh_port,
              winrm_port,winrm_scheme,ansible_user,ansible_pass,auth_type,ssh_key_path,
              win_domain,win_transport,become_method,become_user,become_pass,become_same,
              vars_yaml,notes,active)
            VALUES(:name,:hostname,:os_type,:connection_type,:ssh_port,
              :winrm_port,:winrm_scheme,:ansible_user,:ansible_pass,:auth_type,:ssh_key_path,
              :win_domain,:win_transport,:become_method,:become_user,:become_pass,:become_same,
              :vars_yaml,:notes,:active)
        ''', fields)
        new_id = c.lastrowid
        for gid in sel_groups:
            conn.execute('INSERT OR IGNORE INTO ansible_host_groups(host_id,group_id) VALUES(?,?)',
                         (new_id, int(gid)))
        conn.commit(); conn.close()
        flash(f'Host "{fields["name"]}" eklendi.', 'success')
        return redirect(url_for('ansible_hosts'))


@app.route('/ansible/hosts/<int:hid>/delete', methods=['POST'])
@login_required
def ansible_host_delete(hid):
    conn = get_db()
    h = conn.execute('SELECT name FROM ansible_hosts WHERE id=?', (hid,)).fetchone()
    conn.execute('DELETE FROM ansible_host_groups WHERE host_id=?', (hid,))
    conn.execute('DELETE FROM ansible_hosts WHERE id=?', (hid,))
    conn.commit(); conn.close()
    flash(f'Host "{h["name"] if h else hid}" silindi.', 'success')
    return redirect(url_for('ansible_hosts'))


# ── Groups ───────────────────────────────────────────────────
@app.route('/ansible/groups', methods=['GET', 'POST'])
@login_required
def ansible_groups():
    conn = get_db()
    if request.method == 'POST':
        action = request.form.get('action', 'add')
        if action == 'add':
            name = request.form.get('name', '').strip()
            desc = request.form.get('description', '').strip()
            if name:
                try:
                    conn.execute('INSERT INTO ansible_groups(name,description) VALUES(?,?)',
                                 (name, desc))
                    conn.commit()
                    flash(f'Grup "{name}" eklendi.', 'success')
                except Exception:
                    flash('Grup adı zaten mevcut.', 'danger')
        elif action == 'delete':
            gid = int(request.form.get('gid', 0))
            conn.execute('DELETE FROM ansible_host_groups WHERE group_id=?', (gid,))
            conn.execute('DELETE FROM ansible_groups WHERE id=?', (gid,))
            conn.commit()
            flash('Grup silindi.', 'success')
        elif action == 'save_vars':
            gid   = int(request.form.get('gid', 0))
            vyaml = request.form.get('vars_yaml', '')
            conn.execute('UPDATE ansible_groups SET vars_yaml=? WHERE id=?', (vyaml, gid))
            conn.commit()
            flash('Grup değişkenleri kaydedildi.', 'success')
        conn.close()
        return redirect(url_for('ansible_groups'))

    groups = conn.execute('SELECT * FROM ansible_groups ORDER BY name').fetchall()
    # Her grup için host sayısı
    hcounts = {}
    for g in groups:
        cnt = conn.execute(
            'SELECT COUNT(*) FROM ansible_host_groups WHERE group_id=?', (g['id'],)
        ).fetchone()[0]
        hcounts[g['id']] = cnt
    conn.close()
    return render_template('ansible_groups.html', groups=groups, hcounts=hcounts)


# ── Playbooks ────────────────────────────────────────────────
@app.route('/ansible/playbooks')
@login_required
def ansible_playbooks():
    conn = get_db()
    pbs   = conn.execute('SELECT * FROM ansible_playbooks ORDER BY name').fetchall()
    # Son çalışma bilgisi
    last_runs = {}
    for pb in pbs:
        r = conn.execute(
            "SELECT * FROM ansible_runs WHERE playbook_id=? ORDER BY id DESC LIMIT 1",
            (pb['id'],)
        ).fetchone()
        if r:
            last_runs[pb['id']] = dict(r)
    conn.close()
    return render_template('ansible_playbooks.html', playbooks=pbs, last_runs=last_runs)


@app.route('/ansible/playbooks/add', methods=['GET', 'POST'])
@login_required
def ansible_playbook_add():
    if request.method == 'POST':
        return _save_playbook(None)
    conn = get_db()
    groups = conn.execute('SELECT name FROM ansible_groups ORDER BY name').fetchall()
    conn.close()
    return render_template('ansible_playbook_editor.html',
                           pb=None, title='Yeni Playbook', groups=groups)


@app.route('/ansible/playbooks/<int:pid>/edit', methods=['GET', 'POST'])
@login_required
def ansible_playbook_edit(pid):
    conn = get_db()
    pb     = conn.execute('SELECT * FROM ansible_playbooks WHERE id=?', (pid,)).fetchone()
    groups = conn.execute('SELECT name FROM ansible_groups ORDER BY name').fetchall()
    conn.close()
    if not pb:
        flash('Playbook bulunamadı.', 'danger')
        return redirect(url_for('ansible_playbooks'))
    if request.method == 'POST':
        return _save_playbook(pid)
    return render_template('ansible_playbook_editor.html',
                           pb=dict(pb), title=f'Düzenle: {pb["name"]}', groups=groups)


def _save_playbook(pid):
    d = request.form
    name    = d.get('name', '').strip()
    content = d.get('content', '')
    desc    = d.get('description', '')
    tags    = d.get('tags', '')

    if not name:
        flash('Playbook adı zorunlu.', 'danger')
        return redirect(url_for('ansible_playbooks'))

    conn = get_db()
    if pid:
        conn.execute('''
            UPDATE ansible_playbooks SET name=?, description=?, content=?, tags=?,
            updated_at=datetime('now','localtime') WHERE id=?
        ''', (name, desc, content, tags, pid))
        conn.commit(); conn.close()
        # Diske yaz
        _sync_playbook_to_disk({'name': name, 'content': content})
        flash('Playbook kaydedildi.', 'success')
        return redirect(url_for('ansible_playbook_edit', pid=pid))
    else:
        c = conn.execute('''
            INSERT INTO ansible_playbooks(name, description, content, tags)
            VALUES(?,?,?,?)
        ''', (name, desc, content, tags))
        new_id = c.lastrowid
        conn.commit(); conn.close()
        _sync_playbook_to_disk({'name': name, 'content': content})
        flash(f'Playbook "{name}" oluşturuldu.', 'success')
        return redirect(url_for('ansible_playbook_edit', pid=new_id))


@app.route('/ansible/playbooks/<int:pid>/delete', methods=['POST'])
@login_required
def ansible_playbook_delete(pid):
    conn = get_db()
    pb = conn.execute('SELECT * FROM ansible_playbooks WHERE id=?', (pid,)).fetchone()
    conn.execute('DELETE FROM ansible_playbooks WHERE id=?', (pid,))
    conn.commit(); conn.close()
    if pb:
        # Diskten sil
        safe_name = re.sub(r'[^\w\-]', '_', pb['name']) + '.yml'
        path = os.path.join(ANSIBLE_PLAYS_DIR, safe_name)
        try: os.unlink(path)
        except Exception: pass
        flash(f'Playbook "{pb["name"]}" silindi.', 'success')
    return redirect(url_for('ansible_playbooks'))


@app.route('/ansible/playbooks/<int:pid>/run', methods=['GET', 'POST'])
@login_required
def ansible_playbook_run(pid):
    conn  = get_db()
    pb     = conn.execute('SELECT * FROM ansible_playbooks WHERE id=?', (pid,)).fetchone()
    groups = conn.execute('SELECT name FROM ansible_groups ORDER BY name').fetchall()
    hosts  = conn.execute('SELECT name FROM ansible_hosts WHERE active=1 ORDER BY name').fetchall()
    conn.close()
    if not pb:
        flash('Playbook bulunamadı.', 'danger')
        return redirect(url_for('ansible_playbooks'))

    if request.method == 'GET':
        return render_template('ansible_run_form.html', pb=dict(pb),
                               groups=groups, hosts=hosts)

    # POST — çalıştır
    limit      = request.form.get('limit', '').strip()
    tags_run   = request.form.get('tags_run', '').strip()
    extra_vars = request.form.get('extra_vars', '').strip()
    verbosity  = request.form.get('verbosity', '0')
    check_mode = request.form.get('check_mode', '0') == '1'

    # Inventory güncelle
    _generate_inventory()

    # Playbook dosyasını diske yaz
    pb_path = _sync_playbook_to_disk(dict(pb))

    # Extra args
    extra_args = []
    if limit:
        extra_args += ['--limit', limit]
    if tags_run:
        extra_args += ['--tags', tags_run]
    if extra_vars:
        extra_args += ['--extra-vars', extra_vars]
    v_int = int(verbosity) if verbosity.isdigit() else 0
    if v_int > 0:
        extra_args.append('-' + 'v' * min(v_int, 4))
    if check_mode:
        extra_args.append('--check')

    # Run kaydı oluştur
    conn = get_db()
    c = conn.execute('''
        INSERT INTO ansible_runs(playbook_id, playbook_name, inventory, extra_vars,
                                 limit_hosts, tags_run, status, triggered_by)
        VALUES(?,?,?,?,?,?,?,?)
    ''', (pid, pb['name'], limit or 'all', extra_vars, limit, tags_run, 'pending',
          session.get('username', 'system')))
    run_id = c.lastrowid
    conn.commit(); conn.close()

    # Thread başlat
    t = threading.Thread(
        target=_do_ansible_run,
        args=(run_id, pb_path, extra_args),
        daemon=True
    )
    t.start()
    with _ansible_run_lock:
        _ansible_running[run_id] = t

    flash(f'Playbook "{pb["name"]}" çalıştırılıyor — Çalışma #{run_id}', 'info')
    return redirect(url_for('ansible_run_detail', rid=run_id))


# ── Runs ─────────────────────────────────────────────────────
@app.route('/ansible/runs')
@login_required
def ansible_runs():
    conn = get_db()
    runs = conn.execute('''
        SELECT r.*, p.name as pb_file
        FROM ansible_runs r
        LEFT JOIN ansible_playbooks p ON p.id = r.playbook_id
        ORDER BY r.id DESC LIMIT 100
    ''').fetchall()
    conn.close()
    return render_template('ansible_runs.html', runs=runs)


@app.route('/ansible/runs/<int:rid>')
@login_required
def ansible_run_detail(rid):
    conn = get_db()
    run = conn.execute('SELECT * FROM ansible_runs WHERE id=?', (rid,)).fetchone()
    conn.close()
    if not run:
        flash('Çalışma bulunamadı.', 'danger')
        return redirect(url_for('ansible_runs'))
    return render_template('ansible_run_detail.html', run=dict(run))


@app.route('/ansible/runs/<int:rid>/cancel', methods=['POST'])
@login_required
def ansible_run_cancel(rid):
    with _ansible_run_lock:
        proc = _ansible_running.get(rid)
    if proc and hasattr(proc, 'terminate'):
        try:
            proc.terminate()
            _append_run_log(rid, '\n[Kullanıcı tarafından durduruldu]')
            _set_run_status(rid, 'cancelled', -1)
            flash(f'Çalışma #{rid} durduruldu.', 'warning')
        except Exception as e:
            flash(f'Durdurma hatası: {e}', 'danger')
    else:
        flash('Aktif süreç bulunamadı.', 'warning')
    return redirect(url_for('ansible_run_detail', rid=rid))


@app.route('/ansible/runs/<int:rid>/delete', methods=['POST'])
@login_required
def ansible_run_delete(rid):
    conn = get_db()
    conn.execute('DELETE FROM ansible_runs WHERE id=?', (rid,))
    conn.commit(); conn.close()
    flash(f'Çalışma #{rid} silindi.', 'success')
    return redirect(url_for('ansible_runs'))


# ── Roles ────────────────────────────────────────────────────
@app.route('/ansible/roles')
@login_required
def ansible_roles():
    conn = get_db()
    roles = conn.execute('''
        SELECT r.*,
               (SELECT COUNT(*) FROM ansible_role_files f WHERE f.role_id=r.id) as file_count
        FROM ansible_roles r
        ORDER BY r.name
    ''').fetchall()
    conn.close()
    return render_template('ansible_roles.html', roles=roles)


@app.route('/ansible/roles/add', methods=['POST'])
@login_required
def ansible_role_add():
    name = request.form.get('name', '').strip()
    desc = request.form.get('description', '').strip()
    if not name:
        flash('Rol adı zorunlu.', 'danger')
        return redirect(url_for('ansible_roles'))
    conn = get_db()
    role_id = None
    try:
        c = conn.execute('INSERT INTO ansible_roles(name,description) VALUES(?,?)', (name, desc))
        role_id = c.lastrowid
        for section, (fname, fcontent) in {
            'tasks':    ('main.yml', f'---\n# Tasks for role: {name}\n'),
            'handlers': ('main.yml', f'---\n# Handlers for role: {name}\n'),
            'vars':     ('main.yml', f'---\n# Variables for role: {name}\n'),
            'defaults': ('main.yml', f'---\n# Default variables for role: {name}\n'),
            'meta':     ('main.yml', '---\ndependencies: []\n'),
        }.items():
            conn.execute(
                'INSERT INTO ansible_role_files(role_id,section,filename,content) VALUES(?,?,?,?)',
                (role_id, section, fname, fcontent)
            )
        conn.commit()
        _sync_role_to_disk(role_id)
        flash(f'Rol "{name}" oluşturuldu.', 'success')
    except Exception as e:
        conn.rollback()
        flash(f'Hata: {e}', 'danger')
        role_id = None
    conn.close()

    if role_id:
        return redirect(url_for('ansible_role_edit', rid=role_id))
    return redirect(url_for('ansible_roles'))


@app.route('/ansible/roles/add_go', methods=['POST'])
@login_required
def ansible_role_add_go():
    """Rol ekle ve direkt editöre git."""
    name = request.form.get('name', '').strip()
    desc = request.form.get('description', '').strip()
    if not name:
        flash('Rol adı zorunlu.', 'danger')
        return redirect(url_for('ansible_roles'))
    conn = get_db()
    try:
        c = conn.execute('INSERT INTO ansible_roles(name,description) VALUES(?,?)', (name, desc))
        role_id = c.lastrowid
        for section, fname, fcontent in [
            ('tasks',    'main.yml', f'---\n# Tasks for {name}\n'),
            ('handlers', 'main.yml', f'---\n# Handlers for {name}\n'),
            ('vars',     'main.yml', f'---\n# Variables for {name}\n'),
            ('defaults', 'main.yml', f'---\n# Default vars for {name}\n'),
            ('meta',     'main.yml', '---\ndependencies: []\n'),
        ]:
            conn.execute(
                'INSERT INTO ansible_role_files(role_id,section,filename,content) VALUES(?,?,?,?)',
                (role_id, section, fname, fcontent)
            )
        conn.commit(); conn.close()
        _sync_role_to_disk(role_id)
        flash(f'Rol "{name}" oluşturuldu.', 'success')
        return redirect(url_for('ansible_role_edit', rid=role_id))
    except Exception as e:
        conn.close()
        flash(f'Hata: {e}', 'danger')
        return redirect(url_for('ansible_roles'))


@app.route('/ansible/roles/<int:rid>')
@login_required
def ansible_role_edit(rid):
    conn = get_db()
    role  = conn.execute('SELECT * FROM ansible_roles WHERE id=?', (rid,)).fetchone()
    files = conn.execute(
        "SELECT * FROM ansible_role_files WHERE role_id=? ORDER BY section,filename",
        (rid,)
    ).fetchall()
    conn.close()
    if not role:
        flash('Rol bulunamadı.', 'danger')
        return redirect(url_for('ansible_roles'))
    # Dosyaları section'a göre grupla
    sections = {}
    for f in files:
        sections.setdefault(f['section'], []).append(dict(f))
    return render_template('ansible_role_editor.html', role=dict(role), sections=sections)


@app.route('/ansible/roles/<int:rid>/save-file', methods=['POST'])
@login_required
def ansible_role_save_file(rid):
    fid     = request.form.get('file_id')
    content = request.form.get('content', '')
    conn = get_db()
    if fid:
        conn.execute('UPDATE ansible_role_files SET content=? WHERE id=? AND role_id=?',
                     (content, int(fid), rid))
    conn.commit(); conn.close()
    _sync_role_to_disk(rid)
    return jsonify({'ok': True})


@app.route('/ansible/roles/<int:rid>/add-file', methods=['POST'])
@login_required
def ansible_role_add_file(rid):
    section  = request.form.get('section', 'tasks')
    filename = request.form.get('filename', 'new_file.yml').strip()
    conn = get_db()
    try:
        conn.execute('''
            INSERT INTO ansible_role_files(role_id,section,filename,content)
            VALUES(?,?,?,?)
        ''', (rid, section, filename, '---\n'))
        conn.commit()
        _sync_role_to_disk(rid)
        flash(f'{section}/{filename} oluşturuldu.', 'success')
    except Exception:
        flash('Dosya zaten mevcut.', 'danger')
    conn.close()
    return redirect(url_for('ansible_role_edit', rid=rid))


@app.route('/ansible/roles/<int:rid>/delete', methods=['POST'])
@login_required
def ansible_role_delete(rid):
    conn = get_db()
    role = conn.execute('SELECT name FROM ansible_roles WHERE id=?', (rid,)).fetchone()
    conn.execute('DELETE FROM ansible_role_files WHERE role_id=?', (rid,))
    conn.execute('DELETE FROM ansible_roles WHERE id=?', (rid,))
    conn.commit(); conn.close()
    if role:
        import shutil
        try:
            shutil.rmtree(os.path.join(ANSIBLE_ROLES_DIR, role['name']), ignore_errors=True)
        except Exception:
            pass
    flash('Rol silindi.', 'success')
    return redirect(url_for('ansible_roles'))


# ── API: run status polling ───────────────────────────────────
@app.route('/api/ansible/run-status/<int:rid>')
@login_required
def api_ansible_run_status(rid):
    conn = get_db()
    run  = conn.execute(
        'SELECT status, exit_code, started_at, finished_at, '
        'length(output) as out_len FROM ansible_runs WHERE id=?', (rid,)
    ).fetchone()
    conn.close()
    if not run:
        return jsonify({'error': 'not found'}), 404
    return jsonify(dict(run))


@app.route('/api/ansible/run-output/<int:rid>')
@login_required
def api_ansible_run_output(rid):
    """Son N satırı döner (polling için)."""
    offset = int(request.args.get('offset', 0))
    conn = get_db()
    row = conn.execute(
        'SELECT substr(output,?) as chunk, status, finished_at FROM ansible_runs WHERE id=?',
        (offset + 1, rid)
    ).fetchone()
    conn.close()
    if not row:
        return jsonify({'chunk': '', 'status': 'unknown'})
    return jsonify({'chunk': row['chunk'] or '', 'status': row['status'],
                    'finished_at': row['finished_at'] or ''})


@app.route('/api/ansible/ping-host', methods=['POST'])
@login_required
def api_ansible_ping_host():
    """Tek bir hosta ansible ping atar (bağlantı testi)."""
    hid = request.json.get('host_id')
    conn = get_db()
    host = conn.execute('SELECT * FROM ansible_hosts WHERE id=?', (hid,)).fetchone()
    conn.close()
    if not host:
        return jsonify({'ok': False, 'msg': 'Host bulunamadı.'})

    if not _ansible_check():
        return jsonify({'ok': False, 'msg': 'Ansible kurulu değil.'})

    import tempfile
    try:
        import yaml as _yaml
        _has_yaml = True
    except ImportError:
        _has_yaml = False

    # Inventory değişkenleri — _generate_inventory ile aynı mantık
    host = dict(host)
    hvars = {'ansible_host': host['hostname']}

    if host['os_type'] == 'windows':
        hvars['ansible_connection']      = 'winrm'
        hvars['ansible_port']            = int(host['winrm_port'] or 5985)
        hvars['ansible_winrm_scheme']    = host['winrm_scheme'] or 'http'
        hvars['ansible_winrm_transport'] = host['win_transport'] or 'ntlm'
        hvars['ansible_winrm_server_cert_validation'] = 'ignore'
        hvars['ansible_user']     = host['ansible_user']
        hvars['ansible_password'] = host['ansible_pass']
        ping_module = 'win_ping'
    else:
        hvars['ansible_connection'] = 'ssh'
        hvars['ansible_port']       = int(host['ssh_port'] or 22)
        hvars['ansible_user']       = host['ansible_user']
        if host['auth_type'] == 'key' and host['ssh_key_path']:
            hvars['ansible_ssh_private_key_file'] = host['ssh_key_path']
        else:
            hvars['ansible_password'] = host['ansible_pass']

        # become
        bm = host['become_method'] or 'none'
        if bm != 'none':
            hvars['ansible_become']        = True
            hvars['ansible_become_method'] = bm
            hvars['ansible_become_user']   = host['become_user'] or 'root'
            if int(host['become_same'] or 0) == 1:
                bp = host['ansible_pass']
            else:
                bp = host['become_pass'] or ''
            if bp:
                hvars['ansible_become_password'] = bp
        ping_module = 'ping'

    inv_dict = {'all': {'hosts': {host['name']: hvars}}}

    if _has_yaml:
        try:
            inv_str = _yaml.dump(inv_dict, default_flow_style=False, allow_unicode=True)
        except Exception:
            _has_yaml = False

    if not _has_yaml:
        # fallback INI format
        inv_str = f"[all]\n{host['name']}"
        for k, v in hvars.items():
            inv_str += f" {k}={v}"
        inv_str += "\n"

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yml',
                                     delete=False, prefix='anstmp_') as tf:
        tf.write(inv_str)
        tmp_inv = tf.name

    try:
        env = os.environ.copy()
        env['ANSIBLE_HOST_KEY_CHECKING'] = 'False'
        env['ANSIBLE_NOCOLOR']           = '1'
        env['ANSIBLE_CONFIG']            = os.path.join(ANSIBLE_DIR, 'ansible.cfg')
        r = subprocess.run(
            ['ansible', 'all', '-i', tmp_inv, '-m', ping_module],
            capture_output=True, text=True, timeout=30, env=env
        )
        ok  = r.returncode == 0
        out = (r.stdout + r.stderr).strip()
        return jsonify({'ok': ok, 'msg': out[:800]})
    except subprocess.TimeoutExpired:
        return jsonify({'ok': False, 'msg': 'Zaman aşımı (30 saniye)'})
    except Exception as e:
        return jsonify({'ok': False, 'msg': str(e)})
    finally:
        try: os.unlink(tmp_inv)
        except Exception: pass
if __name__ == '__main__':
    init_db()
    os.makedirs(BACKUP_ROOT, exist_ok=True)
    init_scheduler()

    print("=" * 64)
    print("  ReaR Manager v2.0 - Merkezi Yedekleme Yönetim Paneli")
    print(f"  Adres     : http://0.0.0.0:5000")
    print(f"  DB        : {DB_PATH}")
    print(f"  Yedekler  : {BACKUP_ROOT}")
    print(f"  Scheduler : {'APScheduler ✓' if HAS_SCHEDULER else 'Kurulu değil!'}")
    print(f"  LDAP/AD   : {'ldap3 ✓' if HAS_LDAP else 'Kurulu değil'}")
    print(f"  Varsayılan: admin / admin123  (Lütfen değiştirin!)")
    print("=" * 64)
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
