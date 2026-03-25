"""Repository for the users table."""
from db import get_db


def get_by_username(username):
    conn = get_db()
    row = conn.execute(
        'SELECT * FROM users WHERE username=? COLLATE NOCASE', (username,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_by_id(uid):
    conn = get_db()
    row = conn.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
    conn.close()
    return row


def get_all():
    conn = get_db()
    rows = conn.execute('SELECT * FROM users ORDER BY username').fetchall()
    conn.close()
    return rows


def create(username, password_hash, full_name, role, auth_type):
    conn = get_db()
    c = conn.execute(
        '''INSERT INTO users(username, password_hash, full_name, role, auth_type, is_builtin, active)
           VALUES(?,?,?,?,?,0,1)''',
        (username, password_hash, full_name, role, auth_type)
    )
    new_id = c.lastrowid
    conn.commit()
    conn.close()
    return new_id


def update(uid, **kwargs):
    conn = get_db()
    for k, v in kwargs.items():
        conn.execute(f"UPDATE users SET {k}=? WHERE id=?", (v, uid))
    conn.commit()
    conn.close()


def update_full(uid, full_name, role, active, password_hash):
    conn = get_db()
    conn.execute(
        'UPDATE users SET full_name=?, role=?, active=?, password_hash=? WHERE id=?',
        (full_name, role, active, password_hash, uid)
    )
    conn.commit()
    conn.close()


def delete(uid):
    conn = get_db()
    conn.execute('DELETE FROM users WHERE id=?', (uid,))
    conn.commit()
    conn.close()


def update_password(uid, password_hash):
    conn = get_db()
    conn.execute('UPDATE users SET password_hash=? WHERE id=?', (password_hash, uid))
    conn.commit()
    conn.close()


def update_last_login(uid):
    conn = get_db()
    conn.execute("UPDATE users SET last_login=datetime('now','localtime') WHERE id=?", (uid,))
    conn.commit()
    conn.close()


def upsert_ad_user(username, full_name, role):
    """Insert or update an AD user. Returns (user_id, is_new)."""
    conn = get_db()
    user = conn.execute(
        "SELECT * FROM users WHERE username=? COLLATE NOCASE AND auth_type='ad'",
        (username,)
    ).fetchone()
    if user:
        conn.execute(
            "UPDATE users SET role=?, full_name=?, last_login=datetime('now','localtime'), active=1 WHERE id=?",
            (role, full_name or username, user['id'])
        )
        user_id = user['id']
        is_new = False
    else:
        c = conn.execute(
            "INSERT INTO users(username, full_name, role, auth_type, is_builtin, active, last_login) "
            "VALUES(?,?,?,?,0,1,datetime('now','localtime'))",
            (username, full_name or username, role, 'ad')
        )
        user_id = c.lastrowid
        is_new = True
    conn.commit()
    conn.close()
    return user_id, is_new


def check_username_exists(username):
    conn = get_db()
    row = conn.execute('SELECT id FROM users WHERE username=? COLLATE NOCASE', (username,)).fetchone()
    conn.close()
    return row is not None
