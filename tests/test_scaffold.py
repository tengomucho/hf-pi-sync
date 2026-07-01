"""Smoke tests for the scaffold: imports, version, excludes, bucket URI, CLI help."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from hf_pi_sync import __version__
from hf_pi_sync.buckets import DEFAULT_BUCKET_NAME, Buckets
from hf_pi_sync.cli import app
from hf_pi_sync.staging import (
    EXCLUDED_DIRS,
    EXCLUDED_FILES_DEFAULT,
    agent_dir,
    default_excludes,
)
from hf_pi_sync.sync import SyncResult

runner = CliRunner()


def test_excludes_default_includes_auth() -> None:
    excl = default_excludes()
    assert "auth.json" in excl
    assert all(d in excl for d in EXCLUDED_DIRS)


def test_excludes_with_auth_drops_authfile() -> None:
    excl = default_excludes(with_auth=True)
    assert "auth.json" not in excl
    assert all(d in excl for d in EXCLUDED_DIRS)
    assert excl == EXCLUDED_DIRS


def test_excluded_dirs_constants() -> None:
    assert set(EXCLUDED_DIRS) == {"npm", "bin", "sessions"}
    assert EXCLUDED_FILES_DEFAULT == ("auth.json",)


def test_agent_dir_under_home() -> None:
    assert agent_dir() == Path.home() / ".pi" / "agent"


def test_default_bucket_name() -> None:
    assert DEFAULT_BUCKET_NAME == "pi-config"


def test_sync_result_summary() -> None:
    r = SyncResult(action="push", bucket_id="alice/pi-config", files=3)
    s = r.summary()
    assert "push" in s and "alice/pi-config" in s and "3 files" in s


def test_sync_result_dry_run_summary() -> None:
    r = SyncResult(action="pull", bucket_id="u/b", files=1, dry_run=True)
    assert "[dry-run]" in r.summary()


def test_cli_version_flag() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_buckets_uri_helper(monkeypatch) -> None:
    # _uri is module-private but stable; sanity check the shape.
    from hf_pi_sync import buckets as bmod

    assert bmod._uri("alice/pi-config") == "hf://buckets/alice/pi-config"
    assert bmod._uri("alice/pi-config", "sub/x") == (
        "hf://buckets/alice/pi-config/sub/x"
    )


def test_buckets_resolve_explicit_id(monkeypatch) -> None:
    bk = Buckets.__new__(Buckets)  # avoid hitting network init
    bk.api = None
    # already-qualified: used as-is, no whoami call
    assert (
        bk.resolve_bucket_id("someoneelse/their-bucket") == "someoneelse/their-bucket"
    )
    # bare name with explicit namespace
    assert bk.resolve_bucket_id("mybuck", namespace="alice") == "alice/mybuck"


def test_buckets_resolve_default_name(monkeypatch) -> None:
    bk = Buckets.__new__(Buckets)
    bk.api = None
    monkeypatch.setattr(Buckets, "whoami", lambda self: "bob", raising=False)
    assert bk.resolve_bucket_id(None, namespace="bob") == "bob/pi-config"
    assert bk.resolve_bucket_id(None) == "bob/pi-config"
