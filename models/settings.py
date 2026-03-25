"""Repository for the settings table."""
from db import get_db


def get_settings():
    conn = get_db()
    rows = conn.execute('SELECT key, value FROM settings').fetchall()
    conn.close()
    return {r['key']: r['value'] for r in rows}


def save_setting(key, value):
    conn = get_db()
    conn.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', (key, value))
    conn.commit()
    conn.close()


def save_many(keys_values):
    """Save multiple settings in a single transaction. keys_values is a dict."""
    conn = get_db()
    for k, v in keys_values.items():
        conn.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', (k, v))
    conn.commit()
    conn.close()


def get_nfs_target(hostname, get_local_ip_fn, safe_dirname_fn, backup_root):
    """Return the NFS backup URL for the given hostname."""
    cfg = get_settings()
    ip = cfg.get('central_ip', get_local_ip_fn()).strip() or get_local_ip_fn()
    path = cfg.get('nfs_export_path', backup_root).strip() or backup_root
    return f"nfs://{ip}{path}/{safe_dirname_fn(hostname)}"
