import json
from pathlib import Path

import pytest
from devserver import TARGETS, make_project, wynd_dev

pytestmark = pytest.mark.e2e


@TARGETS
def test_durable_objects_and_context_in_workerd(
    tmp_path: Path, date: str, version: str
) -> None:
    make_project(
        tmp_path,
        Path(__file__).parent / "durable",
        {
            "compatibility_date": date,
            "compatibility_flags": ["python_workers"],
            "kv_namespaces": [{"binding": "KV", "id": "kv"}],
            "durable_objects": {"bindings": [{"name": "ROOM", "class_name": "Room"}]},
            "migrations": [{"tag": "v1", "new_sqlite_classes": ["Room"]}],
        },
    )

    with wynd_dev(tmp_path) as server:
        _, body = server.get("/")

    result = json.loads(body)
    assert len(result["ran"]) == 8
    assert result["failures"] == {}, "\n".join(result["failures"].values())
