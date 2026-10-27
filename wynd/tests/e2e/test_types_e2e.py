import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_dev_e2e import CROSSWIND, serve

FIXTURE = Path(__file__).parent / "typed"
WYND = Path(sys.executable).parent / "wynd"

pytestmark = pytest.mark.e2e


def test_generated_env_types_and_runs_a_crosswind_worker(tmp_path: Path) -> None:
    shutil.copytree(FIXTURE, tmp_path, dirs_exist_ok=True)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "typed"\nversion = "0"\n'
        'dependencies = ["crosswind-workers"]\n'
        f'[tool.uv.sources]\ncrosswind-workers = {{ path = "{CROSSWIND}" }}\n'
    )
    config = {
        "name": "typed",
        "main": "src/entry.py",
        "compatibility_date": "2025-06-01",
        "compatibility_flags": ["python_workers"],
        "kv_namespaces": [{"binding": "CACHE", "id": "cache"}],
        "durable_objects": {"bindings": [{"name": "COUNTER", "class_name": "Counter"}]},
        "migrations": [{"tag": "v1", "new_sqlite_classes": ["Counter"]}],
        "vars": {"GREETING": "hi"},
    }
    (tmp_path / "wrangler.json").write_text(json.dumps(config))

    assert serve(tmp_path, [str(WYND), "dev"]) == "2 hi"

    checks = [
        ["uvx", "ty", "check", "src", "--python-version", "3.12"]
        + ["--extra-search-path", str(CROSSWIND / "src")],
        ["uvx", "ruff", "format", "--check", "src/env.py"],
        ["uvx", "ruff", "check", "--select", "I,E,W", "src/env.py"],
    ]
    for command in checks:
        result = subprocess.run(
            command, check=False, cwd=tmp_path, capture_output=True, text=True
        )
        assert result.returncode == 0, result.stdout + result.stderr

    # ty follows the types: a misspelled binding and RPC method both fail.
    (tmp_path / "src" / "bad.py").write_text(
        "from env import Env\n\n\n"
        "async def bad(env: Env) -> None:\n"
        "    env.CACHEE\n"
        '    await env.COUNTER.get_by_name("x").incremnt(1)\n'
    )
    result = subprocess.run(
        checks[0], check=False, cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode == 1
    assert "CACHEE" in result.stdout and "incremnt" in result.stdout

    config["vars"]["ADDED"] = "x"
    (tmp_path / "wrangler.json").write_text(json.dumps(config))
    stale = subprocess.run([str(WYND), "types", "--check"], check=False, cwd=tmp_path)
    assert stale.returncode == 1
