"""API Blueprint — /api/status, /api/schedules-status, /api/offline-packages routes."""

from flask import Blueprint, jsonify

from services.auth import login_required
from services import jobs as job_service
from services import rear as rear_service
from services.scheduler import get_all_jobs as get_scheduler_jobs
from models import jobs as job_repo
from config import OFFLINE_PKG_DIR, UBUNTU_CODENAMES


api_bp = Blueprint('api', __name__)


@api_bp.route('/api/status')
@login_required
def api_status():
    running = []
    _job_ids = job_service.get_running_job_ids()
    for jid in _job_ids:
        row = job_repo.get_running_job_info(jid)
        if row:
            running.append(dict(row))
    return jsonify({'running': running, 'count': len(running)})


@api_bp.route('/api/schedules-status')
@login_required
def api_schedules_status():
    jobs = []
    for job in get_scheduler_jobs():
        jobs.append({
            'id': job.id,
            'next_run': job.next_run_time.strftime('%Y-%m-%d %H:%M:%S') if job.next_run_time else None
        })
    return jsonify({'jobs': jobs})


@api_bp.route('/api/offline-packages')
@login_required
def api_offline_packages():
    return jsonify({
        'status':     rear_service.get_offline_pkg_status(),
        'base_dir':   OFFLINE_PKG_DIR,
        'codenames':  UBUNTU_CODENAMES,
    })
