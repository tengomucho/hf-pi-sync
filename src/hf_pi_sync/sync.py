"""Push/pull/init orchestration for hf-pi-sync.

Ties together staging (``staging.py``) and the Buckets wrapper (``buckets.py``).
Call signatures are exposed so ``cli.py`` can wire them up.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .buckets import DEFAULT_BUCKET_NAME, Buckets
from .lastsync import read_last_sync, write_last_sync
from .staging import EXCLUDED_DIRS, EXCLUDED_FILES_DEFAULT, agent_dir, default_excludes


class NotLoggedInError(RuntimeError):
    """Raised when no Hugging Face login token is available."""


class AgentDirMissing(RuntimeError):
    """Raised when the pi agent config directory does not exist."""


class BucketExistsError(RuntimeError):
    """Raised when init targets an existing bucket without confirmation."""

    def __init__(self, bucket_id: str) -> None:
        self.bucket_id = bucket_id
        super().__init__(f"bucket already exists: {bucket_id}")


class BucketMissingError(RuntimeError):
    """Raised when push/pull target a bucket that does not exist."""

    def __init__(self, bucket_id: str) -> None:
        self.bucket_id = bucket_id
        super().__init__(
            f"bucket does not exist: {bucket_id}. Run `hf pi-sync init` first."
        )


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

    def with_action(self, action: str) -> SyncResult:
        """Return a copy with a relabeled action (used by auto-sync)."""
        return SyncResult(
            action=action,
            bucket_id=self.bucket_id,
            files=self.files,
            dry_run=self.dry_run,
            message=self.message,
        )


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


def _downloads_from_plan(plan: Any) -> int:
    try:
        return int(plan.summary().get("downloads", 0))
    except Exception:
        return 0


def _download_count_from_plan(plan: Any) -> int:
    try:
        return int(plan.summary().get("downloads", 0))
    except Exception:
        return 0


def _count_files(root: Path) -> int:
    return sum(1 for p in root.rglob("*") if p.is_file())


def _download_to_stage(
    bucket_id: str, with_auth: bool, dry_run: bool
) -> tuple[Path, int]:
    """Download the bucket into a fresh temp staging dir.

    Returns (stage_dir, downloads). Excluded paths are filtered out of the
    download via the bucket-side exclude list so nothing unwanted lands in the
    stage at all.
    """
    stage = Path(tempfile.mkdtemp(prefix="hf-pi-sync-pull-"))
    bk = Buckets()
    plan = bk.sync_from_bucket(
        bucket_id,
        stage,
        exclude=list(default_excludes(with_auth=with_auth)),
        dry_run=dry_run,
    )
    return stage, _downloads_from_plan(plan)


def _merge_stage_into_agent(
    stage: Path, with_auth: bool, mirror: bool
) -> tuple[int, int]:
    """Copy the staged bucket contents into the live agent dir.

    Returns (files_copied, files_deleted). Excluded paths are never copied over
    and never deleted even in mirror mode, so ``npm/``, ``bin/``, ``sessions/``
    and (by default) ``auth.json`` are preserved untouched on the local side.
    """
    dst = agent_dir()
    dst.mkdir(parents=True, exist_ok=True)
    excluded_dirs = set(EXCLUDED_DIRS)
    excluded_files = set(EXCLUDED_FILES_DEFAULT if not with_auth else ())

    copied = 0
    deleted = 0

    for root, dirnames, filenames in shutil.os.walk(stage):
        # prune excluded dirs so we don't recurse into them
        dirnames[:] = [d for d in dirnames if d not in excluded_dirs]
        rel_root = Path(root).relative_to(stage)
        for name in dirnames:
            (dst / rel_root / name).mkdir(parents=True, exist_ok=True)
        for name in filenames:
            if name in excluded_files:
                continue
            src_file = Path(root) / name
            dst_file = dst / rel_root / name
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dst_file)
            copied += 1

    if mirror:
        bucket_paths = {p.relative_to(stage) for p in stage.rglob("*") if p.is_file()}
        bucket_paths = {
            p
            for p in bucket_paths
            if p.parts[0] not in excluded_dirs
            and (len(p.parts) > 1 or p.name not in excluded_files)
        }
        for root, _dirnames, filenames in shutil.os.walk(dst):
            rel_root = Path(root).relative_to(dst)
            if rel_root.parts and rel_root.parts[0] in excluded_dirs:
                continue
            for name in filenames:
                if name in excluded_files:
                    continue
                if (rel_root / name) not in bucket_paths:
                    Path(root, name).unlink()
                    deleted += 1

    return copied, deleted


def _apply_bucket_mtimes(bk: Buckets, bucket_id: str, with_auth: bool) -> None:
    """Restore bucket mtimes on merged local files.

    ``sync_from_bucket`` stamps downloaded files with download-time mtimes, not
    the bucket's stored mtimes. Since the bucket sync layer diffs by mtime, that
    would make the next push re-upload unchanged files. Re-applying the bucket's
    own mtimes keeps local and remote aligned so a post-pull push is a true
    no-op. Excluded paths are skipped (they are not in the bucket anyway).
    """
    dst = agent_dir()
    excluded_dirs = set(EXCLUDED_DIRS)
    excluded_files = set(EXCLUDED_FILES_DEFAULT if not with_auth else ())
    for rf in bk.list_files(bucket_id):
        rel = Path(rf.path)
        if rel.parts and rel.parts[0] in excluded_dirs:
            continue
        if rel.name in excluded_files:
            continue
        local = dst / rel
        if local.is_file() and rf.mtime > 0:
            os.utime(local, (rf.mtime, rf.mtime))


def _pi_install(really_run: bool) -> None:
    """Rebuild ~/.pi/agent/npm/ from settings.json packages[]."""
    if not really_run:
        return
    try:
        subprocess.run(["pi", "update", "--extensions"], check=True)
    except FileNotFoundError as exc:
        raise RuntimeError(
            "`pi` command not found on PATH. "
            "Run `npm i -g @earendil-works/pi-coding-agent` first."
        ) from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"`pi update --extensions` failed (exit code {exc.returncode}). "
            "Run it manually to see the full output."
        ) from exc


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
        local_now = _max_mtime(stage)
        created_uri = bk.create_bucket(bucket_id, private=private)
        existed = created_uri is None
        if existed and not overwrite:
            raise BucketExistsError(bucket_id)
        uploads = _uploads_from_plan(bk.sync_to_bucket(stage, bucket_id, delete=False))
    finally:
        shutil.rmtree(stage, ignore_errors=True)

    write_last_sync(bucket_id, local_now, _remote_latest_mtime(bk, bucket_id))

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
) -> SyncResult:
    """Stage the shareable subset and upload to the bucket.

    Push requires the bucket to already exist (run ``init`` to create it).
    The result's ``files`` field is the number of files actually uploaded; a
    no-op run (remote already up to date) reports zero.
    """
    bk = Buckets()
    namespace = _require_login(bk)
    bucket_id = bk.resolve_bucket_id(bucket, namespace)
    if not bk.bucket_exists(bucket_id):
        raise BucketMissingError(bucket_id)

    stage = _stage(with_auth)
    try:
        local_now = _max_mtime(stage)
        uploads = _uploads_from_plan(
            bk.sync_to_bucket(stage, bucket_id, dry_run=dry_run)
        )
    finally:
        shutil.rmtree(stage, ignore_errors=True)

    if not dry_run:
        # Push does not touch local files, so local mtime is unchanged; the
        # remote is now whatever the upload produced.
        write_last_sync(bucket_id, local_now, _remote_latest_mtime(bk, bucket_id))

    return SyncResult(
        "push",
        bucket_id,
        uploads,
        dry_run=dry_run,
        message="pushed" if uploads else "nothing to sync",
    )


def cmd_pull(
    bucket: str | None = None,
    *,
    with_auth: bool = False,
    mirror: bool = False,
    dry_run: bool = False,
) -> SyncResult:
    """Download bucket, merge into ~/.pi/agent/, run ``pi update --extensions``.

    Pull requires the bucket to already exist (run ``init`` to create it). The
    merge is additive by default (never deletes local files); ``mirror`` removes
    local shareable files not present in the bucket. Excluded paths
    (``npm/``, ``bin/``, ``sessions/``, and by default ``auth.json``) are never
    overwritten or deleted. After merge, ``pi update --extensions`` rebuilds the
    local ``npm/`` tree from ``settings.json`` (skipped on dry-run).

    The result ``files`` field is the number of files downloaded; ``message``
    carries the local merge outcome and install status.
    """
    bk = Buckets()
    namespace = _require_login(bk)
    bucket_id = bk.resolve_bucket_id(bucket, namespace)
    if not bk.bucket_exists(bucket_id):
        raise BucketMissingError(bucket_id)

    # Remote is not modified by a pull, so capture its mtime once up front.
    remote_now = _remote_latest_mtime(bk, bucket_id)

    stage, downloads = _download_to_stage(bucket_id, with_auth, dry_run)
    copied, deleted = 0, 0
    installed = False
    try:
        if not dry_run:
            copied, deleted = _merge_stage_into_agent(stage, with_auth, mirror)
            _apply_bucket_mtimes(bk, bucket_id, with_auth)
            _pi_install(really_run=True)
            installed = True
    finally:
        shutil.rmtree(stage, ignore_errors=True)

    if not dry_run:
        # Local files were just written; recompute their mtime from the merged
        # agent dir. Remote is unchanged (remote_now).
        local_now = _local_latest_mtime(with_auth)
        write_last_sync(bucket_id, local_now, remote_now)

    if dry_run:
        message = "would pull" if downloads else "nothing to pull"
    else:
        parts = [f"copied {copied}"]
        if mirror and deleted:
            parts.append(f"deleted {deleted}")
        parts.append("installed" if installed else "install skipped")
        message = ", ".join(parts)

    return SyncResult(
        "pull",
        bucket_id,
        downloads,
        dry_run=dry_run,
        message=message,
    )


def _max_mtime(root: Path) -> float:
    """Latest mtime among files under ``root`` (0 if empty)."""
    mtimes = [p.stat().st_mtime for p in root.rglob("*") if p.is_file()]
    return max(mtimes) if mtimes else 0.0


def _local_latest_mtime(with_auth: bool) -> float:
    """Latest mtime across the shareable subset of the agent dir.

    Staging uses ``shutil.copytree`` (``copy2``), so staged copies preserve the
    source mtimes and the comparison is faithful.
    """
    stage = _stage(with_auth)
    try:
        return _max_mtime(stage)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _remote_latest_mtime(bk: Buckets, bucket_id: str) -> float:
    """Latest mtime across all files in the bucket (0 if empty)."""
    mtimes = [f.mtime for f in bk.list_files(bucket_id)]
    return max(mtimes) if mtimes else 0.0


# Bucket mtimes are stored at millisecond precision and some filesystems have
# coarse (>=1s) mtime resolution, so exact equality is unreliable for deciding
# "no diff". A small tolerance keeps the in-sync check robust.
_MTIME_TOLERANCE = 2.0


@dataclass
class StatusResult:
    """Outcome of a status check, for the CLI to print."""

    bucket_id: str
    initialized: bool
    diff: str  # "local-newer" | "remote-newer" | "none" | "n/a" | "diverged"
    message: str
    hint: str = ""


def cmd_auto(
    bucket: str | None = None,
    *,
    with_auth: bool = False,
    dry_run: bool = False,
) -> SyncResult:
    """Compare local vs remote against the last-sync marker and sync accordingly.

    Direction is decided by what *changed since the last reconciliation*, not by
    whose absolute mtime is larger. A machine that has never reconciled with
    this bucket (no marker) treats a non-empty remote as the source of truth
    and pulls — so a freshly-seeded local config never clobbers a populated
    remote. When both sides changed since the last sync, it refuses to pick a
    side and raises (run ``push`` or ``pull`` explicitly).
    """
    bk = Buckets()
    namespace = _require_login(bk)
    bucket_id = bk.resolve_bucket_id(bucket, namespace)
    if not bk.bucket_exists(bucket_id):
        raise BucketMissingError(bucket_id)

    try:
        local_latest = _local_latest_mtime(with_auth)
    except AgentDirMissing:
        # Fresh machine: no ~/.pi/agent/ yet. The bucket is the only source
        # of truth, so pull (which creates the dir) instead of erroring out.
        return cmd_pull(
            bucket_id, with_auth=with_auth, dry_run=dry_run
        ).with_action("auto-pull")

    remote_latest = _remote_latest_mtime(bk, bucket_id)
    marker = read_last_sync(bucket_id)

    if marker is None:
        # Never reconciled with this bucket. If the remote already has data it
        # is the source of truth; otherwise this machine seeds the bucket.
        if remote_latest > 0.0:
            return cmd_pull(
                bucket_id, with_auth=with_auth, dry_run=dry_run
            ).with_action("auto-pull")
        return cmd_push(
            bucket_id, with_auth=with_auth, dry_run=dry_run
        ).with_action("auto-push")

    local_changed = local_latest - marker.local_mtime > _MTIME_TOLERANCE
    remote_changed = remote_latest - marker.remote_mtime > _MTIME_TOLERANCE

    if local_changed and remote_changed:
        raise RuntimeError(
            "both local and remote changed since last sync; "
            "run `hf pi-sync push` or `hf pi-sync pull` to pick a direction"
        )
    if remote_changed:
        return cmd_pull(
            bucket_id, with_auth=with_auth, dry_run=dry_run
        ).with_action("auto-pull")
    # local changed only, or nothing changed -> push (a no-op when unchanged).
    return cmd_push(
        bucket_id, with_auth=with_auth, dry_run=dry_run
    ).with_action("auto-push")


def cmd_status(
    bucket: str | None = None,
    *,
    with_auth: bool = False,
) -> StatusResult:
    """Report whether the bucket is initialized and which side is newer.

    Read-only. When both sides are initialized, compares the latest mtime of
    the shareable subset locally vs in the bucket. If they differ, suggests
    running ``hf pi-sync`` (auto-sync) to synchronize.
    """
    bk = Buckets()
    namespace = _require_login(bk)
    bucket_id = bk.resolve_bucket_id(bucket, namespace)

    if not bk.bucket_exists(bucket_id):
        return StatusResult(
            bucket_id,
            initialized=False,
            diff="n/a",
            message="bucket is not initialized",
            hint="run `hf pi-sync init`",
        )

    try:
        local_latest = _local_latest_mtime(with_auth)
    except AgentDirMissing:
        return StatusResult(
            bucket_id,
            initialized=True,
            diff="remote-newer",
            message="no local pi config; bucket has data",
            hint="run `hf pi-sync pull`",
        )

    remote_latest = _remote_latest_mtime(bk, bucket_id)
    marker = read_last_sync(bucket_id)

    if marker is None:
        # No recorded reconciliation: fall back to absolute mtime comparison.
        if local_latest - remote_latest > _MTIME_TOLERANCE:
            return StatusResult(
                bucket_id,
                initialized=True,
                diff="local-newer",
                message="local config is newer than remote",
                hint="run `hf pi-sync` to push",
            )
        if remote_latest - local_latest > _MTIME_TOLERANCE:
            return StatusResult(
                bucket_id,
                initialized=True,
                diff="remote-newer",
                message="remote config is newer than local",
                hint="run `hf pi-sync` to pull",
            )
        return StatusResult(
            bucket_id,
            initialized=True,
            diff="none",
            message="local and remote are in sync",
        )

    local_changed = local_latest - marker.local_mtime > _MTIME_TOLERANCE
    remote_changed = remote_latest - marker.remote_mtime > _MTIME_TOLERANCE

    if local_changed and remote_changed:
        return StatusResult(
            bucket_id,
            initialized=True,
            diff="diverged",
            message="local and remote both changed since last sync",
            hint="run `hf pi-sync push` or `hf pi-sync pull`",
        )
    if local_changed:
        return StatusResult(
            bucket_id,
            initialized=True,
            diff="local-newer",
            message="local config is newer than last sync",
            hint="run `hf pi-sync` to push",
        )
    if remote_changed:
        return StatusResult(
            bucket_id,
            initialized=True,
            diff="remote-newer",
            message="remote config is newer than last sync",
            hint="run `hf pi-sync` to pull",
        )
    return StatusResult(
        bucket_id,
        initialized=True,
        diff="none",
        message="local and remote are in sync",
    )


__all__ = [
    "AgentDirMissing",
    "BucketExistsError",
    "BucketMissingError",
    "DEFAULT_BUCKET_NAME",
    "Buckets",
    "NotLoggedInError",
    "StatusResult",
    "SyncResult",
    "cmd_auto",
    "cmd_init",
    "cmd_pull",
    "cmd_push",
    "cmd_status",
]
