import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "stock"
CROSSWIND = Path(__file__).parents[3] / "crosswind-workers"
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


def test_sync_drops_the_sdk_a_library_pulls_in(tmp_path: Path) -> None:
    library = tmp_path / "libdep"
    (library / "src" / "libdep").mkdir(parents=True)
    (library / "src" / "libdep" / "__init__.py").write_text("")
    (library / "pyproject.toml").write_text(
        '[project]\nname = "libdep"\nversion = "0"\n'
        'dependencies = ["workers-runtime-sdk"]\n'
        '[build-system]\nrequires = ["uv_build>=0.12.19,<0.13.0"]\n'
        'build-backend = "uv_build"\n'
    )
    project = tmp_path / "worker"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "fixture"\nversion = "0"\n'
        'dependencies = ["crosswind-workers", "libdep"]\n'
        "[tool.uv.sources]\n"
        f'crosswind-workers = {{ path = "{CROSSWIND}" }}\n'
        f'libdep = {{ path = "{library}" }}\n'
    )
    config = {
        "main": "src/entry.py",
        "compatibility_date": "2025-10-01",
        "compatibility_flags": ["python_workers"],
    }
    (project / "wrangler.json").write_text(json.dumps(config))

    subprocess.run([str(WYND), "sync"], cwd=project, check=True)

    lock = tomllib.loads((project / "pylock.toml").read_text())
    names = sorted(package["name"] for package in lock["packages"])
    assert names == ["crosswind-workers", "libdep"]
    installed = {path.name for path in (project / "python_modules").iterdir()}
    assert "libdep" in installed
    assert not any(name.startswith("workers_runtime_sdk") for name in installed)


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
