"""Run the stock SDK's in-Worker test suites unchanged under `wynd dev`.

Each project serves testlib's runner: GET /run-tests/<suite> runs
src/test_<suite>.py with pytest inside the Worker and answers with each test's
result, as stock's testlib.host reads it at cb2db3c.
"""

import json
from pathlib import Path

import pytest
from devserver import wynd_dev
from stock_workerd import STOCK_CONFIGS, STOCK_SDK, prepare

pytestmark = pytest.mark.e2e

TESTS = STOCK_SDK / "tests"
# Each project, and whether stock runs it on 3.12. Stock skips async suites
# there, because Pyodide 0.26.0a2 can't run them and reports false passes.
PROJECTS = {
    "bindings-test": True,
    "flask-tests": True,
    "fastapi-tests": False,
    "django-async": False,
}
# Stock runs these only with -m hyperdrive, against local databases.
OPT_IN = {"hyperdrive_mysql", "hyperdrive_postgresql"}


@STOCK_CONFIGS
@pytest.mark.parametrize("project", PROJECTS)
def test_stock_in_worker_suites(
    tmp_path: Path, project: str, date: str, flags: list[str]
) -> None:
    if not PROJECTS[project] and date < "2026-01-01":
        pytest.skip("stock runs async suites only on 3.13 and later")
    source = TESTS / project
    if not source.is_dir():
        source = TESTS / "web-frameworks-test" / project
    target = prepare(source, tmp_path / project, date, flags)
    suites = sorted(path.stem[len("test_") :] for path in target.glob("src/test_*.py"))

    failures = {}
    ran = 0
    with wynd_dev(target) as server:
        server.get("/health")
        for suite in suites:
            if suite in OPT_IN:
                continue
            # As stock's client, requests, asks. FastAPI's suite answers
            # through GZipMiddleware.
            _, body = server.request(
                "GET",
                f"/run-tests/{suite}",
                headers={"Accept-Encoding": "gzip"},
                timeout=300,
                check=False,
            )
            results = json.loads(body)
            ran += len(results)
            for key, result in results.items():
                if result["status"] in ("failed", "error"):
                    failures[f"{suite}::{key}"] = (
                        f"{result.get('error', '')}\n{result.get('traceback', '')}"
                    )

    assert ran, "no in-Worker test reported a result"
    print(f"{project}: {ran} in-Worker tests")
    assert not failures, "\n\n".join(f"{k}\n{v}" for k, v in failures.items())
