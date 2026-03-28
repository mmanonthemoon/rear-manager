"""Scheduler service — APScheduler management for scheduled backups."""

import traceback

from models import schedules as schedule_repo
from models import settings as settings_repo


# ─────────────────────────────────────────────────────────────
# OPTIONAL DEPENDENCY IMPORTS
# ─────────────────────────────────────────────────────────────
try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.jobstores.base import JobLookupError
    HAS_SCHEDULER = True
except ImportError:
    HAS_SCHEDULER = False


# ─────────────────────────────────────────────────────────────
# MUTABLE GLOBALS
# ─────────────────────────────────────────────────────────────
_scheduler = None


# ─────────────────────────────────────────────────────────────
# SCHEDULER CALLBACK
# ─────────────────────────────────────────────────────────────
def _scheduler_run_backup(schedule_id):
    """APScheduler tarafından çağrılır. Runs in APScheduler's thread pool — needs app context."""
    from flask import current_app
    from services import jobs as job_service
    from models import servers as server_repo

    try:
        app = current_app._get_current_object()
    except RuntimeError:
        # No app context available — this should not happen if init_scheduler is
        # called within an app context. Log and bail out.
        return

    with app.app_context():
        sched = schedule_repo.get_by_id(schedule_id)
        if not sched or not sched['enabled']:
            return
        server = server_repo.get_by_id(sched['server_id'])
        if not server:
            return
        if not server['rear_installed'] or not server['rear_configured']:
            # ReaR hazır değil, zamanlanmış yedekleme atlandı
            return

        job_id = job_service.create_job(server['id'], 'backup', triggered_by='scheduler', schedule_id=schedule_id)
        job_service.start_job_thread(job_service._do_backup, job_id, dict(server),
                                     sched['backup_type'] or 'mkbackup', 'scheduler', schedule_id)


# ─────────────────────────────────────────────────────────────
# SCHEDULER MANAGEMENT
# ─────────────────────────────────────────────────────────────
def init_scheduler():
    global _scheduler
    if not HAS_SCHEDULER:
        return

    cfg = settings_repo.get_settings()
    tz = cfg.get('scheduler_timezone', 'Europe/Istanbul')
    _scheduler = BackgroundScheduler(timezone=tz, daemon=True)
    _scheduler.start()

    # Mevcut aktif zamanlamaları yükle
    schedules = schedule_repo.get_all_enabled()

    for sched in schedules:
        _add_scheduler_job(sched['id'],
                           sched['cron_minute'], sched['cron_hour'],
                           sched['cron_dom'],    sched['cron_month'],
                           sched['cron_dow'])


def _restart_scheduler_with_timezone(new_tz):
    """Zamanlayıcıyı yeni timezone ile yeniden başlatır ve aktif zamanlamaları yeniden yükler."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)

    _scheduler = BackgroundScheduler(timezone=new_tz, daemon=True)
    _scheduler.start()

    schedules = schedule_repo.get_all_enabled()

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
    except (JobLookupError, KeyError, AttributeError):
        pass  # Job not present — expected on first add
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
    except (ValueError, KeyError) as e:
        try:
            from flask import current_app
            current_app.logger.error("Zamanlayıcı eklenemedi (sched %d): %s", schedule_id, e)
        except RuntimeError:
            pass  # No app context for logging


def _remove_scheduler_job(schedule_id):
    if not _scheduler:
        return
    try:
        _scheduler.remove_job(f'sched_{schedule_id}')
    except JobLookupError:
        pass  # Job already removed — expected


def get_next_run(schedule_id):
    if not _scheduler:
        return None
    try:
        job = _scheduler.get_job(f'sched_{schedule_id}')
        if job and job.next_run_time:
            return job.next_run_time.strftime('%Y-%m-%d %H:%M:%S')
    except (JobLookupError, AttributeError):
        pass
    return None


def get_all_jobs():
    """Return list of all scheduler jobs (for API status endpoint)."""
    if not _scheduler:
        return []
    try:
        return _scheduler.get_jobs()
    except (RuntimeError, AttributeError):
        return []
