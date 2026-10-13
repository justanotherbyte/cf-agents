"""Run a fixture Worker under `wynd dev` and talk to it over HTTP."""

import gzip
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
from contextlib import contextmanager, suppress
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


def make_project(
    project: Path,
    fixture: Path,
    config: dict[str, Any],
    dependencies: tuple[str, ...] = (),
) -> None:
    """Copy `fixture` into `project` with crosswind from this checkout."""
    shutil.copytree(fixture, project, dirs_exist_ok=True)
    names = ", ".join(f'"{name}"' for name in ("crosswind-workers", *dependencies))
    (project / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "0"\n'
        f"dependencies = [{names}]\n"
        f'[tool.uv.sources]\ncrosswind-workers = {{ path = "{CROSSWIND}" }}\n'
    )
    config = {"name": "fixture", "main": "src/entry.py", **config}
    (project / "wrangler.json").write_text(json.dumps(config))


@dataclass
class DevServer:
    url: str
    process: subprocess.Popen[bytes]
    log: Path

    def get(self, path: str, timeout: float = 30) -> tuple[int, str]:
        """GET `path`, waiting up to 3 minutes for the server to come up."""
        return self.request("GET", path, timeout=timeout)

    def request(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30,
        check: bool = True,
    ) -> tuple[int, str]:
        """Send a request, waiting up to 3 minutes for the server to come up.

        With `check`, an error status fails the test.
        """
        request = urllib.request.Request(
            self.url + path, body, headers or {}, method=method
        )
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                pytest.fail(f"wynd dev exited:\n{self.log.read_text()}")
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return response.status, _text(response)
            except urllib.error.HTTPError as error:
                answer = _text(error)
                if not check:
                    return error.code, answer
                pytest.fail(f"HTTP {error.code}: {answer}\n{self.log.read_text()}")
            except OSError:
                time.sleep(1)
        pytest.fail(f"wynd dev never answered:\n{self.log.read_text()}")


def _text(response: Any) -> str:
    # An app may compress its answer, as FastAPI's GZipMiddleware does.
    data = response.read()
    if response.headers.get("Content-Encoding") == "gzip":
        data = gzip.decompress(data)
    return data.decode()


@contextmanager
def wynd_dev(project: Path) -> Generator[DevServer]:
    with dev_server(
        project, ["uv", "run", "--project", str(WYND), "wynd", "dev"]
    ) as server:
        yield server


@contextmanager
def dev_server(project: Path, command: list[str]) -> Generator[DevServer]:
    """Run a dev server `command` in `project`, with --port appended."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    log = project / "dev.log"
    with log.open("w") as out:
        process = subprocess.Popen(
            [*command, "--port", str(port)],
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
        # The group is gone already when the server exited on its own.
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        process.wait()
