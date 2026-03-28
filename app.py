#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ReaR Manager v2.0 - Merkezi ReaR Yedekleme Yönetim Paneli
App factory: creates Flask app, registers Blueprints, initializes DB and scheduler.
"""

import os
import re
import datetime
import secrets

from flask import Flask

from config import SECRET_KEY_FILE
from db import init_db


def _load_or_create_secret_key():
    if os.path.exists(SECRET_KEY_FILE):
        with open(SECRET_KEY_FILE, 'r') as f:
            key = f.read().strip()
        if key:
            return key
    key = secrets.token_hex(32)
    with open(SECRET_KEY_FILE, 'w') as f:
        f.write(key)
    os.chmod(SECRET_KEY_FILE, 0o600)
    return key


def _cron_describe(minute, hour, dom, month, dow):
    """Cron ifadesini insan okunabilir Türkçe metne çevirir."""
    try:
        m  = str(minute or '*').strip()
        h  = str(hour or '*').strip()
        d  = str(dom or '*').strip()
        mo = str(month or '*').strip()
        dw = str(dow or '*').strip()

        gun_adlari = {
            '0': 'Pazar', '1': 'Pazartesi', '2': 'Salı', '3': 'Çarşamba',
            '4': 'Perşembe', '5': 'Cuma', '6': 'Cumartesi', '7': 'Pazar',
            '1-5': 'Hft içi', '0-4': 'Pzt-Per', '0,6': 'Hft sonu', '6,0': 'Hft sonu',
        }
        ay_adlari = {
            '1': 'Oca', '2': 'Şub', '3': 'Mar', '4': 'Nis',
            '5': 'May', '6': 'Haz', '7': 'Tem', '8': 'Ağu',
            '9': 'Eyl', '10': 'Eki', '11': 'Kas', '12': 'Ara',
        }

        if h.startswith('*/') and m == '0' and d == '*' and mo == '*' and dw == '*':
            return f'Her {h[2:]} saatte'
        if m.startswith('*/') and h == '*' and d == '*' and mo == '*' and dw == '*':
            return f'Her {m[2:]} dakikada'
        if m.isdigit() and h.isdigit() and d == '*' and mo == '*' and dw == '*':
            return f'Her gün {h.zfill(2)}:{m.zfill(2)}'
        if m.isdigit() and h.isdigit() and d == '*' and mo == '*' and dw in gun_adlari:
            return f'Her {gun_adlari[dw]} {h.zfill(2)}:{m.zfill(2)}'
        if m.isdigit() and h.isdigit() and d.isdigit() and mo == '*' and dw == '*':
            return f'Her ay {d}. gün {h.zfill(2)}:{m.zfill(2)}'
        if m.isdigit() and h.isdigit() and d.isdigit() and mo in ay_adlari and dw == '*':
            return f'Her yıl {ay_adlari[mo]} {d}. gün {h.zfill(2)}:{m.zfill(2)}'
        return f'{m} {h} {d} {mo} {dw}'
    except Exception:
        return ''


def _safe_dirname(hostname):
    """
    Hostname'i güvenli bir dizin adına dönüştürür.
    """
    safe = re.sub(r'[^a-zA-Z0-9_-]', lambda m: '-' if m.group() == '.' else '', hostname)
    safe = re.sub(r'-{2,}', '-', safe)
    safe = safe.strip('-')
    return safe or hostname


def calc_duration_filter(started_at, finished_at):
    """İki tarih string'i arasındaki süreyi insan okunabilir formatta döner."""
    if not started_at or not finished_at:
        return '-'
    try:
        fmt = '%Y-%m-%d %H:%M:%S'
        start = datetime.datetime.strptime(str(started_at)[:19], fmt)
        end   = datetime.datetime.strptime(str(finished_at)[:19], fmt)
        secs  = int((end - start).total_seconds())
        if secs < 0:
            return '-'
        if secs < 60:
            return f'{secs}s'
        elif secs < 3600:
            return f'{secs // 60}m {secs % 60}s'
        else:
            h = secs // 3600
            m = (secs % 3600) // 60
            return f'{h}h {m}m'
    except Exception:
        return '-'


app = Flask(__name__)
app.secret_key = _load_or_create_secret_key()

# Register template filters and globals
app.jinja_env.filters['calc_duration'] = calc_duration_filter
app.jinja_env.globals['_cron_describe'] = _cron_describe
app.jinja_env.globals['_safe_dirname'] = _safe_dirname

# Register Blueprints
from routes.auth import auth_bp
from routes.dashboard import dashboard_bp
from routes.servers import servers_bp
from routes.schedules import schedules_bp
from routes.jobs import jobs_bp
from routes.settings import settings_bp
from routes.users import users_bp
from routes.api import api_bp

app.register_blueprint(auth_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(servers_bp)
app.register_blueprint(schedules_bp)
app.register_blueprint(jobs_bp)
app.register_blueprint(settings_bp)
app.register_blueprint(users_bp)
app.register_blueprint(api_bp)

# DB init
with app.app_context():
    init_db()

# Scheduler init
try:
    from services.scheduler import init_scheduler
    init_scheduler()
except Exception:
    pass

if __name__ == '__main__':
    from config import BACKUP_ROOT
    os.makedirs(BACKUP_ROOT, exist_ok=True)
    app.run(host='0.0.0.0', port=80, debug=False, threaded=True)
