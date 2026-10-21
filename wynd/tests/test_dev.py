import json
from pathlib import Path
from typing import Any

import pytest
from conftest import Tools

from wynd.cli import main

PYLOCK_WITH_LOCAL_PACKAGE = """
[[packages]]
name = "mylib"
directory = { path = "../mylib" }

[[packages]]
name = "wheel-only"
archive = { path = "dist/wheel_only-1.0-py3-none-any.whl" }
"""


def make_project(
    root: Path,
    config: dict[str, Any] | None = None,
    dependencies: tuple[str, ...] = ("workers-runtime-sdk>=1.9",),
    tool_wynd: str = "",
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    deps = ", ".join(f'"{dep}"' for dep in dependencies)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "w"\ndependencies = [{deps}]\n{tool_wynd}'
    )
    if config is not None:
        config = {"main": "src/entry.py", **config}
        text = "// a JSONC comment\n" + json.dumps(config)
        (root / "wrangler.jsonc").write_text(text)
    return root


def config(date: str = "2025-06-01", *flags: str) -> dict[str, Any]:
    return {
        "compatibility_date": date,
        "compatibility_flags": ["python_workers", *flags],
    }


def interpreter(tools: Tools) -> str:
    (compile_args,) = tools.uv("pip", "compile")
    return compile_args[compile_args.index("--python") + 1]


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = make_project(tmp_path / "worker", config())
    monkeypatch.chdir(root)
    return root


def test_dev_installs_the_full_tree_then_runs_wrangler_dev(
    tools: Tools, project: Path
) -> None:
    assert main(["dev", "--port", "8788"]) == 0

    assert tools.wrangler() == [["dev", "--port", "8788"]]
    modules = project / "python_modules"
    assert (modules / "workers" / "__init__.py").is_file()
    # uv --target extras: console scripts with host paths, and its lock file.
    assert not (modules / "bin").exists()
    assert not (modules / ".lock").exists()
    assert (project / "pylock.toml").is_file()


def test_resolves_against_the_target_index_with_no_source_builds(
    tools: Tools, project: Path
) -> None:
    main(["dev"])

    (compile_args,) = tools.uv("pip", "compile")
    assert compile_args[compile_args.index("--extra-index-url") + 1] == (
        "https://index.pyodide.org/0.27.7"
    )
    assert "--no-build" in compile_args
    assert compile_args[compile_args.index("-o") + 1] == "pylock.toml"
    (install_args,) = tools.uv("pip", "install")
    assert install_args[install_args.index("--only-binary") + 1] == ":all:"
    assert install_args[install_args.index("--python") + 1] == (
        "cpython-3.12.7-emscripten-wasm32-musl"
    )


@pytest.mark.parametrize(
    ("date", "flags", "expected"),
    [
        ("2025-06-01", (), "cpython-3.12.7-emscripten-wasm32-musl"),
        ("2025-09-28", (), "cpython-3.12.7-emscripten-wasm32-musl"),
        ("2025-09-29", (), "cpython-3.13.2-emscripten-wasm32-musl"),
        (
            "2025-06-01",
            ("python_workers_20250116",),
            "cpython-3.13.2-emscripten-wasm32-musl",
        ),
        ("2026-09-08", (), "cpython-3.14.2-emscripten-wasm32-musl"),
        (
            "2025-06-01",
            ("python_workers_314",),
            "cpython-3.14.2-emscripten-wasm32-musl",
        ),
    ],
)
def test_target_follows_compatibility_date_and_flags(
    tools: Tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    date: str,
    flags: tuple[str, ...],
    expected: str,
) -> None:
    monkeypatch.chdir(make_project(tmp_path / "w", config(date, *flags)))

    assert main(["dev"]) == 0
    assert interpreter(tools) == expected


def test_configs_in_different_directories_sync_separately(
    tools: Tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_project(tmp_path / "w", config())
    (root / "other").mkdir()
    (root / "other" / "wrangler.jsonc").write_text(json.dumps(config()))
    monkeypatch.chdir(root)
    main(["dev"])
    main(["-c", "other/wrangler.jsonc", "dev"])
    (root / "pyproject.toml").write_text(
        '[project]\nname = "w"\ndependencies = ["workers-runtime-sdk", "attrs"]\n'
    )
    main(["dev"])
    main(["-c", "other/wrangler.jsonc", "dev"])

    assert len(tools.uv("pip", "compile")) == 4


@pytest.mark.parametrize(
    ("bad_config", "message"),
    [
        ({"compatibility_flags": ["python_workers"]}, "compatibility_date"),
        ({"compatibility_date": "2025-06-01"}, "python_workers"),
        (
            {"compatibility_date": "June", "compatibility_flags": ["python_workers"]},
            "June",
        ),
    ],
)
def test_a_config_without_a_target_fails_before_wrangler(
    tools: Tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    bad_config: dict[str, Any],
    message: str,
) -> None:
    monkeypatch.chdir(make_project(tmp_path / "w", bad_config))

    assert main(["dev"]) == 1
    assert message in capsys.readouterr().err
    assert tools.wrangler() == []


def test_a_second_dev_skips_sync(tools: Tools, project: Path) -> None:
    main(["dev"])
    main(["dev"])

    assert len(tools.uv("pip", "compile")) == 1
    assert len(tools.wrangler()) == 2


@pytest.mark.parametrize(
    "change",
    [
        lambda root: (root / "wrangler.jsonc").write_text(
            json.dumps(config("2026-09-08"))
        ),
        lambda root: (root / "pyproject.toml").write_text(
            '[project]\nname = "w"\ndependencies = ["workers-runtime-sdk", "attrs"]\n'
        ),
        lambda root: (root / "pylock.toml").write_text("# edited\n"),
        lambda root: __import__("shutil").rmtree(root / "python_modules"),
    ],
    ids=["date", "pyproject", "pylock", "python_modules"],
)
def test_sync_reruns_when_an_input_changes(
    tools: Tools, project: Path, change: Any
) -> None:
    main(["dev"])
    change(project)
    main(["dev"])

    assert len(tools.uv("pip", "compile")) == 2


def test_env_flag_chooses_the_environments_target(tools: Tools, project: Path) -> None:
    (project / "wrangler.jsonc").write_text(
        json.dumps({**config(), "env": {"next": {"compatibility_date": "2026-09-08"}}})
    )

    assert main(["dev", "--env=next"]) == 0
    assert interpreter(tools) == "cpython-3.14.2-emscripten-wasm32-musl"
    assert tools.wrangler() == [["dev", "--env=next"]]


def test_an_unknown_env_fails(
    tools: Tools, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (project / "wrangler.jsonc").write_text(
        json.dumps({**config(), "env": {"next": {}}})
    )

    assert main(["dev", "-e", "nope"]) == 1
    assert "nope" in capsys.readouterr().err


def test_an_env_without_env_tables_uses_the_top_level(
    tools: Tools, project: Path
) -> None:
    # Wrangler only warns here, and runs with the top-level settings.
    assert main(["dev", "-e", "staging"]) == 0
    assert interpreter(tools) == "cpython-3.12.7-emscripten-wasm32-musl"


def test_cloudflare_env_chooses_the_environment_like_wrangler(
    tools: Tools, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (project / "wrangler.jsonc").write_text(
        json.dumps({**config(), "env": {"next": {"compatibility_date": "2026-09-08"}}})
    )
    monkeypatch.setenv("CLOUDFLARE_ENV", "next")

    assert main(["dev"]) == 0
    assert interpreter(tools) == "cpython-3.14.2-emscripten-wasm32-musl"


def test_wrangler_value_flags_are_not_taken_for_the_command(
    tools: Tools, project: Path
) -> None:
    args = ["--env-file", ".env.x", "--profile", "p", "dev"]

    assert main(args) == 0
    assert tools.uv("pip", "compile") != []
    assert tools.wrangler() == [args]


def test_cwd_flag_moves_config_discovery(
    tools: Tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_project(tmp_path / "w", config("2026-09-08"))
    monkeypatch.chdir(tmp_path)

    assert main(["dev", "--cwd", "w"]) == 0
    assert interpreter(tools) == "cpython-3.14.2-emscripten-wasm32-musl"
    assert (tmp_path / "w" / "python_modules" / "workers").is_dir()


def test_config_flag_chooses_the_file(tools: Tools, project: Path) -> None:
    (project / "other.toml").write_text(
        'compatibility_date = "2026-09-08"\ncompatibility_flags = ["python_workers"]\n'
    )

    assert main(["-c", "other.toml", "dev"]) == 0
    assert interpreter(tools) == "cpython-3.14.2-emscripten-wasm32-musl"
    assert tools.wrangler() == [["-c", "other.toml", "dev"]]


def test_discovery_tries_each_name_up_the_tree_before_the_next(
    tools: Tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Wrangler finds the parent's wrangler.json before the cwd's wrangler.toml.
    parent = tmp_path / "parent"
    (parent / "child").mkdir(parents=True)
    (parent / "wrangler.json").write_text(json.dumps(config("2026-09-08")))
    child = make_project(parent / "child")
    (child / "wrangler.toml").write_text(
        'compatibility_date = "2025-06-01"\ncompatibility_flags = ["python_workers"]\n'
    )
    monkeypatch.chdir(child)

    assert main(["dev"]) == 0
    assert interpreter(tools) == "cpython-3.14.2-emscripten-wasm32-musl"
    # python_modules sits beside the config Wrangler reads.
    assert (parent / "python_modules" / "workers").is_dir()


@pytest.mark.parametrize(
    ("dependencies", "message"),
    [
        (("attrs",), "declares neither"),
        (("crosswind-workers", "Workers_Runtime.SDK==1.9"), "declares both"),
    ],
)
def test_the_project_must_declare_exactly_one_sdk(
    tools: Tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    dependencies: tuple[str, ...],
    message: str,
) -> None:
    monkeypatch.chdir(make_project(tmp_path / "w", config(), dependencies))

    assert main(["dev"]) == 1
    assert message in capsys.readouterr().err
    assert tools.wrangler() == []


def test_a_uv_failure_stops_before_wrangler(
    tools: Tools,
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    main(["sync"])
    (project / "pyproject.toml").write_text(
        '[project]\nname = "w"\ndependencies = ["workers-runtime-sdk", "nope"]\n'
    )
    monkeypatch.setenv("WYND_TEST_UV_FAIL", "install")

    assert main(["dev"]) == 1
    assert "no solution found" in capsys.readouterr().err
    assert tools.wrangler() == []
    assert (project / "python_modules" / "workers").is_dir()


def test_an_old_uv_stops_before_any_uv_work(
    tools: Tools,
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("WYND_TEST_UV_VERSION", "0.12.2")

    assert main(["dev"]) == 1
    assert "0.12.3" in capsys.readouterr().err
    assert tools.uv() == [["--version"]]
    assert tools.wrangler() == []


def test_local_packages_are_built_and_refreshed(
    tools: Tools, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WYND_TEST_PYLOCK", PYLOCK_WITH_LOCAL_PACKAGE)

    main(["dev"])

    (install_args,) = tools.uv("pip", "install")
    assert install_args[install_args.index("--no-binary") + 1] == "mylib"
    assert install_args.count("--no-binary") == 1
    refreshed = [
        a
        for i, a in enumerate(install_args)
        if install_args[i - 1] == "--refresh-package"
    ]
    assert refreshed == ["mylib", "wheel-only"]


def test_allow_build_permits_source_builds(
    tools: Tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_project(
        tmp_path / "w", config(), tool_wynd="[tool.wynd]\nallow-build = true\n"
    )
    monkeypatch.chdir(root)

    main(["dev"])

    (compile_args,) = tools.uv("pip", "compile")
    (install_args,) = tools.uv("pip", "install")
    assert "--no-build" not in compile_args
    assert "--only-binary" not in install_args


def test_warns_when_the_venv_python_differs_from_the_target(
    tools: Tools, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (project / ".venv").mkdir()
    (project / ".venv" / "pyvenv.cfg").write_text("home = /x\nversion_info = 3.13.5\n")

    main(["dev"])

    assert capsys.readouterr().err == (
        "warning: .venv is Python 3.13 but wrangler.jsonc targets 3.12"
        " (python_workers, 2025-06-01).\n"
        "Editor types may differ from production."
        " To match: uv venv --python 3.12 && uv sync\n"
    )


def test_sync_installs_without_running_wrangler(tools: Tools, project: Path) -> None:
    assert main(["sync"]) == 0

    assert (project / "python_modules" / "workers").is_dir()
    assert tools.wrangler() == []


def test_a_resync_replaces_python_modules(tools: Tools, project: Path) -> None:
    main(["dev"])
    (project / "python_modules" / "dropped.py").write_text("")
    (project / "wrangler.jsonc").write_text(json.dumps(config("2026-09-08")))
    main(["dev"])

    pythons = [args[args.index("--python") + 1] for args in tools.uv("pip", "install")]
    assert pythons == [
        "cpython-3.12.7-emscripten-wasm32-musl",
        "cpython-3.14.2-emscripten-wasm32-musl",
    ]
    assert not (project / "python_modules" / "dropped.py").exists()
    assert (project / "python_modules" / "workers").is_dir()


@pytest.mark.parametrize(
    ("declared", "excluded"),
    [
        ("crosswind-workers", "workers-runtime-sdk"),
        ("workers-runtime-sdk", "crosswind-workers"),
    ],
)
def test_resolution_excludes_the_sdk_the_project_did_not_choose(
    tools: Tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    declared: str,
    excluded: str,
) -> None:
    root = make_project(tmp_path / "w", config(), (declared, "some-library"))
    monkeypatch.chdir(root)

    main(["sync"])

    (compile_args,) = tools.uv("pip", "compile")
    excludes = root / compile_args[compile_args.index("--excludes") + 1]
    assert excludes.read_text().split() == [excluded]


def test_a_bare_toml_date_chooses_the_target(
    tools: Tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = make_project(tmp_path / "toml")
    (root / "wrangler.toml").write_text(
        'main = "src/entry.py"\n'
        "compatibility_date = 2025-10-01\n"
        'compatibility_flags = ["python_workers"]\n'
    )
    monkeypatch.chdir(root)

    assert main(["sync"]) == 0

    assert interpreter(tools) == "cpython-3.13.2-emscripten-wasm32-musl"


def test_a_local_source_is_reinstalled_on_every_sync(
    tools: Tools, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WYND_TEST_PYLOCK", PYLOCK_WITH_LOCAL_PACKAGE)

    main(["sync"])
    main(["sync"])

    assert len(tools.uv("pip", "install")) == 2


@pytest.mark.parametrize(
    "flags",
    [["--compatibility-date", "2026-09-08"], ["--compatibility-flags=x"], ["--latest"]],
)
@pytest.mark.parametrize("command", ["dev", "deploy"])
def test_flags_that_change_the_target_are_refused(
    tools: Tools,
    project: Path,
    capsys: pytest.CaptureFixture[str],
    command: str,
    flags: list[str],
) -> None:
    assert main([command, *flags]) == 1

    assert "compatibility_date" in capsys.readouterr().err
    assert tools.uv() == []
    assert tools.wrangler() == []
