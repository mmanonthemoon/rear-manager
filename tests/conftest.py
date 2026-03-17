"""Shared pytest fixtures for the rear-manager test suite."""
import threading
import pytest


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
