"""
Tests for BUG-04: app.secret_key must persist across restarts via secret.key file.

These tests import _load_or_create_secret_key from app (monkeypatching the
SECRET_KEY_FILE global) and verify the file-based persistence behavior.
"""
import os
import pytest
import app as app_module


def test_creates_key_file(tmp_path, monkeypatch):
    """Key file is created when it does not exist; returned key matches file content."""
    key_file = tmp_path / "secret.key"
    monkeypatch.setattr(app_module, "SECRET_KEY_FILE", str(key_file))

    key = app_module._load_or_create_secret_key()

    assert key_file.exists(), "secret.key file should have been created"
    assert key_file.read_text().strip() == key, "File content must match returned key"
    assert len(key) == 64, f"Key should be 64 hex chars (32 bytes), got {len(key)}"


def test_reads_existing_key(tmp_path, monkeypatch):
    """Existing key file content is returned without regeneration."""
    key_file = tmp_path / "secret.key"
    existing_key = "abcd1234" * 8  # 64 chars
    key_file.write_text(existing_key)
    monkeypatch.setattr(app_module, "SECRET_KEY_FILE", str(key_file))

    key = app_module._load_or_create_secret_key()

    assert key == existing_key, "Should return existing file content unchanged"


def test_key_stable(tmp_path, monkeypatch):
    """Two consecutive calls with no file return the same key."""
    key_file = tmp_path / "secret.key"
    monkeypatch.setattr(app_module, "SECRET_KEY_FILE", str(key_file))

    key1 = app_module._load_or_create_secret_key()
    key2 = app_module._load_or_create_secret_key()

    assert key1 == key2, "Key must be stable across multiple calls"


def test_file_permissions(tmp_path, monkeypatch):
    """Newly created secret.key file has mode 0o600."""
    key_file = tmp_path / "secret.key"
    monkeypatch.setattr(app_module, "SECRET_KEY_FILE", str(key_file))

    app_module._load_or_create_secret_key()

    mode = os.stat(str(key_file)).st_mode & 0o777
    assert mode == 0o600, f"Expected permissions 0o600, got 0o{mode:03o}"


def test_empty_file_regenerates(tmp_path, monkeypatch):
    """An empty secret.key file causes a new key to be generated and written."""
    key_file = tmp_path / "secret.key"
    key_file.write_text("")
    monkeypatch.setattr(app_module, "SECRET_KEY_FILE", str(key_file))

    key = app_module._load_or_create_secret_key()

    assert len(key) == 64, f"Regenerated key should be 64 hex chars, got {len(key)}"
    assert key_file.read_text().strip() == key, "File should contain the new key"
