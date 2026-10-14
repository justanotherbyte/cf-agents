import json
import re
from pathlib import Path

from .project import CONFIG_NAMES, WyndError, load_project
from .targets import TARGETS
from .typegen import write_env

__all__ = ["scaffold"]

PYPROJECT = """\
[project]
name = "{name}"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["crosswind-workers"]
"""

MAIN = """\
from env import Env
from workers import Response, WorkerEntrypoint


class Default(WorkerEntrypoint[Env]):
    async def fetch(self, request):
        return Response("Hello from Python!")
"""

IGNORED = (".venv/", "python_modules/", ".wynd/")


def scaffold(directory: Path) -> None:
    """Write a new crosswind-workers project into `directory`."""
    name = re.sub(r"[^a-z0-9]+", "-", directory.resolve().name.lower()).strip("-")
    name = name or "worker"
    # The newest target's date, because the pinned Wrangler rejects later ones.
    target = TARGETS[0]
    config = {
        "name": name,
        "main": "src/main.py",
        "compatibility_date": str(target.since),
        "compatibility_flags": ["python_workers"],
    }
    files = {
        "pyproject.toml": PYPROJECT.format(name=name),
        # uv sync builds the editor's .venv on the target's Python.
        ".python-version": f"{target.version}\n",
        # Not wrangler.jsonc, which loses to a wrangler.json in any parent.
        "wrangler.json": json.dumps(config, indent=2) + "\n",
        "src/main.py": MAIN,
    }

    paths = [*files, *CONFIG_NAMES, "src/env.py"]
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
