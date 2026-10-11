import json
from pathlib import Path

import pytest
from devserver import TARGETS, make_project, wynd_dev

pytestmark = pytest.mark.e2e


@TARGETS
def test_values_round_trip_between_entrypoints(
    tmp_path: Path, date: str, version: str
) -> None:
    make_project(
        tmp_path,
        Path(__file__).parent / "rpc",
        {
            "compatibility_date": date,
            "compatibility_flags": ["python_workers"],
            "services": [
                {"binding": "OTHER", "service": "fixture", "entrypoint": "Rpc"}
            ],
        },
    )

    with wynd_dev(tmp_path) as server:
        _, body = server.get("/")

    assert json.loads(body) == []
