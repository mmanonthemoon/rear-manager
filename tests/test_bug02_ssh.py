"""
Tests for BUG-02: SSH sudo prompt detection and 30-second timeout.

These tests verify:
1. Sudo prompt detected correctly in a single recv() chunk
2. Sudo prompt detected when split across multiple recv() chunks (accumulated buffer)
3. 30-second timeout fires when sudo prompt is never received
4. No timeout applies when actual_method is 'none' (no become)
5. Wrong password detection returns [HATA] Become and exit_code 1
"""
import time
import pytest
from unittest.mock import MagicMock, patch


# ── MockChannel ─────────────────────────────────────────────────────────────


class MockChannel:
    """Simulates a paramiko Channel for testing the recv() loop in ssh_exec_stream."""

    def __init__(self, recv_data_sequence, exit_code=0):
        """
        recv_data_sequence: list of bytes chunks to deliver via recv().
        After all chunks are delivered, exit_status_ready() returns True.
        """
        self._chunks = list(recv_data_sequence)
        self._exit_code = exit_code
        self.sent_data = []

    def recv_ready(self):
        return len(self._chunks) > 0

    def recv(self, n):
        if self._chunks:
            return self._chunks.pop(0)
        return b''

    def exit_status_ready(self):
        return len(self._chunks) == 0

    def recv_exit_status(self):
        return self._exit_code

    def sendall(self, data):
        self.sent_data.append(data)

    def close(self):
        pass

    def get_pty(self, **kwargs):
        pass

    def exec_command(self, cmd):
        pass


def _build_mock_client(mock_channel):
    """Build a mock paramiko SSHClient that returns mock_channel from open_session()."""
    mock_transport = MagicMock()
    mock_transport.open_session.return_value = mock_channel

    mock_client = MagicMock()
    mock_client.get_transport.return_value = mock_transport
    return mock_client


def _make_server(become_method='sudo', become_pass='secret'):
    """Return a minimal server dict used by ssh_exec_stream.

    become_same_pass=0 so become_password is used (not ssh_password).
    """
    return {
        'ip_address': '127.0.0.1',
        'ssh_port': '22',
        'ssh_user': 'testuser',
        'ssh_auth': 'password',
        'ssh_password': 'ssh_pass',
        'become_method': become_method,
        'become_password': become_pass,
        'become_same_pass': '0',
        'become_user': 'root',
    }


# ── Helpers ─────────────────────────────────────────────────────────────────


def _call_ssh_exec(mock_channel, server, command='echo hi'):
    """Patch build_ssh_client and call ssh_exec_stream; return (exit_code, output)."""
    import app as app_module
    mock_client = _build_mock_client(mock_channel)
    with patch.object(app_module, 'build_ssh_client', return_value=mock_client):
        return app_module.ssh_exec_stream(server, command, log_cb=lambda x: None)


# ── Tests ────────────────────────────────────────────────────────────────────


def test_prompt_detected_single_chunk():
    """Sudo prompt arrives in one chunk — password must be sent."""
    chan = MockChannel([
        b'sudo_pass_prompt: ',
        b'hello\n',
    ])
    server = _make_server(become_method='sudo', become_pass='secret')
    exit_code, output = _call_ssh_exec(chan, server)

    # Password was sent
    assert chan.sent_data, "Expected password to be sent to channel"
    assert b'secret\n' in chan.sent_data, f"Expected 'secret\\n' in sent_data, got: {chan.sent_data}"


def test_prompt_detected_split_chunks():
    """Sudo prompt split across two recv() chunks — buffer accumulation must catch it."""
    chan = MockChannel([
        b'sudo_pass_',
        b'prompt: ',
        b'hello\n',
    ])
    server = _make_server(become_method='sudo', become_pass='mypassword')
    exit_code, output = _call_ssh_exec(chan, server)

    # Password was sent despite split prompt
    assert chan.sent_data, "Expected password to be sent (split prompt not detected)"
    assert b'mypassword\n' in chan.sent_data, f"Expected 'mypassword\\n' in sent_data, got: {chan.sent_data}"


def test_prompt_timeout():
    """When sudo prompt never arrives, function must return exit_code != 0 after 30 seconds."""
    # Channel delivers some output but never the sudo prompt; never exits
    # We use a custom channel that blocks after one chunk
    class BlockingChannel(MockChannel):
        def __init__(self):
            super().__init__([b'some random output\n'])
            self._exhausted = False

        def recv_ready(self):
            # After the initial chunk is consumed, pretend more data will come (never exits)
            return not self._exhausted

        def recv(self, n):
            if self._chunks:
                data = self._chunks.pop(0)
                if not self._chunks:
                    self._exhausted = True
                return data
            return b''

        def exit_status_ready(self):
            # Never signal exit — force the timeout branch
            return False

    chan = BlockingChannel()
    server = _make_server(become_method='sudo', become_pass='secret')

    # Simulate monotonic time advancing past 30 seconds
    _call_count = [0]
    _base_time = [time.monotonic()]

    def fake_monotonic():
        _call_count[0] += 1
        # First call returns base (sets deadline). Subsequent calls return base+31.
        if _call_count[0] <= 1:
            return _base_time[0]
        return _base_time[0] + 31

    import app as app_module
    mock_client = _build_mock_client(chan)
    with patch.object(app_module, 'build_ssh_client', return_value=mock_client):
        with patch('app.time.monotonic', side_effect=fake_monotonic):
            with patch('app.time.sleep', return_value=None):
                exit_code, output = app_module.ssh_exec_stream(
                    server, 'echo hi', log_cb=lambda x: None
                )

    assert exit_code != 0, f"Expected non-zero exit code on timeout, got {exit_code}"
    assert 'Sudo prompt not received' in output, f"Expected timeout message in output, got: {output!r}"


def test_no_timeout_when_no_sudo():
    """When become_method is 'none', no timeout applies — normal completion."""
    chan = MockChannel([
        b'hello world\n',
        b'done\n',
    ])
    server = _make_server(become_method='none', become_pass='')
    exit_code, output = _call_ssh_exec(chan, server)

    # Should complete normally, no password sent, no timeout
    assert exit_code == 0, f"Expected exit_code 0, got {exit_code}"
    assert 'Sudo prompt not received' not in output


def test_wrong_password_detected():
    """After sending password, 'sorry' in response triggers [HATA] Become exit."""
    chan = MockChannel([
        b'sudo_pass_prompt: ',
        b'Sorry, try again.\n',
    ])
    server = _make_server(become_method='sudo', become_pass='wrongpass')
    exit_code, output = _call_ssh_exec(chan, server)

    assert exit_code == 1, f"Expected exit_code 1 for wrong password, got {exit_code}"
    assert '[HATA] Become' in output or 'HATA' in output, (
        f"Expected [HATA] Become in output, got: {output!r}"
    )
