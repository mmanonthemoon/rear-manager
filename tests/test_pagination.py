"""Tests for pagination in repository layer and route handlers."""
import pytest


# ── Repository layer tests ───────────────────────────────────────────────────

def test_jobs_list_page_1(app_with_db):
    """get_all_filtered returns (rows, total) and respects limit."""
    from models import jobs as job_repo
    rows, total = job_repo.get_all_filtered(offset=0, limit=25)
    assert isinstance(rows, list)
    assert isinstance(total, int)
    assert len(rows) <= 25


def test_jobs_pagination_offset(app_with_db):
    """get_all_filtered with offset=25, limit=25 returns second page."""
    from models import jobs as job_repo
    rows, total = job_repo.get_all_filtered(offset=25, limit=25)
    assert isinstance(rows, list)
    assert len(rows) <= 25


def test_servers_get_all_paginated(app_with_db):
    """get_all returns (rows, total) tuple."""
    from models import servers as server_repo
    rows, total = server_repo.get_all(offset=0, limit=25)
    assert isinstance(rows, list)
    assert isinstance(total, int)
    assert len(rows) <= 25


def test_ansible_runs_paginated(app_with_db):
    """get_runs returns (rows, total) tuple."""
    from models import ansible as ansible_repo
    rows, total = ansible_repo.get_runs(offset=0, limit=25)
    assert isinstance(rows, list)
    assert isinstance(total, int)
    assert len(rows) <= 25


# ── Route handler tests ──────────────────────────────────────────────────────

def test_pagination_nav_links(app_with_db):
    """Jobs list route returns 200 and accepts page query param."""
    client = app_with_db
    # Login first
    client.post('/login', data={'username': 'admin', 'password': 'admin123', 'auth_method': 'local'}, follow_redirects=True)
    resp = client.get('/jobs?page=1')
    assert resp.status_code == 200


def test_jobs_page_clamp(app_with_db):
    """Page param < 1 is clamped to 1 (no crash, returns 200)."""
    client = app_with_db
    client.post('/login', data={'username': 'admin', 'password': 'admin123', 'auth_method': 'local'}, follow_redirects=True)
    resp = client.get('/jobs?page=0')
    assert resp.status_code == 200
    resp2 = client.get('/jobs?page=-5')
    assert resp2.status_code == 200


def test_servers_list_paginated(app_with_db):
    """Servers list route returns 200 and accepts page query param."""
    client = app_with_db
    client.post('/login', data={'username': 'admin', 'password': 'admin123', 'auth_method': 'local'}, follow_redirects=True)
    resp = client.get('/servers?page=1')
    assert resp.status_code == 200


def test_ansible_runs_list_paginated(app_with_db):
    """Ansible runs list route returns 200 and accepts page query param."""
    client = app_with_db
    client.post('/login', data={'username': 'admin', 'password': 'admin123', 'auth_method': 'local'}, follow_redirects=True)
    resp = client.get('/ansible/runs?page=1')
    assert resp.status_code == 200
