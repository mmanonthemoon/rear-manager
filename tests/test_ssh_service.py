"""
Unit tests for services/ssh.py — ssh_upload_file, ssh_test_connection, ssh_get_os_info.

All tests run without a real SSH server. build_ssh_client is patched at module level
via patch.object(ssh_module, 'build_ssh_client', ...) so no network calls occur.

Reuses MockChannel and _build_mock_client from test_bug02_ssh.py.
"""
import io
import pytest
from unittest.mock import MagicMock, patch, call

import services.ssh as ssh_module
from services.ssh import SSHAuthenticationError
from tests.test_bug02_ssh import MockChannel, _build_mock_client


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def _make_sftp_client():
    """Return a MagicMock SFTP client whose open() supports context manager usage."""
    sftp = MagicMock()
    # open() returns a context manager — simulate file write
    mock_file = MagicMock()
    sftp.open.return_value.__enter__ = MagicMock(return_value=mock_file)
    sftp.open.return_value.__exit__ = MagicMock(return_value=False)
    return sftp, mock_file


def _make_ssh_client_with_sftp(sftp):
    """Return a MagicMock SSH client whose open_sftp() returns sftp."""
    mock_client = MagicMock()
    mock_client.open_sftp.return_value = sftp
    return mock_client


def _make_exec_command_stdout(text):
    """Return a (stdin, stdout, stderr) triple where stdout yields text bytes."""
    stdout = MagicMock()
    stdout.read.return_value = text.encode('utf-8') if isinstance(text, str) else text
    stderr = MagicMock()
    stderr.read.return_value = b''
    stdin = MagicMock()
    return stdin, stdout, stderr


# ─────────────────────────────────────────────────────────────
# ssh_upload_file TESTS
# ─────────────────────────────────────────────────────────────

def test_ssh_upload_file_no_become(server_dict, app_context):
    """become_method='none' writes content directly via SFTP to exact remote_path."""
    server = {**server_dict, 'become_method': 'none'}
    remote_path = '/etc/rear/site.conf'
    content = 'BACKUP=NETFS\n'

    sftp, mock_file = _make_sftp_client()
    mock_client = _make_ssh_client_with_sftp(sftp)

    with patch.object(ssh_module, 'build_ssh_client', return_value=mock_client):
        ok, msg = ssh_module.ssh_upload_file(server, content, remote_path)

    assert ok is True
    assert msg == 'OK'
    # SFTP open must be called with the exact remote_path in write mode
    sftp.open.assert_called_once_with(remote_path, 'w')
    # Content must be written
    mock_file.write.assert_called_once_with(content)


def test_ssh_upload_file_with_become(server_dict, app_context):
    """become_method='sudo' writes to /tmp then calls ssh_exec_stream to mv + chmod."""
    server = {**server_dict, 'become_method': 'sudo'}
    remote_path = '/etc/rear/site.conf'
    content = 'BACKUP=NETFS\n'

    sftp, mock_file = _make_sftp_client()
    mock_client = _make_ssh_client_with_sftp(sftp)

    with patch.object(ssh_module, 'build_ssh_client', return_value=mock_client):
        with patch.object(ssh_module, 'ssh_exec_stream', return_value=(0, '')) as mock_exec:
            ok, msg = ssh_module.ssh_upload_file(server, content, remote_path)

    assert ok is True
    assert msg == 'OK'

    # SFTP open must have written to a /tmp path (not remote_path directly)
    assert sftp.open.call_count == 1
    tmp_path_used = sftp.open.call_args[0][0]
    assert tmp_path_used.startswith('/tmp/'), (
        f"Expected /tmp/ path for become upload, got: {tmp_path_used}"
    )
    assert tmp_path_used != remote_path

    # ssh_exec_stream must have been called to mv + chmod
    assert mock_exec.call_count == 1
    cmd_arg = mock_exec.call_args[0][1]
    assert 'mv' in cmd_arg, f"Expected mv in exec command, got: {cmd_arg!r}"
    assert remote_path in cmd_arg, f"Expected remote_path in exec command, got: {cmd_arg!r}"


# ─────────────────────────────────────────────────────────────
# ssh_test_connection TESTS
# ─────────────────────────────────────────────────────────────

def test_ssh_test_connection_success(server_dict, app_context):
    """build_ssh_client returns mock; exec_command yields uid=0(root); returns (True, 'SSH OK')."""
    # ssh_test_connection calls:
    #   1. build_ssh_client → client
    #   2. client.exec_command('id && uname -r') → (stdin, stdout, stderr)
    #   3. client.close()
    #   4. ssh_exec_stream(server, 'id && whoami', ...) → (0, 'uid=0(root)\nroot')

    _, stdout, stderr = _make_exec_command_stdout('uid=0(root)\nLinux 5.15')
    mock_client = MagicMock()
    mock_client.exec_command.return_value = (MagicMock(), stdout, stderr)

    with patch.object(ssh_module, 'build_ssh_client', return_value=mock_client):
        with patch.object(ssh_module, 'ssh_exec_stream',
                          return_value=(0, 'uid=0(root)\nroot')) as mock_exec:
            ok, msg = ssh_module.ssh_test_connection(server_dict)

    assert ok is True, f"Expected True, got {ok!r}. Message: {msg!r}"
    assert 'SSH OK' in msg, f"Expected 'SSH OK' in message, got: {msg!r}"


def test_ssh_test_connection_auth_failure(server_dict, app_context):
    """build_ssh_client raises SSHAuthenticationError; returns (False, message with 'Auth failed')."""
    with patch.object(ssh_module, 'build_ssh_client',
                      side_effect=SSHAuthenticationError('Auth failed for test.local: bad key')):
        ok, msg = ssh_module.ssh_test_connection(server_dict)

    assert ok is False, f"Expected False, got {ok!r}"
    assert 'Auth failed' in msg, f"Expected 'Auth failed' in message, got: {msg!r}"


def test_ssh_test_connection_become_failure(server_dict, app_context):
    """build_ssh_client succeeds, ssh_exec_stream returns (1, 'sorry'); returns (False, msg with 'become')."""
    _, stdout, stderr = _make_exec_command_stdout('uid=1000(testuser)\nLinux 5.15')
    mock_client = MagicMock()
    mock_client.exec_command.return_value = (MagicMock(), stdout, stderr)

    with patch.object(ssh_module, 'build_ssh_client', return_value=mock_client):
        with patch.object(ssh_module, 'ssh_exec_stream',
                          return_value=(1, 'sorry, try again')):
            ok, msg = ssh_module.ssh_test_connection(server_dict)

    assert ok is False, f"Expected False for become failure, got {ok!r}"
    # Message should mention become failure
    msg_lower = msg.lower()
    assert 'become' in msg_lower, f"Expected 'become' in message, got: {msg!r}"


# ─────────────────────────────────────────────────────────────
# ssh_get_os_info TESTS
# ─────────────────────────────────────────────────────────────

def test_ssh_get_os_info_success(server_dict, app_context):
    """build_ssh_client returns mock with exec_command returning os-release content."""
    os_release_content = 'NAME="Ubuntu"\nVERSION_ID="22.04"'
    _, stdout, _ = _make_exec_command_stdout(os_release_content)
    mock_client = MagicMock()
    mock_client.exec_command.return_value = (MagicMock(), stdout, MagicMock())

    with patch.object(ssh_module, 'build_ssh_client', return_value=mock_client):
        result = ssh_module.ssh_get_os_info(server_dict)

    assert 'NAME="Ubuntu"' in result, f"Expected Ubuntu in result, got: {result!r}"
    assert 'VERSION_ID="22.04"' in result, f"Expected VERSION_ID in result, got: {result!r}"
