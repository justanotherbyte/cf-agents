"""A NonRetryableError raised in a step stops the local Workflows engine's
retries, through crosswind's rejecting thenable and no JavaScript."""

import json
import time
from pathlib import Path

import pytest
from devserver import TARGETS, make_project, wynd_dev

pytestmark = pytest.mark.e2e


@TARGETS
def test_non_retryable_error_stops_retries(
    tmp_path: Path, date: str, version: str
) -> None:
    make_project(
        tmp_path,
        Path(__file__).parent / "workflow",
        {
            "compatibility_date": date,
            "compatibility_flags": ["python_workers", "python_workflows"],
            "workflows": [{"name": "flow", "binding": "FLOW", "class_name": "Flow"}],
        },
    )

    with wynd_dev(tmp_path) as server:
        _, instance_id = server.get("/start")
        deadline = time.monotonic() + 60
        while True:
            _, body = server.get(f"/status?id={instance_id}")
            status = json.loads(body)
            if status["status"] in ("complete", "errored"):
                break
            if time.monotonic() > deadline:
                pytest.fail(f"workflow still {status}:\n{server.log.read_text()}")
            time.sleep(1)

    assert status["status"] == "complete", status
    output = status["output"]
    assert output["attempts"] == {"non_retryable": 1, "retryable": 3}, output
    assert output["errors"]["non_retryable"] == ["NonRetryableError", "stop"]
    assert output["errors"]["retryable"][0] == "ValueError", output
    assert "again" in output["errors"]["retryable"][1], output
