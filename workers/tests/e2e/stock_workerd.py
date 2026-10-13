"""Run the stock SDK's workerd tests unchanged against crosswind.

This mirrors the stock runner (tests/test_in_workerd.py at cb2db3c): it copies
one test directory, installs its packages, embeds python_modules/ and tests/
into the .wd-test config, and runs `workerd test` twice, saving a snapshot and
then loading it. The only changes are that the project declares crosswind
instead of workers-runtime-sdk and that Wynd, not pywrangler, installs it.
"""

import json
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
from devserver import CROSSWIND, WYND

# The stock SDK checkout, at cb2db3c, beside this repository by default.
STOCK_SDK = Path(
    os.environ.get(
        "STOCK_SDK", CROSSWIND.parents[1] / "workers-py" / "packages" / "runtime-sdk"
    )
)
WORKERD_TESTS = STOCK_SDK / "tests" / "workerd-test"
TESTLIB = STOCK_SDK.parent / "testlib"

# The stock suite's compatibility settings (testlib/host.py at cb2db3c).
STOCK_CONFIGS = pytest.mark.parametrize(
    ("date", "flags"),
    [
        pytest.param(
            "2025-09-01",
            [
                "enable_python_external_sdk",
                "python_process_pth_files",
                "python_request_headers_preserve_commas",
            ],
            id="3.12",
        ),
        pytest.param(
            "2026-01-01",
            [
                "enable_python_external_sdk",
                "python_process_pth_files",
                "python_request_headers_preserve_commas",
            ],
            id="3.13",
        ),
        pytest.param("2026-07-01", ["python_workers_314", "experimental"], id="3.14"),
    ],
)


def run_stock_test(target: Path, name: str, date: str, flags: list[str]) -> None:
    """Run the stock workerd test `name` in `target`. Raises if it fails."""
    if not WORKERD_TESTS.is_dir():
        pytest.skip(f"no stock SDK checkout at {STOCK_SDK}; set STOCK_SDK")
    source = WORKERD_TESTS / name
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(".venv"))
    _declare_crosswind(target / "pyproject.toml")
    (wd_test,) = target.glob("*.wd-test")
    for config in (target / "wrangler.jsonc", wd_test):
        text = config.read_text().replace("%COMPAT_DATE", date)
        for flag in flags:
            text = text.replace('"python_workers"', f'"python_workers", "{flag}"')
        config.write_text(text)
    subprocess.run(
        ["uv", "run", "--project", WYND, "wynd", "sync"], cwd=target, check=True
    )
    modules = _embed(target / "python_modules", target) + _embed(
        target / "tests", target
    )
    wd_test.write_text(
        wd_test.read_text()
        .replace("%PYTHON_MODULES", ",\n".join(modules) + ",\n")
        .replace("%COLOR", "false")
    )
    disk = target / "TEST_TMPDIR"
    disk.mkdir(exist_ok=True)
    workerd = [
        _workerd(),
        "test",
        wd_test.name,
        "--experimental",
        "--python-snapshot-dir",
        ".",
        f"-dTEST_TMPDIR={disk}",
        "--pyodide-bundle-disk-cache-dir",
        str(target.parent / "bundle-cache"),
    ]
    subprocess.run([*workerd, "--python-save-snapshot"], cwd=target, check=True)
    subprocess.run(
        [*workerd, "--python-load-snapshot", "snapshot.bin"], cwd=target, check=True
    )


def _declare_crosswind(pyproject: Path) -> None:
    project = tomllib.loads(pyproject.read_text())["project"]
    dependencies = [
        dependency
        for dependency in project.get("dependencies", [])
        if dependency != "workers-runtime-sdk"
    ]
    names = ", ".join(json.dumps(d) for d in ["crosswind-workers", *dependencies])
    sources = f'crosswind-workers = {{ path = "{CROSSWIND}" }}\n'
    if "testlib" in dependencies:
        sources += f'testlib = {{ path = "{TESTLIB}" }}\n'
    pyproject.write_text(
        f'[project]\nname = "{project["name"]}"\nversion = "0"\n'
        f"dependencies = [{names}]\n[tool.uv.sources]\n{sources}"
    )


def _embed(directory: Path, root: Path) -> list[str]:
    """The .wd-test module entries for every file under `directory`."""
    modules = []
    for path in sorted(directory.glob("**/*")):
        if path.is_dir():
            continue
        name = path.relative_to(directory.parent)
        kind = "pythonModule" if path.suffix == ".py" else "data"
        modules.append(f'(name = "{name}", {kind} = embed "{path.relative_to(root)}")')
    return modules


def _workerd() -> str:
    """The workerd binary that ships with Wynd's pinned Wrangler."""
    pin = re.search(
        r'VERSION = "([^"]+)"', (WYND / "src" / "wynd" / "wrangler.py").read_text()
    )
    assert pin is not None
    version = pin.group(1)
    subprocess.run(
        ["npx", "--yes", f"wrangler@{version}", "--version"],
        check=True,
        capture_output=True,
    )
    for package in Path.home().glob(".npm/_npx/*/node_modules/wrangler/package.json"):
        if json.loads(package.read_text())["version"] == version:
            return str(package.parents[1] / "workerd" / "bin" / "workerd")
    raise FileNotFoundError(f"no workerd for wrangler {version} in the npx cache")
