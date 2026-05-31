"""Tests for the shared credential redaction helper (lablink.redaction)."""

from dataclasses import dataclass

from lablink.base import AuthConfig, DriverConfig
from lablink.redaction import contains_secret, redact, secret_values


@dataclass(kw_only=True)
class _AuthCfg(DriverConfig, AuthConfig):
    pass


def _auth_config(**overrides) -> _AuthCfg:
    defaults = dict(alias="a", type="ssh", timeout_ms=1000)
    defaults.update(overrides)
    return _AuthCfg(**defaults)


class TestSecretValues:
    def test_resolves_named_env_vars(self, monkeypatch):
        monkeypatch.setenv("MY_PASS", "hunter2")
        monkeypatch.setenv("MY_TOKEN", "tok-abc")
        cfg = _auth_config(auth_password_env="MY_PASS", auth_token_env="MY_TOKEN")

        assert secret_values(cfg) == {"hunter2", "tok-abc"}

    def test_unset_env_var_yields_no_secret(self, monkeypatch):
        monkeypatch.delenv("MISSING", raising=False)
        cfg = _auth_config(auth_password_env="MISSING")

        assert secret_values(cfg) == set()

    def test_blank_env_var_ignored(self, monkeypatch):
        monkeypatch.setenv("BLANK", "")
        cfg = _auth_config(auth_password_env="BLANK")

        assert secret_values(cfg) == set()

    def test_key_path_is_not_treated_as_secret(self, monkeypatch):
        # auth_ssh_key_path is a path, not an env-var reference — excluded.
        cfg = _auth_config(auth_ssh_key_path="/home/me/.ssh/id_rsa")

        assert secret_values(cfg) == set()

    def test_config_without_auth_fields(self):
        plain = DriverConfig(alias="a", type="visa", timeout_ms=1000)
        assert secret_values(plain) == set()

    def test_short_secret_excluded(self, monkeypatch):
        # A value below the min-length floor is dropped: substring-redacting a
        # tiny value would corrupt unrelated log text more than it protects.
        monkeypatch.setenv("SHORT", "ab12")
        cfg = _auth_config(auth_password_env="SHORT")
        assert secret_values(cfg) == set()

    def test_value_at_floor_kept(self, monkeypatch):
        monkeypatch.setenv("ATLEN", "abc123")  # exactly _MIN_SECRET_LEN (6)
        cfg = _auth_config(auth_password_env="ATLEN")
        assert secret_values(cfg) == {"abc123"}


class TestContainsSecret:
    def test_detects_present_secret(self):
        assert contains_secret("echo hunter2 | sudo -S id", {"hunter2"}) is True

    def test_absent_secret(self):
        assert contains_secret("uname -a", {"hunter2"}) is False

    def test_empty_inputs(self):
        assert contains_secret("text", set()) is False
        assert contains_secret(None, {"x"}) is False
        assert contains_secret("", {"x"}) is False


class TestRedact:
    def test_scrubs_single_secret(self):
        out, found = redact("echo hunter2 | sudo -S id", {"hunter2"})
        assert out == "echo *** | sudo -S id"
        assert found is True

    def test_scrubs_multiple_occurrences(self):
        out, found = redact("a hunter2 b hunter2", {"hunter2"})
        assert out == "a *** b ***"
        assert found is True

    def test_no_secret_present(self):
        out, found = redact("echo hello", {"hunter2"})
        assert out == "echo hello"
        assert found is False

    def test_overlapping_secrets_longest_first(self):
        # The shorter secret is a substring of the longer; longest-first
        # replacement must not leave a fragment behind.
        out, found = redact("token=abcdef", {"abc", "abcdef"})
        assert out == "token=***"
        assert found is True

    def test_empty_inputs_are_noops(self):
        assert redact("", {"x"}) == ("", False)
        assert redact("text", set()) == ("text", False)
        assert redact(None, {"x"}) == (None, False)
