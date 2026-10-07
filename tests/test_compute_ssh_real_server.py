# tests/test_compute_ssh_real_server.py
#
# _get_ssh_client() against a REAL SSH server (paramiko in server mode, on localhost), so the
# authentication order and the host-key pinning are exercised for real rather than mocked.

import socket
import threading
from unittest.mock import MagicMock, patch

import paramiko
import pytest

from compute import manager as ssh


class _Server(paramiko.ServerInterface):
    def __init__(self, authorized_key=None, password=None):
        self.authorized_key, self.password = authorized_key, password
        self.method = None                      # which method finally succeeded

    def get_allowed_auths(self, username):
        return "publickey,password"

    def check_auth_publickey(self, username, key):
        if self.authorized_key is not None and key.asbytes() == self.authorized_key.asbytes():
            self.method = "key"
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_auth_password(self, username, password):
        if self.password is not None and password == self.password:
            self.method = "password"
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED


@pytest.fixture
def sshd():
    """Start one-connection SSH servers on demand: sshd(host_key, authorized_key=None, password=None)."""
    started = []

    def start(host_key, authorized_key=None, password=None):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        sock.settimeout(10)
        server = _Server(authorized_key, password)

        def run():
            try:
                conn, _ = sock.accept()
                t = paramiko.Transport(conn)
                t.add_server_key(host_key)
                t.start_server(server=server)
                t.join(5)
            except Exception:
                pass
            finally:
                sock.close()

        threading.Thread(target=run, daemon=True).start()
        started.append(sock)
        return sock.getsockname()[1], server

    yield start
    for s in started:
        s.close()


def settings_for(tmp_path, port, *, client_key=None, password=None, known_host_key=None):
    s = MagicMock()
    s.compute_host, s.compute_port, s.compute_username = "127.0.0.1", port, "compute"
    s.compute_password = password
    s.compute_ssh_key_path = None
    s.compute_known_hosts_path = None
    if client_key is not None:
        p = tmp_path / "client_key"
        client_key.write_private_key_file(str(p))
        s.compute_ssh_key_path = str(p)
    if known_host_key is not None:
        hk = paramiko.HostKeys()
        hk.add(f"[127.0.0.1]:{port}", known_host_key.get_name(), known_host_key)
        p = tmp_path / "known_hosts"
        hk.save(str(p))
        s.compute_known_hosts_path = str(p)
    return s


def connect(settings):
    with patch.object(ssh, "settings", settings):
        return ssh._get_ssh_client()


def test_key_authentication_with_a_pinned_host_key(tmp_path, sshd):
    host, client_key = paramiko.ECDSAKey.generate(), paramiko.ECDSAKey.generate()
    port, server = sshd(host, authorized_key=client_key, password="pw")
    c = connect(settings_for(tmp_path, port, client_key=client_key, password="pw", known_host_key=host))
    try:
        assert c.get_transport().is_authenticated()
        assert server.method == "key"                  # the key was used, not the password
    finally:
        c.close()


def test_password_is_the_fallback_when_the_key_is_not_authorised(tmp_path, sshd):
    host, other_key = paramiko.ECDSAKey.generate(), paramiko.ECDSAKey.generate()
    port, server = sshd(host, authorized_key=None, password="pw")
    c = connect(settings_for(tmp_path, port, client_key=other_key, password="pw", known_host_key=host))
    try:
        assert c.get_transport().is_authenticated()
        assert server.method == "password"             # the key was refused, the password then worked
    finally:
        c.close()


def test_wrong_credentials_are_an_authentication_error(tmp_path, sshd):
    host, other_key = paramiko.ECDSAKey.generate(), paramiko.ECDSAKey.generate()
    port, _ = sshd(host, authorized_key=None, password="right")
    with pytest.raises(paramiko.AuthenticationException):
        connect(settings_for(tmp_path, port, client_key=other_key, password="wrong", known_host_key=host))


def test_an_impostor_host_key_is_refused_when_pinned(tmp_path, sshd):
    real_host, impostor = paramiko.ECDSAKey.generate(), paramiko.ECDSAKey.generate()
    client_key = paramiko.ECDSAKey.generate()
    port, server = sshd(impostor, authorized_key=client_key, password="pw")     # someone else answers
    with pytest.raises(paramiko.SSHException):
        connect(settings_for(tmp_path, port, client_key=client_key, password="pw", known_host_key=real_host))
    assert server.method is None                       # credentials were never sent to the impostor


def test_without_pinning_any_host_key_is_still_accepted(tmp_path, sshd):
    host, client_key = paramiko.ECDSAKey.generate(), paramiko.ECDSAKey.generate()
    port, server = sshd(host, authorized_key=client_key)
    c = connect(settings_for(tmp_path, port, client_key=client_key))
    try:
        assert c.get_transport().is_authenticated() and server.method == "key"
    finally:
        c.close()


def test_a_host_missing_from_known_hosts_is_refused_when_pinning_is_on(tmp_path, sshd):
    """The reject policy only matters for hosts that are NOT in the file (a mismatching key for a
    known host is refused by paramiko regardless of policy)."""
    host, client_key, other = paramiko.ECDSAKey.generate(), paramiko.ECDSAKey.generate(), paramiko.ECDSAKey.generate()
    port, server = sshd(host, authorized_key=client_key, password="pw")
    s = settings_for(tmp_path, port, client_key=client_key, password="pw")
    hk = paramiko.HostKeys()
    hk.add(f"[127.0.0.1]:{port + 1}", other.get_name(), other)     # pins a *different* host:port only
    p = tmp_path / "known_hosts"
    hk.save(str(p))
    s.compute_known_hosts_path = str(p)
    with pytest.raises(paramiko.SSHException):
        connect(s)
    assert server.method is None                       # nothing was authenticated against the unknown host
