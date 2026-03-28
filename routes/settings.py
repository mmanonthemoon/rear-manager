"""Settings Blueprint — /settings/* routes."""

import os
import subprocess

from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify

from services.auth import login_required, admin_required, authenticate_ad
from services import ssh as ssh_service
from services import rear as rear_service
from models import settings as settings_repo
from models import servers as server_repo
from config import KEY_PATH, SCHEDULER_TIMEZONES, OFFLINE_PKG_DIR, UBUNTU_CODENAMES, BACKUP_ROOT


settings_bp = Blueprint('settings', __name__)


try:
    from apscheduler.schedulers.background import BackgroundScheduler
    HAS_SCHEDULER = True
except ImportError:
    HAS_SCHEDULER = False

try:
    from ldap3 import Server as LdapServer
    HAS_LDAP = True
except ImportError:
    HAS_LDAP = False


def _get_settings():
    return settings_repo.get_settings()


@settings_bp.route('/settings', methods=['GET', 'POST'])
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
                pytz.timezone(tz)
            except Exception:
                flash('Geçersiz timezone seçimi.', 'danger')
                return redirect(url_for('settings.settings_page', tab='scheduler'))
            settings_repo.save_setting('scheduler_timezone', tz)
            if HAS_SCHEDULER:
                from services.scheduler import _restart_scheduler_with_timezone
                _restart_scheduler_with_timezone(tz)
            flash('Zamanlayıcı ayarları kaydedildi.', 'success')
            return redirect(url_for('settings.settings_page', tab='scheduler'))
        else:
            keys = []

        kv = {k: request.form.get(k, '') for k in keys}
        settings_repo.save_many(kv)
        flash('Ayarlar kaydedildi.', 'success')
        return redirect(url_for('settings.settings_page', tab=tab))

    settings = _get_settings()
    active_tab = request.args.get('tab', 'general')

    du_info = ''
    if os.path.isdir(BACKUP_ROOT):
        try:
            r = subprocess.run(['df', '-h', BACKUP_ROOT], capture_output=True, text=True)
            du_info = r.stdout
        except Exception:
            pass

    offline_pkg_status = rear_service.get_offline_pkg_status()

    return render_template('settings.html', settings=settings,
                           du_info=du_info, active_tab=active_tab,
                           has_scheduler=HAS_SCHEDULER, has_ldap=HAS_LDAP,
                           offline_pkg_status=offline_pkg_status,
                           ubuntu_codenames=UBUNTU_CODENAMES,
                           offline_pkg_dir=OFFLINE_PKG_DIR,
                           scheduler_timezones=SCHEDULER_TIMEZONES)


@settings_bp.route('/settings/setup-nfs', methods=['POST'])
@login_required
@admin_required
def setup_nfs():
    flash('NFS/SMB yapılandırması Linux sunucuda kendiniz tarafından yapılmalıdır. '
          'Yedek Sunucu IP ve Yedek Dizini Ayarlar → Genel sekmesinde yapılandırın.', 'info')
    return redirect(url_for('settings.settings_page', tab='tools'))


@settings_bp.route('/settings/generate-key', methods=['POST'])
@login_required
@admin_required
def generate_ssh_key():
    key_path = _get_settings().get('ssh_key_path', KEY_PATH)
    os.makedirs(os.path.dirname(os.path.abspath(key_path)), exist_ok=True)
    try:
        subprocess.run(['ssh-keygen', '-t', 'rsa', '-b', '4096', '-f', key_path,
                        '-N', '', '-C', 'rear-manager'], check=True, capture_output=True)
        pub = open(f"{key_path}.pub").read().strip()
        flash(f'SSH anahtarı oluşturuldu. Public key: {pub[:60]}...', 'success')
    except Exception as e:
        flash(f'Hata: {str(e)}', 'danger')
    return redirect(url_for('settings.settings_page'))


@settings_bp.route('/settings/copy-key/<int:sid>', methods=['POST'])
@login_required
def copy_ssh_key(sid):
    server = server_repo.get_by_id(sid)
    if not server:
        return jsonify({'ok': False, 'msg': 'Sunucu bulunamadı'})
    kp = _get_settings().get('ssh_key_path', KEY_PATH)
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


@settings_bp.route('/settings/test-ad', methods=['POST'])
@login_required
@admin_required
def test_ad():
    username = request.form.get('test_username', '').strip()
    password = request.form.get('test_password', '')
    if not username or not password:
        return jsonify({'ok': False, 'msg': 'Kullanıcı adı ve şifre gerekli'})
    ok, role, full_name, msg = authenticate_ad(username, password)
    return jsonify({'ok': ok, 'role': role, 'full_name': full_name, 'msg': msg})
