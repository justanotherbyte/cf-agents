import argparse
import os
import sys
import traceback
from pathlib import Path

from . import wrangler
from .errors import ProjectNotFoundError, WyndError
from .host import warn_on_venv_mismatch
from .project import load_project
from .scaffold import ADDONS, scaffold
from .stage import stage_run, stage_upload
from .sync import SDKS, select_sdk, sync
from .targets import select_target
from .typegen import write_env

__all__ = ("main",)


def _global_parser() -> argparse.ArgumentParser:
    """Parse the Wrangler flags Wynd reads before any command."""
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    target_flags = parser.add_argument_group()
    target_flags.add_argument("-c", "--config")
    target_flags.add_argument("-e", "--env")
    target_flags.add_argument("--cwd")
    skipped_value_flags = parser.add_argument_group()
    skipped_value_flags.add_argument("--env-file", action="append")
    skipped_value_flags.add_argument("--profile")
    refused_flags = parser.add_argument_group()
    refused_flags.add_argument("--compatibility-date")
    refused_flags.add_argument(
        "--compatibility-flags", "--compatibility-flag", action="append"
    )
    refused_flags.add_argument("--latest", action="store_true")
    return parser


def _init_parser() -> argparse.ArgumentParser:
    """Parse `wynd init`'s own arguments. A flag left out is asked for."""
    parser = argparse.ArgumentParser(prog="wynd init", allow_abbrev=False)
    parser.add_argument("command", choices=["init"], help=argparse.SUPPRESS)
    parser.add_argument("directory", nargs="?")
    parser.add_argument("--sdk", choices=SDKS, help="the Workers SDK to build on")
    for addon, dist in ADDONS.items():
        parser.add_argument(
            f"--{addon}",
            action=argparse.BooleanOptionalAction,
            help=f"add {dist}",
        )
    return parser


_parser = _global_parser()

# Wrangler 4.129.0's subcommands. Of these, only `versions upload` uploads code,
# and `preview` uploads code when it has none of them.
SUBCOMMANDS = {
    "versions": {"view", "list", "upload", "deploy", "secret"},
    "preview": {"delete", "settings", "secret", "base-config"},
}


def main(argv: list[str] | None = None) -> int:
    """Run a `wynd` command line. Commands Wynd doesn't own go to Wrangler,
    on the config Wynd stages in .wynd/."""
    args = sys.argv[1:] if argv is None else argv
    options, rest = _parser.parse_known_args(args)
    flow = _flow(rest)

    if flow in ("dev", "upload") and (
        options.compatibility_date or options.compatibility_flags or options.latest
    ):
        print(
            "wynd: error: --compatibility-date, --compatibility-flags and --latest "
            "change the Python version after Wynd installs packages. Set "
            "compatibility_date and compatibility_flags in the Wrangler config.",
            file=sys.stderr,
        )
        return 1

    cwd = Path.cwd() / options.cwd if options.cwd else Path.cwd()
    mode = options.env or os.environ.get("CLOUDFLARE_ENV")
    try:
        if flow == "init":
            return _init(cwd, rest)
        try:
            project = load_project(cwd, options.config, mode)
        except ProjectNotFoundError:
            if flow != "passthrough":
                raise
            # `wynd login` outside any project.
            return wrangler.run(args)

        if flow == "types":
            write_env(project, check="--check" in rest)
            return 0
        if flow != "passthrough":
            target = select_target(project.config)
            sync(project, target)
            if (
                flow != "sync"
                and select_sdk(project.dependencies) == "crosswind-workers"
            ):
                write_env(project)
            if flow == "dev":
                warn_on_venv_mismatch(project, target)
        if flow == "sync":
            return 0

        if flow == "upload":
            staged, plan = stage_upload(project)
            if "--dry-run" in rest:
                # Flushed, so it comes before Wrangler's output in a pipe.
                report = plan.size_report(staged.path.parent / "python_modules")
                print(f"wynd: {report}", flush=True)
        else:
            staged = stage_run(project)
    except WyndError as error:
        print(f"wynd: error: {error}", file=sys.stderr)
        return 1

    argv, environ = project.source.wrangler_args(args, staged.path)
    if flow == "dev":
        return wrangler.run(
            argv,
            environ=environ,
            watch=project.source.path,
            restage=lambda: _restage(cwd, options.config, mode),
        )

    code = wrangler.run(argv, environ=environ)
    staged.warn_if_edited(project.source.path)
    return code


def _flow(rest: list[str]) -> str:
    """Sort a command line into its flow, or passthrough."""
    words = [arg for arg in rest if not arg.startswith("-")]
    command = words[0] if words else ""
    known = SUBCOMMANDS.get(command, set())
    subcommand = next((word for word in words[1:] if word in known), "")

    if (
        command == "deploy"
        or (command == "versions" and subcommand == "upload")
        or (command == "preview" and not subcommand)
    ):
        return "upload"
    return command if command in ("dev", "sync", "types", "init") else "passthrough"


def _init(cwd: Path, rest: list[str]) -> int:
    """Write a new project, asking for each choice its flags leave out."""
    options = _init_parser().parse_args(rest)
    ask = sys.stdin.isatty()
    sdk = options.sdk or (_ask_sdk() if ask else SDKS[0])
    unset = [addon for addon in ADDONS if getattr(options, addon) is None]
    chosen = set(_ask_addons(unset)) if ask and unset else set()
    addons = tuple(
        addon for addon in ADDONS if getattr(options, addon) or addon in chosen
    )
    scaffold(cwd / options.directory if options.directory else cwd, sdk, addons)
    return 0


def _ask_sdk() -> str:
    """Ask which of SDKS to build on. Enter picks the first."""
    for number, sdk in enumerate(SDKS, 1):
        print(f"  {number}) {sdk}")
    while True:
        answer = input("Workers SDK [1]: ").strip() or "1"
        if answer.isdigit() and 1 <= int(answer) <= len(SDKS):
            return SDKS[int(answer) - 1]
        print(f"Enter a number from 1 to {len(SDKS)}.")


def _ask_addons(addons: list[str]) -> list[str]:
    """Ask which of `addons` to add, as numbers. Enter picks none."""
    for number, addon in enumerate(addons, 1):
        print(f"  {number}) {ADDONS[addon]}")
    while True:
        answer = input("Add SDKs, comma-separated [none]: ").replace(",", " ")
        numbers = answer.split()
        if all(n.isdigit() and 1 <= int(n) <= len(addons) for n in numbers):
            return [addons[int(n) - 1] for n in numbers]
        print(f"Enter numbers from 1 to {len(addons)}, or nothing.")


def _restage(cwd: Path, config: str | None, mode: str | None) -> None:
    """Stage an edited config again, so the running wrangler dev reloads.

    A broken edit is reported and dev keeps serving the last good config.
    """
    try:
        stage_run(load_project(cwd, config, mode))
    except WyndError as error:
        print(f"wynd: error: {error}", file=sys.stderr)
    # Dev outlives a typo in cloudflare.config.py's own code, which keeps its
    # traceback.
    except Exception:  # noqa: BLE001
        traceback.print_exc()
