"""Push/pull/init orchestration for hf-pi-sync.

Ties together staging (``staging.py``) and the Buckets wrapper (``buckets.py``).
Call signatures are exposed so ``cli.py`` can wire them up.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .buckets import DEFAULT_BUCKET_NAME, Buckets
from .staging import default_excludes


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


def cmd_init(
    bucket: str | None = None,
    *,
    private: bool = True,
    with_auth: bool = False,
    dry_run: bool = False,
) -> SyncResult:
    """Login check, create/get private bucket, first push."""
    raise NotImplementedError("init is not implemented yet")


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
    "DEFAULT_BUCKET_NAME",
    "Buckets",
    "SyncResult",
    "cmd_init",
    "cmd_push",
    "cmd_pull",
    "cmd_auto",
]


def _excludes(with_auth: bool) -> list[str]:
    return list(default_excludes(with_auth=with_auth))


def _agent_dir() -> Path:
    from .staging import agent_dir

    return agent_dir()
