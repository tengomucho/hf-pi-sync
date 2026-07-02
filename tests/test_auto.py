"""Tests for auto-sync (no subcommand): marker-based direction decisions.

Direction is decided by what changed since the last reconciliation (the
``~/.pi/.pi-sync/last-sync`` marker), not by absolute mtimes. Mtimes are forced
to a clearly-future or clearly-past epoch so the comparison is deterministic
regardless of filesystem mtime resolution. Pulls run against a ``fake_agent`` so
the real ``~/.pi/agent/`` is never touched.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from hf_pi_sync import lastsync
from hf_pi_sync import sync as syncmod
from hf_pi_sync.buckets import Buckets
from hf_pi_sync.sync import BucketMissingError

FUTURE = 4_000_000_000.0  # year 2096
PAST = 1.0


def _set_mtime(path, epoch: float) -> None:
    os.utime(path, (epoch, epoch))


def _local_mtime(ag: Path) -> float:
    return max(p.stat().st_mtime for p in ag.rglob("*") if p.is_file())


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


def _seed_bucket_directly(bucket_id: str, text: str = '{"defaultModel":"x"}') -> None:
    """Seed the bucket without going through cmd_push (so no marker is written)."""
    st = Path(tempfile.mkdtemp())
    (st / "settings.json").write_text(text)
    try:
        Buckets().api.sync_bucket(source=str(st), dest=f"hf://buckets/{bucket_id}")
    finally:
        shutil.rmtree(st, ignore_errors=True)


def test_auto_missing_bucket_raises(dummy_bucket, fake_agent):
    with pytest.raises(BucketMissingError) as exc:
        syncmod.cmd_auto(bucket=dummy_bucket)
    assert exc.value.bucket_id == dummy_bucket


def test_auto_pulls_when_no_marker_and_remote_has_data(dummy_bucket, fake_agent):
    """Fresh machine with an agent dir but never reconciled: pull, don't push.

    This is the core fix: even if the local config has a recent mtime, a missing
    marker means the remote (which already has data) is the source of truth.
    """
    bk = Buckets()
    bk.create_bucket(dummy_bucket)
    _seed_bucket_directly(dummy_bucket)  # remote has data, no marker written

    r = syncmod.cmd_auto(bucket=dummy_bucket)

    assert r.action == "auto-pull"


def test_auto_pushes_when_no_marker_and_remote_empty(dummy_bucket, fake_agent):
    """First machine seeding an empty bucket: push."""
    bk = Buckets()
    bk.create_bucket(dummy_bucket)  # empty, no marker

    r = syncmod.cmd_auto(bucket=dummy_bucket)

    assert r.action == "auto-push"
    assert r.files >= 1


def test_auto_pushes_when_local_changed(dummy_bucket, fake_agent):
    bk = Buckets()
    bk.create_bucket(dummy_bucket)
    syncmod.cmd_push(bucket=dummy_bucket)  # baseline + marker
    _set_mtime(fake_agent / "settings.json", FUTURE)  # local newer than marker

    r = syncmod.cmd_auto(bucket=dummy_bucket)

    assert r.action == "auto-push"
    assert r.dry_run is False
    assert r.files >= 1  # the newer settings.json was uploaded


def test_auto_pulls_when_remote_changed(dummy_bucket, fake_agent):
    bk = Buckets()
    bk.create_bucket(dummy_bucket)
    syncmod.cmd_push(bucket=dummy_bucket)  # baseline: marker records remote = T0
    # Simulate another machine having pushed newer config: this machine's marker
    # still remembers an older remote.
    lastsync.write_last_sync(
        dummy_bucket, local_mtime=_local_mtime(fake_agent), remote_mtime=PAST
    )

    r = syncmod.cmd_auto(bucket=dummy_bucket)

    assert r.action == "auto-pull"


def test_auto_noop_when_unchanged(dummy_bucket, fake_agent):
    bk = Buckets()
    bk.create_bucket(dummy_bucket)
    syncmod.cmd_push(bucket=dummy_bucket)  # marker written

    r = syncmod.cmd_auto(bucket=dummy_bucket)

    assert r.action == "auto-push"
    assert r.files == 0  # nothing changed -> push is a no-op


def test_auto_conflict_when_both_changed(dummy_bucket, fake_agent):
    bk = Buckets()
    bk.create_bucket(dummy_bucket)
    syncmod.cmd_push(bucket=dummy_bucket)  # marker: local = L0, remote = T0
    local_before = _local_mtime(fake_agent)
    _set_mtime(fake_agent / "settings.json", FUTURE)  # local changed
    # ...and pretend the remote also advanced beyond what we recorded:
    lastsync.write_last_sync(dummy_bucket, local_mtime=local_before, remote_mtime=PAST)

    with pytest.raises(RuntimeError) as exc:
        syncmod.cmd_auto(bucket=dummy_bucket)

    assert "both local and remote changed" in str(exc.value)


def test_auto_pulls_when_local_agent_dir_missing(dummy_bucket, tmp_path, monkeypatch):
    """Fresh machine with no ~/.pi/agent/: auto-sync should pull, not error."""
    seed = tmp_path / "seed-agent"
    seed.mkdir()
    (seed / "settings.json").write_text('{"defaultModel": "zai-org/GLM-5.2"}')
    monkeypatch.setattr(syncmod, "agent_dir", lambda: seed)
    monkeypatch.setattr(syncmod, "_pi_install", lambda really_run: None)
    bk = Buckets()
    bk.create_bucket(dummy_bucket)
    syncmod.cmd_push(bucket=dummy_bucket)

    fresh = tmp_path / "fresh-agent"  # intentionally not created
    monkeypatch.setattr(syncmod, "agent_dir", lambda: fresh)

    r = syncmod.cmd_auto(bucket=dummy_bucket)

    assert r.action == "auto-pull"
    assert r.files >= 1
    assert fresh.is_dir()
    assert (fresh / "settings.json").exists()


def test_auto_dry_run_reports_direction_without_executing(dummy_bucket, fake_agent):
    bk = Buckets()
    bk.create_bucket(dummy_bucket)
    syncmod.cmd_push(bucket=dummy_bucket)  # baseline + marker
    _set_mtime(fake_agent / "settings.json", FUTURE)  # local changed -> would push
    before = bk.list_files(dummy_bucket)

    r = syncmod.cmd_auto(bucket=dummy_bucket, dry_run=True)

    assert r.dry_run is True
    assert r.action == "auto-push"
    # no change to the bucket, and no marker written (dry-run)
    after = bk.list_files(dummy_bucket)
    assert [f.path for f in before] == [f.path for f in after]
    # marker still reflects the baseline push (dry-run must not update it)
    marker = lastsync.read_last_sync(dummy_bucket)
    assert marker is not None