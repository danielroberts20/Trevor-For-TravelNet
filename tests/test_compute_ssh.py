# tests/test_compute_ssh.py
#
# _get_ssh_client(): key-first authentication with password fallback, and host-key pinning.

from unittest.mock import patch, MagicMock

import paramiko
import pytest

from compute import manager as ssh


@pytest.fixture
def cfg(tmp_path):
    """A settings stand-in; individual tests override fields."""
    key = tmp_path / "compute_id"
    key.write_text("not a real key")
    known = tmp_path / "known_hosts"
    known.write_text("[pc.example]:2222 ssh-ed25519 AAAA\n")
    s = MagicMock()
    s.compute_host, s.compute_port, s.compute_username = "pc.example", 2222, "compute"
    s.compute_password = "pw"
    s.compute_ssh_key_path = str(key)
    s.compute_known_hosts_path = str(known)
    s.key_file, s.known_file = key, known
    return s


def connect(cfg):
    """Run _get_ssh_client() with a mocked SSHClient; return (client_mock, connect_kwargs)."""
    with patch.object(ssh, "settings", cfg), patch.object(ssh.paramiko, "SSHClient") as cls:
        ssh._get_ssh_client()
    client = cls.return_value
    return client, client.connect.call_args


def test_key_and_password_are_both_offered_key_first(cfg):
    client, call = connect(cfg)
    assert call.args == ("pc.example",)
    assert call.kwargs["key_filename"] == str(cfg.key_file)
    assert call.kwargs["password"] == "pw"          # paramiko tries the key before the password
    assert call.kwargs["port"] == 2222 and call.kwargs["username"] == "compute"


def test_key_only_after_the_password_is_removed(cfg):
    cfg.compute_password = None
    _, call = connect(cfg)
    assert "password" not in call.kwargs and call.kwargs["key_filename"] == str(cfg.key_file)


def test_password_only_keeps_working_before_the_key_exists(cfg):
    cfg.compute_ssh_key_path = None
    _, call = connect(cfg)
    assert "key_filename" not in call.kwargs and call.kwargs["password"] == "pw"


def test_a_configured_but_missing_key_file_falls_back_to_the_password(cfg):
    cfg.compute_ssh_key_path = "/does/not/exist"
    _, call = connect(cfg)
    assert "key_filename" not in call.kwargs and call.kwargs["password"] == "pw"


def test_no_credentials_at_all_is_a_clear_error_and_never_connects(cfg):
    cfg.compute_password = None
    cfg.compute_ssh_key_path = None
    with patch.object(ssh, "settings", cfg), patch.object(ssh.paramiko, "SSHClient") as cls:
        with pytest.raises(RuntimeError, match="No SSH credentials"):
            ssh._get_ssh_client()
    cls.return_value.connect.assert_not_called()


def test_agent_and_default_key_lookup_are_off(cfg):
    _, call = connect(cfg)
    assert call.kwargs["allow_agent"] is False and call.kwargs["look_for_keys"] is False


def test_pinned_host_key_rejects_unknown_hosts(cfg):
    client, _ = connect(cfg)
    client.load_host_keys.assert_called_once_with(str(cfg.known_file))
    (policy,), _ = client.set_missing_host_key_policy.call_args
    assert isinstance(policy, paramiko.RejectPolicy)


def test_without_known_hosts_the_old_trust_on_first_use_behaviour_remains(cfg):
    cfg.compute_known_hosts_path = None
    client, _ = connect(cfg)
    client.load_host_keys.assert_not_called()
    (policy,), _ = client.set_missing_host_key_policy.call_args
    assert isinstance(policy, paramiko.AutoAddPolicy)


def test_a_known_hosts_path_that_does_not_exist_does_not_pin(cfg):
    cfg.compute_known_hosts_path = "/does/not/exist"
    client, _ = connect(cfg)
    client.load_host_keys.assert_not_called()
    (policy,), _ = client.set_missing_host_key_policy.call_args
    assert isinstance(policy, paramiko.AutoAddPolicy)
