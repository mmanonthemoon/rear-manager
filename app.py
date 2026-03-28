#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ReaR Manager v2.0 - Merkezi ReaR Yedekleme Yönetim Paneli
App factory: creates Flask app, registers Blueprints, initializes DB and scheduler.
"""

import os
import secrets

from flask import Flask

from config import SECRET_KEY_FILE, SCHEDULER_TIMEZONES  # noqa: F401 — re-exported for test compatibility
from db import init_db
from utils import cron_describe, safe_dirname, calc_duration


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


app = Flask(__name__)
app.secret_key = _load_or_create_secret_key()

# Register template filters and globals
app.jinja_env.filters['calc_duration'] = calc_duration
app.jinja_env.globals['_cron_describe'] = cron_describe
app.jinja_env.globals['_safe_dirname'] = safe_dirname

# Register Blueprints
from routes.auth import auth_bp
from routes.dashboard import dashboard_bp
from routes.servers import servers_bp
from routes.schedules import schedules_bp
from routes.jobs import jobs_bp
from routes.settings import settings_bp
from routes.users import users_bp
from routes.api import api_bp
from routes.ansible import ansible_bp

app.register_blueprint(auth_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(servers_bp)
app.register_blueprint(schedules_bp)
app.register_blueprint(jobs_bp)
app.register_blueprint(settings_bp)
app.register_blueprint(users_bp)
app.register_blueprint(api_bp)
app.register_blueprint(ansible_bp)

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
