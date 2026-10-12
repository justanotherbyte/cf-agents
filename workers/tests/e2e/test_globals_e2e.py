import json
import time
from pathlib import Path

import pytest
from devserver import TARGETS, make_project, wynd_dev

pytestmark = pytest.mark.e2e


@TARGETS
def test_module_globals_in_workerd(tmp_path: Path, date: str, version: str) -> None:
    make_project(
        tmp_path,
        Path(__file__).parent / "globals",
        {
            "compatibility_date": date,
            "compatibility_flags": ["python_workers", "nodejs_compat"],
            "kv_namespaces": [{"binding": "KV", "id": "kv"}],
            "vars": {"TEXT": "hello"},
        },
    )

    with wynd_dev(tmp_path) as server:
        _, body = server.get("/")
        assert server.get("/defer")[1] == "scheduled"
        time.sleep(1.5)
        # Written 0.3 s after /defer had already responded.
        assert server.get("/deferred")[1] == "yes"

    result = json.loads(body)
    assert result["python"] == version
    assert result["top_text"] == "hello"
    assert result["sockets_connect"] is True
    assert result["module_env_kv"] is True
    assert result["patch_env"] is True
    assert result["module_wait_until_runs"] is True
    if version == "3.12":
        # Pyodide 0.26 has no run_sync, so only the preloaded modules import.
        no_jspi = "available until the next python runtime version"
        assert no_jspi in result["top_level_import"]
        assert no_jspi in result["handler_import"]
    else:
        assert result["top_level_import"] == "imported"
        assert result["handler_import"] is True
