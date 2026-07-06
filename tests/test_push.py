"""Tests for the `push` command (live Hugging Face Buckets)."""

from __future__ import annotations

import pytest

from hf_pi_sync import sync as syncmod
from hf_pi_sync.buckets import Buckets
from hf_pi_sync.sync import BucketMissingError, SyncResult


def _files(bucket_id: str) -> list[str]:
    return [f.path for f in Buckets().list_files(bucket_id)]


def test_push_uploads_settings_and_respects_excludes(dummy_bucket):
    Buckets().create_bucket(dummy_bucket)  # push requires an existing bucket

    r = syncmod.cmd_push(bucket=dummy_bucket)

    assert isinstance(r, SyncResult)
    assert r.action == "push"
    assert r.bucket_id == dummy_bucket
    assert r.files >= 1
    assert r.dry_run is False

    files = _files(dummy_bucket)
    assert "settings.json" in files
    assert not any(p.startswith(("npm/", "bin/", "sessions/")) for p in files)
    assert "auth.json" not in files


def test_push_again_is_noop(dummy_bucket):
    Buckets().create_bucket(dummy_bucket)
    syncmod.cmd_push(bucket=dummy_bucket)  # first push

    r = syncmod.cmd_push(bucket=dummy_bucket)  # nothing changed locally

    assert r.action == "push"
    assert r.files == 0
    assert "nothing to sync" in r.message


def test_push_missing_bucket_raises(dummy_bucket):
    # dummy_bucket is provisioned but never created here
    with pytest.raises(BucketMissingError) as exc:
        syncmod.cmd_push(bucket=dummy_bucket)

    assert exc.value.bucket_id == dummy_bucket


def test_push_dry_run_does_not_write(dummy_bucket):
    Buckets().create_bucket(dummy_bucket)

    r = syncmod.cmd_push(bucket=dummy_bucket, dry_run=True)

    assert r.dry_run is True
    assert r.files >= 1
    assert _files(dummy_bucket) == []  # nothing actually uploaded


def test_push_with_auth_includes_auth_json(dummy_bucket):
    Buckets().create_bucket(dummy_bucket)

    syncmod.cmd_push(bucket=dummy_bucket, with_auth=True)

    assert "auth.json" in _files(dummy_bucket)


def test_push_syncs_only_memory_md(dummy_bucket):
    # fake_agent has memory/MEMORY.md plus memory/daily/*.md and SCRATCHPAD.md
    Buckets().create_bucket(dummy_bucket)

    syncmod.cmd_push(bucket=dummy_bucket)

    files = _files(dummy_bucket)
    assert "memory/MEMORY.md" in files
    assert not any(p.startswith("memory/daily/") for p in files)
    assert "memory/SCRATCHPAD.md" not in files
    assert not any(p == "memory/daily" for p in files)


def test_push_syncs_top_level_pi_json(dummy_bucket, fake_agent):
    # fake_agent seeds ~/.pi/web-search.json (and a non-json notes.txt)
    Buckets().create_bucket(dummy_bucket)

    syncmod.cmd_push(bucket=dummy_bucket)

    files = _files(dummy_bucket)
    assert "_pi-root/web-search.json" in files
    # non-json siblings at ~/.pi/ are not synced
    assert not any(p == "notes.txt" or p.endswith("/notes.txt") for p in files)
