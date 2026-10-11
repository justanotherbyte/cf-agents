import json
import os
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "tracer"
CROSSWIND = Path(__file__).parents[2]
WYND = CROSSWIND.parent / "wynd"

pytestmark = pytest.mark.e2e


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.parametrize(
    ("date", "version"),
    [
        # Before 2025-08-14, so workerd calls on_fetch.
        ("2025-06-01", "3.12"),
        ("2025-10-01", "3.13"),
        ("2026-09-08", "3.14"),
    ],
)
def test_tracer_answers_http(tmp_path: Path, date: str, version: str) -> None:
    shutil.copytree(FIXTURE, tmp_path, dirs_exist_ok=True)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "tracer"\nversion = "0"\n'
        'dependencies = ["crosswind-workers"]\n'
        f'[tool.uv.sources]\ncrosswind-workers = {{ path = "{CROSSWIND}" }}\n'
    )
    config = {
        "name": "tracer",
        "main": "src/entry.py",
        "compatibility_date": date,
        "compatibility_flags": ["python_workers"],
    }
    (tmp_path / "wrangler.json").write_text(json.dumps(config))
    port = free_port()
    log = tmp_path / "dev.log"
    with log.open("w") as out:
        server = subprocess.Popen(
            ["uv", "run", "--project", WYND, "wynd", "dev", "--port", str(port)],
            cwd=tmp_path,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            # Its own process group, so one kill also stops Wrangler and workerd.
            start_new_session=True,
        )
    try:
        status, body = wait_for_response(f"http://127.0.0.1:{port}/", server, log)
    finally:
        os.killpg(server.pid, signal.SIGTERM)
        server.wait()

    assert status == 201
    py_version, method, sdk_file = body.split(" ")
    assert (py_version, method) == (version, "GET")
    assert "python_modules" in sdk_file, "workerd loaded its builtin workers SDK"


def wait_for_response(
    url: str, server: subprocess.Popen[bytes], log: Path
) -> tuple[int, str]:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if server.poll() is not None:
            pytest.fail(f"wynd dev exited:\n{log.read_text()}")
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as error:
            body = error.read().decode()
            pytest.fail(f"HTTP {error.code}: {body}\n{log.read_text()}")
        except OSError:
            time.sleep(1)
    pytest.fail(f"wynd dev never answered:\n{log.read_text()}")
