"""Ansible Blueprint — /ansible/* and /api/ansible/* routes."""

import os
import re
import sqlite3
import subprocess
import tempfile

from flask import Blueprint, render_template, redirect, url_for, flash, request, session, jsonify

from services.auth import login_required, admin_required
from services import ansible as ansible_service
from services.ansible import (
    _ansible_check, _ansible_version, _generate_inventory,
    _sync_playbook_to_disk, _sync_role_to_disk,
    _append_run_log, _set_run_status, start_ansible_run,
    _save_ansible_host, _save_playbook,
)
from models import ansible as ansible_repo
from models import audit as audit_repo
from models import servers as server_repo
from models import settings as settings_repo
from config import ANSIBLE_DIR, ANSIBLE_PLAYS_DIR, ANSIBLE_ROLES_DIR


ansible_bp = Blueprint('ansible', __name__)

PAGE_SIZE = 25


def _get_settings():
    return settings_repo.get_settings()


# ── Dashboard ─────────────────────────────────────────────────

@ansible_bp.route('/ansible/')
@login_required
def ansible_dashboard():
    stats = ansible_repo.get_dashboard_stats()
    recent_runs = ansible_repo.get_recent_runs(15)
    ansible_ok  = _ansible_check()
    ansible_ver = _ansible_version() if ansible_ok else 'Kurulu değil'
    return render_template('ansible_dashboard.html',
                           stats=stats, recent_runs=recent_runs,
                           ansible_ok=ansible_ok, ansible_ver=ansible_ver)


# ── Hosts ─────────────────────────────────────────────────────

@ansible_bp.route('/ansible/hosts')
@login_required
def ansible_hosts():
    hosts, groups, hg = ansible_repo.get_hosts_with_groups()
    hg_map = {}
    for row in hg:
        hg_map.setdefault(row['host_id'], []).append(row['group_id'])
    group_map = {g['id']: g['name'] for g in groups}
    return render_template('ansible_hosts.html', hosts=hosts, groups=groups,
                           hg_map=hg_map, group_map=group_map)


@ansible_bp.route('/ansible/hosts/add', methods=['GET', 'POST'])
@login_required
def ansible_host_add():
    groups = ansible_repo.get_groups()
    if request.method == 'POST':
        return _save_ansible_host(None)
    settings = _get_settings()
    return render_template('ansible_host_form.html', host=None,
                           groups=groups, title='Host Ekle', settings=settings)


@ansible_bp.route('/ansible/hosts/bulk-add', methods=['GET', 'POST'])
@login_required
def ansible_host_bulk_add():
    """Ansible host toplu ekleme."""
    groups = ansible_repo.get_groups()

    if request.method == 'GET':
        return render_template('ansible_host_bulk.html', groups=groups)

    raw_text = ''
    uploaded = request.files.get('csv_file')
    if uploaded and uploaded.filename:
        raw_text = uploaded.read().decode('utf-8', errors='replace')
    else:
        raw_text = request.form.get('bulk_text', '')

    if not raw_text.strip():
        flash('Veri girilmedi.', 'warning')
        return redirect(url_for('ansible.ansible_host_bulk_add'))

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

        except (ValueError, IndexError, TypeError) as e:
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

    return redirect(url_for('ansible.ansible_hosts'))


@ansible_bp.route('/ansible/hosts/<int:hid>/edit', methods=['GET', 'POST'])
@login_required
def ansible_host_edit(hid):
    host = ansible_repo.get_host_by_id(hid)
    groups = ansible_repo.get_groups()
    sel_groups = ansible_repo.get_host_groups(hid)
    if not host:
        flash('Host bulunamadı.', 'danger')
        return redirect(url_for('ansible.ansible_hosts'))
    if request.method == 'POST':
        return _save_ansible_host(hid)
    settings = _get_settings()
    return render_template('ansible_host_form.html', host=dict(host),
                           groups=groups, sel_groups=sel_groups,
                           title='Host Düzenle', settings=settings)


@ansible_bp.route('/ansible/hosts/<int:hid>/delete', methods=['POST'])
@login_required
def ansible_host_delete(hid):
    h = ansible_repo.delete_host(hid)
    flash(f'Host "{h["name"] if h else hid}" silindi.', 'success')
    return redirect(url_for('ansible.ansible_hosts'))


# ── Groups ────────────────────────────────────────────────────

@ansible_bp.route('/ansible/groups', methods=['GET', 'POST'])
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
                except sqlite3.IntegrityError:
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
        return redirect(url_for('ansible.ansible_groups'))

    groups = ansible_repo.get_groups()
    hcounts = ansible_repo.get_group_host_counts(groups)
    return render_template('ansible_groups.html', groups=groups, hcounts=hcounts)


# ── Playbooks ─────────────────────────────────────────────────

@ansible_bp.route('/ansible/playbooks')
@login_required
def ansible_playbooks():
    pbs = ansible_repo.get_playbooks()
    last_runs = {}
    for pb in pbs:
        r = ansible_repo.get_playbook_last_run(pb['id'])
        if r:
            last_runs[pb['id']] = r
    return render_template('ansible_playbooks.html', playbooks=pbs, last_runs=last_runs)


@ansible_bp.route('/ansible/playbooks/add', methods=['GET', 'POST'])
@login_required
def ansible_playbook_add():
    if request.method == 'POST':
        return _save_playbook(None)
    groups = ansible_repo.get_group_names()
    return render_template('ansible_playbook_editor.html',
                           pb=None, title='Yeni Playbook', groups=groups)


@ansible_bp.route('/ansible/playbooks/<int:pid>/edit', methods=['GET', 'POST'])
@login_required
def ansible_playbook_edit(pid):
    pb = ansible_repo.get_playbook_by_id(pid)
    groups = ansible_repo.get_group_names()
    if not pb:
        flash('Playbook bulunamadı.', 'danger')
        return redirect(url_for('ansible.ansible_playbooks'))
    if request.method == 'POST':
        return _save_playbook(pid)
    return render_template('ansible_playbook_editor.html',
                           pb=dict(pb), title=f'Düzenle: {pb["name"]}', groups=groups)


@ansible_bp.route('/ansible/playbooks/<int:pid>/delete', methods=['POST'])
@login_required
def ansible_playbook_delete(pid):
    pb = ansible_repo.delete_playbook(pid)
    if pb:
        safe_name = re.sub(r'[^\w\-]', '_', pb['name']) + '.yml'
        path = os.path.join(ANSIBLE_PLAYS_DIR, safe_name)
        try: os.unlink(path)
        except OSError: pass
        flash(f'Playbook "{pb["name"]}" silindi.', 'success')
    return redirect(url_for('ansible.ansible_playbooks'))


@ansible_bp.route('/ansible/playbooks/<int:pid>/run', methods=['GET', 'POST'])
@login_required
def ansible_playbook_run(pid):
    pb     = ansible_repo.get_playbook_by_id(pid)
    groups = ansible_repo.get_group_names()
    hosts  = ansible_repo.get_host_names_active()
    if not pb:
        flash('Playbook bulunamadı.', 'danger')
        return redirect(url_for('ansible.ansible_playbooks'))

    if request.method == 'GET':
        return render_template('ansible_run_form.html', pb=dict(pb),
                               groups=groups, hosts=hosts)

    limit      = request.form.get('limit', '').strip()
    tags_run   = request.form.get('tags_run', '').strip()
    extra_vars = request.form.get('extra_vars', '').strip()
    verbosity  = request.form.get('verbosity', '0')
    check_mode = request.form.get('check_mode', '0') == '1'

    _generate_inventory()
    pb_path = _sync_playbook_to_disk(dict(pb))

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

    run_id = ansible_repo.create_run(
        playbook_id=pid,
        playbook_name=pb['name'],
        inventory=limit or 'all',
        extra_vars=extra_vars,
        limit_hosts=limit,
        tags_run=tags_run,
        triggered_by=session.get('username', 'system')
    )

    # Audit: log AFTER run_id is created and committed
    username = session.get('username', 'anonymous')
    audit_repo.log_action(
        username=username,
        action='ansible_run_started',
        resource_id=run_id,
        resource_type='ansible_run',
        details=f'Playbook: {pb["name"]}'
    )

    start_ansible_run(run_id, pb_path, extra_args)

    flash(f'Playbook "{pb["name"]}" çalıştırılıyor — Çalışma #{run_id}', 'info')
    return redirect(url_for('ansible.ansible_run_detail', rid=run_id))


# ── Runs ──────────────────────────────────────────────────────

@ansible_bp.route('/ansible/runs')
@login_required
def ansible_runs():
    page = request.args.get('page', 1, type=int)
    if page < 1:
        page = 1
    offset = (page - 1) * PAGE_SIZE

    runs, total = ansible_repo.get_runs(offset=offset, limit=PAGE_SIZE)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    return render_template('ansible_runs.html',
                           runs=runs,
                           current_page=page,
                           total_pages=total_pages,
                           total=total)


@ansible_bp.route('/ansible/runs/<int:rid>')
@login_required
def ansible_run_detail(rid):
    run = ansible_repo.get_run_by_id(rid)
    if not run:
        flash('Çalışma bulunamadı.', 'danger')
        return redirect(url_for('ansible.ansible_runs'))
    return render_template('ansible_run_detail.html', run=dict(run))


@ansible_bp.route('/ansible/runs/<int:rid>/cancel', methods=['POST'])
@login_required
def ansible_run_cancel(rid):
    proc = ansible_service.get_running_proc(rid)
    if proc and hasattr(proc, 'terminate'):
        try:
            proc.terminate()
            _append_run_log(rid, '\n[Kullanıcı tarafından durduruldu]')
            _set_run_status(rid, 'cancelled', -1)
            flash(f'Çalışma #{rid} durduruldu.', 'warning')
        except OSError as e:
            flash(f'Durdurma hatası: {e}', 'danger')
    else:
        flash('Aktif süreç bulunamadı.', 'warning')
    return redirect(url_for('ansible.ansible_run_detail', rid=rid))


@ansible_bp.route('/ansible/runs/<int:rid>/delete', methods=['POST'])
@login_required
def ansible_run_delete(rid):
    ansible_repo.delete_run(rid)
    flash(f'Çalışma #{rid} silindi.', 'success')
    return redirect(url_for('ansible.ansible_runs'))


# ── Roles ─────────────────────────────────────────────────────

@ansible_bp.route('/ansible/roles')
@login_required
def ansible_roles():
    roles = ansible_repo.get_roles()
    return render_template('ansible_roles.html', roles=roles)


@ansible_bp.route('/ansible/roles/add', methods=['POST'])
@login_required
def ansible_role_add():
    name = request.form.get('name', '').strip()
    desc = request.form.get('description', '').strip()
    if not name:
        flash('Rol adı zorunlu.', 'danger')
        return redirect(url_for('ansible.ansible_roles'))
    role_id = None
    try:
        role_id = ansible_repo.create_role(name, desc)
        _sync_role_to_disk(role_id)
        flash(f'Rol "{name}" oluşturuldu.', 'success')
    except (sqlite3.IntegrityError, sqlite3.OperationalError, OSError) as e:
        flash(f'Hata: {e}', 'danger')
        role_id = None

    if role_id:
        return redirect(url_for('ansible.ansible_role_edit', rid=role_id))
    return redirect(url_for('ansible.ansible_roles'))


@ansible_bp.route('/ansible/roles/add_go', methods=['POST'])
@login_required
def ansible_role_add_go():
    """Rol ekle ve direkt editöre git."""
    name = request.form.get('name', '').strip()
    desc = request.form.get('description', '').strip()
    if not name:
        flash('Rol adı zorunlu.', 'danger')
        return redirect(url_for('ansible.ansible_roles'))
    try:
        role_id = ansible_repo.create_role(name, desc)
        _sync_role_to_disk(role_id)
        flash(f'Rol "{name}" oluşturuldu.', 'success')
        return redirect(url_for('ansible.ansible_role_edit', rid=role_id))
    except (sqlite3.IntegrityError, sqlite3.OperationalError, OSError) as e:
        flash(f'Hata: {e}', 'danger')
        return redirect(url_for('ansible.ansible_roles'))


@ansible_bp.route('/ansible/roles/<int:rid>')
@login_required
def ansible_role_edit(rid):
    role, files = ansible_repo.get_role_by_id(rid)
    if not role:
        flash('Rol bulunamadı.', 'danger')
        return redirect(url_for('ansible.ansible_roles'))
    sections = {}
    for f in files:
        sections.setdefault(f['section'], []).append(dict(f))
    return render_template('ansible_role_editor.html', role=dict(role), sections=sections)


@ansible_bp.route('/ansible/roles/<int:rid>/save-file', methods=['POST'])
@login_required
def ansible_role_save_file(rid):
    fid     = request.form.get('file_id')
    content = request.form.get('content', '')
    if fid:
        ansible_repo.update_role_file(int(fid), rid, content)
    _sync_role_to_disk(rid)
    return jsonify({'ok': True})


@ansible_bp.route('/ansible/roles/<int:rid>/add-file', methods=['POST'])
@login_required
def ansible_role_add_file(rid):
    section  = request.form.get('section', 'tasks')
    filename = request.form.get('filename', 'new_file.yml').strip()
    try:
        ansible_repo.create_role_file(rid, section, filename, '---\n')
        _sync_role_to_disk(rid)
        flash(f'{section}/{filename} oluşturuldu.', 'success')
    except sqlite3.IntegrityError:
        flash('Dosya zaten mevcut.', 'danger')
    return redirect(url_for('ansible.ansible_role_edit', rid=rid))


@ansible_bp.route('/ansible/roles/<int:rid>/delete', methods=['POST'])
@login_required
def ansible_role_delete(rid):
    role = ansible_repo.delete_role(rid)
    if role:
        import shutil
        try:
            shutil.rmtree(os.path.join(ANSIBLE_ROLES_DIR, role['name']), ignore_errors=True)
        except OSError:
            pass
    flash('Rol silindi.', 'success')
    return redirect(url_for('ansible.ansible_roles'))


# ── API: Ansible endpoints ────────────────────────────────────

@ansible_bp.route('/api/ansible/run-status/<int:rid>')
@login_required
def api_ansible_run_status(rid):
    run = ansible_repo.get_run_status(rid)
    if not run:
        return jsonify({'error': 'not found'}), 404
    return jsonify(dict(run))


@ansible_bp.route('/api/ansible/run-output/<int:rid>')
@login_required
def api_ansible_run_output(rid):
    """Son N satırı döner (polling için)."""
    offset = int(request.args.get('offset', 0))
    row = ansible_repo.get_run_output(rid, offset)
    if not row:
        return jsonify({'chunk': '', 'status': 'unknown'})
    return jsonify({'chunk': row['chunk'] or '', 'status': row['status'],
                    'finished_at': row['finished_at'] or ''})


@ansible_bp.route('/api/ansible/ping-host', methods=['POST'])
@login_required
def api_ansible_ping_host():
    """Tek bir hosta ansible ping atar (bağlantı testi)."""
    hid = request.json.get('host_id')
    host = ansible_repo.get_host_by_id(hid)
    if not host:
        return jsonify({'ok': False, 'msg': 'Host bulunamadı.'})

    if not _ansible_check():
        return jsonify({'ok': False, 'msg': 'Ansible kurulu değil.'})

    try:
        import yaml as _yaml
        _has_yaml = True
    except ImportError:
        _has_yaml = False

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
        except (TypeError, ValueError):
            _has_yaml = False

    if not _has_yaml:
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
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as e:
        return jsonify({'ok': False, 'msg': str(e)})
    finally:
        try: os.unlink(tmp_inv)
        except OSError: pass
