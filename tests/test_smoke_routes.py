"""Smoke tests: hit every GET route and assert non-500 response."""
import pytest


# ─────────────────────────────────────────────────────────────
# Helper: authenticate the test client
# ─────────────────────────────────────────────────────────────

def _login(client):
    """POST /login with the built-in admin credentials.

    The default built-in admin password is 'admin123' (set in init_db).
    Returns the response from the login POST.
    """
    return client.post(
        '/login',
        data={'username': 'admin', 'password': 'admin123', 'auth_method': 'local'},
        follow_redirects=True,
    )


# ─────────────────────────────────────────────────────────────
# Smoke test: all GET routes
# ─────────────────────────────────────────────────────────────

# Routes that don't require an ID
SIMPLE_GET_ROUTES = [
    '/login',
    '/',
    '/servers',
    '/servers/add',
    '/servers/bulk-add',
    '/jobs',
    '/settings',
    '/users',
    '/users/add',
    '/users/change-password',
    '/api/status',
    '/api/schedules-status',
    '/api/offline-packages',
    '/ansible/',
    '/ansible/hosts',
    '/ansible/hosts/add',
    '/ansible/hosts/bulk-add',
    '/ansible/groups',
    '/ansible/playbooks',
    '/ansible/playbooks/add',
    '/ansible/runs',
    '/ansible/roles',
]

# Routes that require an ID — 404 is expected (not 500)
ID_GET_ROUTES = [
    '/servers/1',
    '/servers/1/edit',
    '/servers/1/configure',
    '/jobs/1',
    '/jobs/1/log',
    '/users/1/edit',
    '/ansible/hosts/1/edit',
    '/ansible/playbooks/1/edit',
    '/ansible/playbooks/1/run',
    '/ansible/runs/1',
    '/ansible/roles/1',
    '/api/ansible/run-status/1',
    '/api/ansible/run-output/1',
]


@pytest.mark.smoke
def test_smoke_unauthenticated_login_page(app_client):
    """GET /login returns 200 without authentication."""
    resp = app_client.get('/login')
    assert resp.status_code == 200


@pytest.mark.smoke
@pytest.mark.parametrize("route", SIMPLE_GET_ROUTES[1:])  # skip /login
def test_smoke_authenticated_simple_routes(app_client, route):
    """Authenticated GET of simple routes must not return 500."""
    _login(app_client)
    resp = app_client.get(route, follow_redirects=True)
    assert resp.status_code != 500, (
        f"Route {route} returned 500:\n{resp.data[:500].decode('utf-8', errors='replace')}"
    )


@pytest.mark.smoke
@pytest.mark.parametrize("route", ID_GET_ROUTES)
def test_smoke_authenticated_id_routes(app_client, route):
    """Authenticated GET of ID-based routes must not return 500 (404 is fine)."""
    _login(app_client)
    resp = app_client.get(route, follow_redirects=True)
    assert resp.status_code != 500, (
        f"Route {route} returned 500:\n{resp.data[:500].decode('utf-8', errors='replace')}"
    )


@pytest.mark.smoke
def test_smoke_logout(app_client):
    """GET /logout returns redirect (302) or 200."""
    _login(app_client)
    resp = app_client.get('/logout')
    assert resp.status_code in (200, 302)
