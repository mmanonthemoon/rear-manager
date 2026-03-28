"""Jobs Blueprint — /jobs/* routes."""

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify

from services.auth import login_required
from services import jobs as job_service
from models import jobs as job_repo


jobs_bp = Blueprint('jobs', __name__)


@jobs_bp.route('/jobs')
@login_required
def jobs_list():
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


@jobs_bp.route('/jobs/<int:jid>')
@login_required
def job_detail(jid):
    job = job_repo.get_by_id(jid)
    if not job:
        flash('İş bulunamadı.', 'danger')
        return redirect(url_for('jobs.jobs_list'))
    _is_running = job_service.is_job_running(jid)
    return render_template('job_detail.html',
                           job=dict(job),
                           is_running=_is_running)


@jobs_bp.route('/jobs/<int:jid>/log')
@login_required
def job_log_api(jid):
    row = job_repo.get_log(jid)
    if not row:
        return jsonify({'log': '', 'status': 'notfound'})
    _is_running = job_service.is_job_running(jid)
    return jsonify({'log': row['log_output'] or '', 'status': row['status'],
                    'finished_at': row['finished_at'] or '',
                    'running': _is_running})


@jobs_bp.route('/jobs/<int:jid>/cancel', methods=['POST'])
@login_required
def job_cancel(jid):
    job_service._set_job_status(jid, 'cancelled')
    flash(f'İş #{jid} iptal edildi.', 'warning')
    return redirect(url_for('jobs.job_detail', jid=jid))


@jobs_bp.route('/jobs/<int:jid>/delete', methods=['POST'])
@login_required
def job_delete(jid):
    job = job_repo.get_server_id(jid)
    job_repo.delete(jid)
    flash('İş silindi.', 'success')
    if job:
        return redirect(url_for('servers.server_detail', sid=job['server_id']))
    return redirect(url_for('jobs.jobs_list'))
