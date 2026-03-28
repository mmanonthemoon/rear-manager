"""Authentication Blueprint — /login, /logout routes."""

import time

from flask import Blueprint, render_template, redirect, url_for, flash, request, session

from services.auth import authenticate_local, authenticate_ad
from models import users as user_repo
from models import settings as settings_repo


auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect(url_for('dashboard.dashboard'))

    cfg = settings_repo.get_settings()
    ad_enabled = cfg.get('ad_enabled') == '1'
    error = None

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        auth_method = request.form.get('auth_method', 'local')

        if auth_method == 'ad' and ad_enabled:
            ok, role, full_name, msg = authenticate_ad(username, password)
            if ok:
                user_id, _ = user_repo.upsert_ad_user(username, full_name, role)

                session['user_id']       = user_id
                session['username']      = username
                session['user_role']     = role
                session['full_name']     = full_name or username
                session['last_active']   = time.time()
                return redirect(request.args.get('next') or url_for('dashboard.dashboard'))
            else:
                error = f'AD Giriş Hatası: {msg}'

        else:  # local
            ok, user, msg = authenticate_local(username, password)
            if ok:
                user_repo.update_last_login(user['id'])

                session['user_id']     = user['id']
                session['username']    = user['username']
                session['user_role']   = user['role']
                session['full_name']   = user.get('full_name') or user['username']
                session['last_active'] = time.time()
                return redirect(request.args.get('next') or url_for('dashboard.dashboard'))
            else:
                error = f'Giriş Hatası: {msg}'

    return render_template('login.html', ad_enabled=ad_enabled, error=error)


@auth_bp.route('/logout')
def logout():
    session.clear()
    flash('Oturum kapatıldı.', 'info')
    return redirect(url_for('auth.login'))
