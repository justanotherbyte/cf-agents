import json
from pathlib import Path

import pytest
from devserver import TARGETS, make_project, wynd_dev

pytestmark = pytest.mark.e2e


def config(date: str, *flags: str) -> dict:
    return {
        "compatibility_date": date,
        "compatibility_flags": ["python_workers", *flags],
        "vars": {"GREETING": "hi"},
    }


@TARGETS
def test_fastapi_app_answers(tmp_path: Path, date: str, version: str) -> None:
    # FastAPI 0.142 and later import opentelemetry, which draws entropy at import
    # time. workerd's own patches, used while this flag is off before 3.14, don't
    # cover it, so the flag hands patching to crosswind's .pth hook.
    flags = ["python_process_pth_files"] if version != "3.14" else []
    fixture = Path(__file__).parent / "asgi"
    make_project(tmp_path, fixture, config(date, *flags), ("fastapi",))

    with wynd_dev(tmp_path) as server:
        assert json.loads(server.get("/")[1]) == {"greeting": "hi"}
        assert json.loads(server.get("/env")[1]) == {"greeting": "hi"}
        _, created = server.request(
            "POST", "/items", b'{"name": "x"}', {"content-type": "application/json"}
        )
        assert json.loads(created) == {"name": "X"}
        assert server.get("/stream") == (200, "0\n1\n2\n")
        gzipped = server.request("GET", "/large", headers={"Accept-Encoding": "gzip"})
        assert gzipped == (200, "x" * 5000)
        assert json.loads(server.get("/sync")[1]) == {"sync": True}


@TARGETS
def test_flask_app_answers(tmp_path: Path, date: str, version: str) -> None:
    make_project(tmp_path, Path(__file__).parent / "wsgi", config(date), ("flask",))

    with wynd_dev(tmp_path) as server:
        assert json.loads(server.get("/")[1]) == {"greeting": "hi"}
        # 3.12 has no run_sync, so this body is read up front.
        _, echoed = server.request("POST", "/echo", b"body " * 1000)
        assert json.loads(echoed) == {"echo": "body " * 1000}
        assert server.get("/stream") == (200, "0\n1\n2\n")
