import json
from pathlib import Path

import pytest
from devserver import TARGETS, make_project, wynd_dev

pytestmark = pytest.mark.e2e

BINDINGS = {
    "kv_namespaces": [{"binding": "KV", "id": "kv"}],
    "d1_databases": [
        {
            "binding": "DB",
            "database_name": "db",
            "database_id": "00000000-0000-0000-0000-000000000000",
        }
    ],
    "r2_buckets": [{"binding": "BUCKET", "bucket_name": "bucket"}],
    "queues": {"producers": [{"binding": "QUEUE", "queue": "queue"}]},
    "durable_objects": {"bindings": [{"name": "COUNTER", "class_name": "Counter"}]},
    "migrations": [{"tag": "v1", "new_sqlite_classes": ["Counter"]}],
    "services": [{"binding": "OTHER", "service": "fixture", "entrypoint": "Other"}],
    "workflows": [{"name": "flow", "binding": "FLOW", "class_name": "Flow"}],
    "version_metadata": {"binding": "VERSION"},
    "vars": {"TEXT": "hello", "FLAGS": {"beta": True}},
}


@TARGETS
def test_bindings_in_workerd(tmp_path: Path, date: str, version: str) -> None:
    make_project(
        tmp_path,
        Path(__file__).parent / "bindings",
        {
            "compatibility_date": date,
            "compatibility_flags": ["python_workers", "python_workflows"],
            **BINDINGS,
        },
    )

    with wynd_dev(tmp_path) as server:
        _, body = server.get("/")

    result = json.loads(body)
    assert len(result["ran"]) == 9
    assert result["failures"] == {}, "\n".join(result["failures"].values())
