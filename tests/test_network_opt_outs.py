"""Network opt-outs: the two settings that stop claude-swap phoning home.

Settings interface assumed (as already declared in src/claude_swap/settings.py,
nothing new is invented here):
  * ``usage.headerProbe`` -> ``UsageSettings.header_probe``, read with
    ``load_usage_settings(backup_root)``; gates the 1-token Haiku
    ``/v1/messages`` request (``oauth.request_usage_via_probe``).
  * ``updates.check`` -> ``UpdateSettings.check``, read with
    ``load_update_settings(backup_root)``; gates the daily PyPI lookup in
    ``cli.main``.
A test sets one the way a user does: ``settings.set_setting(root,
"<section>.<key>", "false")`` against ``paths.get_backup_root()`` (the
conftest-isolated temporary home), never by patching the loader.

HTTP layer: every request goes through ``urllib.request.urlopen``; the spy
below replaces it and records the URL. Each opt-out test has a positive
control (setting on -> the request IS seen), so a green cannot be a spy that
sees nothing.
"""
from __future__ import annotations

import json
import sys
import urllib.error
from unittest.mock import patch

import pytest

from claude_swap import cli, oauth, paths
from claude_swap.settings import load_update_settings, load_usage_settings, set_setting

MESSAGES = "/v1/messages"
PYPI = "pypi.org"


@pytest.fixture
def requested(monkeypatch):
    """URLs passed to urlopen; every call then fails like a dead network."""
    urls: list[str] = []

    def spy(req, *args, **kwargs):
        urls.append(req if isinstance(req, str) else req.full_url)
        raise urllib.error.URLError("spy: no network")

    monkeypatch.setattr("urllib.request.urlopen", spy)
    return urls


def _setup_token_credentials() -> str:
    return json.dumps({"claudeAiOauth": {
        "accessToken": "sk-ant-oat01-fake", "scopes": list(oauth.SETUP_TOKEN_SCOPES),
    }})


def _oauth_credentials() -> str:
    return json.dumps({"claudeAiOauth": {
        "accessToken": "sk-ant-fake", "refreshToken": "r",
        "expiresAt": 4_102_444_800_000, "scopes": ["user:profile", "user:inference"],
    }})


def _fetch_usage(credentials: str):
    root = paths.get_backup_root()
    return oauth.try_fetch_usage_for_account(
        "1", "a@example.com", credentials, is_active=False,
        header_probe=load_usage_settings(root).header_probe,
    )


def _run_cli_list() -> None:
    """``cswap --list`` against a stub switcher: runs to the end of
    ``cli.main``, where the passive update notification fires (``config``
    returns before it)."""
    with patch("claude_swap.cli.ClaudeAccountSwitcher"), \
         patch("os.geteuid", return_value=1000, create=True), \
         patch.object(sys, "argv", ["claude-swap", "--list"]):
        try:
            cli.main()
        except SystemExit:
            pass


def _isolate_update_cache(monkeypatch, tmp_path):
    monkeypatch.setattr("claude_swap.update_check.CACHE_PATH", tmp_path / "c.json")


def test_usage_probe_off_sends_no_messages_request_for_setup_token(requested):
    set_setting(paths.get_backup_root(), "usage.headerProbe", "false")
    outcome = _fetch_usage(_setup_token_credentials())
    assert not [u for u in requested if MESSAGES in u]
    assert outcome.usage is None  # "no data", not a crash


def test_usage_probe_off_sends_no_messages_request_when_usage_endpoint_fails(requested):
    set_setting(paths.get_backup_root(), "usage.headerProbe", "false")
    outcome = _fetch_usage(_oauth_credentials())  # endpoint read fails (spy)
    assert any("usage" in u for u in requested)  # the endpoint was tried...
    assert not [u for u in requested if MESSAGES in u]  # ...and nothing fell back
    assert outcome.usage is None


def test_usage_probe_on_control_does_send_messages_request(requested):
    set_setting(paths.get_backup_root(), "usage.headerProbe", "true")
    _fetch_usage(_setup_token_credentials())
    assert [u for u in requested if MESSAGES in u]


def test_update_check_off_sends_no_pypi_request(requested, monkeypatch, tmp_path):
    _isolate_update_cache(monkeypatch, tmp_path)
    set_setting(paths.get_backup_root(), "updates.check", "false")
    assert load_update_settings(paths.get_backup_root()).check is False
    _run_cli_list()
    assert not [u for u in requested if PYPI in u]


def test_update_check_on_control_does_send_pypi_request(requested, monkeypatch, tmp_path):
    _isolate_update_cache(monkeypatch, tmp_path)
    set_setting(paths.get_backup_root(), "updates.check", "true")
    _run_cli_list()
    assert [u for u in requested if PYPI in u]
