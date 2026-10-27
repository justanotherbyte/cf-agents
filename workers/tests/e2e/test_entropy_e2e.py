from pathlib import Path

import pytest
from devserver import TARGETS, make_project, wynd_dev

pytestmark = pytest.mark.e2e


@TARGETS
def test_numpy_and_pydantic_import_at_top_level(
    tmp_path: Path, date: str, version: str
) -> None:
    make_project(
        tmp_path,
        Path(__file__).parent / "entropy",
        {"compatibility_date": date, "compatibility_flags": ["python_workers"]},
        ("numpy", "pydantic"),
    )

    with wynd_dev(tmp_path) as server:
        _, body = server.get("/")

    name, value = body.split(" ")
    assert name == "ok"
    assert 0 <= float(value) < 1
