"""Users Blueprint — /users/* routes."""

import hashlib

from flask import Blueprint, render_template, redirect, url_for, flash, request, session

from services.auth import login_required, admin_required
from models import users as user_repo
from config import BUILTIN_ADMIN


users_bp = Blueprint('users', __name__)


try:
    from werkzeug.security import generate_password_hash, check_password_hash
except ImportError:
    def generate_password_hash(pw):
        return hashlib.sha256(pw.encode()).hexdigest()
    def check_password_hash(h, pw):
        return h == hashlib.sha256(pw.encode()).hexdigest()


@users_bp.route('/users')
@login_required
@admin_required
def users_list():
    users = user_repo.get_all()
    return render_template('users.html', users=users)


@users_bp.route('/users/add', methods=['GET', 'POST'])
@login_required
@admin_required
def user_add():
    if request.method == 'POST':
        d = request.form
        uname = d['username'].strip()
        if not uname:
            flash('Kullanıcı adı boş olamaz.', 'danger')
            return redirect(url_for('users.user_add'))

        if user_repo.check_username_exists(uname):
            flash('Bu kullanıcı adı zaten mevcut.', 'danger')
            return redirect(url_for('users.user_add'))

        pw_hash = None
        if d.get('auth_type', 'local') == 'local':
            pw = d.get('password', '')
            if not pw:
                flash('Yerel hesap için şifre gerekli.', 'danger')
                return redirect(url_for('users.user_add'))
            pw_hash = generate_password_hash(pw)

        user_repo.create(uname, pw_hash, d.get('full_name', ''), d.get('role', 'user'), d.get('auth_type', 'local'))
        flash(f'Kullanıcı "{uname}" eklendi.', 'success')
        return redirect(url_for('users.users_list'))
    return render_template('user_form.html', user=None, title='Kullanıcı Ekle')


@users_bp.route('/users/<int:uid>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def user_edit(uid):
    user = user_repo.get_by_id(uid)
    if not user:
        flash('Kullanıcı bulunamadı.', 'danger')
        return redirect(url_for('users.users_list'))

    if request.method == 'POST':
        d = request.form
        pw_hash = user['password_hash']
        new_pw = d.get('password', '').strip()
        if new_pw:
            pw_hash = generate_password_hash(new_pw)

        user_repo.update_full(
            uid,
            d.get('full_name', ''),
            d.get('role', 'user') if not user['is_builtin'] else 'admin',
            1 if d.get('active') else 0,
            pw_hash
        )
        flash('Kullanıcı güncellendi.', 'success')
        return redirect(url_for('users.users_list'))

    return render_template('user_form.html', user=dict(user), title='Kullanıcı Düzenle')


@users_bp.route('/users/<int:uid>/delete', methods=['POST'])
@login_required
@admin_required
def user_delete(uid):
    user = user_repo.get_by_id(uid)
    if not user:
        flash('Kullanıcı bulunamadı.', 'danger')
        return redirect(url_for('users.users_list'))
    if user['is_builtin']:
        flash('Yerleşik admin hesabı silinemez!', 'danger')
        return redirect(url_for('users.users_list'))
    if user['id'] == session.get('user_id'):
        flash('Kendi hesabınızı silemezsiniz!', 'danger')
        return redirect(url_for('users.users_list'))
    user_repo.delete(uid)
    flash('Kullanıcı silindi.', 'success')
    return redirect(url_for('users.users_list'))


@users_bp.route('/users/change-password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == 'POST':
        old_pw  = request.form.get('old_password', '')
        new_pw  = request.form.get('new_password', '')
        new_pw2 = request.form.get('new_password2', '')

        if not new_pw:
            flash('Yeni şifre boş olamaz.', 'danger')
            return redirect(url_for('users.change_password'))

        if new_pw != new_pw2:
            flash('Yeni şifreler eşleşmiyor.', 'danger')
            return redirect(url_for('users.change_password'))

        user = user_repo.get_by_id(session['user_id'])

        if not user or user['auth_type'] != 'local':
            flash('Bu işlem sadece yerel hesaplar için geçerlidir.', 'danger')
            return redirect(url_for('dashboard.dashboard'))

        if not check_password_hash(user['password_hash'] or '', old_pw):
            flash('Mevcut şifre hatalı.', 'danger')
            return redirect(url_for('users.change_password'))

        user_repo.update_password(session['user_id'], generate_password_hash(new_pw))
        flash('Şifre değiştirildi.', 'success')
        return redirect(url_for('dashboard.dashboard'))

    return render_template('change_password.html')
