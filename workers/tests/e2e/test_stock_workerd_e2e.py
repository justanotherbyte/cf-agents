from pathlib import Path

import pytest
from stock_workerd import STOCK_CONFIGS, run_stock_test

pytestmark = pytest.mark.e2e


@STOCK_CONFIGS
@pytest.mark.parametrize("name", ["workflow"])
def test_stock_workerd_suite(
    tmp_path: Path, name: str, date: str, flags: list[str]
) -> None:
    run_stock_test(tmp_path / name, name, date, flags)
