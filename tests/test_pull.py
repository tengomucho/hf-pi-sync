"""Tests for the `pull` command (live Hugging Face Buckets).

Pull merges downloaded files into the live agent dir. To avoid mutating the
real ``~/.pi/agent/``, every test redirects ``agent_dir`` to a tmp path and
stubs out ``pi update --extensions``. The ``dummy_bucket`` fixture (conftest)
provisions and deletes a unique private bucket, and a ``fake_agent`` fixture
seeds a local agent dir with the shareable subset to push from.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from hf_pi_sync import sync as syncmod
from hf_pi_sync.buckets import Buckets
from hf_pi_sync.sync import BucketMissingError, SyncResult


@pytest.fixture
def fake_agent(tmp_path, monkeypatch):
    """A throwaway agent dir with settings.json + an excluded npm/ tree."""
    ag = tmp_path / "agent"
    ag.mkdir()
    (ag / "settings.json").write_text(
        json.dumps(
            {
                "defaultProvider": "huggingface",
                "defaultModel": "zai-org/GLM-5.2",
                "packages": ["npm:pi-web-access"],
            }
        )
    )
    (ag / "npm").mkdir()
    (ag / "npm" / "package.json").write_text("{}")
    (ag / "sessions").mkdir()
    (ag / "sessions" / "local.jsonl").write_text("[]")
    (ag / "auth.json").write_text('{"token":"secret"}')
    monkeypatch.setattr(syncmod, "agent_dir", lambda: ag)
    monkeypatch.setattr(syncmod, "_pi_install", lambda really_run: None)
    return ag


def _files(ag) -> set[str]:
    return {str(p.relative_to(ag)) for p in ag.rglob("*") if p.is_file()}


def test_pull_downloads_settings_into_agent(dummy_bucket, fake_agent):
    Buckets().create_bucket(dummy_bucket)
    # seed the bucket from a different stage that has a different settings.json
    syncmod.cmd_push(bucket=dummy_bucket)  # uploads the fake_agent settings.json
    # corrupt local settings + add a stray shareable file, then pull
    (fake_agent / "settings.json").write_text("{}")
    # upload an extra file to the bucket so pull has something new to bring down
    st = Path(tempfile.mkdtemp())
    (st / "AGENTS.md").write_text("# agents")
    try:
        Buckets().api.sync_bucket(source=str(st), dest=f"hf://buckets/{dummy_bucket}")
    finally:
        shutil.rmtree(st, ignore_errors=True)

    r = syncmod.cmd_pull(bucket=dummy_bucket)

    assert isinstance(r, SyncResult)
    assert r.action == "pull"
    assert r.bucket_id == dummy_bucket
    assert r.files >= 1
    assert r.dry_run is False
    fs = _files(fake_agent)
    assert "AGENTS.md" in fs  # newly pulled
    assert "auth.json" in fs  # local auth.json preserved (not overwritten)
    assert "npm/package.json" in fs  # local npm tree preserved
    assert "sessions/local.jsonl" in fs  # local sessions preserved
    # settings.json was overwritten by the pulled version (matches what push sent)
    assert json.loads((fake_agent / "settings.json").read_text())["defaultModel"] == (
        "zai-org/GLM-5.2"
    )


def test_pull_missing_bucket_raises(dummy_bucket, fake_agent):
    with pytest.raises(BucketMissingError) as exc:
        syncmod.cmd_pull(bucket=dummy_bucket)
    assert exc.value.bucket_id == dummy_bucket


def _seed_bucket_with_settings(dummy_bucket, text):
    Buckets().create_bucket(dummy_bucket)
    st = Path(tempfile.mkdtemp())
    (st / "settings.json").write_text(text)
    try:
        Buckets().api.sync_bucket(source=str(st), dest=f"hf://buckets/{dummy_bucket}")
    finally:
        shutil.rmtree(st, ignore_errors=True)


def test_pull_into_fresh_agent_dir_creates_it(dummy_bucket, tmp_path, monkeypatch):
    # Simulate a brand-new VM: ~/.pi/agent does not exist yet.
    fresh = tmp_path / "fresh-home"
    fresh.mkdir()
    monkeypatch.setattr(syncmod, "agent_dir", lambda: fresh / ".pi" / "agent")
    monkeypatch.setattr(syncmod, "_pi_install", lambda really_run: None)
    _seed_bucket_with_settings(dummy_bucket, '{"defaultModel": "zai-org/GLM-5.2"}')

    r = syncmod.cmd_pull(bucket=dummy_bucket)

    assert r.files >= 1
    dst = fresh / ".pi" / "agent"
    assert dst.is_dir()  # created on pull
    assert (dst / "settings.json").is_file()


def test_pull_dry_run_writes_nothing(dummy_bucket, fake_agent):
    Buckets().create_bucket(dummy_bucket)
    syncmod.cmd_push(bucket=dummy_bucket)
    # make local state differ from bucket so dry-run has something to "do"
    (fake_agent / "settings.json").write_text("{changed}")
    before = _files(fake_agent)

    r = syncmod.cmd_pull(bucket=dummy_bucket, dry_run=True)

    assert r.dry_run is True
    assert _files(fake_agent) == before  # nothing changed locally


def test_pull_preserves_excluded_dirs_no_mirror(dummy_bucket, fake_agent):
    Buckets().create_bucket(dummy_bucket)
    syncmod.cmd_push(bucket=dummy_bucket)
    # local-only file in a shareable spot that the bucket does NOT have
    (fake_agent / "stray.txt").write_text("local only")

    r = syncmod.cmd_pull(bucket=dummy_bucket)  # additive default

    # additive: local-only shareable file is not deleted
    assert "stray.txt" in _files(fake_agent)
    # excluded paths are always preserved
    assert "npm/package.json" in _files(fake_agent)
    assert "sessions/local.jsonl" in _files(fake_agent)
    assert "auth.json" in _files(fake_agent)
    assert "deleted" not in r.message


def test_pull_mirror_deletes_local_only_shareable(dummy_bucket, fake_agent):
    Buckets().create_bucket(dummy_bucket)
    syncmod.cmd_push(bucket=dummy_bucket)
    (fake_agent / "stray.txt").write_text("local only")
    assert "stray.txt" in _files(fake_agent)

    r = syncmod.cmd_pull(bucket=dummy_bucket, mirror=True)

    fs = _files(fake_agent)
    assert "stray.txt" not in fs  # mirror removed the local-only shareable file
    # excluded paths survive mirror
    assert "npm/package.json" in fs
    assert "sessions/local.jsonl" in fs
    assert "auth.json" in fs
    assert "deleted" in r.message


def test_pull_with_auth_restores_auth_json(dummy_bucket, fake_agent, monkeypatch):
    Buckets().create_bucket(dummy_bucket)
    # push auth.json into the bucket, then wipe locally, then pull it back
    syncmod.cmd_push(bucket=dummy_bucket, with_auth=True)
    (fake_agent / "auth.json").unlink()

    syncmod.cmd_pull(bucket=dummy_bucket, with_auth=True)

    assert "auth.json" in _files(fake_agent)
    assert (fake_agent / "auth.json").read_text() == '{"token":"secret"}'
