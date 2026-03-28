"""Schedules Blueprint — schedule management routes."""

from flask import Blueprint, redirect, url_for, flash, request, jsonify

from services.auth import login_required
from services.scheduler import _add_scheduler_job, _remove_scheduler_job
from services import jobs as job_service
from models import schedules as schedule_repo
from models import servers as server_repo


schedules_bp = Blueprint('schedules', __name__)


@schedules_bp.route('/servers/<int:sid>/schedules/add', methods=['POST'])
@login_required
def schedule_add(sid):
    server = server_repo.get_by_id(sid)
    if not server:
        flash('Sunucu bulunamadı.', 'danger')
        return redirect(url_for('servers.servers_list'))

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
    return redirect(url_for('servers.server_detail', sid=sid))


@schedules_bp.route('/schedules/<int:scid>/toggle', methods=['POST'])
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
    return redirect(url_for('servers.server_detail', sid=sid))


@schedules_bp.route('/schedules/<int:scid>/delete', methods=['POST'])
@login_required
def schedule_delete(scid):
    sched = schedule_repo.delete(scid)
    if not sched:
        flash('Zamanlama bulunamadı.', 'danger')
        return redirect(url_for('servers.servers_list'))
    sid = sched['server_id']
    _remove_scheduler_job(scid)
    flash(f'Zamanlama #{scid} silindi.', 'success')
    return redirect(url_for('servers.server_detail', sid=sid))


@schedules_bp.route('/schedules/<int:scid>/run-now', methods=['POST'])
@login_required
def schedule_run_now(scid):
    sched  = schedule_repo.get_by_id(scid)
    server = server_repo.get_by_id(sched['server_id']) if sched else None
    if not sched or not server:
        flash('Bulunamadı.', 'danger')
        return redirect(url_for('servers.servers_list'))
    if not server['rear_installed']:
        flash('ReaR kurulu değil. Önce ReaR kurulumunu tamamlayın.', 'warning')
        return redirect(url_for('servers.server_detail', sid=server['id']))
    if not server['rear_configured']:
        flash('ReaR yapılandırılmamış. Önce yapılandırma uygulayın.', 'warning')
        return redirect(url_for('servers.server_detail', sid=server['id']))
    job_id = job_service.create_job(server['id'], 'backup', triggered_by='manual-schedule', schedule_id=scid)
    job_service.start_job_thread(job_service._do_backup, job_id, dict(server),
                     sched['backup_type'] or 'mkbackup', 'manual-schedule', scid)
    flash(f'Zamanlama #{scid} hemen çalıştırıldı. İş #{job_id}', 'info')
    return redirect(url_for('jobs.job_detail', jid=job_id))
