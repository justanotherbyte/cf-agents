"""The agents port's verify Worker passes under both toolchains: pywrangler
with the stock SDK, and Wynd with crosswind (decisions.md#agents-and-containers-ports).
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from devserver import CROSSWIND, dev_server, wynd_dev
from stock_workerd import STOCK_SDK

pytestmark = pytest.mark.e2e

AGENTS = Path(
    os.environ.get("AGENTS_PY", CROSSWIND.parents[2] / "agents-port" / "agents-py")
)
VERIFY = AGENTS / "verify"
# Run from source, as the stock suite's testlib runs it.
PYWRANGLER = [
    "uv",
    "run",
    "--no-project",
    "--with",
    str(STOCK_SDK.parent / "cli"),
    "pywrangler",
    "dev",
]
GENERATED = shutil.ignore_patterns(
    ".venv*",
    ".wrangler",
    "python_modules",
    "node_modules",
    "results",
    "*.lock",
    "pylock.toml",
    "chat-client",
    "mcp",
)


@pytest.mark.parametrize("toolchain", ["pywrangler", "wynd"])
def test_verify_worker_passes(tmp_path: Path, toolchain: str) -> None:
    if not VERIFY.is_dir():
        pytest.skip(f"no agents port at {AGENTS}; set AGENTS_PY")
    project = tmp_path / "verify"
    shutil.copytree(VERIFY, project, ignore=GENERATED)
    sdk = ""
    if toolchain == "wynd":
        sdk = f'crosswind-workers = {{ path = "{CROSSWIND}" }}\n'
    dependencies = '"httpx", "aiohttp", "cf-agents"'
    if toolchain == "wynd":
        dependencies += ', "crosswind-workers"'
    (project / "pyproject.toml").write_text(
        '[project]\nname = "verify"\nversion = "0"\nrequires-python = ">=3.13"\n'
        f"dependencies = [{dependencies}]\n"
        f'[tool.uv.sources]\ncf-agents = {{ path = "{AGENTS}", editable = false }}\n'
        + sdk
    )
    # The fetch-cancellation probes fetch from this server on port 8799.
    slow = subprocess.Popen(
        [sys.executable, str(VERIFY / "slow_server.py"), str(tmp_path / "slow.log")]
    )
    try:
        serve = (
            wynd_dev(project)
            if toolchain == "wynd"
            else dev_server(project, PYWRANGLER)
        )
        with serve as server:
            _, body = server.get("/run", timeout=300)
    finally:
        slow.terminate()
        slow.wait()

    results = json.loads(body)
    assert len(results) == 18
    failed = {r["name"]: r["detail"] for r in results if not r["ok"]}
    assert not failed, json.dumps(failed, indent=1)
