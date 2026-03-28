"""Ansible orchestration service — inventory generation, run management, host/playbook helpers."""

import os
import re
import subprocess
import threading
import traceback

from flask import current_app, request, flash, redirect, url_for

from config import (
    ANSIBLE_DIR, ANSIBLE_PLAYS_DIR, ANSIBLE_ROLES_DIR,
    ANSIBLE_INV_DIR, ANSIBLE_HVARS_DIR, ANSIBLE_GVARS_DIR,
)
from models import ansible as ansible_repo
from models import servers as server_repo


# ─────────────────────────────────────────────────────────────
# CUSTOM EXCEPTIONS
# ─────────────────────────────────────────────────────────────
class AnsibleNotInstalledError(Exception):
    """Raised when ansible binary is not found."""


class AnsibleRunError(Exception):
    """Raised when ansible-playbook exits non-zero."""


# ─────────────────────────────────────────────────────────────
# MUTABLE GLOBALS (thread-safe via _ansible_run_lock)
# ─────────────────────────────────────────────────────────────
_ansible_running = {}    # run_id -> process or thread
_ansible_run_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────
# ACCESSOR FUNCTIONS
# ─────────────────────────────────────────────────────────────
def is_run_active(run_id):
    with _ansible_run_lock:
        return run_id in _ansible_running


def get_active_run_ids():
    with _ansible_run_lock:
        return list(_ansible_running.keys())


def get_running_proc(run_id):
    """Return the process/thread for a run_id (for cancel support)."""
    with _ansible_run_lock:
        return _ansible_running.get(run_id)


# ─────────────────────────────────────────────────────────────
# ANSIBLE BINARY CHECKS
# ─────────────────────────────────────────────────────────────
def _ansible_check() -> bool:
    """Ansible kurulu mu kontrol eder."""
    try:
        r = subprocess.run(['ansible', '--version'],
                           capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return False


def _ansible_version() -> str:
    try:
        r = subprocess.run(['ansible', '--version'],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.split('\n')[0].strip()
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return 'Kurulu değil'


# ─────────────────────────────────────────────────────────────
# INVENTORY GENERATION
# ─────────────────────────────────────────────────────────────
def _generate_inventory() -> str:
    """
    DB'deki host ve gruplardan YAML inventory üretir.
    Host değişkenleri (şifre, become, port vb.) doğrudan inventory YAML'ına
    host_vars anahtarı olarak yazılır; Ansible bunları host başına okur.
    """
    hosts, groups, hg = ansible_repo.get_hosts_active_with_groups()

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
            except (OSError, IOError):
                pass

    # ── host_vars dosyaları (ek özel değişkenler) ───────────────
    for h in hosts:
        if h['vars_yaml']:
            hv_path = os.path.join(ANSIBLE_HVARS_DIR, f"{h['name']}.yml")
            try:
                with open(hv_path, 'w') as f:
                    f.write(f"---\n{h['vars_yaml']}\n")
            except (OSError, IOError):
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


# ─────────────────────────────────────────────────────────────
# DISK SYNC HELPERS
# ─────────────────────────────────────────────────────────────
def _sync_playbook_to_disk(pb: dict):
    """Playbook içeriğini diske yazar."""
    safe_name = re.sub(r'[^\w\-]', '_', pb['name']) + '.yml'
    path = os.path.join(ANSIBLE_PLAYS_DIR, safe_name)
    with open(path, 'w') as f:
        f.write(pb['content'])
    return path


def _sync_role_to_disk(role_id: int):
    """Rol dosyalarını diske yazar."""
    role, files = ansible_repo.get_role_for_disk_sync(role_id)
    if not role:
        return

    rname = role['name']
    for section in ['tasks', 'handlers', 'templates', 'files', 'vars', 'defaults', 'meta']:
        os.makedirs(os.path.join(ANSIBLE_ROLES_DIR, rname, section), exist_ok=True)

    for rf in files:
        sec_dir = os.path.join(ANSIBLE_ROLES_DIR, rname, rf['section'])
        os.makedirs(sec_dir, exist_ok=True)
        fpath = os.path.join(sec_dir, rf['filename'])
        with open(fpath, 'w') as f:
            f.write(rf['content'] or '')


# ─────────────────────────────────────────────────────────────
# RUN LOG / STATUS HELPERS
# ─────────────────────────────────────────────────────────────
def _append_run_log(run_id, text):
    """Ansible run log'una satır ekle."""
    ansible_repo.append_run_log(run_id, text)


def _set_run_status(run_id, status, exit_code=None):
    ansible_repo.update_run_status(run_id, status, exit_code)


# ─────────────────────────────────────────────────────────────
# ANSIBLE RUN EXECUTION
# ─────────────────────────────────────────────────────────────
def _do_ansible_run(run_id, playbook_path, extra_args: list):
    """Arka planda ansible-playbook çalıştırır."""
    log = lambda t: _append_run_log(run_id, t)
    _set_run_status(run_id, 'running')
    ansible_repo.set_run_started(run_id)

    # Inventory üret
    log("► Inventory üretiliyor...")
    try:
        _generate_inventory()
        log("► Inventory hazır ✓")
    except Exception:  # broad-catch-ok: background thread must not crash
        current_app.logger.error("Inventory generation failed for run %d:\n%s", run_id, traceback.format_exc())
        log(f"[HATA] Inventory üretme hatası — ayrıntılar için uygulama loguna bakın")
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
        ansible_repo.append_run_output_raw(run_id, f"[PID: {proc.pid}]\n")

        with _ansible_run_lock:
            _ansible_running[run_id] = proc

        for line in proc.stdout:
            line = line.rstrip()
            log(line)

        proc.wait()
        exit_code = proc.returncode

    except (FileNotFoundError, OSError, subprocess.SubprocessError) as e:
        current_app.logger.error("ansible-playbook launch failed for run %d: %s", run_id, e)
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


def start_ansible_run(run_id, playbook_path, extra_args):
    """Spawn a background thread for _do_ansible_run with Flask app context."""
    app = current_app._get_current_object()

    def _wrapper():
        with app.app_context():
            _do_ansible_run(run_id, playbook_path, extra_args)

    t = threading.Thread(target=_wrapper, daemon=True)
    with _ansible_run_lock:
        _ansible_running[run_id] = t
    t.start()
    return t


# ─────────────────────────────────────────────────────────────
# HOST / PLAYBOOK FORM HELPERS (run within request context)
# ─────────────────────────────────────────────────────────────
def _save_ansible_host(hid):
    d = request.form
    fields = {
        'name':            d.get('name', '').strip(),
        'hostname':        d.get('hostname', '').strip(),
        'os_type':         d.get('os_type', 'linux'),
        'connection_type': d.get('connection_type', 'ssh'),
        'ssh_port':        int(d.get('ssh_port') or 22),
        'winrm_port':      int(d.get('winrm_port') or 5985),
        'winrm_scheme':    d.get('winrm_scheme', 'http'),
        'ansible_user':    d.get('ansible_user', ''),
        'ansible_pass':    d.get('ansible_pass', ''),
        'auth_type':       d.get('auth_type', 'password'),
        'ssh_key_path':    d.get('ssh_key_path', ''),
        'win_domain':      d.get('win_domain', ''),
        'win_transport':   d.get('win_transport', 'ntlm'),
        'become_method':   d.get('become_method', 'none'),
        'become_user':     d.get('become_user', 'root'),
        'become_pass':     d.get('become_pass', ''),
        'become_same':     1 if d.get('become_same') else 0,
        'vars_yaml':       d.get('vars_yaml', ''),
        'notes':           d.get('notes', ''),
        'active':          1 if d.get('active', '1') != '0' else 0,
    }
    sel_groups = request.form.getlist('group_ids')

    if hid:
        ansible_repo.update_host(hid, fields)
        ansible_repo.set_host_groups(hid, sel_groups)
        flash(f'Host "{fields["name"]}" güncellendi.', 'success')
        return redirect(url_for('ansible_host_edit', hid=hid))
    else:
        new_id = ansible_repo.create_host(**fields)
        ansible_repo.set_host_groups(new_id, sel_groups)
        flash(f'Host "{fields["name"]}" eklendi.', 'success')
        return redirect(url_for('ansible_hosts'))


def _save_playbook(pid):
    d = request.form
    name    = d.get('name', '').strip()
    content = d.get('content', '')
    desc    = d.get('description', '')
    tags    = d.get('tags', '')

    if not name:
        flash('Playbook adı zorunlu.', 'danger')
        return redirect(url_for('ansible_playbooks'))

    if pid:
        ansible_repo.update_playbook(pid, name, desc, content, tags)
        # Diske yaz
        _sync_playbook_to_disk({'name': name, 'content': content})
        flash('Playbook kaydedildi.', 'success')
        return redirect(url_for('ansible_playbook_edit', pid=pid))
    else:
        new_id = ansible_repo.create_playbook(name, desc, content, tags)
        _sync_playbook_to_disk({'name': name, 'content': content})
        flash(f'Playbook "{name}" oluşturuldu.', 'success')
        return redirect(url_for('ansible_playbook_edit', pid=new_id))
