import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import jsonc

from .errors import WyndError
from .project import Project
from .shake import ShakePlan, shake

__all__ = ("StagedFile", "stage_run", "stage_upload")

# Files Wrangler reads beside its config for dev variables.
ENV_FILE_PREFIXES = (".dev.vars", ".env")


@dataclass(frozen=True)
class StagedFile:
    """A Wrangler config Wynd wrote, and the text it wrote there."""

    path: Path
    text: str

    def warn_if_edited(self, source: Path) -> None:
        """Name the keys Wrangler changed in the file, such as a binding that
        `d1 create` wrote, and `source` as the file to copy them into."""
        try:
            current = self.path.read_text()
        except FileNotFoundError:
            return
        if current == self.text:
            return

        before = json.loads(self.text)
        try:
            after = jsonc.loads(current)
        except ValueError:
            after = None
        keys = (
            [key for key in after if after[key] != before.get(key)]
            if isinstance(after, dict)
            else []
        )
        print(
            f"wynd: Wrangler changed {', '.join(keys) or 'the config'} in "
            f"{self.path}, which Wynd replaces on its next run. Copy the change "
            f"into {source.name}.",
            file=sys.stderr,
        )


def stage_run(project: Project) -> StagedFile:
    """Stage the config for dev and passthrough in the run directory.

    Mirrors the .dev.vars* and .env* files beside the config, and writes
    nothing that hasn't changed.
    """
    config_dir = project.source.path.parent
    path = project.run_dir / project.source.staged_name(project.mode)
    text = project.config.to_wrangler(path, code_root=config_dir)

    _mirror_env_files(config_dir, project.run_dir)
    _warn_on_old_state(config_dir, project.run_dir)
    return StagedFile(path, text)


def stage_upload(project: Project) -> tuple[StagedFile, ShakePlan]:
    """Shake and copy the Worker into .wynd/build/, with its config."""
    config_dir = project.source.path.parent
    source = _source_dir(project)

    plan = shake(
        project.python_modules,
        config_dir / source,
        project.config.main,
        project.keep_modules,
    )
    if plan.errors:
        raise WyndError("\n".join(plan.errors))

    build = project.wynd_dir / "build"
    shutil.rmtree(build, ignore_errors=True)
    shutil.copytree(config_dir / source, build / source)
    plan.copy_kept(build / "python_modules", docstrings=not project.keep_docstrings)
    (project.wynd_dir / "shake.txt").write_text(plan.log())

    path = build / "wrangler.json"
    return StagedFile(path, project.config.to_wrangler(path, code_root=build)), plan


def _source_dir(project: Project) -> Path:
    """The module root, relative to the config directory."""
    config = project.config
    config_dir = config.source.parent
    source = config.source_dir

    if (
        source == config_dir
        or not source.is_relative_to(config_dir)
        or not config.main.is_relative_to(source)
    ):
        raise WyndError(
            f"main and base_dir must point into a subdirectory beside "
            f"{config.source.name}, such as src/, so Wynd can stage the "
            "source without the rest of the project."
        )

    return source.relative_to(config_dir)


def _mirror_env_files(config_dir: Path, run_dir: Path) -> None:
    """Copy each changed env file into run_dir, and drop copies of gone ones."""
    sources = {
        path.name: path
        for path in config_dir.iterdir()
        if path.name.startswith(ENV_FILE_PREFIXES) and path.is_file()
    }
    for copied in run_dir.iterdir():
        if copied.name.startswith(ENV_FILE_PREFIXES) and copied.name not in sources:
            copied.unlink()

    for name, path in sources.items():
        copied = run_dir / name
        if not copied.is_file() or copied.read_bytes() != path.read_bytes():
            shutil.copyfile(path, copied)


def _warn_on_old_state(config_dir: Path, run_dir: Path) -> None:
    """Point at local data Wrangler kept beside the config before Wynd staged it."""
    old = config_dir / ".wrangler" / "state"
    new = run_dir / ".wrangler" / "state"
    if not old.is_dir() or new.exists():
        return

    print(
        f"wynd: local D1, KV and R2 data now lives in {new}. Move the contents "
        f"of {old} there to keep it.",
        file=sys.stderr,
    )
    # An empty state directory, so the warning shows once.
    new.mkdir(parents=True)
