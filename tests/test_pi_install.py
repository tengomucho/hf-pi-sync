"""Unit tests for the post-pull ``pi update --extensions`` rebuild helper.

Monkeypatches ``subprocess.run`` so no real ``pi`` invocation touches the host.
"""

from __future__ import annotations

import subprocess

import pytest

from hf_pi_sync import sync as syncmod


def _capture_run(captured: dict):
    def _fake(cmd, *args, **kwargs):
        captured["cmd"] = cmd
        captured["called"] = True
        return subprocess.CompletedProcess(cmd, 0)

    return _fake


def test_pi_install_runs_update_extensions(monkeypatch):
    captured: dict = {"called": False}
    monkeypatch.setattr(syncmod.subprocess, "run", _capture_run(captured))

    syncmod._pi_install(really_run=True)

    assert captured["called"] is True
    assert captured["cmd"] == ["pi", "update", "--extensions"]


def test_pi_install_skipped_when_not_running(monkeypatch):
    monkeypatch.setattr(
        syncmod.subprocess, "run", lambda *a, **k: pytest.fail("must not run")
    )

    syncmod._pi_install(really_run=False)  # no exception, no subprocess call


def test_pi_install_missing_command_raises_runtime_error(monkeypatch):
    def _raise(cmd, *args, **kwargs):
        raise FileNotFoundError("pi")

    monkeypatch.setattr(syncmod.subprocess, "run", _raise)

    with pytest.raises(RuntimeError, match="`pi` command not found"):
        syncmod._pi_install(really_run=True)


def test_pi_install_failure_raises_runtime_error(monkeypatch):
    def _raise(cmd, *args, **kwargs):
        raise subprocess.CalledProcessError(returncode=2, cmd=cmd)

    monkeypatch.setattr(syncmod.subprocess, "run", _raise)

    with pytest.raises(RuntimeError, match="`pi update --extensions` failed"):
        syncmod._pi_install(really_run=True)
