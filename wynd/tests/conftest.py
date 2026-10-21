import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

LOG = """
import json, os, sys
with open(os.environ["WYND_TEST_LOG"], "a") as log:
    record = {"tool": TOOL, "args": sys.argv[1:], "cwd": os.getcwd()}
    log.write(json.dumps(record) + "\\n")
"""

FAKE_NPX = """
import signal
if os.environ.get("WYND_TEST_SIGNAL"):
    os.kill(os.getpid(), getattr(signal, "SIG" + os.environ["WYND_TEST_SIGNAL"]))
sys.exit(int(os.environ.get("WYND_TEST_EXIT", "0")))
"""

FAKE_UV = """
import shutil
from pathlib import Path
args = sys.argv[1:]
if args == ["--version"]:
    print(f"uv {os.environ.get('WYND_TEST_UV_VERSION', '0.12.19')} (abc 2026-10-01)")
elif os.environ.get("WYND_TEST_UV_FAIL") in args:
    print("uv: no solution found", file=sys.stderr)
    sys.exit(2)
elif args[:2] == ["pip", "compile"]:
    Path(args[args.index("-o") + 1]).write_text(os.environ.get("WYND_TEST_PYLOCK", ""))
elif args[:2] == ["pip", "install"]:
    target = Path(args[args.index("--target") + 1])
    shutil.copytree(os.environ["WYND_TEST_SITE_PACKAGES"], target, dirs_exist_ok=True)
    (target / "bin").mkdir()
    (target / "bin" / "normalizer").write_text("#!/host/python")
    (target / ".lock").write_text("")
"""


@dataclass
class Tools:
    log: Path
    site_packages: Path

    def calls(self, tool: str) -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        records = [json.loads(line) for line in self.log.read_text().splitlines()]
        return [record for record in records if record["tool"] == tool]

    def uv(self, *prefix: str) -> list[list[str]]:
        """Arguments of each uv call that starts with `prefix`."""
        return [
            call["args"]
            for call in self.calls("uv")
            if call["args"][: len(prefix)] == list(prefix)
        ]

    def wrangler(self) -> list[list[str]]:
        """Arguments of each Wrangler run, after `npx --yes wrangler@<pin>`."""
        return [call["args"][2:] for call in self.calls("npx")]


@pytest.fixture
def tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Tools:
    """Fake `npx` and `uv` on PATH, the process boundary Wynd talks to."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool, body in [("npx", FAKE_NPX), ("uv", FAKE_UV)]:
        script = bin_dir / tool
        script.write_text(f"#!{sys.executable}\nTOOL = {tool!r}\n{LOG}{body}")
        script.chmod(0o755)
    site_packages = tmp_path / "site-packages"
    (site_packages / "workers").mkdir(parents=True)
    (site_packages / "workers" / "__init__.py").write_text("")
    log = tmp_path / "tools.log"
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("WYND_TEST_LOG", str(log))
    monkeypatch.setenv("WYND_TEST_SITE_PACKAGES", str(site_packages))
    return Tools(log, site_packages)
