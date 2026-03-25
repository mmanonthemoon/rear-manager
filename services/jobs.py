"""Job management service — job creation, thread management, log/status helpers, backup runner."""

import os
import threading
import traceback
import datetime
import subprocess

from flask import current_app

from config import BACKUP_ROOT
from models import jobs as job_repo
from models import schedules as schedule_repo


# ─────────────────────────────────────────────────────────────
# MUTABLE GLOBALS (thread-safe access via accessor functions)
# ─────────────────────────────────────────────────────────────
_running_jobs = {}
_job_lock     = threading.Lock()


def get_running_job_ids():
    """Return a snapshot list of currently running job IDs."""
    with _job_lock:
        return list(_running_jobs.keys())


def is_job_running(job_id):
    """Return True if the given job_id is currently running."""
    with _job_lock:
        return job_id in _running_jobs


def get_running_count():
    """Return the number of currently running jobs."""
    with _job_lock:
        return len(_running_jobs)


# ─────────────────────────────────────────────────────────────
# LOG / STATUS HELPERS
# ─────────────────────────────────────────────────────────────
def _append_log(job_id, text):
    """Backup job log'una satır ekle. 2 MB limitini aşarsa eski satırları kırpar."""
    job_repo.append_log(job_id, text)


def _set_job_status(job_id, status, extra=None):
    job_repo.update_status(job_id, status, extra)


# ─────────────────────────────────────────────────────────────
# BACKUP RUNNER (background thread function)
# ─────────────────────────────────────────────────────────────
def _do_backup(job_id, server_dict, backup_cmd='mkbackup', triggered_by='manual', schedule_id=None):
    """Run a rear backup/mkrescue in a background thread.

    Callers should invoke this via start_job_thread() so app context is available.
    """
    # Import here to avoid circular imports at module load time
    from services import ssh as ssh_service

    def _safe_dirname(hostname):
        import re
        return re.sub(r'[^a-zA-Z0-9_-]', '-', hostname).strip('-') or 'unknown'

    log = lambda t: _append_log(job_id, t)
    _set_job_status(job_id, 'running')
    job_repo.set_started(job_id)

    log(f"=== ReaR {'Yedekleme' if backup_cmd == 'mkbackup' else 'ISO Oluşturma'} Başlıyor ===")
    log(f"► Tetikleyen  : {triggered_by}")
    log(f"► Sunucu      : {server_dict['hostname']} ({server_dict['ip_address']})")
    log(f"► Başlangıç   : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log("")

    from models import settings as settings_repo
    cfg = settings_repo.get_settings()
    nfs_ip = cfg.get('central_ip', _get_local_ip())
    log(f"► Yedek Sunucu: {nfs_ip}")
    log(f"► Yedek Yolu  : {cfg.get('nfs_export_path', BACKUP_ROOT)}/{_safe_dirname(server_dict['hostname'])}")
    log("")

    hostname   = server_dict['hostname']
    backup_dir = os.path.join(BACKUP_ROOT, _safe_dirname(hostname))

    # NFS hedef dizinini rear çalışmadan önce oluştur
    try:
        os.makedirs(backup_dir, exist_ok=True)
        os.chmod(backup_dir, 0o755)
        log(f"► NFS dizini hazırlandı: {backup_dir}")
    except OSError as e:
        log(f"[UYARI] NFS dizini oluşturulamadı: {e}")

    log(f"► rear -v {backup_cmd} çalıştırılıyor (bu uzun sürebilir)...")
    log("─" * 60)
    ec, _ = ssh_service.ssh_exec_stream(server_dict, f'rear -v {backup_cmd} 2>&1', log)
    log("─" * 60)
    status = 'success' if ec == 0 else 'failed'

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
        schedule_repo.update_last_run(
            schedule_id,
            datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            status
        )


def _get_local_ip():
    """Local IP helper for backup log messages (mirrors app.py _get_local_ip)."""
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
    return '127.0.0.1'


# ─────────────────────────────────────────────────────────────
# THREAD MANAGEMENT
# ─────────────────────────────────────────────────────────────
def start_job_thread(target_fn, job_id, *args):
    """Spawn a daemon thread for target_fn(job_id, *args) with Flask app context.

    The app context is captured from the calling request context so that
    current_app / current_app.logger work inside background threads.
    """
    app = current_app._get_current_object()

    def _wrapper():
        with app.app_context():
            try:
                target_fn(job_id, *args)
            except Exception:
                err = traceback.format_exc()
                _append_log(job_id, f"[BEKLENMEYEN HATA]\n{err}")
                _set_job_status(job_id, 'failed')
            finally:
                with _job_lock:
                    _running_jobs.pop(job_id, None)

    t = threading.Thread(target=_wrapper, daemon=True, name=f"job-{job_id}")
    with _job_lock:
        _running_jobs[job_id] = t
    t.start()
    return t


# ─────────────────────────────────────────────────────────────
# JOB CREATION
# ─────────────────────────────────────────────────────────────
def create_job(server_id, job_type, triggered_by='manual', schedule_id=None):
    """Create a new job record and return its ID."""
    return job_repo.create(server_id, job_type, triggered_by, schedule_id)
