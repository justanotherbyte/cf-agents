import argparse
import os
import sys
from pathlib import Path

from . import wrangler
from .host import warn_on_venv_mismatch
from .project import WyndError, load_project
from .stage import stage
from .sync import select_sdk, sync
from .targets import select_target
from .typegen import write_env

__all__ = ["main"]

# Reads the flags that choose the target and leaves everything else to Wrangler.
# --env-file and --profile are Wrangler's other global value flags, read only so
# their values aren't taken for the command.
_parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
_parser.add_argument("-c", "--config")
_parser.add_argument("-e", "--env")
_parser.add_argument("--cwd")
_parser.add_argument("--env-file", action="append")
_parser.add_argument("--profile")

# Wrangler 4.129.0's subcommands. Of these, only `versions upload` uploads code,
# and `preview` uploads code when it has none of them.
SUBCOMMANDS = {
    "versions": {"view", "list", "upload", "deploy", "secret"},
    "preview": {"delete", "settings", "secret", "base-config"},
}

# Takes only the config flag, so the remaining arguments keep their order.
_config_parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
_config_parser.add_argument("-c", "--config")


def main(argv: list[str] | None = None) -> int:
    """Run a `wynd` command line. Commands Wynd doesn't own go to Wrangler."""
    args = sys.argv[1:] if argv is None else argv
    options, rest = _parser.parse_known_args(args)
    flow = _flow(rest)
    if flow == "passthrough":
        return wrangler.run(args)
    try:
        cwd = Path.cwd() / options.cwd if options.cwd else Path.cwd()
        env = options.env or os.environ.get("CLOUDFLARE_ENV")
        project = load_project(cwd, options.config, env)
        if flow == "types":
            write_env(project, check="--check" in rest)
            return 0
        target = select_target(project)
        sync(project, target)
        if flow != "sync" and select_sdk(project.dependencies) == "crosswind-workers":
            write_env(project)
        staged = stage(project) if flow == "upload" else None
    except WyndError as error:
        print(f"wynd: error: {error}", file=sys.stderr)
        return 1
    if flow == "sync":
        return 0
    if staged is not None:
        path, plan = staged
        if "--dry-run" in rest:
            # Flushed, so it comes before Wrangler's output in a pipe.
            print(f"wynd: {plan.size_report()}", flush=True)
        _, rest = _config_parser.parse_known_args(args)
        return wrangler.run([*rest, "--config", str(path)])
    warn_on_venv_mismatch(project, target)
    return wrangler.run(args)


def _flow(rest: list[str]) -> str:
    """Sort a command line into the upload, dev, sync, types or passthrough flow."""
    words = [arg for arg in rest if not arg.startswith("-")]
    command = words[0] if words else ""
    # A known name, not the second word, because a flag's value can come first.
    known = SUBCOMMANDS.get(command, set())
    subcommand = next((word for word in words[1:] if word in known), "")
    if (
        command == "deploy"
        or (command == "versions" and subcommand == "upload")
        or (command == "preview" and not subcommand)
    ):
        return "upload"
    return command if command in ("dev", "sync", "types") else "passthrough"
