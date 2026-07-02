"""Per-machine last-sync marker for auto-sync direction decisions.

Stores the reconciled local/remote mtimes after each successful push or pull so
``cmd_auto`` can decide direction by "what changed since we last reconciled"
rather than "whose absolute mtime is bigger". That makes the decision immune to
freshly-seeded configs (a brand-new ``settings.json`` with mtime = now no longer
clobbers a populated remote) and to clock skew between machines.

The marker lives OUTSIDE the synced tree (``~/.pi/.pi-sync/last-sync``) so it is
never staged, pushed, pulled, or mirror-deleted. It is per-machine, per-bucket:
the ``bucket_id`` is checked on read, so switching buckets (``--bucket`` or
``PI_SYNC_BUCKET``) is treated as "never reconciled" with the new bucket.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

_MARKER_DIR = Path.home() / ".pi" / ".pi-sync"
_MARKER_FILE = _MARKER_DIR / "last-sync"


@dataclass(frozen=True)
class LastSync:
    """Snapshot of the reconciled state at the last successful sync."""

    bucket_id: str
    local_mtime: float
    remote_mtime: float
    synced_at: float


def read_last_sync(bucket_id: str) -> LastSync | None:
    """Return the marker for ``bucket_id``, or None if absent/stale/wrong bucket."""
    try:
        data = json.loads(Path(_MARKER_FILE).read_text())
    except (OSError, ValueError):
        return None
    if data.get("bucket_id") != bucket_id:
        return None  # different bucket -> never reconciled with this one
    return LastSync(
        bucket_id=data["bucket_id"],
        local_mtime=float(data.get("local_mtime", 0.0)),
        remote_mtime=float(data.get("remote_mtime", 0.0)),
        synced_at=float(data.get("synced_at", 0.0)),
    )


def write_last_sync(
    bucket_id: str, local_mtime: float, remote_mtime: float
) -> None:
    """Persist the reconciled mtimes atomically."""
    marker_dir = Path(_MARKER_DIR)
    marker_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "bucket_id": bucket_id,
        "local_mtime": local_mtime,
        "remote_mtime": remote_mtime,
        "synced_at": time.time(),
    }
    tmp = marker_dir / "last-sync.tmp"
    tmp.write_text(json.dumps(payload, indent=2))
    os.replace(tmp, Path(_MARKER_FILE))


__all__ = ["LastSync", "read_last_sync", "write_last_sync"]