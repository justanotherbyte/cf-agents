import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "stock"
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
        # Both are relative to the project, so they break from .wynd/build/
        # unless Wynd rewrites them.
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
