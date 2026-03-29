"""Dashboard Blueprint — / route."""

import os
import subprocess

from flask import Blueprint, render_template

from services.auth import login_required
from services.jobs import get_running_count
from models import servers as server_repo, jobs as job_repo, schedules as schedule_repo
from config import BACKUP_ROOT
from utils import safe_dirname as _safe_dirname


dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/')
@login_required
def dashboard():
    servers, _total_servers = server_repo.get_all(offset=0, limit=10000)
    jobs    = job_repo.get_recent(12)
    _running_count = get_running_count()
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
            except (OSError, subprocess.SubprocessError, IndexError):
                backup_info[s['id']] = '?'
        else:
            backup_info[s['id']] = '-'

    return render_template('dashboard.html', servers=servers, jobs=jobs,
                           stats=stats, backup_info=backup_info)
