import signal
from pathlib import Path

import pytest
from conftest import Tools

from wynd.cli import main


def test_forwards_arguments_unchanged_to_pinned_wrangler(tools: Tools) -> None:
    args = ["kv", "namespace", "list", "-e", "staging", "--config=wrangler.toml"]

    assert main(args) == 0
    assert [call["args"] for call in tools.calls("npx")] == [
        ["--yes", "wrangler@4.129.0", *args]
    ]
    assert tools.uv() == []


def test_exits_with_wranglers_exit_code(
    tools: Tools, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WYND_TEST_EXIT", "3")

    assert main(["whoami"]) == 3


def test_reports_a_signal_death_like_a_shell(
    tools: Tools, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WYND_TEST_SIGNAL", "TERM")

    assert main(["whoami"]) == 128 + signal.SIGTERM


def test_missing_npx_names_nodejs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))

    assert main(["whoami"]) == 1
    assert "Node.js" in capsys.readouterr().err
