"""Servers Blueprint — /servers/* routes."""

import os
import datetime
import subprocess

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify

from services.auth import login_required
from services import ssh as ssh_service
from services import rear as rear_service
from services import jobs as job_service
from services.ansible import _generate_inventory
from models import servers as server_repo
from models import schedules as schedule_repo
from models import jobs as job_repo
from models import settings as settings_repo
from models import ansible as ansible_repo
from config import BACKUP_ROOT
from utils import safe_dirname as _safe_dirname


servers_bp = Blueprint('servers', __name__)


def _get_settings():
    return settings_repo.get_settings()


@servers_bp.route('/servers')
@login_required
def servers_list():
    servers = server_repo.get_all()
    ansible_map = {}
    for s in servers:
        if s['ansible_host_id']:
            ah = server_repo.get_ansible_host_info(s['ansible_host_id'])
            ansible_map[s['id']] = ah
        else:
            ansible_map[s['id']] = None
    return render_template('servers.html', servers=servers, ansible_map=ansible_map)


@servers_bp.route('/servers/add', methods=['GET', 'POST'])
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
            cfg = _get_settings()
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

        settings = _get_settings()
        srv_dict = dict(server_row)
        content = rear_service.generate_rear_config(srv_dict, settings)
        job_id = job_service.create_job(new_sid, 'configure', triggered_by='auto')
        job_service.start_job_thread(rear_service._run_configure_rear, job_id, srv_dict, content)

        flash(f'Sunucu "{label}" eklendi. Varsayılan ReaR yapılandırması uygulanıyor...', 'success')
        return redirect(url_for('jobs.job_detail', jid=job_id))
    cfg = _get_settings()
    return render_template('server_form.html', server=None, title='Sunucu Ekle', cfg=cfg)


@servers_bp.route('/servers/<int:sid>/edit', methods=['GET', 'POST'])
@login_required
def server_edit(sid):
    server = server_repo.get_by_id(sid)
    if not server:
        flash('Sunucu bulunamadı.', 'danger')
        return redirect(url_for('servers.servers_list'))
    if request.method == 'POST':
        d = request.form
        label      = d.get('label', '').strip()
        hostname   = d.get('hostname', '').strip()
        ip_address = d.get('ip_address', '').strip()
        ssh_user   = d.get('ssh_user', '').strip()

        if not label or not hostname or not ip_address or not ssh_user:
            flash('Zorunlu alanlar eksik: Ad, Hostname, IP Adresi ve SSH Kullanıcısı gereklidir.', 'danger')
            cfg = _get_settings()
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
        return redirect(url_for('servers.server_detail', sid=sid))
    cfg = _get_settings()
    return render_template('server_form.html', server=dict(server),
                           title='Sunucu Düzenle', cfg=cfg)


@servers_bp.route('/servers/<int:sid>/delete', methods=['POST'])
@login_required
def server_delete(sid):
    server_repo.delete(sid)
    flash('Sunucu silindi.', 'success')
    return redirect(url_for('servers.servers_list'))


@servers_bp.route('/servers/bulk-add', methods=['GET', 'POST'])
@login_required
def server_bulk_add():
    """
    Toplu sunucu ekleme.
    Her satır bir sunucu; alanlar sekme veya virgülle ayrılır.
    """
    if request.method == 'GET':
        return render_template('server_bulk.html')

    raw_text = ''
    uploaded = request.files.get('csv_file')
    if uploaded and uploaded.filename:
        raw_text = uploaded.read().decode('utf-8', errors='replace')
    else:
        raw_text = request.form.get('bulk_text', '')

    if not raw_text.strip():
        flash('Veri girilmedi.', 'warning')
        return redirect(url_for('servers.server_bulk_add'))

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
        if not line or line.startswith('#'):
            continue

        sep = '\t' if '\t' in line else ','
        parts = [p.strip() for p in line.split(sep)]

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
        for e in errors[:10]:
            flash(e, 'warning')
        if len(errors) > 10:
            flash(f'... ve {len(errors)-10} hata daha.', 'warning')

    return redirect(url_for('servers.servers_list'))


@servers_bp.route('/servers/<int:sid>')
@login_required
def server_detail(sid):
    server = server_repo.get_by_id(sid)
    if not server:
        flash('Sunucu bulunamadı.', 'danger')
        return redirect(url_for('servers.servers_list'))
    jobs = job_repo.get_by_server(sid)
    schedules = schedule_repo.get_by_server(sid)

    ansible_host = None
    if server['ansible_host_id']:
        ah = ansible_repo.get_host_by_id(server['ansible_host_id'])
        if ah:
            ansible_host = dict(ah)

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

    from services.scheduler import get_next_run
    sched_next = {}
    for s in schedules:
        sched_next[s['id']] = get_next_run(s['id'])

    cfg = _get_settings()
    running_job_ids = set(job_service.get_running_job_ids())

    return render_template('server_detail.html',
                           server=dict(server), jobs=jobs,
                           schedules=schedules, sched_next=sched_next,
                           backup_files=backup_files,
                           running_job_ids=running_job_ids,
                           cfg=cfg,
                           ansible_host=ansible_host,
                           all_ansible_hosts=all_ansible_hosts)


@servers_bp.route('/servers/<int:sid>/ansible-auto-add', methods=['POST'])
@login_required
def server_ansible_auto_add(sid):
    server = server_repo.get_by_id(sid)
    if not server:
        flash('Sunucu bulunamadı.', 'danger')
        return redirect(url_for('servers.servers_list'))

    server = dict(server)

    if server['ansible_host_id']:
        ah = ansible_repo.get_linked_host_info(server['ansible_host_id'])
        if ah:
            flash(f'Bu sunucu zaten "{ah["name"]}" Ansible hostuna bağlı.', 'info')
            return redirect(url_for('servers.server_detail', sid=sid))

    existing = ansible_repo.get_existing_ansible_host_for_server(server['ip_address'], server['hostname'])

    if existing:
        ansible_repo.link_server_to_host(sid, existing['id'])
        flash(f'Mevcut Ansible hostu "{existing["name"]}" ile bağlandı.', 'success')
        return redirect(url_for('servers.server_detail', sid=sid))

    _hn = server['hostname']
    if all(p.isdigit() for p in _hn.split('.') if p):
        host_name = _hn.replace('.', '-')
    else:
        host_name = _hn.split('.')[0]

    if ansible_repo.check_host_name_exists(host_name):
        host_name = f"{host_name}-rear"

    try:
        ansible_repo.create_host_from_server(server, host_name)
        _generate_inventory()
        flash(f'✓ Ansible hostu "{host_name}" oluşturuldu ve bağlandı. '
              f'Gerekirse Ansible → Hostlar sayfasından düzenleyebilirsiniz.', 'success')
    except Exception as e:
        flash(f'Ansible host oluşturma hatası: {e}', 'danger')

    return redirect(url_for('servers.server_detail', sid=sid))


@servers_bp.route('/servers/<int:sid>/ansible-link', methods=['POST'])
@login_required
def server_ansible_link(sid):
    ansible_host_id = request.form.get('ansible_host_id', type=int)

    server = server_repo.get_by_id(sid)
    if not server:
        flash('Sunucu bulunamadı.', 'danger')
        return redirect(url_for('servers.servers_list'))

    if ansible_host_id:
        ah = ansible_repo.get_linked_host_info(ansible_host_id)
        if ah:
            ansible_repo.link_server_to_host(sid, ansible_host_id)
            flash(f'"{ah["name"]}" Ansible hostuna bağlandı.', 'success')
        else:
            flash('Seçilen Ansible hostu bulunamadı.', 'danger')
    else:
        flash('Geçerli bir Ansible hostu seçin.', 'warning')

    return redirect(url_for('servers.server_detail', sid=sid))


@servers_bp.route('/servers/<int:sid>/ansible-unlink', methods=['POST'])
@login_required
def server_ansible_unlink(sid):
    ansible_repo.unlink_server_host(sid)
    flash('Ansible host bağlantısı kaldırıldı.', 'info')
    return redirect(url_for('servers.server_detail', sid=sid))


@servers_bp.route('/servers/<int:sid>/test', methods=['POST'])
@login_required
def server_test(sid):
    server = server_repo.get_by_id(sid)
    if not server:
        return jsonify({'ok': False, 'msg': 'Sunucu bulunamadı'})
    ok, msg = ssh_service.ssh_test_connection(dict(server))
    return jsonify({'ok': ok, 'msg': msg})


@servers_bp.route('/servers/<int:sid>/install', methods=['POST'])
@login_required
def server_install_rear(sid):
    server = server_repo.get_by_id(sid)
    if not server:
        flash('Sunucu bulunamadı.', 'danger')
        return redirect(url_for('servers.servers_list'))
    job_id = job_service.create_job(sid, 'install')
    job_service.start_job_thread(rear_service._run_install_rear, job_id, dict(server))
    flash(f'ReaR kurulumu başlatıldı. İş #{job_id}', 'info')
    return redirect(url_for('jobs.job_detail', jid=job_id))


@servers_bp.route('/servers/<int:sid>/configure', methods=['GET', 'POST'])
@login_required
def server_configure(sid):
    server = server_repo.get_by_id(sid)
    if not server:
        flash('Sunucu bulunamadı.', 'danger')
        return redirect(url_for('servers.servers_list'))
    settings = _get_settings()

    if request.method == 'POST':
        if not server['rear_installed']:
            flash('ReaR kurulu değil. Önce ReaR kurulumunu tamamlayın.', 'warning')
            return redirect(url_for('servers.server_detail', sid=sid))

        cfg = dict(settings)
        cfg['autoresize']    = request.form.get('autoresize', '0')
        cfg['migration_mode']= request.form.get('migration_mode', '0')
        cfg['rear_output']   = request.form.get('rear_output', 'ISO')
        cfg['rear_backup']   = request.form.get('rear_backup', 'NETFS')

        server_excl = request.form.get('server_exclude_dirs', '')
        server_repo.update_exclude_dirs(sid, server_excl)

        srv_dict = dict(server)
        srv_dict['exclude_dirs'] = server_excl
        content  = rear_service.generate_rear_config(srv_dict, cfg)

        job_id = job_service.create_job(sid, 'configure')
        job_service.start_job_thread(rear_service._run_configure_rear, job_id, srv_dict, content)
        flash(f'Yapılandırma gönderildi. İş #{job_id}', 'info')
        return redirect(url_for('jobs.job_detail', jid=job_id))

    srv_dict = dict(server)
    preview  = rear_service.generate_rear_config(srv_dict, settings)
    return render_template('configure.html', server=srv_dict,
                           settings=settings, preview=preview)


@servers_bp.route('/servers/<int:sid>/backup', methods=['POST'])
@login_required
def server_backup(sid):
    server = server_repo.get_by_id(sid)
    if not server:
        flash('Sunucu bulunamadı.', 'danger')
        return redirect(url_for('servers.servers_list'))

    if not server['rear_installed']:
        flash('ReaR kurulu değil. Önce ReaR kurulumunu tamamlayın.', 'warning')
        return redirect(url_for('servers.server_detail', sid=sid))
    if not server['rear_configured']:
        flash('ReaR yapılandırılmamış. Önce yapılandırma uygulayın.', 'warning')
        return redirect(url_for('servers.server_detail', sid=sid))

    btype  = request.form.get('backup_type', 'mkbackup')
    job_id = job_service.create_job(sid, 'backup', triggered_by='manual')
    job_service.start_job_thread(job_service._do_backup, job_id, dict(server), btype, 'manual', None)
    flash(f'Yedekleme başlatıldı. İş #{job_id}', 'info')
    return redirect(url_for('jobs.job_detail', jid=job_id))
