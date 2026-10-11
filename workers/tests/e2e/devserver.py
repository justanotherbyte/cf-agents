"""Run a fixture Worker under `wynd dev` and talk to it over HTTP."""

import json
import os
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

CROSSWIND = Path(__file__).parents[2]
WYND = CROSSWIND.parent / "wynd"

# One compatibility date per Python target.
TARGETS = pytest.mark.parametrize(
    ("date", "version"),
    [
        # Before 2025-08-14, so workerd calls on_fetch.
        ("2025-06-01", "3.12"),
        ("2025-10-01", "3.13"),
        ("2026-09-08", "3.14"),
    ],
)


def make_project(project: Path, fixture: Path, config: dict[str, Any]) -> None:
    """Copy `fixture` into `project` with crosswind from this checkout."""
    shutil.copytree(fixture, project, dirs_exist_ok=True)
    (project / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "0"\n'
        'dependencies = ["crosswind-workers"]\n'
        f'[tool.uv.sources]\ncrosswind-workers = {{ path = "{CROSSWIND}" }}\n'
    )
    config = {"name": "fixture", "main": "src/entry.py", **config}
    (project / "wrangler.json").write_text(json.dumps(config))


@dataclass
class DevServer:
    url: str
    process: subprocess.Popen[bytes]
    log: Path

    def get(self, path: str) -> tuple[int, str]:
        """GET `path`, waiting up to 3 minutes for the server to come up."""
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                pytest.fail(f"wynd dev exited:\n{self.log.read_text()}")
            try:
                with urllib.request.urlopen(self.url + path, timeout=30) as response:
                    return response.status, response.read().decode()
            except urllib.error.HTTPError as error:
                body = error.read().decode()
                pytest.fail(f"HTTP {error.code}: {body}\n{self.log.read_text()}")
            except OSError:
                time.sleep(1)
        pytest.fail(f"wynd dev never answered:\n{self.log.read_text()}")


@contextmanager
def wynd_dev(project: Path) -> Generator[DevServer]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    log = project / "dev.log"
    with log.open("w") as out:
        process = subprocess.Popen(
            ["uv", "run", "--project", WYND, "wynd", "dev", "--port", str(port)],
            cwd=project,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            # Its own process group, so one kill also stops Wrangler and workerd.
            start_new_session=True,
        )
    try:
        yield DevServer(f"http://127.0.0.1:{port}", process, log)
    finally:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait()
