import json
from pathlib import Path
from typing import Any

import pytest
from conftest import Tools
from test_dev import config, make_project

from wynd.cli import main


def worker(
    root: Path,
    settings: dict[str, Any],
    dependencies: tuple[str, ...] = ("workers-runtime-sdk>=1.9",),
) -> Path:
    make_project(root, {**config(), **settings}, dependencies)
    (root / "src").mkdir(exist_ok=True)
    (root / "src" / "entry.py").write_text("from workers import Response\n")
    return root


def staged(root: Path) -> dict[str, Any]:
    return json.loads((root / ".wynd" / "build" / "wrangler.json").read_text())


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = worker(tmp_path / "worker", {})
    monkeypatch.chdir(root)
    return root


def test_deploy_stages_the_full_tree_and_runs_wrangler_on_it(
    tools: Tools, project: Path
) -> None:
    assert main(["deploy", "--dry-run"]) == 0

    build = project / ".wynd" / "build"
    assert (build / "src" / "entry.py").read_text() == "from workers import Response\n"
    assert (build / "python_modules" / "workers" / "__init__.py").is_file()
    assert staged(project)["main"] == "src/entry.py"
    assert tools.wrangler() == [
        ["deploy", "--dry-run", "--config", str(build / "wrangler.json")]
    ]


@pytest.mark.parametrize(
    "flag",
    [["-c", "other.jsonc"], ["--config", "other.jsonc"], ["--config=other.jsonc"]],
)
def test_the_staged_config_replaces_the_users_config_flag(
    tools: Tools, project: Path, flag: list[str]
) -> None:
    (project / "other.jsonc").write_text(
        json.dumps({**config(), "main": "src/entry.py", "name": "other"})
    )

    assert main([*flag, "deploy", "-e", "x"]) == 0

    build = project / ".wynd" / "build"
    assert tools.wrangler() == [
        ["deploy", "-e", "x", "--config", str(build / "wrangler.json")]
    ]
    assert staged(project)["name"] == "other"


def test_known_path_keys_point_back_at_the_project(
    tools: Tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "worker"
    root.mkdir()
    (root / "Dockerfile").write_text("FROM scratch\n")
    paths = {
        "assets": {"directory": "./public", "binding": "ASSETS"},
        "containers": [
            {"image": "./Dockerfile", "image_build_context": "ctx"},
            {"image": "docker.io/library/nginx:1"},
            {"image": "./missing/Dockerfile"},
        ],
        "d1_databases": [
            {"binding": "DB", "migrations_dir": "m", "migrations_pattern": "m/*.sql"}
        ],
        "tsconfig": "tsconfig.json",
        "build": {"command": "./build.sh", "cwd": "tools", "watch_dir": ["a", "/abs"]},
        "alias": {"fs": "./polyfills/fs", "path": "path-browserify"},
        "site": {"bucket": "./site", "entry-point": "workers-site"},
    }
    worker(
        root,
        {
            "$schema": "./node_modules/wrangler/config-schema.json",
            "base_dir": "src",
            "vars": {"WHERE": "./here"},
            **paths,
            "env": {"staging": {"assets": {"directory": "public2"}}},
            "previews": {"d1_databases": [{"binding": "DB", "migrations_dir": "pm"}]},
        },
    )
    monkeypatch.chdir(root)

    assert main(["deploy"]) == 0

    result = staged(root)
    assert result["main"] == "src/entry.py"
    assert result["base_dir"] == "src"
    assert result["$schema"] == "./node_modules/wrangler/config-schema.json"
    assert result["vars"] == {"WHERE": "./here"}
    assert result["assets"] == {"directory": str(root / "public"), "binding": "ASSETS"}
    assert result["containers"] == [
        {"image": str(root / "Dockerfile"), "image_build_context": str(root / "ctx")},
        {"image": "docker.io/library/nginx:1"},
        # Wrangler then reports the missing file by its absolute path.
        {"image": str(root / "missing" / "Dockerfile")},
    ]
    assert result["d1_databases"] == [
        {
            "binding": "DB",
            "migrations_dir": str(root / "m"),
            "migrations_pattern": str(root / "m/*.sql"),
        }
    ]
    assert result["tsconfig"] == str(root / "tsconfig.json")
    # build.cwd and build.command resolve from the process working directory.
    assert result["build"] == {
        "command": "./build.sh",
        "cwd": "tools",
        "watch_dir": [str(root / "a"), "/abs"],
    }
    assert result["alias"] == {
        "fs": str(root / "polyfills/fs"),
        "path": "path-browserify",
    }
    assert result["site"] == {
        "bucket": str(root / "site"),
        "entry-point": str(root / "workers-site"),
    }
    assert result["env"]["staging"]["assets"] == {"directory": str(root / "public2")}
    assert result["previews"]["d1_databases"][0]["migrations_dir"] == str(root / "pm")


def test_a_relative_path_under_an_unknown_key_fails_before_wrangler(
    tools: Tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = worker(tmp_path / "w", {"env": {"next": {"text_blobs": {"T": "./t.txt"}}}})
    monkeypatch.chdir(root)

    assert main(["deploy"]) == 1
    assert "env.next.text_blobs.T" in capsys.readouterr().err
    assert tools.wrangler() == []


def test_a_toml_config_is_staged_as_json(
    tools: Tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = worker(tmp_path / "w", {})
    (root / "wrangler.jsonc").unlink()
    (root / "wrangler.toml").write_text(
        'main = "src/entry.py"\ncompatibility_date = "2025-06-01"\n'
        'compatibility_flags = ["python_workers"]\n[assets]\ndirectory = "public"\n'
    )
    monkeypatch.chdir(root)

    assert main(["deploy"]) == 0
    assert staged(root)["assets"] == {"directory": str(root / "public")}


def test_a_new_deploy_wipes_the_previous_build(tools: Tools, project: Path) -> None:
    assert main(["deploy"]) == 0
    (project / "src" / "entry.py").rename(project / "src" / "main.py")
    (project / "wrangler.jsonc").write_text(
        json.dumps({**config(), "main": "src/main.py"})
    )
    assert main(["deploy"]) == 0

    assert sorted(p.name for p in (project / ".wynd" / "build" / "src").iterdir()) == [
        "main.py"
    ]


def test_an_entry_beside_the_config_fails(
    tools: Tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = worker(tmp_path / "w", {"main": "entry.py"})
    monkeypatch.chdir(root)

    assert main(["deploy"]) == 1
    assert "subdirectory" in capsys.readouterr().err
    assert tools.wrangler() == []


def test_a_source_directory_holding_the_project_fails(
    tools: Tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The config sits above pyproject.toml, so base_dir holds .wynd/ itself.
    child = worker(tmp_path / "child", {})
    (child / "wrangler.jsonc").unlink()
    settings = {**config(), "main": "child/src/entry.py", "base_dir": "child"}
    (tmp_path / "wrangler.json").write_text(json.dumps(settings))
    monkeypatch.chdir(child)

    assert main(["deploy"]) == 1
    assert "subdirectory" in capsys.readouterr().err
    assert tools.wrangler() == []


def test_an_entry_outside_base_dir_fails(
    tools: Tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = worker(tmp_path / "w", {"base_dir": "lib"})
    (root / "lib").mkdir()
    monkeypatch.chdir(root)

    assert main(["deploy"]) == 1
    assert "subdirectory" in capsys.readouterr().err
    assert tools.wrangler() == []


@pytest.mark.parametrize(
    "command",
    [
        ["versions", "upload", "--dry-run"],
        # A flag's value comes before the subcommand.
        ["versions", "--message", "m", "upload"],
        ["preview", "--name", "pr-1"],
    ],
)
def test_every_upload_command_runs_wrangler_on_the_staged_tree(
    tools: Tools, project: Path, command: list[str]
) -> None:
    assert main(["-c", "wrangler.jsonc", *command]) == 0

    build = project / ".wynd" / "build"
    assert (build / "python_modules" / "workers" / "__init__.py").is_file()
    assert tools.wrangler() == [[*command, "--config", str(build / "wrangler.json")]]


@pytest.mark.parametrize(
    "command",
    [
        ["versions", "list", "-c", "wrangler.jsonc"],
        ["versions", "deploy", "--message", "upload"],
        ["preview", "delete", "-c", "wrangler.jsonc"],
        ["preview", "--name", "pr-1", "delete"],
        ["preview", "secret", "put", "TOKEN"],
        ["preview", "settings"],
        ["preview", "base-config"],
    ],
)
def test_commands_that_upload_no_code_pass_through(
    tools: Tools, project: Path, command: list[str]
) -> None:
    assert main(command) == 0

    assert tools.wrangler() == [command]
    assert tools.uv() == []
    assert not (project / ".wynd" / "build").exists()


def test_a_main_path_with_dot_dot_still_names_the_entry_module(
    tools: Tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = worker(tmp_path / "w", {"main": "src/sub/../entry.py"})
    (root / "src" / "sub").mkdir()
    monkeypatch.chdir(root)

    assert main(["deploy", "--dry-run"]) == 0

    build = root / ".wynd" / "build"
    assert (build / "python_modules" / "workers" / "__init__.py").is_file()


def test_an_entry_module_the_shaker_cant_find_stops_the_build(
    tools: Tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(worker(tmp_path / "w", {"main": "src/missing.py"}))

    assert main(["deploy", "--dry-run"]) == 1

    assert "entry module missing" in capsys.readouterr().err
    assert tools.wrangler() == []
