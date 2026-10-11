"""Spike: a step callback that returns a thenable rejecting with an error named
NonRetryableError stops the Workflows engine's retries, with no JavaScript."""

import json
import time
from pathlib import Path

import pytest
from devserver import TARGETS, make_project, wynd_dev

pytestmark = pytest.mark.e2e


@TARGETS
def test_rejecting_thenable_stops_retries(
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
    # Only the error's name differs, and only NonRetryableError stops the retries.
    assert output["attempts"] == {"non_retryable": 1, "other_name": 3}, output
    # The engine rethrows the step error into run() with the name in the message.
    assert output["errors"] == {
        "non_retryable": "Error: NonRetryableError: stop",
        "other_name": "Error: OtherError: stop",
    }
