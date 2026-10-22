import re
from importlib.metadata import version
from pathlib import Path

from .errors import WyndError
from .project import load_project
from .sources import PythonConfigParser, WranglerConfigParser
from .targets import TARGETS
from .typegen import write_env

__all__ = ("scaffold",)

# Wynd is a dev dependency, so editors resolve wynd.config. Sync compiles only
# [project] dependencies, so it never reaches the Worker.
PYPROJECT = """\
[project]
name = "{name}"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["crosswind-workers"]

[dependency-groups]
dev = ["wynd>={wynd}"]
"""

CONFIG = """\
from wynd.config import Config, Worker

config = Config(
    worker=Worker(
        name="{name}",
        entrypoint="src/main.py",
        compatibility_date="{date}",
    ),
)
"""

MAIN = """\
from env import Env
from workers import Response, WorkerEntrypoint


class Default(WorkerEntrypoint[Env]):
    async def fetch(self, request):
        return Response("Hello from Python!")
"""

IGNORED = (".venv/", ".wynd/")


def scaffold(directory: Path) -> None:
    """Write a new crosswind-workers project into `directory`."""
    name = re.sub(r"[^a-z0-9]+", "-", directory.resolve().name.lower()).strip("-")
    name = name or "worker"
    # The newest target's date, because the pinned Wrangler rejects later ones.
    target = TARGETS[0]
    files = {
        "pyproject.toml": PYPROJECT.format(name=name, wynd=version("wynd")),
        # uv sync builds the editor's .venv on the target's Python.
        ".python-version": f"{target.version}\n",
        PythonConfigParser.names[0]: CONFIG.format(name=name, date=target.since),
        "src/main.py": MAIN,
    }

    paths = [*files, *WranglerConfigParser.names, "src/env.py"]
    existing = [path for path in dict.fromkeys(paths) if (directory / path).exists()]
    if existing:
        raise WyndError(
            f"{directory} already has {', '.join(existing)}. "
            "wynd init only writes a new project."
        )

    for path, text in files.items():
        (directory / path).parent.mkdir(parents=True, exist_ok=True)
        (directory / path).write_text(text)
    _ignore(directory / ".gitignore")
    write_env(load_project(directory, None, None))
    print(f"wynd: created {name}. Run `wynd dev` in {directory} to serve it.")


def _ignore(path: Path) -> None:
    """Add the ignore lines a .gitignore lacks, keeping what it has."""
    text = path.read_text() if path.is_file() else ""
    missing = [line for line in IGNORED if line not in text.splitlines()]
    if not missing:
        return
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text + "".join(f"{line}\n" for line in missing))
