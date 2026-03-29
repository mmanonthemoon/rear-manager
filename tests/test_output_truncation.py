"""Tests for truncate_output helper and 1 MB cap in repository append functions."""
import pytest


# ── Unit tests for truncate_output ──────────────────────────────────────────

def test_truncate_output_no_op_under_limit():
    """Text under 1 MB is returned unchanged."""
    from utils import truncate_output
    text = 'a' * 100
    result = truncate_output(text)
    assert result == text


def test_truncate_output_none_passthrough():
    """None input is returned unchanged."""
    from utils import truncate_output
    assert truncate_output(None) is None


def test_truncate_output_empty_passthrough():
    """Empty string is returned unchanged."""
    from utils import truncate_output
    assert truncate_output('') == ''


def test_truncate_output():
    """Output over 1 MB is truncated to at most 1 MB bytes + marker."""
    from utils import truncate_output
    big_text = 'x' * 2_000_000
    result = truncate_output(big_text)
    assert len(result.encode('utf-8')) <= 1_000_000 + 200  # 200 bytes slack for marker
    assert '[... çıkış 1 MB sınırında kesildi ...]' in result


def test_truncate_output_utf8_safety():
    """Multi-byte UTF-8 characters are not corrupted during truncation."""
    from utils import truncate_output
    # Turkish chars are 2 bytes each in UTF-8
    # Create text that exceeds 1 MB using Turkish chars
    turkish_char = 'ş'  # 2-byte UTF-8 sequence
    text = turkish_char * 600_000  # ~1.2 MB in UTF-8
    result = truncate_output(text)
    # Must decode cleanly — no UnicodeDecodeError on encode/decode round-trip
    try:
        result.encode('utf-8').decode('utf-8')
    except UnicodeDecodeError:
        pytest.fail('truncate_output produced invalid UTF-8 sequence')
    assert '[... çıkış 1 MB sınırında kesildi ...]' in result


def test_truncate_output_exact_limit():
    """Text exactly at 1 MB is NOT truncated."""
    from utils import truncate_output
    text = 'a' * 1_000_000  # exactly 1 MB for ASCII
    result = truncate_output(text)
    assert result == text  # no truncation marker
    assert '[... çıkış 1 MB sınırında kesildi ...]' not in result


# ── Integration tests ────────────────────────────────────────────────────────

def test_job_output_capped(app_with_db):
    """append_log caps backup_jobs.log_output at 1 MB."""
    import db as db_module
    from models import jobs as job_repo
    # Insert a minimal backup_jobs row directly
    conn = db_module.get_db()
    conn.execute(
        "INSERT INTO backup_jobs(server_id, status, job_type, triggered_by) VALUES(?,?,?,?)",
        (0, 'running', 'backup', 'test')
    )
    conn.commit()
    jid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    # Append 1.1 MB of text
    big_text = 'L' * 1_100_000
    job_repo.append_log(jid, big_text)
    # Read back
    conn = db_module.get_db()
    row = conn.execute("SELECT log_output FROM backup_jobs WHERE id=?", (jid,)).fetchone()
    conn.close()
    stored = row['log_output'] or ''
    assert len(stored.encode('utf-8')) <= 1_000_000 + 200  # 200 bytes for marker


def test_ansible_run_output_capped(app_with_db):
    """append_run_log caps ansible_runs.output at 1 MB."""
    import db as db_module
    from models import ansible as ansible_repo
    # Insert minimal ansible_runs row
    conn = db_module.get_db()
    # Need a playbook first (foreign key may not be enforced in SQLite without PRAGMA)
    conn.execute(
        "INSERT INTO ansible_runs(playbook_id, playbook_name, inventory, extra_vars, limit_hosts, tags_run, triggered_by, status)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (0, 'test', '', '', '', '', 'test', 'running')
    )
    conn.commit()
    rid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    # Append 1.1 MB of text
    big_text = 'A' * 1_100_000
    ansible_repo.append_run_log(rid, big_text)
    # Read back
    conn = db_module.get_db()
    row = conn.execute("SELECT output FROM ansible_runs WHERE id=?", (rid,)).fetchone()
    conn.close()
    stored = row['output'] or ''
    assert len(stored.encode('utf-8')) <= 1_000_000 + 200
