import signal
from pathlib import Path

import pytest

from wynd.cli import main

# Records its arguments one per line, then exits or kills itself as told.
FAKE_NPX = """#!/bin/sh
printf '%s\\n' "$@" > "$WYND_TEST_LOG"
[ -n "$WYND_TEST_SIGNAL" ] && kill -"$WYND_TEST_SIGNAL" $$
exit "${WYND_TEST_EXIT:-0}"
"""


@pytest.fixture
def npx_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    npx = bin_dir / "npx"
    npx.write_text(FAKE_NPX)
    npx.chmod(0o755)
    log = tmp_path / "npx.log"
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("WYND_TEST_LOG", str(log))
    return log


def test_forwards_arguments_unchanged_to_pinned_wrangler(npx_log: Path) -> None:
    args = ["kv", "namespace", "list", "-e", "staging", "--config=wrangler.toml"]

    assert main(args) == 0
    assert npx_log.read_text().splitlines() == ["--yes", "wrangler@4.129.0", *args]


def test_exits_with_wranglers_exit_code(
    npx_log: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WYND_TEST_EXIT", "3")

    assert main(["whoami"]) == 3


def test_reports_a_signal_death_like_a_shell(
    npx_log: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WYND_TEST_SIGNAL", "TERM")

    assert main(["whoami"]) == 128 + signal.SIGTERM


def test_missing_npx_names_nodejs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))

    assert main(["whoami"]) == 1
    assert "Node.js" in capsys.readouterr().err
