import json
from pathlib import Path

import pytest
from conftest import Tools
from test_dev import config, make_project, run_dir, staged_path

from wynd.cli import main


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = make_project(tmp_path / "worker", config())
    monkeypatch.chdir(root)
    return root


def test_a_wrangler_file_reaches_wrangler_unchanged_but_for_its_paths(
    tools: Tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = {
        **config(),
        "name": "w",
        "migrations": [{"tag": "v1", "new_sqlite_classes": ["Counter"]}],
        "unsafe": {"bindings": [{"name": "X", "type": "y"}]},
        "rules": [{"type": "Data", "globs": ["**/*.txt"]}],
        "triggers": {"crons": []},
        "assets": {"directory": "./public"},
        "d1_databases": [{"binding": "DB", "database_name": "d", "database_id": "i"}],
        "env": {"staging": {"name": "w-staging", "assets": {"directory": "./staging"}}},
    }
    root = make_project(tmp_path / "w", raw)
    (root / ".dev.vars").write_text("API_KEY=1\n")
    monkeypatch.chdir(root)

    assert main(["d1", "migrations", "apply", "DB", "--local", "-e", "staging"]) == 0

    staged = run_dir(root) / "wrangler.jsonc.json"
    assert tools.wrangler() == [
        [
            "d1", "migrations", "apply", "DB", "--local", "-e", "staging",
            "--config", str(staged),
        ]
    ]  # fmt: skip
    assert json.loads(staged.read_text()) == {
        **raw,
        "main": "../../src/entry.py",
        "assets": {"directory": str(root / "public")},
        "d1_databases": [
            {**raw["d1_databases"][0], "migrations_dir": str(root / "migrations")}
        ],
        "env": {
            "staging": {
                "name": "w-staging",
                "assets": {"directory": str(root / "staging")},
            }
        },
    }
    assert (run_dir(root) / ".dev.vars").read_text() == "API_KEY=1\n"


def test_a_config_in_a_subdirectory_gets_its_own_run_directory(
    tools: Tools, project: Path
) -> None:
    (project / "other").mkdir()
    (project / "other" / "wrangler.jsonc").write_text(
        json.dumps({**config(), "main": "src/entry.py"})
    )

    assert main(["-c", "other/wrangler.jsonc", "whoami"]) == 0

    staged = json.loads(
        (run_dir(project) / "other" / "wrangler.jsonc.json").read_text()
    )
    assert staged["main"] == "../../../other/src/entry.py"


def test_env_files_beside_the_config_are_mirrored(tools: Tools, project: Path) -> None:
    (project / ".dev.vars").write_text("A=1\n")
    (project / ".env.staging").write_text("B=2\n")
    main(["whoami"])
    (project / ".dev.vars").write_text("A=2\n")
    (project / ".env.staging").unlink()

    assert main(["whoami"]) == 0

    assert (run_dir(project) / ".dev.vars").read_text() == "A=2\n"
    assert not (run_dir(project) / ".env.staging").exists()


def test_local_state_beside_the_config_is_named_once(
    tools: Tools, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (project / ".wrangler" / "state" / "v3").mkdir(parents=True)

    main(["whoami"])
    main(["whoami"])

    err = capsys.readouterr().err
    assert err.count("local D1, KV and R2 data now lives in") == 1
    assert str(run_dir(project) / ".wrangler" / "state") in err


def test_a_change_wrangler_writes_into_the_staged_file_is_named(
    tools: Tools,
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    main(["whoami"])
    staged = json.loads(Path(staged_path(project)).read_text())
    database = {"binding": "DB", "database_name": "db", "database_id": "1"}
    edited = json.dumps({**staged, "d1_databases": [database]})
    monkeypatch.setenv("WYND_TEST_EDIT", json.dumps(["--config", edited]))

    assert main(["d1", "create", "db"]) == 0

    err = capsys.readouterr().err
    assert f"Wrangler changed d1_databases in {staged_path(project)}" in err
    assert "Copy the change into wrangler.jsonc." in err


def test_dev_restages_when_the_users_config_changes(
    tools: Tools, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    edited = json.dumps({**config(), "main": "src/entry.py", "vars": {"V": "2"}})
    monkeypatch.setenv("WYND_TEST_EDIT", json.dumps(["wrangler.jsonc", edited]))
    monkeypatch.setenv("WYND_TEST_SLEEP", "2.5")

    assert main(["dev"]) == 0

    assert json.loads(Path(staged_path(project)).read_text())["vars"] == {"V": "2"}
