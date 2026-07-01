"""Push/pull/init orchestration for hf-pi-sync.

Ties together staging (``staging.py``) and the Buckets wrapper (``buckets.py``).
Call signatures are exposed so ``cli.py`` can wire them up.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .buckets import DEFAULT_BUCKET_NAME, Buckets
from .staging import agent_dir, default_excludes


class NotLoggedInError(RuntimeError):
    """Raised when no Hugging Face login token is available."""


class AgentDirMissing(RuntimeError):
    """Raised when the pi agent config directory does not exist."""


class BucketExistsError(RuntimeError):
    """Raised when init targets an existing bucket without confirmation."""

    def __init__(self, bucket_id: str) -> None:
        self.bucket_id = bucket_id
        super().__init__(f"bucket already exists: {bucket_id}")


@dataclass
class SyncResult:
    """Outcome of a sync run, for the CLI to print."""

    action: str
    bucket_id: str
    files: int
    dry_run: bool = False
    message: str = ""

    def summary(self) -> str:
        flag = " [dry-run]" if self.dry_run else ""
        return f"{self.action}{flag} -> {self.bucket_id} ({self.files} files) {self.message}".strip()


def _excludes(with_auth: bool) -> list[str]:
    return list(default_excludes(with_auth=with_auth))


def _agent_dir() -> Path:
    return agent_dir()


def _stage(with_auth: bool) -> Path:
    """Copy the shareable subset of the agent dir into a fresh temp dir."""
    src = agent_dir()
    if not src.is_dir():
        raise AgentDirMissing(str(src))
    patterns = default_excludes(with_auth=with_auth)
    stage = Path(tempfile.mkdtemp(prefix="hf-pi-sync-"))
    shutil.copytree(
        src, stage, dirs_exist_ok=True, ignore=shutil.ignore_patterns(*patterns)
    )
    return stage


def _uploads_from_plan(plan: Any) -> int:
    try:
        return int(plan.summary().get("uploads", 0))
    except Exception:
        return 0


def _download_count_from_plan(plan: Any) -> int:
    try:
        return int(plan.summary().get("downloads", 0))
    except Exception:
        return 0


def _count_files(root: Path) -> int:
    return sum(1 for p in root.rglob("*") if p.is_file())


def cmd_init(
    bucket: str | None = None,
    *,
    private: bool = True,
    with_auth: bool = False,
    dry_run: bool = False,
    overwrite: bool = False,
) -> SyncResult:
    """Login check, create/get private bucket, first push.

    When the bucket already exists, raises ``BucketExistsError`` unless
    ``overwrite`` is set (the CLI confirms with the user before retrying).
    """
    bk = Buckets()
    namespace = _require_login(bk)
    bucket_id = bk.resolve_bucket_id(bucket, namespace)

    if dry_run:
        existed = bk.bucket_exists(bucket_id)
        stage = _stage(with_auth)
        try:
            if existed:
                uploads = _uploads_from_plan(
                    bk.sync_to_bucket(stage, bucket_id, dry_run=True)
                )
            else:
                uploads = _count_files(stage)
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        return SyncResult(
            "init",
            bucket_id,
            uploads,
            dry_run=True,
            message="would create and push"
            if not existed
            else "would push to existing",
        )

    # Stage first: validates the agent dir and applies excludes before any
    # bucket is created, so a staging failure cannot leave a stray bucket.
    stage = _stage(with_auth)
    try:
        created_uri = bk.create_bucket(bucket_id, private=private)
        existed = created_uri is None
        if existed and not overwrite:
            raise BucketExistsError(bucket_id)
        uploads = _uploads_from_plan(bk.sync_to_bucket(stage, bucket_id, delete=False))
    finally:
        shutil.rmtree(stage, ignore_errors=True)

    return SyncResult(
        "init",
        bucket_id,
        uploads,
        message="created and pushed" if not existed else "reused and pushed",
    )


def _require_login(bk: Buckets) -> str:
    try:
        return bk.whoami()
    except Exception as exc:  # noqa: BLE001
        raise NotLoggedInError(
            "Not logged in to Hugging Face. Run `hf auth login` or set HF_TOKEN."
        ) from exc


def cmd_push(
    bucket: str | None = None,
    *,
    with_auth: bool = False,
    dry_run: bool = False,
    quiet: bool = False,
) -> SyncResult:
    """Stage shareable subset and upload to the bucket."""
    raise NotImplementedError("push is not implemented yet")


def cmd_pull(
    bucket: str | None = None,
    *,
    with_auth: bool = False,
    mirror: bool = False,
    dry_run: bool = False,
) -> SyncResult:
    """Download bucket, merge into ~/.pi/agent/, run `pi install`."""
    raise NotImplementedError("pull is not implemented yet")


def cmd_auto(
    bucket: str | None = None,
    *,
    with_auth: bool = False,
    dry_run: bool = False,
) -> SyncResult:
    """Compare local vs remote mtimes and push or pull accordingly."""
    raise NotImplementedError("auto-sync is not implemented yet")


__all__ = [
    "AgentDirMissing",
    "BucketExistsError",
    "DEFAULT_BUCKET_NAME",
    "Buckets",
    "NotLoggedInError",
    "SyncResult",
    "cmd_auto",
    "cmd_init",
    "cmd_pull",
    "cmd_push",
]
