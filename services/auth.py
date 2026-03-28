"""Authentication service — local and AD (LDAP) authentication, login/admin decorators."""

import functools
import time

from flask import session, redirect, url_for, flash, request, jsonify

from models import users as user_repo
from models import settings as settings_repo


# ─────────────────────────────────────────────────────────────
# OPTIONAL DEPENDENCY IMPORTS
# ─────────────────────────────────────────────────────────────
try:
    from ldap3 import Server as LdapServer, Connection as LdapConn, ALL, NTLM, SIMPLE
    from ldap3.core.exceptions import LDAPException
    HAS_LDAP = True
except ImportError:
    HAS_LDAP = False

try:
    from werkzeug.security import check_password_hash
except ImportError:
    import hashlib
    def check_password_hash(h, pw):
        return h == hashlib.sha256(pw.encode()).hexdigest()


# ─────────────────────────────────────────────────────────────
# AUTHENTICATION
# ─────────────────────────────────────────────────────────────
def authenticate_local(username, password):
    """Yerel kullanıcı doğrulama. (ok, user_dict, msg)"""
    user = user_repo.get_by_username(username)
    if not user:
        return False, None, 'Kullanıcı bulunamadı'
    if user['auth_type'] != 'local':
        return False, None, 'Bu kullanıcı için yerel giriş desteklenmiyor'
    if not user['active']:
        return False, None, 'Hesap pasif'
    if not check_password_hash(user['password_hash'] or '', password):
        return False, None, 'Hatalı şifre'
    return True, user, 'OK'


def authenticate_ad(username, password):
    """
    Active Directory LDAP doğrulama.
    Bind → kullanıcıyı ara → grup üyeliğini kontrol et → rol belirle.
    (ok, role, full_name, msg)
    """
    if not HAS_LDAP:
        return False, None, None, 'ldap3 modülü kurulu değil'

    cfg = settings_repo.get_settings()
    if cfg.get('ad_enabled') != '1':
        return False, None, None, 'AD kimlik doğrulama etkin değil'

    ad_server   = cfg.get('ad_server', '').strip()
    ad_port     = int(cfg.get('ad_port', 389))
    ad_domain   = cfg.get('ad_domain', '').strip()
    ad_base_dn  = cfg.get('ad_base_dn', '').strip()
    bind_user   = cfg.get('ad_bind_user', '').strip()
    bind_pass   = cfg.get('ad_bind_password', '')
    user_filter = cfg.get('ad_user_filter', '(sAMAccountName={username})')
    admin_grp   = cfg.get('ad_admin_group', 'ReaR-Admins').strip()
    user_grp    = cfg.get('ad_user_group', 'ReaR-Users').strip()

    if not ad_server or not ad_domain:
        return False, None, None, 'AD yapılandırması eksik'

    try:
        srv = LdapServer(ad_server, port=ad_port, get_info=ALL, connect_timeout=5)

        # Bind kullanıcısı ile bağlan
        bind_dn = f"{bind_user}@{ad_domain}" if bind_user else f"{username}@{ad_domain}"
        bind_pw = bind_pass if bind_user else password

        conn_bind = LdapConn(srv, user=bind_dn, password=bind_pw, auto_bind=True)

        # Kullanıcıyı ara
        search_filter = user_filter.replace('{username}', username)
        conn_bind.search(
            search_base=ad_base_dn,
            search_filter=search_filter,
            attributes=['distinguishedName', 'displayName', 'memberOf', 'sAMAccountName']
        )

        if not conn_bind.entries:
            conn_bind.unbind()
            return False, None, None, 'Kullanıcı AD\'de bulunamadı'

        entry     = conn_bind.entries[0]
        user_dn   = str(entry.distinguishedName)
        full_name = str(entry.displayName) if entry.displayName else username
        member_of = [str(g) for g in entry.memberOf] if entry.memberOf else []
        conn_bind.unbind()

        # Kullanıcı adına bind (şifre doğrulama)
        user_upn = f"{username}@{ad_domain}"
        try:
            conn_user = LdapConn(srv, user=user_upn, password=password, auto_bind=True)
            conn_user.unbind()
        except LDAPException:
            return False, None, None, 'AD şifre doğrulama başarısız'

        # Grup üyeliği → rol
        role = None
        for grp_dn in member_of:
            cn = grp_dn.split(',')[0].replace('CN=', '').replace('cn=', '')
            if cn.lower() == admin_grp.lower():
                role = 'admin'
                break
            if cn.lower() == user_grp.lower():
                role = 'user'

        if role is None:
            return False, None, None, f'Kullanıcı yetkili bir AD grubunda değil ({admin_grp} / {user_grp})'

        return True, role, full_name, 'OK'

    except LDAPException as e:
        return False, None, None, f'LDAP bağlantı hatası: {str(e)}'
    except (ConnectionError, OSError, TimeoutError) as e:
        return False, None, None, f'AD sunucu bağlantı hatası: {str(e)}'


# ─────────────────────────────────────────────────────────────
# DECORATORS
# ─────────────────────────────────────────────────────────────
def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            # AJAX isteği ise JSON döndür, normal istek ise redirect
            if (request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                    or request.accept_mimetypes.best == 'application/json'
                    or request.path.startswith('/api/')):
                return jsonify({'ok': False, 'msg': 'Oturum süresi doldu, sayfayı yenileyin.'}), 401
            return redirect(url_for('login', next=request.url))
        # Session timeout kontrolü
        cfg = settings_repo.get_settings()
        timeout_min = int(cfg.get('session_timeout', 480))
        last_active = session.get('last_active', 0)
        if time.time() - last_active > timeout_min * 60:
            session.clear()
            if (request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                    or request.accept_mimetypes.best == 'application/json'
                    or request.path.startswith('/api/')):
                return jsonify({'ok': False, 'msg': 'Oturum süresi doldu, sayfayı yenileyin.'}), 401
            flash('Oturum süresi doldu.', 'warning')
            return redirect(url_for('login'))
        session['last_active'] = time.time()
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if session.get('user_role') != 'admin':
            flash('Bu işlem için yönetici yetkisi gerekli.', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return login_required(decorated)
