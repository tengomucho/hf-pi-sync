# Plan: Sync only `~/.pi/agent/memory/MEMORY.md`

## Goal

The `pi-mem` extension stores durable facts in `~/.pi/agent/memory/MEMORY.md` and
per-machine / ephemeral state alongside it (`daily/*.md` logs, `SCRATCHPAD.md`,
future index DBs). We want `MEMORY.md` to sync across machines, but **nothing
else under `memory/`** should.

## Current behavior (problem)

`staging._stage()` runs `shutil.copytree(..., ignore=shutil.ignore_patterns(*patterns))`
where `patterns = EXCLUDED_DIRS + EXCLUDED_FILES_DEFAULT = ("npm","bin","sessions","auth.json")`.
`memory/` is **not** in that list, so *every* file under `memory/` — including
`daily/*.md` and `SCRATCHPAD.md` — is already being pushed to the bucket. The
README "What gets synced" table simply never mentioned it.

So this feature is really: **stop syncing the non-`MEMORY.md` part of `memory/`**
while keeping `MEMORY.md` synced.

## Constraints discovered in the code

1. `shutil.ignore_patterns` matches **basenames only** (not paths), and cannot
   express "everything in `memory/` except `MEMORY.md`". We need a **custom
   ignore callable** instead of `ignore_patterns`.
2. `_merge_stage_into_agent`'s **mirror-delete loop** deletes any local file not
   present in the bucket. If we merely stop *uploading* `memory/daily/*`, a
   subsequent `pull --mirror` would **delete the local daily logs**. They must be
   explicitly protected (treated like `sessions/`: local-only, never deleted).
3. `buckets.sync_to_bucket` defaults to `delete=False` (additive). Stale
   `memory/daily/*` files already in existing buckets will linger as dead weight
   but are harmless as long as **pull-merge never copies them back locally**.
4. `default_excludes()` is reused as the flat fnmatch exclude list passed to the
   bucket API (`sync_from_bucket`). We must **not** add `memory` to that list
   (it would exclude `MEMORY.md` too). The `memory/` filtering lives entirely in
   staging + merge, not in the bucket exclude list.

## Design

### New constants in `staging.py`

```python
# memory/ is a partially-synced directory: only MEMORY.md is shareable.
# Everything else under memory/ (daily logs, scratchpad, index DBs) is
# local-only, like sessions/.
MEMORY_DIR_NAME = "memory"
MEMORY_SYNCED_FILE = "MEMORY.md"
```

`EXCLUDED_DIRS` and `default_excludes()` are **unchanged** (the bucket-side
exclude list must still allow `memory/MEMORY.md` through).

### Custom ignore callable (replaces `shutil.ignore_patterns` in `_stage`)

`shutil.copytree(src, stage, dirs_exist_ok=True, ignore=<callable>)` where the
callable prunes:

- `EXCLUDED_DIRS` entries by basename (npm/bin/sessions),
- `EXCLUDED_FILES` entries by basename (auth.json, unless `with_auth`),
- **only at the top-level `memory/` directory** (`Path(dir) == src/"memory"`):
  every entry except `MEMORY.md` (this also prevents recursing into
  `memory/daily/`).

Pseudocode:

```python
def _build_ignore(with_auth: bool, root: Path):
    excluded_dirs = set(EXCLUDED_DIRS)
    excluded_files = set(EXCLUDED_FILES_DEFAULT if not with_auth else ())
    memory_root = Path(root) / MEMORY_DIR_NAME
    def _ignore(dir_path, names):
        out = set()
        is_memory = Path(dir_path) == memory_root
        for n in names:
            if n in excluded_dirs or n in excluded_files:
                out.add(n)
            elif is_memory and n != MEMORY_SYNCED_FILE:
                out.add(n)
        return out
    return _ignore
```

`_stage()` switches from `shutil.ignore_patterns(*patterns)` to
`ignore=_build_ignore(with_auth, src)`. `default_excludes()` stays as-is for the
bucket API path.

### Merge: protect local-only memory files (`_merge_stage_into_agent`)

Add one helper:

```python
def _is_local_only_memory(rel: Path) -> bool:
    """True for files under memory/ that are not MEMORY.md (never synced)."""
    return rel.parts and rel.parts[0] == MEMORY_DIR_NAME \
        and not (len(rel.parts) == 2 and rel.parts[1] == MEMORY_SYNCED_FILE)
```

- **Copy phase** (walks `stage`): skip any staged file where
  `_is_local_only_memory(rel)` is true. In practice the stage will only contain
  `memory/MEMORY.md` anyway (push filtered it), but stale bucket entries
  downloaded into the stage on pull must not be merged back locally.
- **Mirror-delete phase** (walks `dst`): skip deletion when
  `_is_local_only_memory(rel)` is true, so `pull --mirror` never wipes local
  `memory/daily/*.md` / `SCRATCHPAD.md`. `MEMORY.md` itself is *not* protected —
  if the bucket dropped it, mirror is allowed to delete the local copy
  (consistent with mirror semantics for a synced file).

### `_apply_bucket_mtimes`

No change needed: it already guards `if local.is_file()`. Stale `memory/daily/*`
bucket entries won't exist locally (merge skipped them), so they're a no-op.

### mtime comparisons (`cmd_auto` / `cmd_status` / `_local_latest_mtime`)

No change needed. They flow through `_stage`, which now only contains
`memory/MEMORY.md`, so the latest-mtime computation is faithful to the synced
subset.

## Stale-bucket migration (pre-existing users)

Because the current code already uploads all of `memory/`, existing buckets
likely contain `memory/daily/*.md`. After this change:

- Push stops uploading them (additive, `delete=False`) — they stop growing.
- Pull-merge refuses to copy them back locally — they're inert dead weight.

No forced migration required. If we want to *clean* them, a future one-off flag
(e.g. `hf pi-sync push --prune` passing `delete=True` for a single run, or a
targeted `api.delete_file` loop for `memory/*` minus `MEMORY.md`) can do it.
**Out of scope for this feature**; noted as a follow-up.

## Files to change

| File | Change |
|---|---|
| `src/hf_pi_sync/staging.py` | Add `MEMORY_DIR_NAME`/`MEMORY_SYNCED_FILE` constants; add `_build_ignore()` and `_is_local_only_memory()` helpers. |
| `src/hf_pi_sync/sync.py` | `_stage()`: use `_build_ignore`. `_merge_stage_into_agent()`: apply `_is_local_only_memory` skip in copy + mirror-delete phases. |
| `tests/test_push.py` | Add: `memory/MEMORY.md` is uploaded; `memory/daily/x.md` and `memory/SCRATCHPAD.md` are NOT uploaded. |
| `tests/test_pull.py` | Add: bucket containing `memory/MEMORY.md` + `memory/daily/x.md` → only `MEMORY.md` lands locally; `--mirror` does NOT delete a pre-existing local `memory/daily/y.md`. |
| `README.md` | Add a `memory/` row to the "What gets synced" table: "`memory/MEMORY.md` ✅; rest of `memory/` ❌ (local per-machine: daily logs, scratchpad)". |

## Verification steps

1. `ruff check src tests` clean.
2. `pytest -q` green, including the two new push/pull cases above (live
   `dummy_bucket` fixture already exists).
3. Manual: with a real `~/.pi/agent/memory/` containing `MEMORY.md` +
   `daily/2026-07-03.md`, run `hf pi-sync push` against a test bucket and
   `assert` via `bk.list_files` that only `memory/MEMORY.md` (no `memory/daily/*`)
   is present.
4. Manual mirror safety: pre-create local `memory/daily/keep.md`, push, then
   `hf pi-sync pull --mirror` from a bucket that lacks it → file survives.

## Non-goals

- No new CLI flag (sync `MEMORY.md` by default; matches user intent).
- No changing the global push `delete=False` additive semantic.
- No pruning existing stale bucket entries (follow-up).