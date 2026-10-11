from pathlib import Path

import pytest
from devserver import TARGETS, make_project, wynd_dev

pytestmark = pytest.mark.e2e


@TARGETS
def test_tracer_answers_http(tmp_path: Path, date: str, version: str) -> None:
    make_project(
        tmp_path,
        Path(__file__).parent / "tracer",
        {"compatibility_date": date, "compatibility_flags": ["python_workers"]},
    )

    with wynd_dev(tmp_path) as server:
        status, body = server.get("/")

    assert status == 201
    py_version, method, sdk_file = body.split(" ")
    assert (py_version, method) == (version, "GET")
    assert "python_modules" in sdk_file, "workerd loaded its builtin workers SDK"
