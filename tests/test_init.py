"""Tests for the `init` command.

Core behaviour is exercised against the live Hugging Face Buckets backend using
a uniquely-named private dummy bucket that is deleted at teardown (even on
failure). The two failure modes that cannot be reproduced over the network
(not logged in, missing agent dir) are driven via ``monkeypatch`` on the live
``Buckets`` class, without any fake plan/bucket classes.

These are integration tests: they require a valid Hugging Face login on the
host (``hf auth login`` or ``HF_TOKEN``). They are skipped automatically when
no login is available, so ``pytest`` still passes in environments without
network or credentials.
"""

from __future__ import annotations

import contextlib
import uuid

import pytest

from hf_pi_sync import sync as syncmod
from hf_pi_sync.buckets import Buckets
from hf_pi_sync.sync import (
    AgentDirMissing,
    BucketExistsError,
    NotLoggedInError,
    SyncResult,
)


@pytest.fixture
def dummy_bucket():
    """Provision a unique private bucket name; delete it after the test.

    Skips the test when the host is not logged in to Hugging Face. Cleanup runs
    even if the test fails or raises.
    """
    bk = Buckets()
    try:
        namespace = bk.whoami()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"not logged in to Hugging Face: {exc}")
    bucket_id = f"{namespace}/pi-sync-test-{uuid.uuid4().hex[:8]}"
    assert not bk.bucket_exists(bucket_id), f"dummy bucket already exists: {bucket_id}"
    try:
        yield bucket_id
    finally:
        with contextlib.suppress(Exception):
            bk.api.delete_bucket(bucket_id, missing_ok=True)


def test_init_creates_bucket_and_pushes(dummy_bucket):
    r = syncmod.cmd_init(bucket=dummy_bucket)

    assert isinstance(r, SyncResult)
    assert r.action == "init"
    assert r.bucket_id == dummy_bucket
    assert r.files >= 1
    assert "created and pushed" in r.message

    files = [f.path for f in Buckets().list_files(dummy_bucket)]
    assert "settings.json" in files
    assert not any(p.startswith(("npm/", "bin/", "sessions/")) for p in files)
    assert "auth.json" not in files


def test_init_existing_bucket_raises_without_overwrite(dummy_bucket):
    Buckets().create_bucket(dummy_bucket)  # pre-create so init sees it existing

    with pytest.raises(BucketExistsError) as exc:
        syncmod.cmd_init(bucket=dummy_bucket)

    assert exc.value.bucket_id == dummy_bucket


def test_init_existing_bucket_overwrite_pushes(dummy_bucket):
    Buckets().create_bucket(dummy_bucket)  # pre-create

    r = syncmod.cmd_init(bucket=dummy_bucket, overwrite=True)

    assert r.action == "init"
    assert r.bucket_id == dummy_bucket
    assert "reused and pushed" in r.message
    assert "settings.json" in [f.path for f in Buckets().list_files(dummy_bucket)]


def test_init_dry_run_does_not_create_bucket(dummy_bucket):
    r = syncmod.cmd_init(bucket=dummy_bucket, dry_run=True)

    assert r.dry_run is True
    assert r.files >= 1
    assert "would create and push" in r.message
    assert not Buckets().bucket_exists(dummy_bucket)


def test_init_not_logged_in(monkeypatch):
    def _raise_no_token(self: Buckets) -> str:
        raise RuntimeError("no token")

    monkeypatch.setattr(Buckets, "whoami", _raise_no_token)

    with pytest.raises(NotLoggedInError):
        syncmod.cmd_init()


def test_init_missing_agent_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(syncmod, "agent_dir", lambda: tmp_path / "does-not-exist")

    # Dry-run reaches `_stage` (which raises) before any bucket is created.
    with pytest.raises(AgentDirMissing):
        syncmod.cmd_init(dry_run=True)
