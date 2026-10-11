import argparse
import os
import sys
from pathlib import Path

from . import wrangler
from .host import warn_on_venv_mismatch
from .project import WyndError, load_project
from .sync import sync
from .targets import select_target

# Reads the flags that choose the target and leaves everything else to Wrangler.
# --env-file and --profile are Wrangler's other global value flags, read only so
# their values aren't taken for the command.
_parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
_parser.add_argument("-c", "--config")
_parser.add_argument("-e", "--env")
_parser.add_argument("--cwd")
_parser.add_argument("--env-file", action="append")
_parser.add_argument("--profile")


def main(argv: list[str] | None = None) -> int:
    """Run a `wynd` command line. Commands Wynd doesn't own go to Wrangler."""
    args = sys.argv[1:] if argv is None else argv
    options, rest = _parser.parse_known_args(args)
    command = next((arg for arg in rest if not arg.startswith("-")), None)
    if command not in ("dev", "sync"):
        return wrangler.run(args)
    try:
        cwd = Path.cwd() / options.cwd if options.cwd else Path.cwd()
        env = options.env or os.environ.get("CLOUDFLARE_ENV")
        project = load_project(cwd, options.config, env)
        target = select_target(project)
        sync(project, target)
    except WyndError as error:
        print(f"wynd: error: {error}", file=sys.stderr)
        return 1
    if command == "sync":
        return 0
    warn_on_venv_mismatch(project, target)
    return wrangler.run(args)
