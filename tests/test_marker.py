"""Tests for the last-sync marker written by push/pull."""

from __future__ import annotations

from hf_pi_sync import lastsync
from hf_pi_sync import sync as syncmod
from hf_pi_sync.buckets import Buckets


def test_marker_absent_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(lastsync, "_MARKER_DIR", tmp_path / "m")
    monkeypatch.setattr(lastsync, "_MARKER_FILE", tmp_path / "m" / "last-sync")
    assert lastsync.read_last_sync("user/pi-config") is None


def test_push_writes_marker(dummy_bucket, fake_agent):
    Buckets().create_bucket(dummy_bucket)

    syncmod.cmd_push(bucket=dummy_bucket)

    m = lastsync.read_last_sync(dummy_bucket)
    assert m is not None
    assert m.bucket_id == dummy_bucket
    assert m.local_mtime > 0
    assert m.remote_mtime > 0
    assert m.synced_at > 0


def test_pull_writes_marker(dummy_bucket, fake_agent):
    Buckets().create_bucket(dummy_bucket)
    syncmod.cmd_push(bucket=dummy_bucket)

    syncmod.cmd_pull(bucket=dummy_bucket)

    m = lastsync.read_last_sync(dummy_bucket)
    assert m is not None
    assert m.bucket_id == dummy_bucket


def test_init_writes_marker(dummy_bucket, fake_agent):
    syncmod.cmd_init(bucket=dummy_bucket)

    m = lastsync.read_last_sync(dummy_bucket)
    assert m is not None
    assert m.bucket_id == dummy_bucket


def test_dry_run_push_writes_no_marker(dummy_bucket, fake_agent):
    Buckets().create_bucket(dummy_bucket)

    syncmod.cmd_push(bucket=dummy_bucket, dry_run=True)

    assert lastsync.read_last_sync(dummy_bucket) is None


def test_dry_run_pull_writes_no_marker(dummy_bucket, fake_agent):
    Buckets().create_bucket(dummy_bucket)
    syncmod.cmd_push(bucket=dummy_bucket)

    syncmod.cmd_pull(bucket=dummy_bucket, dry_run=True)

    # marker from the baseline push must be untouched by the dry-run pull
    m = lastsync.read_last_sync(dummy_bucket)
    assert m is not None


def test_marker_wrong_bucket_returns_none(dummy_bucket, fake_agent):
    Buckets().create_bucket(dummy_bucket)
    syncmod.cmd_push(bucket=dummy_bucket)

    assert lastsync.read_last_sync("someone-else/pi-config") is None


def test_marker_survives_pull_after_push_is_in_sync(dummy_bucket, fake_agent):
    """After a push then a pull (no changes), a subsequent auto is a no-op push."""
    bk = Buckets()
    bk.create_bucket(dummy_bucket)
    syncmod.cmd_push(bucket=dummy_bucket)
    syncmod.cmd_pull(bucket=dummy_bucket)  # rewrites marker

    r = syncmod.cmd_auto(bucket=dummy_bucket)

    assert r.action == "auto-push"
    assert r.files == 0