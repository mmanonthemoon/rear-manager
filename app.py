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

from config import (
    BASE_DIR, DB_PATH, BACKUP_ROOT, KEY_PATH, BUILTIN_ADMIN, OFFLINE_PKG_DIR,
    UBUNTU_CODENAMES, ANSIBLE_DIR, ANSIBLE_PLAYS_DIR, ANSIBLE_ROLES_DIR,
    ANSIBLE_FILES_DIR, ANSIBLE_INV_DIR, ANSIBLE_HVARS_DIR, ANSIBLE_GVARS_DIR,
    SECRET_KEY_FILE, SCHEDULER_TIMEZONES,
)
from db import init_db
from models import users as user_repo, servers as server_repo, schedules as schedule_repo, \
    jobs as job_repo, settings as settings_repo
from models import ansible as ansible_repo
from services import ssh as ssh_service
from services import rear as rear_service
from services import jobs as job_service
from services import auth as auth_service
from services import scheduler as scheduler_service
from services import ansible as ansible_service
from services.auth import login_required, admin_required, authenticate_local, authenticate_ad
from services.scheduler import init_scheduler, get_next_run, get_all_jobs as get_scheduler_jobs, _add_scheduler_job, _remove_scheduler_job, _restart_scheduler_with_timezone
from services.ansible import (
    _ansible_check, _ansible_version, _generate_inventory,
    _sync_playbook_to_disk, _sync_role_to_disk,
    _append_run_log, _set_run_status, start_ansible_run,
    _save_ansible_host, _save_playbook,
)

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


def _load_or_create_secret_key():
    if os.path.exists(SECRET_KEY_FILE):
        with open(SECRET_KEY_FILE, 'r') as f:
            key = f.read().strip()
        if key:
            return key
    key = secrets.token_hex(32)
    with open(SECRET_KEY_FILE, 'w') as f:
        f.write(key)
    os.chmod(SECRET_KEY_FILE, 0o600)
    return key


app = Flask(__name__)
app.secret_key = _load_or_create_secret_key()


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


def _safe_dirname(hostname):
    """
    Hostname'i güvenli bir dizin adına dönüştürür.
    Nokta ve özel karakterleri kaldırır/değiştirir.
    Örnek: 'web01.example.com' → 'web01-example-com'
             '192.168.1.49'    → '192-168-1-49'
    """
    safe = re.sub(r'[^a-zA-Z0-9_-]', lambda m: '-' if m.group() == '.' else '', hostname)
    safe = re.sub(r'-{2,}', '-', safe)
    safe = safe.strip('-')
    return safe or hostname


app.jinja_env.globals['_safe_dirname']  = _safe_dirname


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
# VERİTABANI (get_db, init_db, _migrate_db, _init_ansible_workspace
#              moved to db.py)
# ─────────────────────────────────────────────────────────────


def get_settings():
    return settings_repo.get_settings()


def save_setting(key, value):
    settings_repo.save_setting(key, value)


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
    nfs://<central_ip><nfs_export_path>/<safe_dirname>
    """
    return settings_repo.get_nfs_target(hostname, _get_local_ip, _safe_dirname, BACKUP_ROOT)


# ─────────────────────────────────────────────────────────────
# KİMLİK DOĞRULAMA — moved to services/auth.py
# login_required, admin_required, authenticate_local, authenticate_ad
# imported at top of file via: from services.auth import login_required, ...
# ─────────────────────────────────────────────────────────────




# ─────────────────────────────────────────────────────────────
# ZAMANLAYICI (APScheduler) — moved to services/scheduler.py
# init_scheduler, get_next_run, _add_scheduler_job, _remove_scheduler_job,
# _restart_scheduler_with_timezone, _scheduler_run_backup
# imported at top of file via: from services.scheduler import init_scheduler, ...
# ─────────────────────────────────────────────────────────────


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
                user_id, _ = user_repo.upsert_ad_user(username, full_name, role)

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
                user_repo.update_last_login(user['id'])

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
    servers = server_repo.get_all()
    jobs    = job_repo.get_recent(12)
    _running_count = job_service.get_running_count()
    server_stats = server_repo.get_dashboard_stats()
    job_stats = job_repo.get_stats()
    stats = {
        'total_servers':      server_stats['total_servers'],
        'installed_servers':  server_stats['installed_servers'],
        'configured_servers': server_stats['configured_servers'],
        'total_backups':      job_stats['total_backups'],
        'success_backups':    job_stats['success_backups'],
        'failed_backups':     job_stats['failed_backups'],
        'running_jobs':       _running_count,
        'active_schedules':   schedule_repo.get_count(),
    }

    backup_info = {}
    for s in servers:
        d = os.path.join(BACKUP_ROOT, _safe_dirname(s['hostname']))
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
    servers = server_repo.get_all()
    # Ansible bağlantı durumu
    ansible_map = {}
    for s in servers:
        if s['ansible_host_id']:
            ah = server_repo.get_ansible_host_info(s['ansible_host_id'])
            ansible_map[s['id']] = ah
        else:
            ansible_map[s['id']] = None
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

        server_row = server_repo.create(
            label, hostname, ip_address, ssh_port, ssh_user,
            d.get('ssh_auth', 'password'), d.get('ssh_password', ''),
            d.get('become_method', 'none'), d.get('become_user', 'root'),
            d.get('become_password', ''), 1 if d.get('become_same_pass') else 0,
            d.get('exclude_dirs', ''), d.get('notes', '')
        )
        new_sid = server_row['id']

        # Sunucu eklenince varsayılan ayarlarla /etc/rear/local.conf otomatik oluştur
        settings = get_settings()
        srv_dict = dict(server_row)
        content = rear_service.generate_rear_config(srv_dict, settings)
        job_id = job_service.create_job(new_sid, 'configure', triggered_by='auto')
        job_service.start_job_thread(rear_service._run_configure_rear, job_id, srv_dict, content)

        flash(f'Sunucu "{label}" eklendi. Varsayılan ReaR yapılandırması uygulanıyor...', 'success')
        return redirect(url_for('job_detail', jid=job_id))
    cfg = get_settings()
    return render_template('server_form.html', server=None, title='Sunucu Ekle', cfg=cfg)


@app.route('/servers/<int:sid>/edit', methods=['GET', 'POST'])
@login_required
def server_edit(sid):
    server = server_repo.get_by_id(sid)
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

        server_repo.update(
            sid, label, hostname, ip_address, ssh_port, ssh_user,
            d.get('ssh_auth', 'password'), d.get('ssh_password', ''),
            d.get('become_method', 'none'), d.get('become_user', 'root'),
            d.get('become_password', ''), 1 if d.get('become_same_pass') else 0,
            d.get('exclude_dirs', ''), d.get('notes', '')
        )
        flash('Sunucu güncellendi.', 'success')
        return redirect(url_for('server_detail', sid=sid))
    cfg = get_settings()
    return render_template('server_form.html', server=dict(server),
                           title='Sunucu Düzenle', cfg=cfg)


@app.route('/servers/<int:sid>/delete', methods=['POST'])
@login_required
def server_delete(sid):
    server_repo.delete(sid)
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

    servers_to_add = []
    errors = []
    skipped = 0

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

            servers_to_add.append((label, hostname, ip, port, ssh_user,
                                   ssh_auth, ssh_pass, bmethod, buser, bpass, bsame, notes))

        except Exception as e:
            errors.append(f"Satır {lineno}: {str(e)} → '{line}'")
            skipped += 1

    added, repo_skipped, repo_errors = server_repo.bulk_create(servers_to_add)
    skipped += repo_skipped
    errors.extend(repo_errors)

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
    server = server_repo.get_by_id(sid)
    if not server:
        flash('Sunucu bulunamadı.', 'danger')
        return redirect(url_for('servers_list'))
    jobs = job_repo.get_by_server(sid)
    schedules = schedule_repo.get_by_server(sid)

    # Ansible bağlantısı
    ansible_host = None
    if server['ansible_host_id']:
        ah = ansible_repo.get_host_by_id(server['ansible_host_id'])
        if ah:
            ansible_host = dict(ah)

    # Bağlanabilecek mevcut Ansible hostları (henüz bağlı olmayanlar)
    all_ansible_hosts = ansible_repo.get_hosts()

    backup_dir = os.path.join(BACKUP_ROOT, _safe_dirname(server['hostname']))
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
    running_job_ids = set(job_service.get_running_job_ids())

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
    server = server_repo.get_by_id(sid)
    if not server:
        flash('Sunucu bulunamadı.', 'danger')
        return redirect(url_for('servers_list'))

    server = dict(server)

    # Zaten bağlı mı?
    if server['ansible_host_id']:
        ah = ansible_repo.get_linked_host_info(server['ansible_host_id'])
        if ah:
            flash(f'Bu sunucu zaten "{ah["name"]}" Ansible hostuna bağlı.', 'info')
            return redirect(url_for('server_detail', sid=sid))

    # Aynı IP veya hostname ile mevcut Ansible host var mı?
    existing = ansible_repo.get_existing_ansible_host_for_server(server['ip_address'], server['hostname'])

    if existing:
        # Mevcut hosta bağla
        ansible_repo.link_server_to_host(sid, existing['id'])
        flash(f'Mevcut Ansible hostu "{existing["name"]}" ile bağlandı.', 'success')
        return redirect(url_for('server_detail', sid=sid))

    # Yeni Ansible host oluştur
    # IP adresi girilmişse (örn: 192.168.1.49) noktaları tire ile değiştir,
    # FQDN girilmişse (örn: web01.example.com) kısa ismi al.
    _hn = server['hostname']
    if all(p.isdigit() for p in _hn.split('.') if p):
        host_name = _hn.replace('.', '-')   # 192.168.1.49 → 192-168-1-49
    else:
        host_name = _hn.split('.')[0]       # web01.example.com → web01

    # İsim çakışması varsa suffix ekle
    if ansible_repo.check_host_name_exists(host_name):
        host_name = f"{host_name}-rear"

    try:
        ansible_repo.create_host_from_server(server, host_name)

        # Inventory'i güncelle
        _generate_inventory()

        flash(f'✓ Ansible hostu "{host_name}" oluşturuldu ve bağlandı. '
              f'Gerekirse Ansible → Hostlar sayfasından düzenleyebilirsiniz.', 'success')

    except Exception as e:
        flash(f'Ansible host oluşturma hatası: {e}', 'danger')

    return redirect(url_for('server_detail', sid=sid))


@app.route('/servers/<int:sid>/ansible-link', methods=['POST'])
@login_required
def server_ansible_link(sid):
    """Mevcut bir Ansible hostunu bu sunucuya bağlar."""
    ansible_host_id = request.form.get('ansible_host_id', type=int)

    server = server_repo.get_by_id(sid)
    if not server:
        flash('Sunucu bulunamadı.', 'danger')
        return redirect(url_for('servers_list'))

    if ansible_host_id:
        ah = ansible_repo.get_linked_host_info(ansible_host_id)
        if ah:
            ansible_repo.link_server_to_host(sid, ansible_host_id)
            flash(f'"{ah["name"]}" Ansible hostuna bağlandı.', 'success')
        else:
            flash('Seçilen Ansible hostu bulunamadı.', 'danger')
    else:
        flash('Geçerli bir Ansible hostu seçin.', 'warning')

    return redirect(url_for('server_detail', sid=sid))


@app.route('/servers/<int:sid>/ansible-unlink', methods=['POST'])
@login_required
def server_ansible_unlink(sid):
    """Ansible host bağlantısını kaldırır (Ansible hostu silmez)."""
    ansible_repo.unlink_server_host(sid)
    flash('Ansible host bağlantısı kaldırıldı.', 'info')
    return redirect(url_for('server_detail', sid=sid))


# ─────────────────────────────────────────────────────────────
# SSH BAĞLANTI TESTİ
# ─────────────────────────────────────────────────────────────
@app.route('/servers/<int:sid>/test', methods=['POST'])
@login_required
def server_test(sid):
    server = server_repo.get_by_id(sid)
    if not server:
        return jsonify({'ok': False, 'msg': 'Sunucu bulunamadı'})
    ok, msg = ssh_service.ssh_test_connection(dict(server))
    return jsonify({'ok': ok, 'msg': msg})


# ─────────────────────────────────────────────────────────────
# REAR KURULUM / YAPILANDIRMA / YEDEKLEME
# ─────────────────────────────────────────────────────────────
@app.route('/servers/<int:sid>/install', methods=['POST'])
@login_required
def server_install_rear(sid):
    server = server_repo.get_by_id(sid)
    if not server:
        flash('Sunucu bulunamadı.', 'danger')
        return redirect(url_for('servers_list'))
    job_id = job_service.create_job(sid, 'install')
    job_service.start_job_thread(rear_service._run_install_rear, job_id, dict(server))
    flash(f'ReaR kurulumu başlatıldı. İş #{job_id}', 'info')
    return redirect(url_for('job_detail', jid=job_id))


@app.route('/servers/<int:sid>/configure', methods=['GET', 'POST'])
@login_required
def server_configure(sid):
    server = server_repo.get_by_id(sid)
    if not server:
        flash('Sunucu bulunamadı.', 'danger')
        return redirect(url_for('servers_list'))
    settings = get_settings()

    if request.method == 'POST':
        if not server['rear_installed']:
            flash('ReaR kurulu değil. Önce ReaR kurulumunu tamamlayın.', 'warning')
            return redirect(url_for('server_detail', sid=sid))

        cfg = dict(settings)
        cfg['autoresize']    = request.form.get('autoresize', '0')
        cfg['migration_mode']= request.form.get('migration_mode', '0')
        cfg['rear_output']   = request.form.get('rear_output', 'ISO')
        cfg['rear_backup']   = request.form.get('rear_backup', 'NETFS')

        # Sunucuya özel hariç dizinleri kaydet
        server_excl = request.form.get('server_exclude_dirs', '')
        server_repo.update_exclude_dirs(sid, server_excl)

        srv_dict = dict(server)
        srv_dict['exclude_dirs'] = server_excl
        content  = rear_service.generate_rear_config(srv_dict, cfg)

        job_id = job_service.create_job(sid, 'configure')
        job_service.start_job_thread(rear_service._run_configure_rear, job_id, srv_dict, content)
        flash(f'Yapılandırma gönderildi. İş #{job_id}', 'info')
        return redirect(url_for('job_detail', jid=job_id))

    srv_dict = dict(server)
    preview  = rear_service.generate_rear_config(srv_dict, settings)
    return render_template('configure.html', server=srv_dict,
                           settings=settings, preview=preview)


@app.route('/servers/<int:sid>/backup', methods=['POST'])
@login_required
def server_backup(sid):
    server = server_repo.get_by_id(sid)
    if not server:
        flash('Sunucu bulunamadı.', 'danger')
        return redirect(url_for('servers_list'))

    if not server['rear_installed']:
        flash('ReaR kurulu değil. Önce ReaR kurulumunu tamamlayın.', 'warning')
        return redirect(url_for('server_detail', sid=sid))
    if not server['rear_configured']:
        flash('ReaR yapılandırılmamış. Önce yapılandırma uygulayın.', 'warning')
        return redirect(url_for('server_detail', sid=sid))

    btype  = request.form.get('backup_type', 'mkbackup')
    job_id = job_service.create_job(sid, 'backup', triggered_by='manual')
    job_service.start_job_thread(job_service._do_backup, job_id, dict(server), btype, 'manual', None)
    flash(f'Yedekleme başlatıldı. İş #{job_id}', 'info')
    return redirect(url_for('job_detail', jid=job_id))


# ─────────────────────────────────────────────────────────────
# ZAMANLAMA YÖNETİMİ
# ─────────────────────────────────────────────────────────────
@app.route('/servers/<int:sid>/schedules/add', methods=['POST'])
@login_required
def schedule_add(sid):
    server = server_repo.get_by_id(sid)
    if not server:
        flash('Sunucu bulunamadı.', 'danger')
        return redirect(url_for('servers_list'))

    d = request.form
    sched_id = schedule_repo.create(
        sid,
        d.get('backup_type', 'mkbackup'),
        d.get('cron_minute', '0'),
        d.get('cron_hour', '2'),
        d.get('cron_dom', '*'),
        d.get('cron_month', '*'),
        d.get('cron_dow', '*'),
    )

    _add_scheduler_job(sched_id,
                       d.get('cron_minute', '0'), d.get('cron_hour', '2'),
                       d.get('cron_dom', '*'), d.get('cron_month', '*'),
                       d.get('cron_dow', '*'))

    flash(f'Zamanlama #{sched_id} oluşturuldu.', 'success')
    return redirect(url_for('server_detail', sid=sid))


@app.route('/schedules/<int:scid>/toggle', methods=['POST'])
@login_required
def schedule_toggle(scid):
    result = schedule_repo.toggle(scid)
    if not result:
        return jsonify({'ok': False})
    sched, new_state = result
    sid = sched['server_id']

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
    sched = schedule_repo.delete(scid)
    if not sched:
        flash('Zamanlama bulunamadı.', 'danger')
        return redirect(url_for('servers_list'))
    sid = sched['server_id']
    _remove_scheduler_job(scid)
    flash(f'Zamanlama #{scid} silindi.', 'success')
    return redirect(url_for('server_detail', sid=sid))


@app.route('/schedules/<int:scid>/run-now', methods=['POST'])
@login_required
def schedule_run_now(scid):
    sched  = schedule_repo.get_by_id(scid)
    server = server_repo.get_by_id(sched['server_id']) if sched else None
    if not sched or not server:
        flash('Bulunamadı.', 'danger')
        return redirect(url_for('servers_list'))
    if not server['rear_installed']:
        flash('ReaR kurulu değil. Önce ReaR kurulumunu tamamlayın.', 'warning')
        return redirect(url_for('server_detail', sid=server['id']))
    if not server['rear_configured']:
        flash('ReaR yapılandırılmamış. Önce yapılandırma uygulayın.', 'warning')
        return redirect(url_for('server_detail', sid=server['id']))
    job_id = job_service.create_job(server['id'], 'backup', triggered_by='manual-schedule', schedule_id=scid)
    job_service.start_job_thread(job_service._do_backup, job_id, dict(server),
                     sched['backup_type'] or 'mkbackup', 'manual-schedule', scid)
    flash(f'Zamanlama #{scid} hemen çalıştırıldı. İş #{job_id}', 'info')
    return redirect(url_for('job_detail', jid=job_id))


# ─────────────────────────────────────────────────────────────
# İŞ YÖNETİMİ
# ─────────────────────────────────────────────────────────────
@app.route('/jobs')
@login_required
def jobs_list():
    # Filtreleme
    status_filter = request.args.get('status', '')
    type_filter   = request.args.get('type', '')
    server_filter = request.args.get('server', '')

    jobs    = job_repo.get_all_filtered(status_filter, type_filter, server_filter)
    servers = job_repo.get_servers_list()
    running_job_ids = set(job_service.get_running_job_ids())
    return render_template('jobs.html', jobs=jobs, servers=servers,
                           status_filter=status_filter, type_filter=type_filter,
                           server_filter=server_filter,
                           running_job_ids=running_job_ids)


@app.route('/jobs/<int:jid>')
@login_required
def job_detail(jid):
    job = job_repo.get_by_id(jid)
    if not job:
        flash('İş bulunamadı.', 'danger')
        return redirect(url_for('jobs_list'))
    _is_running = job_service.is_job_running(jid)
    return render_template('job_detail.html',
                           job=dict(job),
                           is_running=_is_running)


@app.route('/jobs/<int:jid>/log')
@login_required
def job_log_api(jid):
    row = job_repo.get_log(jid)
    if not row:
        return jsonify({'log': '', 'status': 'notfound'})
    _is_running = job_service.is_job_running(jid)
    return jsonify({'log': row['log_output'] or '', 'status': row['status'],
                    'finished_at': row['finished_at'] or '',
                    'running': _is_running})


@app.route('/jobs/<int:jid>/cancel', methods=['POST'])
@login_required
def job_cancel(jid):
    job_service._set_job_status(jid, 'cancelled')
    flash(f'İş #{jid} iptal edildi.', 'warning')
    return redirect(url_for('job_detail', jid=jid))


@app.route('/jobs/<int:jid>/delete', methods=['POST'])
@login_required
def job_delete(jid):
    job = job_repo.get_server_id(jid)
    job_repo.delete(jid)
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

        if tab == 'general':
            keys = ['central_ip', 'nfs_export_path',
                    'rear_output', 'rear_backup',
                    'ssh_key_path', 'retention_days', 'session_timeout',
                    'autoresize', 'migration_mode', 'global_exclude_dirs']
        elif tab == 'ad':
            keys = ['ad_enabled', 'ad_server', 'ad_port', 'ad_domain',
                    'ad_base_dn', 'ad_bind_user', 'ad_bind_password',
                    'ad_user_filter', 'ad_admin_group', 'ad_user_group']
        elif tab == 'scheduler':
            tz = request.form.get('scheduler_timezone', 'Europe/Istanbul')
            try:
                import pytz
                pytz.timezone(tz)  # validate
            except Exception:
                flash('Geçersiz timezone seçimi.', 'danger')
                return redirect(url_for('settings_page', tab='scheduler'))
            save_setting('scheduler_timezone', tz)
            if HAS_SCHEDULER:
                _restart_scheduler_with_timezone(tz)
            flash('Zamanlayıcı ayarları kaydedildi.', 'success')
            return redirect(url_for('settings_page', tab='scheduler'))
        else:
            keys = []

        kv = {k: request.form.get(k, '') for k in keys}
        settings_repo.save_many(kv)
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
    offline_pkg_status = rear_service.get_offline_pkg_status()

    return render_template('settings.html', settings=settings,
                           du_info=du_info, active_tab=active_tab,
                           has_scheduler=HAS_SCHEDULER, has_ldap=HAS_LDAP,
                           offline_pkg_status=offline_pkg_status,
                           ubuntu_codenames=UBUNTU_CODENAMES,
                           offline_pkg_dir=OFFLINE_PKG_DIR,
                           scheduler_timezones=SCHEDULER_TIMEZONES)


@app.route('/settings/setup-nfs', methods=['POST'])
@login_required
@admin_required
def setup_nfs():
    """Bu rota artık kullanılmıyor. NFS/SMB yapılandırması kullanıcı tarafından yönetilir."""
    flash('NFS/SMB yapılandırması Linux sunucuda kendiniz tarafından yapılmalıdır. '
          'Yedek Sunucu IP ve Yedek Dizini Ayarlar → Genel sekmesinde yapılandırın.', 'info')
    return redirect(url_for('settings_page', tab='tools'))


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
    server = server_repo.get_by_id(sid)
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
        ec, out = ssh_service.ssh_exec_stream(dict(server), cmd, lambda x: None)
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
    users = user_repo.get_all()
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

        if user_repo.check_username_exists(uname):
            flash('Bu kullanıcı adı zaten mevcut.', 'danger')
            return redirect(url_for('user_add'))

        pw_hash = None
        if d.get('auth_type', 'local') == 'local':
            pw = d.get('password', '')
            if not pw:
                flash('Yerel hesap için şifre gerekli.', 'danger')
                return redirect(url_for('user_add'))
            pw_hash = generate_password_hash(pw)

        user_repo.create(uname, pw_hash, d.get('full_name', ''), d.get('role', 'user'), d.get('auth_type', 'local'))
        flash(f'Kullanıcı "{uname}" eklendi.', 'success')
        return redirect(url_for('users_list'))
    return render_template('user_form.html', user=None, title='Kullanıcı Ekle')


@app.route('/users/<int:uid>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def user_edit(uid):
    user = user_repo.get_by_id(uid)
    if not user:
        flash('Kullanıcı bulunamadı.', 'danger')
        return redirect(url_for('users_list'))

    if request.method == 'POST':
        d = request.form
        pw_hash = user['password_hash']
        new_pw = d.get('password', '').strip()
        if new_pw:
            pw_hash = generate_password_hash(new_pw)

        user_repo.update_full(
            uid,
            d.get('full_name', ''),
            d.get('role', 'user') if not user['is_builtin'] else 'admin',
            1 if d.get('active') else 0,
            pw_hash
        )
        flash('Kullanıcı güncellendi.', 'success')
        return redirect(url_for('users_list'))

    return render_template('user_form.html', user=dict(user), title='Kullanıcı Düzenle')


@app.route('/users/<int:uid>/delete', methods=['POST'])
@login_required
@admin_required
def user_delete(uid):
    user = user_repo.get_by_id(uid)
    if not user:
        flash('Kullanıcı bulunamadı.', 'danger')
        return redirect(url_for('users_list'))
    if user['is_builtin']:
        flash('Yerleşik admin hesabı silinemez!', 'danger')
        return redirect(url_for('users_list'))
    if user['id'] == session.get('user_id'):
        flash('Kendi hesabınızı silemezsiniz!', 'danger')
        return redirect(url_for('users_list'))
    user_repo.delete(uid)
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

        user = user_repo.get_by_id(session['user_id'])

        if not user or user['auth_type'] != 'local':
            flash('Bu işlem sadece yerel hesaplar için geçerlidir.', 'danger')
            return redirect(url_for('dashboard'))

        if not check_password_hash(user['password_hash'] or '', old_pw):
            flash('Mevcut şifre hatalı.', 'danger')
            return redirect(url_for('change_password'))

        user_repo.update_password(session['user_id'], generate_password_hash(new_pw))
        flash('Şifre değiştirildi.', 'success')
        return redirect(url_for('dashboard'))

    return render_template('change_password.html')


# ─────────────────────────────────────────────────────────────
# API
# ─────────────────────────────────────────────────────────────
@app.route('/api/status')
@login_required
def api_status():
    running = []
    _job_ids = job_service.get_running_job_ids()
    for jid in _job_ids:
        row = job_repo.get_running_job_info(jid)
        if row:
            running.append(dict(row))
    return jsonify({'running': running, 'count': len(running)})


@app.route('/api/schedules-status')
@login_required
def api_schedules_status():
    jobs = []
    for job in get_scheduler_jobs():
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
        'status':     rear_service.get_offline_pkg_status(),
        'base_dir':   OFFLINE_PKG_DIR,
        'codenames':  UBUNTU_CODENAMES,
    })


# ═════════════════════════════════════════════════════════════
# ██████████████████ ANSIBLE MODÜLÜ ███████████████████████████
# ═════════════════════════════════════════════════════════════
# All ansible service functions moved to services/ansible.py
# _ansible_running, _ansible_run_lock, and all helpers imported at top of file.

# ─── Ansible Rotaları ────────────────────────────────────────
@app.route('/ansible/')
@login_required
def ansible_dashboard():
    stats = ansible_repo.get_dashboard_stats()
    recent_runs = ansible_repo.get_recent_runs(15)
    ansible_ok  = _ansible_check()
    ansible_ver = _ansible_version() if ansible_ok else 'Kurulu değil'
    return render_template('ansible_dashboard.html',
                           stats=stats, recent_runs=recent_runs,
                           ansible_ok=ansible_ok, ansible_ver=ansible_ver)


# ── Hosts ────────────────────────────────────────────────────
@app.route('/ansible/hosts')
@login_required
def ansible_hosts():
    hosts, groups, hg = ansible_repo.get_hosts_with_groups()
    hg_map = {}
    for row in hg:
        hg_map.setdefault(row['host_id'], []).append(row['group_id'])
    group_map = {g['id']: g['name'] for g in groups}
    return render_template('ansible_hosts.html', hosts=hosts, groups=groups,
                           hg_map=hg_map, group_map=group_map)


@app.route('/ansible/hosts/add', methods=['GET', 'POST'])
@login_required
def ansible_host_add():
    groups = ansible_repo.get_groups()
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
    groups = ansible_repo.get_groups()

    if request.method == 'GET':
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

    hosts_to_add = []
    parse_errors = []
    pre_skipped = 0

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
            parse_errors.append(f"Satır {lineno}: En az 2 alan gerekli (name, hostname). → '{line}'")
            pre_skipped += 1
            continue

        try:
            name     = parts[0]
            hostname = parts[1]
            if not name or not hostname:
                parse_errors.append(f"Satır {lineno}: Name veya hostname boş.")
                pre_skipped += 1
                continue

            # 3. sütun: os_type (linux/windows) — yoksa varsayılan
            os_type = parts[2].lower() if len(parts) > 2 and parts[2].lower() in ('linux','windows') else def_os

            if os_type == 'windows':
                winrm_port = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else def_winrm_port
                user       = parts[4] if len(parts) > 4 and parts[4] else def_user
                passwd     = parts[5] if len(parts) > 5 and parts[5] else def_pass
                transport  = parts[6].lower() if len(parts) > 6 and parts[6] in ('ntlm','basic','kerberos') else def_transport
                domain     = parts[7] if len(parts) > 7 else ''
                grp_name   = parts[8].lower() if len(parts) > 8 and parts[8] else def_group.lower()
                notes      = parts[9] if len(parts) > 9 else ''
                hosts_to_add.append({
                    'name': name, 'hostname': hostname, 'os_type': 'windows',
                    'connection_type': 'winrm', 'ssh_port': 22,
                    'winrm_port': winrm_port, 'winrm_scheme': 'http',
                    'ansible_user': user, 'ansible_pass': passwd, 'auth_type': 'password',
                    'become_method': 'none', 'become_user': '', 'become_pass': '', 'become_same': 0,
                    'notes': notes, 'group_name': grp_name,
                })
            else:
                ssh_port     = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else def_ssh_port
                user         = parts[4] if len(parts) > 4 and parts[4] else def_user
                passwd       = parts[5] if len(parts) > 5 and parts[5] else def_pass
                become       = parts[6].lower() if len(parts) > 6 and parts[6] in ('sudo','su','none') else def_become
                become_user  = parts[7] if len(parts) > 7 and parts[7] else def_become_user
                bsame_raw    = parts[8] if len(parts) > 8 else str(def_become_same)
                become_same  = 1 if bsame_raw in ('1','true','evet','yes') else 0
                grp_name     = parts[9].lower() if len(parts) > 9 and parts[9] else def_group.lower()
                notes        = parts[10] if len(parts) > 10 else ''
                hosts_to_add.append({
                    'name': name, 'hostname': hostname, 'os_type': 'linux',
                    'connection_type': 'ssh', 'ssh_port': ssh_port,
                    'winrm_port': 5985, 'winrm_scheme': 'http',
                    'ansible_user': user, 'ansible_pass': passwd, 'auth_type': 'password',
                    'become_method': become, 'become_user': become_user, 'become_pass': '', 'become_same': become_same,
                    'notes': notes, 'group_name': grp_name,
                })

        except Exception as e:
            parse_errors.append(f"Satır {lineno}: {str(e)} → '{line}'")
            pre_skipped += 1

    added, repo_skipped, repo_errors = ansible_repo.bulk_create_hosts(hosts_to_add)
    skipped = pre_skipped + repo_skipped
    errors = parse_errors + repo_errors

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
    host = ansible_repo.get_host_by_id(hid)
    groups = ansible_repo.get_groups()
    sel_groups = ansible_repo.get_host_groups(hid)
    if not host:
        flash('Host bulunamadı.', 'danger')
        return redirect(url_for('ansible_hosts'))
    if request.method == 'POST':
        return _save_ansible_host(hid)
    settings = get_settings()
    return render_template('ansible_host_form.html', host=dict(host),
                           groups=groups, sel_groups=sel_groups,
                           title='Host Düzenle', settings=settings)


@app.route('/ansible/hosts/<int:hid>/delete', methods=['POST'])
@login_required
def ansible_host_delete(hid):
    h = ansible_repo.delete_host(hid)
    flash(f'Host "{h["name"] if h else hid}" silindi.', 'success')
    return redirect(url_for('ansible_hosts'))


# ── Groups ───────────────────────────────────────────────────
@app.route('/ansible/groups', methods=['GET', 'POST'])
@login_required
def ansible_groups():
    if request.method == 'POST':
        action = request.form.get('action', 'add')
        if action == 'add':
            name = request.form.get('name', '').strip()
            desc = request.form.get('description', '').strip()
            if name:
                try:
                    ansible_repo.create_group(name, desc)
                    flash(f'Grup "{name}" eklendi.', 'success')
                except Exception:
                    flash('Grup adı zaten mevcut.', 'danger')
        elif action == 'delete':
            gid = int(request.form.get('gid', 0))
            ansible_repo.delete_group(gid)
            flash('Grup silindi.', 'success')
        elif action == 'save_vars':
            gid   = int(request.form.get('gid', 0))
            vyaml = request.form.get('vars_yaml', '')
            ansible_repo.save_group_vars(gid, vyaml)
            flash('Grup değişkenleri kaydedildi.', 'success')
        return redirect(url_for('ansible_groups'))

    groups = ansible_repo.get_groups()
    hcounts = ansible_repo.get_group_host_counts(groups)
    return render_template('ansible_groups.html', groups=groups, hcounts=hcounts)


# ── Playbooks ────────────────────────────────────────────────
@app.route('/ansible/playbooks')
@login_required
def ansible_playbooks():
    pbs = ansible_repo.get_playbooks()
    last_runs = {}
    for pb in pbs:
        r = ansible_repo.get_playbook_last_run(pb['id'])
        if r:
            last_runs[pb['id']] = r
    return render_template('ansible_playbooks.html', playbooks=pbs, last_runs=last_runs)


@app.route('/ansible/playbooks/add', methods=['GET', 'POST'])
@login_required
def ansible_playbook_add():
    if request.method == 'POST':
        return _save_playbook(None)
    groups = ansible_repo.get_group_names()
    return render_template('ansible_playbook_editor.html',
                           pb=None, title='Yeni Playbook', groups=groups)


@app.route('/ansible/playbooks/<int:pid>/edit', methods=['GET', 'POST'])
@login_required
def ansible_playbook_edit(pid):
    pb = ansible_repo.get_playbook_by_id(pid)
    groups = ansible_repo.get_group_names()
    if not pb:
        flash('Playbook bulunamadı.', 'danger')
        return redirect(url_for('ansible_playbooks'))
    if request.method == 'POST':
        return _save_playbook(pid)
    return render_template('ansible_playbook_editor.html',
                           pb=dict(pb), title=f'Düzenle: {pb["name"]}', groups=groups)


@app.route('/ansible/playbooks/<int:pid>/delete', methods=['POST'])
@login_required
def ansible_playbook_delete(pid):
    pb = ansible_repo.delete_playbook(pid)
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
    pb     = ansible_repo.get_playbook_by_id(pid)
    groups = ansible_repo.get_group_names()
    hosts  = ansible_repo.get_host_names_active()
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
    run_id = ansible_repo.create_run(
        playbook_id=pid,
        playbook_name=pb['name'],
        inventory=limit or 'all',
        extra_vars=extra_vars,
        limit_hosts=limit,
        tags_run=tags_run,
        triggered_by=session.get('username', 'system')
    )

    # Thread başlat — app context sağlayan wrapper ile
    start_ansible_run(run_id, pb_path, extra_args)

    flash(f'Playbook "{pb["name"]}" çalıştırılıyor — Çalışma #{run_id}', 'info')
    return redirect(url_for('ansible_run_detail', rid=run_id))


# ── Runs ─────────────────────────────────────────────────────
@app.route('/ansible/runs')
@login_required
def ansible_runs():
    runs = ansible_repo.get_runs(limit=100)
    return render_template('ansible_runs.html', runs=runs)


@app.route('/ansible/runs/<int:rid>')
@login_required
def ansible_run_detail(rid):
    run = ansible_repo.get_run_by_id(rid)
    if not run:
        flash('Çalışma bulunamadı.', 'danger')
        return redirect(url_for('ansible_runs'))
    return render_template('ansible_run_detail.html', run=dict(run))


@app.route('/ansible/runs/<int:rid>/cancel', methods=['POST'])
@login_required
def ansible_run_cancel(rid):
    proc = ansible_service.get_running_proc(rid)
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
    ansible_repo.delete_run(rid)
    flash(f'Çalışma #{rid} silindi.', 'success')
    return redirect(url_for('ansible_runs'))


# ── Roles ────────────────────────────────────────────────────
@app.route('/ansible/roles')
@login_required
def ansible_roles():
    roles = ansible_repo.get_roles()
    return render_template('ansible_roles.html', roles=roles)


@app.route('/ansible/roles/add', methods=['POST'])
@login_required
def ansible_role_add():
    name = request.form.get('name', '').strip()
    desc = request.form.get('description', '').strip()
    if not name:
        flash('Rol adı zorunlu.', 'danger')
        return redirect(url_for('ansible_roles'))
    role_id = None
    try:
        role_id = ansible_repo.create_role(name, desc)
        _sync_role_to_disk(role_id)
        flash(f'Rol "{name}" oluşturuldu.', 'success')
    except Exception as e:
        flash(f'Hata: {e}', 'danger')
        role_id = None

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
    try:
        role_id = ansible_repo.create_role(name, desc)
        _sync_role_to_disk(role_id)
        flash(f'Rol "{name}" oluşturuldu.', 'success')
        return redirect(url_for('ansible_role_edit', rid=role_id))
    except Exception as e:
        flash(f'Hata: {e}', 'danger')
        return redirect(url_for('ansible_roles'))


@app.route('/ansible/roles/<int:rid>')
@login_required
def ansible_role_edit(rid):
    role, files = ansible_repo.get_role_by_id(rid)
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
    if fid:
        ansible_repo.update_role_file(int(fid), rid, content)
    _sync_role_to_disk(rid)
    return jsonify({'ok': True})


@app.route('/ansible/roles/<int:rid>/add-file', methods=['POST'])
@login_required
def ansible_role_add_file(rid):
    section  = request.form.get('section', 'tasks')
    filename = request.form.get('filename', 'new_file.yml').strip()
    try:
        ansible_repo.create_role_file(rid, section, filename, '---\n')
        _sync_role_to_disk(rid)
        flash(f'{section}/{filename} oluşturuldu.', 'success')
    except Exception:
        flash('Dosya zaten mevcut.', 'danger')
    return redirect(url_for('ansible_role_edit', rid=rid))


@app.route('/ansible/roles/<int:rid>/delete', methods=['POST'])
@login_required
def ansible_role_delete(rid):
    role = ansible_repo.delete_role(rid)
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
    run = ansible_repo.get_run_status(rid)
    if not run:
        return jsonify({'error': 'not found'}), 404
    return jsonify(dict(run))


@app.route('/api/ansible/run-output/<int:rid>')
@login_required
def api_ansible_run_output(rid):
    """Son N satırı döner (polling için)."""
    offset = int(request.args.get('offset', 0))
    row = ansible_repo.get_run_output(rid, offset)
    if not row:
        return jsonify({'chunk': '', 'status': 'unknown'})
    return jsonify({'chunk': row['chunk'] or '', 'status': row['status'],
                    'finished_at': row['finished_at'] or ''})


@app.route('/api/ansible/ping-host', methods=['POST'])
@login_required
def api_ansible_ping_host():
    """Tek bir hosta ansible ping atar (bağlantı testi)."""
    hid = request.json.get('host_id')
    host = ansible_repo.get_host_by_id(hid)
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
    print(f"  Adres     : http://0.0.0.0:80")
    print(f"  DB        : {DB_PATH}")
    print(f"  Yedekler  : {BACKUP_ROOT}")
    print(f"  Scheduler : {'APScheduler ✓' if HAS_SCHEDULER else 'Kurulu değil!'}")
    print(f"  LDAP/AD   : {'ldap3 ✓' if HAS_LDAP else 'Kurulu değil'}")
    print(f"  Varsayılan: admin / admin123  (Lütfen değiştirin!)")
    print("=" * 64)
    app.run(host='0.0.0.0', port=80, debug=False, threaded=True)
