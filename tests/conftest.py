"""Shared pytest fixtures for hf-pi-sync integration tests."""

from __future__ import annotations

import contextlib
import json
import uuid

import pytest

from hf_pi_sync import sync as syncmod
from hf_pi_sync.buckets import Buckets


@pytest.fixture
def dummy_bucket():
    """Provision a unique private bucket; delete it after the test.

    Yields ``<whoami>/pi-sync-test-<8hex>``. Skips the test when the host is not
    logged in to Hugging Face. Cleanup runs even on failure.
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


@pytest.fixture
def fake_agent(tmp_path, monkeypatch):
    """A throwaway agent dir with settings.json + an excluded npm/ tree.

    Redirects ``agent_dir`` to a tmp path and stubs out ``pi update --extensions``
    so the real ``~/.pi/agent/`` is never touched.
    """
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
