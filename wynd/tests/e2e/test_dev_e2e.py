import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "stock"
WYND = Path(sys.executable).parent / "wynd"

pytestmark = pytest.mark.e2e


@pytest.mark.parametrize(
    ("date", "version"),
    [
        # Before 2025-08-14, so workerd calls on_fetch.
        ("2025-06-01", "3.12"),
        ("2025-10-01", "3.13"),
        ("2026-09-08", "3.14"),
    ],
)
def test_dev_serves_a_stock_sdk_worker(tmp_path: Path, date: str, version: str) -> None:
    shutil.copytree(FIXTURE, tmp_path, dirs_exist_ok=True)
    config = {
        "name": "stock",
        "main": "src/entry.py",
        "compatibility_date": date,
        "compatibility_flags": ["python_workers"],
    }
    (tmp_path / "wrangler.json").write_text(json.dumps(config))

    body = serve(tmp_path, [str(WYND), "dev"])

    py_version, sdk_file = body.split(" ")
    assert py_version == version
    assert "python_modules" in sdk_file, "workerd loaded its builtin workers SDK"
    assert (tmp_path / "pylock.toml").is_file()


def serve(project: Path, command: list[str]) -> str:
    """Run a dev server command in `project` and return its answer to GET /."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    log = project / "dev.log"
    with log.open("w") as out:
        server = subprocess.Popen(
            [*command, "--port", str(port)],
            cwd=project,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            # Its own process group, so one kill also stops Wrangler and workerd.
            start_new_session=True,
        )
    try:
        return wait_for_response(f"http://127.0.0.1:{port}/", server, log)
    finally:
        os.killpg(server.pid, signal.SIGTERM)
        server.wait()


def wait_for_response(url: str, server: subprocess.Popen[bytes], log: Path) -> str:
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        if server.poll() is not None:
            pytest.fail(f"dev server exited:\n{log.read_text()}")
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                return response.read().decode()
        except urllib.error.HTTPError as error:
            body = error.read().decode()
            pytest.fail(f"HTTP {error.code}: {body}\n{log.read_text()}")
        except OSError:
            time.sleep(1)
    pytest.fail(f"dev server never answered:\n{log.read_text()}")
