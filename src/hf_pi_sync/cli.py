"""Typer CLI entry point for ``hf pi-sync``.

Dispatched by the ``hf`` CLI via the ``hf-pi-sync`` console-script entry point.

Subcommands:

- ``hf pi-sync init``  login check, create/get private bucket, first push.
- ``hf pi-sync push``  stage shareable subset and upload to the bucket.
- ``hf pi-sync pull``  download bucket, merge into ~/.pi/agent/, run ``pi install``.
- ``hf pi-sync``       auto-sync: compare mtimes and push or pull accordingly.

Common options:

- ``--bucket <user>/<name>``  override default bucket (env: ``PI_SYNC_BUCKET``).
- ``--dry-run``              show what would be synced without doing it.
- ``--with-auth``            include ``auth.json`` in the sync (default: exclude).
"""

from __future__ import annotations

import os

import typer

from . import __version__
from . import sync as syncmod

app = typer.Typer(
    name="pi-sync",
    help="Sync pi agent config across VMs via Hugging Face Buckets.",
    no_args_is_help=False,
    add_completion=False,
)


def _resolve_bucket(bucket: str | None) -> str | None:
    return bucket or os.environ.get("PI_SYNC_BUCKET")


@app.callback(invoke_without_command=True)
def auto_sync_default(
    ctx: typer.Context,
    bucket: str = typer.Option(
        None,
        "--bucket",
        envvar="PI_SYNC_BUCKET",
        help="Bucket id as <user>/<name> (default: <whoami>/pi-config).",
    ),
    with_auth: bool = typer.Option(
        False,
        "--with-auth",
        help="Include auth.json in the sync (default: exclude credentials).",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be synced."),
    version: bool = typer.Option(False, "--version", help="Print version and exit."),
) -> None:
    """hf pi-sync: sync pi agent config across VMs via Hugging Face Buckets."""
    if version:  # pragma: no cover
        typer.echo(f"hf-pi-sync {__version__}")
        raise typer.Exit

    ctx.obj = {
        "bucket": _resolve_bucket(bucket),
        "with_auth": with_auth,
        "dry_run": dry_run,
    }

    if ctx.invoked_subcommand is None:
        try:
            result = syncmod.cmd_auto(
                ctx.obj["bucket"],
                with_auth=ctx.obj["with_auth"],
                dry_run=ctx.obj["dry_run"],
            )
        except NotImplementedError:
            typer.secho(
                "auto-sync is not implemented yet. "
                "Use `hf pi-sync push` or `hf pi-sync pull`.",
                fg=typer.colors.YELLOW,
            )
            raise typer.Exit(code=1) from None
        _print_result(result)
        raise typer.Exit()


@app.command("init")
def init_cmd(
    bucket: str = typer.Option(
        None, "--bucket", envvar="PI_SYNC_BUCKET", help="Bucket id <user>/<name>."
    ),
    private: bool = typer.Option(True, "--private/--public", help="Bucket visibility."),
    with_auth: bool = typer.Option(False, "--with-auth", help="Include auth.json."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show plan, do nothing."),
) -> None:
    """Login check, create/get private bucket, first push."""
    try:
        result = syncmod.cmd_init(
            _resolve_bucket(bucket),
            private=private,
            with_auth=with_auth,
            dry_run=dry_run,
        )
    except NotImplementedError:
        typer.secho("init is not implemented yet.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1) from None
    _print_result(result)


@app.command("push")
def push_cmd(
    bucket: str = typer.Option(
        None, "--bucket", envvar="PI_SYNC_BUCKET", help="Bucket id <user>/<name>."
    ),
    with_auth: bool = typer.Option(False, "--with-auth", help="Include auth.json."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show plan, do nothing."),
    quiet: bool = typer.Option(
        False, "--quiet", help="No output unless changes occur."
    ),
) -> None:
    """Stage the shareable subset and upload to the bucket."""
    try:
        result = syncmod.cmd_push(
            _resolve_bucket(bucket), with_auth=with_auth, dry_run=dry_run, quiet=quiet
        )
    except NotImplementedError:
        typer.secho("push is not implemented yet.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1) from None
    _print_result(result)


@app.command("pull")
def pull_cmd(
    bucket: str = typer.Option(
        None, "--bucket", envvar="PI_SYNC_BUCKET", help="Bucket id <user>/<name>."
    ),
    with_auth: bool = typer.Option(False, "--with-auth", help="Include auth.json."),
    mirror: bool = typer.Option(
        False,
        "--mirror",
        help="Delete local files not in the bucket (destructive; default additive).",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show plan, do nothing."),
) -> None:
    """Download bucket, merge into ~/.pi/agent/, run `pi install`."""
    try:
        result = syncmod.cmd_pull(
            _resolve_bucket(bucket),
            with_auth=with_auth,
            mirror=mirror,
            dry_run=dry_run,
        )
    except NotImplementedError:
        typer.secho("pull is not implemented yet.", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1) from None
    _print_result(result)


def _print_result(result: syncmod.SyncResult) -> None:
    if not result:
        return
    color = typer.colors.GREEN if not result.dry_run else typer.colors.CYAN
    typer.secho(result.summary(), fg=color)


# Console-script entry point: hf-pi-sync = "hf_pi_sync.cli:main".
main = app


if __name__ == "__main__":  # pragma: no cover
    app()
