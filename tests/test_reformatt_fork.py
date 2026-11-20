"""Fork-only sentinels (reForMatt/claude-swap). Keep out of any upstream PR.

Settings interface assumed: the same already-declared settings as
tests/test_network_opt_outs.py (``usage.headerProbe`` ->
``load_usage_settings().header_probe``; ``updates.check`` ->
``load_update_settings().check``), here read with NO settings.json present, so
the fork's declared defaults decide. Upstream defaults are header_probe=False,
check=True; the fork needs both False (change ``UpdateSettings.check`` to
False in settings.py; ``header_probe`` is already False).

Upgrade interface assumed: ``update_check.run_self_upgrade()`` keeps upstream's
``_detect_install_method()`` dispatch and ``subprocess.run(cmd)``. The tag is
resolved by the builder's choice of source; these tests supply it through a
``git ls-remote --tags`` style stdout on any non-installer ``subprocess.run``
(one ``<sha>\\trefs/tags/<tag>`` line per tag). Installer = argv[0] in
{uv, pipx}.
"""
from __future__ import annotations

import re
import urllib.error
from unittest.mock import MagicMock

import pytest

from claude_swap import paths, update_check
from claude_swap.settings import load_update_settings, load_usage_settings

FORK_URL = "git+https://github.com/reForMatt/claude-swap"
TAGS = (
    "aaaa\trefs/tags/v1.1.0\n"
    "bbbb\trefs/tags/v1.1.0-reformatt.1\n"
    "cccc\trefs/tags/v1.2.0-reformatt.1\n"
)


def test_fork_defaults_make_no_probe_and_no_update_request(monkeypatch):
    root = paths.get_backup_root()
    assert not (root / "settings.json").exists()
    assert load_usage_settings(root).header_probe is False
    assert load_update_settings(root).check is False


def test_fork_default_cli_run_makes_no_network_request(monkeypatch, tmp_path):
    import sys
    from unittest.mock import patch

    from claude_swap import cli

    urls = []

    def spy(req, *a, **k):
        urls.append(req if isinstance(req, str) else req.full_url)
        raise urllib.error.URLError("spy")

    monkeypatch.setattr("urllib.request.urlopen", spy)
    monkeypatch.setattr("claude_swap.update_check.CACHE_PATH", tmp_path / "c.json")
    # `--list` runs to the end of main, where the update hint fires; `config`
    # returns before it. Positive control for this spy: test_network_opt_outs.
    with patch("claude_swap.cli.ClaudeAccountSwitcher"), \
         patch("os.geteuid", return_value=1000, create=True), \
         patch.object(sys, "argv", ["claude-swap", "--list"]):
        try:
            cli.main()
        except SystemExit:
            pass
    assert urls == []


@pytest.mark.parametrize("method", ["uv", "pipx"])
def test_upgrade_installs_from_reformatt_git_tag_never_pypi(monkeypatch, method):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr(update_check, "_detect_install_method", lambda: method)
    monkeypatch.setattr(update_check, "_installed_version_on_disk", lambda: None)
    monkeypatch.setattr(update_check, "install_identity", lambda: {})
    monkeypatch.setattr(update_check, "held_back_release_notice", lambda v: None)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("spy")),
    )
    ran: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        ran.append(list(cmd))
        return MagicMock(returncode=0, stdout=TAGS if cmd[0] not in ("uv", "pipx") else "")

    monkeypatch.setattr(update_check.subprocess, "run", fake_run)

    update_check.run_self_upgrade("1.1.0")

    installs = [c for c in ran if c[0] in ("uv", "pipx")]
    assert len(installs) == 1, ran
    cmd = installs[0]
    assert cmd[0] == method
    spec = next((a for a in cmd if "reForMatt/claude-swap" in a), None)
    assert spec is not None, cmd
    assert spec.startswith(FORK_URL)
    # pinned to a fork tag, newest fork tag, not a bare branch or an upstream tag
    assert re.search(r"@v1\.2\.0-reformatt\.1$", spec), spec
    # no PyPI spec: no bare package name argument anywhere in the command
    assert "claude-swap" not in cmd, cmd
    assert not any(re.fullmatch(r"claude-swap([<>=!~].*)?", a) for a in cmd), cmd


def _upgrade_with_ref(monkeypatch, ref, capsys=None):
    """Run run_self_upgrade(ref=...) under uv on linux; returns (rc, calls)."""
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr(update_check, "_detect_install_method", lambda: "uv")
    monkeypatch.setattr(update_check, "_installed_version_on_disk", lambda: None)
    monkeypatch.setattr(update_check, "install_identity", lambda: {})
    monkeypatch.setattr(update_check, "held_back_release_notice", lambda v: None)
    ran: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        ran.append(list(cmd))
        return MagicMock(returncode=0, stdout=TAGS)

    monkeypatch.setattr(update_check.subprocess, "run", fake_run)
    return update_check.run_self_upgrade("1.1.0", ref=ref), ran


@pytest.mark.parametrize(
    "ref",
    ["v1.1.0", "main", "v1.1.0-reformatt.1; rm -rf /", "v1.1.0-reformatt.x"],
)
def test_explicit_non_fork_ref_is_refused_and_runs_no_installer(monkeypatch, ref):
    rc, ran = _upgrade_with_ref(monkeypatch, ref)
    assert rc == 1
    assert [c for c in ran if c[0] in ("uv", "pipx")] == [], ran


def test_explicit_fork_ref_installs_it_without_ls_remote(monkeypatch):
    rc, ran = _upgrade_with_ref(monkeypatch, "v1.2.0-reformatt.3")
    assert rc == 0
    assert not [c for c in ran if c[0] == "git"], ran
    installs = [c for c in ran if c[0] in ("uv", "pipx")]
    assert len(installs) == 1, ran
    assert installs[0][-1] == FORK_URL + "@v1.2.0-reformatt.3"
