import os

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
DB_PATH     = os.path.join(BASE_DIR, 'rear_manager.db')
BACKUP_ROOT = '/srv/rear-backups'
KEY_PATH    = os.path.expanduser('~/.ssh/rear_manager_rsa')
BUILTIN_ADMIN   = 'admin'
OFFLINE_PKG_DIR = os.path.join(BASE_DIR, 'offline-packages')

# Ubuntu codename → sürüm numarası eşleşmesi
UBUNTU_CODENAMES = {
    'focal':  '20.04',
    'jammy':  '22.04',
    'noble':  '24.04',
    'plucky': '25.04',
}

# ── Ansible sabitleri ──────────────────────────────────────────
ANSIBLE_DIR       = os.path.join(BASE_DIR, 'ansible')
ANSIBLE_PLAYS_DIR = os.path.join(ANSIBLE_DIR, 'playbooks')
ANSIBLE_ROLES_DIR = os.path.join(ANSIBLE_DIR, 'roles')
ANSIBLE_FILES_DIR = os.path.join(ANSIBLE_DIR, 'files')
ANSIBLE_INV_DIR   = os.path.join(ANSIBLE_DIR, 'inventories')
ANSIBLE_HVARS_DIR = os.path.join(ANSIBLE_DIR, 'host_vars')
ANSIBLE_GVARS_DIR = os.path.join(ANSIBLE_DIR, 'group_vars')

SECRET_KEY_FILE = os.path.join(BASE_DIR, 'secret.key')

SCHEDULER_TIMEZONES = [
    'UTC',
    'Europe/London',
    'Europe/Berlin',
    'Europe/Istanbul',
    'Europe/Moscow',
    'Asia/Dubai',
    'Asia/Tokyo',
    'America/New_York',
    'America/Chicago',
    'America/Los_Angeles',
]
