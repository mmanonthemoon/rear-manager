"""Shared pytest fixtures for the rear-manager test suite."""
import sys
import os
import threading
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def tmp_base_dir(tmp_path):
    """Return a temporary directory path for secret.key file tests."""
    return tmp_path


@pytest.fixture
def mock_running_jobs():
    """Provide a fresh dict and Lock pair for lock-protection tests."""
    d = {}
    lock = threading.Lock()
    return d, lock


@pytest.fixture
def app_client():
    """Create a Flask test client with a temporary in-memory-like DB."""
    import tempfile
    import app as app_module

    # Use a temp DB file (SQLite in-memory doesn't work across threads)
    db_fd, db_path = tempfile.mkstemp(suffix='.db')

    # Patch DB_PATH in both config and db modules
    import config
    import db as db_module
    old_config_path = config.DB_PATH
    old_db_path = db_module.DB_PATH if hasattr(db_module, 'DB_PATH') else config.DB_PATH
    config.DB_PATH = db_path
    if hasattr(db_module, 'DB_PATH'):
        db_module.DB_PATH = db_path

    # Initialize the DB schema
    db_module.init_db()

    # Create test client
    app_module.app.config['TESTING'] = True
    client = app_module.app.test_client()

    yield client

    # Cleanup
    config.DB_PATH = old_config_path
    if hasattr(db_module, 'DB_PATH'):
        db_module.DB_PATH = old_db_path
    os.close(db_fd)
    os.unlink(db_path)


@pytest.fixture
def server_dict():
    """Minimal server dict for SSH/ReaR service tests."""
    return {
        'id': 1,
        'label': 'test-server',
        'hostname': 'test.local',
        'ip_address': '127.0.0.1',
        'ssh_port': '22',
        'ssh_user': 'testuser',
        'ssh_auth': 'password',
        'ssh_password': 'ssh_pass',
        'become_method': 'sudo',
        'become_password': 'become_pass',
        'become_same_pass': '0',
        'become_user': 'root',
        'exclude_dirs': '',
    }


@pytest.fixture
def app_context():
    """Push Flask app context for service-layer tests that use current_app.logger."""
    import app as app_module
    with app_module.app.app_context():
        yield


@pytest.fixture
def app_with_db(app_client):
    """Provides app context + initialized DB for service-layer tests needing DB access."""
    import app as app_module
    with app_module.app.app_context():
        yield app_client
