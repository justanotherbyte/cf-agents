import subprocess
import sys
from pathlib import Path

import pytest
from test_dev_e2e import CROSSWIND, serve

WYND = Path(sys.executable).parent / "wynd"

pytestmark = pytest.mark.e2e


def test_dev_serves_a_new_project(tmp_path: Path) -> None:
    subprocess.run([str(WYND), "init", "hello"], check=True, cwd=tmp_path)
    root = tmp_path / "hello"
    with (root / "pyproject.toml").open("a") as pyproject:
        pyproject.write(
            f'[tool.uv.sources]\ncrosswind-workers = {{ path = "{CROSSWIND}" }}\n'
        )

    assert serve(root, [str(WYND), "dev"]) == "Hello from Python!"
