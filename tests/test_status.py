"""Tests for the `status` command (live Hugging Face Buckets).

`status` is read-only, so these are safe integration tests against the shared
``dummy_bucket`` fixture. Mtimes are forced to a clearly-future or clearly-past
epoch so the in-sync / diff classification is deterministic. Pulls/reads of the
local side run against a ``fake_agent`` so the real ``~/.pi/agent/`` is untouched.
"""

from __future__ import annotations

import os

from hf_pi_sync import sync as syncmod
from hf_pi_sync.buckets import Buckets
from hf_pi_sync.lastsync import write_last_sync
from hf_pi_sync.sync import StatusResult

FUTURE = 4_000_000_000.0  # year 2096
PAST = 1.0


def _set_mtime(path, epoch: float) -> None:
    os.utime(path, (epoch, epoch))


def test_status_not_initialized(dummy_bucket):
    r = syncmod.cmd_status(bucket=dummy_bucket)

    assert isinstance(r, StatusResult)
    assert r.bucket_id == dummy_bucket
    assert r.initialized is False
    assert r.diff == "n/a"
    assert "not initialized" in r.message
    assert "init" in r.hint


def test_status_in_sync_after_push(dummy_bucket, fake_agent):
    Buckets().create_bucket(dummy_bucket)
    syncmod.cmd_push(bucket=dummy_bucket)  # bucket mtime == local mtime

    r = syncmod.cmd_status(bucket=dummy_bucket)

    assert r.initialized is True
    assert r.diff == "none"
    assert "in sync" in r.message
    assert r.hint == ""


def test_status_local_newer(dummy_bucket, fake_agent):
    Buckets().create_bucket(dummy_bucket)
    syncmod.cmd_push(bucket=dummy_bucket)  # marker: local = L0, remote = T0
    _set_mtime(fake_agent / "settings.json", FUTURE)  # local advanced past marker

    r = syncmod.cmd_status(bucket=dummy_bucket)

    assert r.initialized is True
    assert r.diff == "local-newer"
    assert "local" in r.message and "newer" in r.message
    assert r.hint.startswith("run `hf pi-sync`")


def test_status_remote_newer(dummy_bucket, fake_agent):
    Buckets().create_bucket(dummy_bucket)
    syncmod.cmd_push(bucket=dummy_bucket)  # remote = T0, marker records remote = T0
    # Simulate another machine having pushed newer config: this machine's
    # marker still remembers an older remote, so the bucket looks newer.
    local_now = max(p.stat().st_mtime for p in fake_agent.rglob("*") if p.is_file())
    write_last_sync(dummy_bucket, local_mtime=local_now, remote_mtime=PAST)

    r = syncmod.cmd_status(bucket=dummy_bucket)

    assert r.initialized is True
    assert r.diff == "remote-newer"
    assert "remote" in r.message and "newer" in r.message
    assert r.hint.startswith("run `hf pi-sync`")


def test_status_no_local_config_suggests_pull(dummy_bucket, tmp_path, monkeypatch):
    # bucket has data, but the local agent dir does not exist (fresh VM)
    bk = Buckets()
    bk.create_bucket(dummy_bucket)
    import tempfile
    from pathlib import Path

    st = Path(tempfile.mkdtemp())
    (st / "settings.json").write_text("{}")
    import shutil

    try:
        bk.api.sync_bucket(source=str(st), dest=f"hf://buckets/{dummy_bucket}")
    finally:
        shutil.rmtree(st, ignore_errors=True)
    monkeypatch.setattr(syncmod, "agent_dir", lambda: tmp_path / "missing-agent")

    r = syncmod.cmd_status(bucket=dummy_bucket)

    assert r.initialized is True
    assert r.diff == "remote-newer"
    assert "no local" in r.message
    assert "pull" in r.hint
