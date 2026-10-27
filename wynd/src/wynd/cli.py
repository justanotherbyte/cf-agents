import os
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

import click

from . import wrangler
from .console import choices, error, info
from .errors import ProjectNotFoundError, WyndError
from .host import warn_on_venv_mismatch
from .project import Project, load_project
from .scaffold import ADDONS, scaffold
from .stage import stage_run, stage_upload
from .sync import SDKS, select_sdk, sync
from .targets import select_target
from .typegen import write_env

__all__ = ("main",)

# Wrangler 4.129.0's subcommands. Of these, only `versions upload` uploads code,
# and `preview` uploads code when it has none of them.
SUBCOMMANDS = {
    "versions": {"view", "list", "upload", "deploy", "secret"},
    "preview": {"delete", "settings", "secret", "base-config"},
}

# Commands that run Wrangler take its flags, --help included, and pass them on.
WRANGLER_COMMAND = {
    "ignore_unknown_options": True,
    "allow_extra_args": True,
    "help_option_names": [],
}


def main(argv: list[str] | None = None) -> int:
    """Run a `wynd` command line. Commands Wynd doesn't own go to Wrangler,
    on the config Wynd stages in .wynd/."""
    args = sys.argv[1:] if argv is None else argv
    try:
        code = cli.main(args, prog_name="wynd", standalone_mode=False, obj=args)
    except click.ClickException as exception:
        exception.show()
        return exception.exit_code
    except click.Abort:
        return 1
    except WyndError as exception:
        error(str(exception))
        return 1
    return code if isinstance(code, int) else 0


def _remember(ctx: click.Context, param: click.Parameter, value: Any) -> None:
    # Wrangler reads these flags anywhere on the line, so each command accepts
    # them and the root context keeps the one given.
    if value and param.name:
        ctx.find_root().meta[param.name] = value


def _wrangler_options[F: Callable[..., Any]](command: F) -> F:
    """Add the Wrangler flags Wynd reads, wherever they appear."""
    options: list[tuple[tuple[str, ...], dict[str, Any]]] = [
        (("-c", "--config"), {"help": "The Wrangler or Python config."}),
        (("-e", "--env"), {"help": "The environment, as in Wrangler."}),
        (("--cwd",), {"help": "Run as if started in this directory."}),
        (("--env-file",), {"multiple": True, "hidden": True}),
        (("--profile",), {"hidden": True}),
        (("--compatibility-date",), {"hidden": True}),
        (
            ("--compatibility-flags", "--compatibility-flag"),
            {"multiple": True, "hidden": True},
        ),
        (("--latest",), {"is_flag": True, "hidden": True}),
    ]
    for decls, settings in options:
        command = click.option(
            *decls, callback=_remember, expose_value=False, **settings
        )(command)
    return command


def _setting(name: str) -> Any:
    """Return a Wrangler flag given anywhere on the line, or None."""
    return click.get_current_context().find_root().meta.get(name)


class _WyndGroup(click.Group):
    """A group that sends commands it doesn't own to Wrangler."""

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        if args and self.get_command(ctx, args[0]) is None:
            return args[0], _passthrough, args[1:]
        return super().resolve_command(ctx, args)


@click.group(
    cls=_WyndGroup,
    context_settings={"ignore_unknown_options": True},
    epilog="Any other command runs Wrangler on the config Wynd stages in .wynd/.",
)
@_wrangler_options
def cli() -> None:
    """Build and run Python Workers with Wrangler."""


@cli.command(context_settings=WRANGLER_COMMAND)
@_wrangler_options
def dev() -> int:
    """Sync packages and serve the Worker with wrangler dev."""
    return _run_wrangler("dev")


@cli.command(context_settings=WRANGLER_COMMAND)
@_wrangler_options
def deploy() -> int:
    """Sync packages and deploy the Worker with wrangler deploy."""
    return _run_wrangler("upload")


@cli.command(context_settings=WRANGLER_COMMAND)
@_wrangler_options
def versions() -> int:
    """Run wrangler versions. `versions upload` syncs packages first."""
    upload = _subcommand("versions") == "upload"
    return _run_wrangler("upload" if upload else "passthrough")


@cli.command(context_settings=WRANGLER_COMMAND)
@_wrangler_options
def preview() -> int:
    """Run wrangler preview. Without a subcommand it syncs packages first."""
    return _run_wrangler("passthrough" if _subcommand("preview") else "upload")


@click.command(context_settings=WRANGLER_COMMAND)
@_wrangler_options
def _passthrough() -> int:
    return _run_wrangler("passthrough")


@cli.command("sync")
@_wrangler_options
def sync_command() -> int:
    """Install the Worker's packages into python_modules."""
    project = _load_project()
    sync(project, select_target(project.config))
    return 0


@cli.command()
@click.option("--check", is_flag=True, help="Fail if src/env.py is out of date.")
@_wrangler_options
def types(check: bool) -> int:
    """Write the Env class for the Worker's bindings to env.py."""
    write_env(_load_project(), check=check)
    return 0


@cli.command()
@click.argument("directory", required=False)
@click.option("--sdk", type=click.Choice(SDKS), help="The Workers SDK to build on.")
@click.option(
    "--containers/--no-containers", default=None, help=f"Add {ADDONS['containers']}."
)
@click.option("--agents/--no-agents", default=None, help=f"Add {ADDONS['agents']}.")
@_wrangler_options
def init(directory: str | None, sdk: str | None, **flags: bool | None) -> int:
    """Write a new project. In a terminal, asks for the choices flags leave out."""
    ask = sys.stdin.isatty()
    if sdk is None:
        sdk = _ask_sdk() if ask else SDKS[0]
    unset = [addon for addon in ADDONS if flags[addon] is None]
    chosen = set(_ask_addons(unset)) if ask and unset else set()
    addons = tuple(addon for addon in ADDONS if flags[addon] or addon in chosen)
    cwd = _cwd()
    scaffold(cwd / directory if directory else cwd, sdk, addons)
    return 0


def _ask_sdk() -> str:
    """Ask which of SDKS to build on. Enter picks the first."""
    choices(SDKS)
    number = click.prompt("Workers SDK", type=click.IntRange(1, len(SDKS)), default=1)
    return SDKS[number - 1]


def _ask_addons(addons: list[str]) -> list[str]:
    """Ask which of `addons` to add, as numbers. Enter picks none."""

    def parse(answer: str) -> list[str]:
        numbers = answer.replace(",", " ").split()
        if not all(n.isdigit() and 1 <= int(n) <= len(addons) for n in numbers):
            raise click.BadParameter(
                f"enter numbers from 1 to {len(addons)}, or nothing."
            )
        return [addons[int(n) - 1] for n in numbers]

    choices([ADDONS[addon] for addon in addons])
    return click.prompt(
        "Add SDKs, comma-separated",
        default="",
        show_default=False,
        value_proc=parse,
    )


def _subcommand(command: str) -> str:
    """Return the Wrangler subcommand of `command` on the line, or ''."""
    known = SUBCOMMANDS[command]
    args = click.get_current_context().args
    return next((arg for arg in args if arg in known), "")


def _cwd() -> Path:
    cwd = _setting("cwd")
    return Path.cwd() / cwd if cwd else Path.cwd()


def _mode() -> str | None:
    return _setting("env") or os.environ.get("CLOUDFLARE_ENV")


def _load_project() -> Project:
    return load_project(_cwd(), _setting("config"), _mode())


def _run_wrangler(flow: str) -> int:
    """Run Wrangler on the whole command line and the config Wynd stages.

    `flow` is "dev", "upload" for commands that upload code, or "passthrough",
    which skips the package sync.
    """
    args = click.get_current_context().find_root().obj
    if flow in ("dev", "upload") and any(
        _setting(name)
        for name in ("compatibility_date", "compatibility_flags", "latest")
    ):
        error(
            "--compatibility-date, --compatibility-flags and --latest change the "
            "Python version after Wynd installs packages. Set compatibility_date "
            "and compatibility_flags in the Wrangler config."
        )
        return 1

    try:
        project = _load_project()
    except ProjectNotFoundError:
        if flow != "passthrough":
            raise
        # `wynd login` outside any project.
        return wrangler.run(args)

    if flow != "passthrough":
        target = select_target(project.config)
        sync(project, target)
        if select_sdk(project.dependencies) == "crosswind-workers":
            write_env(project)
        if flow == "dev":
            warn_on_venv_mismatch(project, target)

    if flow == "upload":
        staged, plan = stage_upload(project)
        if "--dry-run" in args:
            info(plan.size_report(staged.path.parent / "python_modules"))
    else:
        staged = stage_run(project)

    argv, environ = project.source.wrangler_args(args, staged.path)
    if flow == "dev":
        cwd, config, mode = _cwd(), _setting("config"), _mode()
        return wrangler.run(
            argv,
            environ=environ,
            watch=project.source.path,
            restage=lambda: _restage(cwd, config, mode),
        )

    code = wrangler.run(argv, environ=environ)
    staged.warn_if_edited(project.source.path)
    return code


def _restage(cwd: Path, config: str | None, mode: str | None) -> None:
    """Stage an edited config again, so the running wrangler dev reloads.

    A broken edit is reported and dev keeps serving the last good config.
    """
    try:
        stage_run(load_project(cwd, config, mode))
    except WyndError as exception:
        error(str(exception))
    # Dev outlives a typo in cloudflare.config.py's own code, which keeps its
    # traceback.
    except Exception:  # noqa: BLE001
        traceback.print_exc()
