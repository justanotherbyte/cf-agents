import os
import subprocess
import sys
from pathlib import Path


def import_workers(*paths: Path) -> subprocess.CompletedProcess[str]:
    """Import workers in a fresh Python with `paths` added to sys.path."""
    path = os.pathsep.join(map(str, [Path(__file__).parent, *paths]))
    return subprocess.run(
        [sys.executable, "-c", "import fake_js; fake_js.install(); import workers"],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": path},
    )


def test_importing_beside_the_stock_sdk_names_both_and_wynd(tmp_path: Path) -> None:
    dist_info = tmp_path / "workers_runtime_sdk-1.9.3.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: workers-runtime-sdk\nVersion: 1.9.3\n"
    )

    result = import_workers(tmp_path)

    assert result.returncode == 1
    error = result.stderr.splitlines()[-1]
    assert error.startswith("ImportError: ")
    assert "workers-runtime-sdk" in error
    assert "crosswind-workers" in error
    assert "Wynd" in error
