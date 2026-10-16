import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from test_dev_e2e import serve

from wynd.wrangler import VERSION

FIXTURE = Path(__file__).parent / "stock"
FASTAPI = Path(__file__).parent / "fastapi_worker"
WYND = Path(sys.executable).parent / "wynd"

pytestmark = pytest.mark.e2e


def test_deploy_dry_run_runs_from_the_staged_tree(tmp_path: Path) -> None:
    shutil.copytree(FIXTURE, tmp_path, dirs_exist_ok=True)
    (tmp_path / "public").mkdir()
    (tmp_path / "public" / "index.html").write_text("hi")
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    config = {
        "name": "stock",
        "main": "src/entry.py",
        "compatibility_date": "2025-06-01",
        "compatibility_flags": ["python_workers"],
        "assets": {"directory": "./public"},
        "containers": [{"class_name": "Box", "image": "./Dockerfile"}],
        "durable_objects": {"bindings": [{"name": "BOX", "class_name": "Box"}]},
        "migrations": [{"tag": "v1", "new_sqlite_classes": ["Box"]}],
    }
    (tmp_path / "wrangler.json").write_text(json.dumps(config))

    result = subprocess.run(
        [str(WYND), "deploy", "--dry-run", "--outdir", "out"],
        check=False,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"Read 1 file from the assets directory {tmp_path / 'public'}" in (
        result.stdout
    )
    assert f"({tmp_path / 'Dockerfile'})" in result.stdout
    assert (tmp_path / "out" / "entry.py").is_file()
    assert (tmp_path / "out" / "python_modules" / "workers").is_dir()
    assert "after pruning" in result.stdout


@pytest.mark.parametrize("date", ["2025-06-01", "2025-10-01", "2026-09-08"])
def test_a_pruned_fastapi_worker_still_answers(tmp_path: Path, date: str) -> None:
    shutil.copytree(FASTAPI, tmp_path, dirs_exist_ok=True)
    config = {
        "name": "fastapi",
        "main": "src/entry.py",
        "compatibility_date": date,
        "compatibility_flags": ["python_workers"],
    }
    (tmp_path / "wrangler.json").write_text(json.dumps(config))

    result = subprocess.run(
        [str(WYND), "deploy", "--dry-run"],
        check=False,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    match = re.search(
        r"python_modules is ([\d,]+) bytes, ([\d,]+) after", result.stdout
    )
    assert match, result.stdout
    before, after = (int(group.replace(",", "")) for group in match.groups())
    assert after < before
    rows = [
        line.split("\t")
        for line in (tmp_path / ".wynd" / "shake.txt").read_text().splitlines()
    ]
    assert all(len(row) == 3 and row[2] for row in rows)
    modules = tmp_path / ".wynd" / "build" / "python_modules"
    staged = {
        p.relative_to(modules).as_posix() for p in modules.rglob("*") if p.is_file()
    }
    assert staged == {name for decision, name, _ in rows if decision == "kept"}
    # FastAPI's CLI and AnyIO's pytest plugin load only through entry points.
    assert not staged & {"fastapi/cli.py", "anyio/pytest_plugin.py"}

    staged_config = ".wynd/build/wrangler.json"
    npx = shutil.which("npx") or "npx"
    body = serve(
        tmp_path, [npx, "--yes", f"wrangler@{VERSION}", "dev", "-c", staged_config]
    )

    assert json.loads(body) == {"name": "PRUNED"}
