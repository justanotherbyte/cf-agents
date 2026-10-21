import json
from pathlib import Path

import pytest
from devserver import TARGETS, make_project, wynd_dev

pytestmark = pytest.mark.e2e


@TARGETS
def test_http_types_in_workerd(tmp_path: Path, date: str, version: str) -> None:
    make_project(
        tmp_path,
        Path(__file__).parent / "http",
        {
            "compatibility_date": date,
            "compatibility_flags": [
                "python_workers",
                "python_request_headers_preserve_commas",
            ],
        },
    )

    with wynd_dev(tmp_path) as server:
        _, body = server.get("/")

    result = json.loads(body)
    assert len(result["ran"]) == 21
    assert result["failures"] == {}, "\n".join(result["failures"].values())
