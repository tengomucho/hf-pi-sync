"""Thin wrapper over the ``huggingface_hub`` HfApi bucket methods."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from huggingface_hub import BucketFile, HfApi

DEFAULT_BUCKET_NAME = "pi-config"


def _mtime_to_epoch(mtime: object) -> float:
    """Best-effort conversion of a bucket file mtime to a unix epoch float."""
    if mtime is None:
        return 0.0
    if isinstance(mtime, (int, float)):
        return float(mtime)
    if hasattr(mtime, "timestamp"):  # datetime.datetime
        return float(mtime.timestamp())  # type: ignore[union-attr]
    return 0.0


def _uri(bucket_id: str, prefix: str | None = None) -> str:
    """Return the ``hf://buckets/<namespace>/<name>[/<prefix>]`` URI."""
    uri = f"hf://buckets/{bucket_id.lstrip('/')}"
    if prefix:
        uri = f"{uri}/{prefix.lstrip('/')}"
    return uri


@dataclass(frozen=True)
class RemoteFile:
    """A file in the bucket with its modification time."""

    path: str
    size: int
    mtime: float


class Buckets:
    """Thin convenience wrapper around ``HfApi`` bucket operations."""

    def __init__(self, api: HfApi | None = None) -> None:
        self.api = api or HfApi()

    def whoami(self) -> str:
        """Return the authenticated user's HF namespace (username)."""
        return self.api.whoami()["name"]

    def resolve_bucket_id(
        self, bucket: str | None, namespace: str | None = None
    ) -> str:
        """Resolve ``<namespace>/<bucket>`` from a (possibly bare) bucket name.

        A ``bucket`` already containing ``/`` is used as-is. Otherwise the
        namespace defaults to the authenticated user unless given.
        """
        if bucket and "/" in bucket:
            return bucket
        ns = namespace or self.whoami()
        name = bucket or DEFAULT_BUCKET_NAME
        return f"{ns}/{name}"

    def bucket_exists(self, bucket_id: str) -> bool:
        """True iff the bucket exists under the resolved namespace."""
        ns, _, _ = bucket_id.partition("/")
        return any(info.id == bucket_id for info in self.api.list_buckets(namespace=ns))

    def create_bucket(self, bucket_id: str, *, private: bool = True) -> str | None:
        """Create a private bucket idempotently.

        Returns the bucket URI, or None if the bucket already existed.
        """
        existed = self.bucket_exists(bucket_id)
        self.api.create_bucket(bucket_id, private=private, exist_ok=True)
        return None if existed else _uri(bucket_id)

    def list_files(self, bucket_id: str) -> list[RemoteFile]:
        """List all files in the bucket, with size and mtime."""
        out: list[RemoteFile] = []
        for item in self.api.list_bucket_tree(bucket_id, recursive=True):
            if isinstance(item, BucketFile):
                out.append(
                    RemoteFile(
                        path=item.path,
                        size=int(item.size or 0),
                        mtime=_mtime_to_epoch(item.mtime),
                    )
                )
        return out

    def sync_to_bucket(
        self,
        local_dir: Path | str,
        bucket_id: str,
        *,
        exclude: list[str] | None = None,
        include: list[str] | None = None,
        delete: bool = False,
        dry_run: bool = False,
    ) -> Any:
        """Upload (mirror) a local directory into the bucket."""
        return self.api.sync_bucket(
            source=str(local_dir),
            dest=_uri(bucket_id),
            exclude=exclude,
            include=include,
            delete=delete,
            dry_run=dry_run,
        )

    def sync_from_bucket(
        self,
        bucket_id: str,
        local_dir: Path | str,
        *,
        exclude: list[str] | None = None,
        include: list[str] | None = None,
        delete: bool = False,
        dry_run: bool = False,
    ) -> Any:
        """Download (mirror) the bucket into a local directory."""
        return self.api.sync_bucket(
            source=_uri(bucket_id),
            dest=str(local_dir),
            exclude=exclude,
            include=include,
            delete=delete,
            dry_run=dry_run,
        )
