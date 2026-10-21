import argparse
import os
import sys
from pathlib import Path

from . import wrangler
from .host import warn_on_venv_mismatch
from .project import Project, WyndError, find_config, load_project
from .scaffold import scaffold
from .shake import ShakePlan
from .stage import stage, stage_dev, stage_passthrough
from .sync import select_sdk, sync
from .targets import select_target
from .typegen import write_env

__all__ = ("main",)

_parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
_target_flags = _parser.add_argument_group()
_target_flags.add_argument("-c", "--config")
_target_flags.add_argument("-e", "--env")
_target_flags.add_argument("--cwd")
_skipped_value_flags = _parser.add_argument_group()
_skipped_value_flags.add_argument("--env-file", action="append")
_skipped_value_flags.add_argument("--profile")
_refused_flags = _parser.add_argument_group()
_refused_flags.add_argument("--compatibility-date")
_refused_flags.add_argument(
    "--compatibility-flags", "--compatibility-flag", action="append"
)
_refused_flags.add_argument("--latest", action="store_true")

# Wrangler 4.129.0's subcommands. Of these, only `versions upload` uploads code,
# and `preview` uploads code when it has none of them.
SUBCOMMANDS = {
    "versions": {"view", "list", "upload", "deploy", "secret"},
    "preview": {"delete", "settings", "secret", "base-config"},
}

_config_parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
_config_parser.add_argument("-c", "--config")

# Also takes the env flag, which is a Python config's mode. Its conversion has
# no environments, and Wrangler would add the env to the Worker's name.
_mode_parser = argparse.ArgumentParser(
    add_help=False, allow_abbrev=False, parents=[_config_parser]
)
_mode_parser.add_argument("-e", "--env")


def main(argv: list[str] | None = None) -> int:
    """Run a `wynd` command line. Commands Wynd doesn't own go to Wrangler."""
    args = sys.argv[1:] if argv is None else argv
    options, rest = _parser.parse_known_args(args)
    words = [arg for arg in rest if not arg.startswith("-")]
    flow = _flow(words)

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

    try:
        cwd = Path.cwd() / options.cwd if options.cwd else Path.cwd()
        if flow == "passthrough" and not _uses_python_config(cwd, options.config):
            return wrangler.run(args)
        if flow == "init":
            scaffold(cwd / words[1] if len(words) > 1 else cwd)
            return 0
        env = options.env or os.environ.get("CLOUDFLARE_ENV")
        preview = bool(words) and words[0] == "preview"
        project = load_project(cwd, options.config, env, preview=preview)
        if flow == "passthrough":
            return _run_wrangler(args, stage_passthrough(project), project)
        if flow == "types":
            if project.python_config:
                print(f"wynd: {project.config_path.name} declares Env itself.")
            else:
                write_env(project, check="--check" in rest)
            return 0
        target = select_target(project)
        sync(project, target)
        if (
            flow != "sync"
            and not project.python_config
            and select_sdk(project.dependencies) == "crosswind-workers"
        ):
            write_env(project)
        staged = stage(project) if flow == "upload" else None
        dev_config = project.config_path
        if flow == "dev" and project.python_config:
            dev_config = stage_dev(project, env)
    except WyndError as error:
        print(f"wynd: error: {error}", file=sys.stderr)
        return 1

    if flow == "sync":
        return 0
    if staged is not None:
        return _upload(args, rest, project, *staged)

    warn_on_venv_mismatch(project, target)
    return _run_wrangler(args, dev_config, project)


def _uses_python_config(cwd: Path, config: str | None) -> bool:
    """Whether a passthrough command needs Wynd to convert the config first.

    Raises WyndError when the nearest config directory has more than one.
    """
    if config is not None:
        return config.endswith(".py")
    found = find_config(cwd)
    return found is not None and found.suffix == ".py"


def _flow(words: list[str]) -> str:
    """Sort a command line's words into its flow, or passthrough."""
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


def _upload(
    args: list[str], rest: list[str], project: Project, path: Path, plan: ShakePlan
) -> int:
    """Run Wrangler on the staged config in place of the project's."""
    if "--dry-run" in rest:
        # Flushed, so it comes before Wrangler's output in a pipe.
        print(f"wynd: {plan.size_report(path.parent / 'python_modules')}", flush=True)

    return _run_wrangler(args, path, project)


def _run_wrangler(args: list[str], config: Path, project: Project) -> int:
    """Run Wrangler on `config` in place of the user's config flag."""
    parser, environ = _config_parser, None
    if project.python_config:
        parser = _mode_parser
        environ = {k: v for k, v in os.environ.items() if k != "CLOUDFLARE_ENV"}

    _, rest = parser.parse_known_args(args)
    return wrangler.run([*rest, "--config", str(config)], env=environ)
