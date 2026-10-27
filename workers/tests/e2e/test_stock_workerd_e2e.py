import os
from pathlib import Path

import pytest
from stock_workerd import STOCK_CONFIGS, WORKERD_TESTS, run_stock_test

pytestmark = pytest.mark.e2e

NAMES = sorted(path.parent.name for path in WORKERD_TESTS.glob("*/*.wd-test"))


@STOCK_CONFIGS
@pytest.mark.parametrize("name", NAMES)
def test_stock_workerd_suite(
    tmp_path: Path, name: str, date: str, flags: list[str]
) -> None:
    # The stock runner's own skips (tests/test_in_workerd.py at cb2db3c).
    if name == "wsgi" and date < "2026-01-01":
        pytest.skip("wsgi requires pyodide.ffi.run_sync, unavailable on 3.12")
    if name == "http-client" and "python_workers_314" not in flags:
        pytest.skip("HTTP client compatibility tests require Python 3.14")
    if (
        name == "entropy-patches"
        and "python_workers_314" in flags
        and "WORKERD_PATH" not in os.environ
    ):
        # The stock SDK fails the same way. workerd 2026-10-13 passes.
        pytest.xfail(
            "workerd 2026-09-03, from the pinned Wrangler, can't restore the "
            "snapshot: rpds's table base is off by one"
        )
    run_stock_test(tmp_path / name, name, date, flags)
