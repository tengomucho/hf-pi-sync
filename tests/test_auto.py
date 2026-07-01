"""Tests for auto-sync (no subcommand): compare mtimes, push or pull.

Mtimes are forced to a clearly-future or clearly-past epoch so the comparison is
deterministic regardless of filesystem mtime resolution. Pulls run against a
``fake_agent`` so the real ``~/.pi/agent/`` is never touched.
"""

from __future__ import annotations

import os

import pytest

from hf_pi_sync import sync as syncmod
from hf_pi_sync.buckets import Buckets
from hf_pi_sync.sync import BucketMissingError


def _set_mtime(path, epoch: float) -> None:
    os.utime(path, (epoch, epoch))


FUTURE = 4_000_000_000.0  # year 2096
PAST = 1.0


@pytest.fixture
def fake_agent(tmp_path, monkeypatch):
    ag = tmp_path / "agent"
    ag.mkdir()
    (ag / "settings.json").write_text('{"defaultModel": "zai-org/GLM-5.2"}')
    (ag / "npm").mkdir()
    (ag / "npm" / "package.json").write_text("{}")
    monkeypatch.setattr(syncmod, "agent_dir", lambda: ag)
    monkeypatch.setattr(syncmod, "_pi_install", lambda really_run: None)
    return ag


def test_auto_missing_bucket_raises(dummy_bucket, fake_agent):
    with pytest.raises(BucketMissingError) as exc:
        syncmod.cmd_auto(bucket=dummy_bucket)
    assert exc.value.bucket_id == dummy_bucket


def test_auto_pushes_when_local_newer(dummy_bucket, fake_agent):
    bk = Buckets()
    bk.create_bucket(dummy_bucket)
    syncmod.cmd_push(bucket=dummy_bucket)  # baseline push
    # make local settings.json clearly newer than the bucket copy
    _set_mtime(fake_agent / "settings.json", FUTURE)

    r = syncmod.cmd_auto(bucket=dummy_bucket)

    assert r.action == "auto-push"
    assert r.dry_run is False
    assert r.files >= 1  # the newer settings.json was uploaded


def test_auto_pulls_when_remote_newer(dummy_bucket, fake_agent):
    bk = Buckets()
    bk.create_bucket(dummy_bucket)
    syncmod.cmd_push(bucket=dummy_bucket)  # baseline push (bucket mtime = local)
    # make local settings.json clearly older than the bucket copy
    _set_mtime(fake_agent / "settings.json", PAST)

    r = syncmod.cmd_auto(bucket=dummy_bucket)

    assert r.action == "auto-pull"
    assert r.dry_run is False
    assert r.files >= 1  # settings.json was re-downloaded from the bucket


def test_auto_dry_run_reports_direction_without_executing(dummy_bucket, fake_agent):
    bk = Buckets()
    bk.create_bucket(dummy_bucket)
    syncmod.cmd_push(bucket=dummy_bucket)
    _set_mtime(fake_agent / "settings.json", FUTURE)  # local newer -> would push
    before = bk.list_files(dummy_bucket)

    r = syncmod.cmd_auto(bucket=dummy_bucket, dry_run=True)

    assert r.dry_run is True
    assert r.action == "auto-push"
    # no change to the bucket (nothing executed)
    after = bk.list_files(dummy_bucket)
    assert [f.path for f in before] == [f.path for f in after]
