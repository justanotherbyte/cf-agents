import json
import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import Tools
from test_dev import make_project

from wynd.cli import main
from wynd.config import (
    Assets,
    Config,
    Container,
    Rule,
    Worker,
    bindings,
    exports,
    triggers,
)
from wynd.config._compile import to_wrangler
from wynd.errors import WyndError

CONFIG = """\
from wynd.config import Config, Worker, bindings, exports


def config(mode):
    suffix = "-staging" if mode == "staging" else ""
    return Config(
        worker=Worker(
            name=f"w{suffix}",
            entrypoint="src/entry.py",
            compatibility_date="2025-06-01",
            env={
                "DATABASE": bindings.D1(name=f"db{suffix}", id="1"),
                "COUNTER": bindings.DurableObject("Counter"),
                "DEBUG": mode == "staging" and bindings.Text("1"),
            },
            exports={"Counter": exports.DurableObject()},
        ),
    )
"""


def python_project(root: Path, text: str = CONFIG) -> Path:
    make_project(root, dependencies=("crosswind-workers",))
    (root / "src").mkdir()
    (root / "src" / "entry.py").write_text("from workers import Response\n")
    (root / "cloudflare.config.py").write_text(text)
    return root


WORKER = Worker(name="w", compatibility_date="2025-06-01")


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = python_project(tmp_path / "worker")
    monkeypatch.chdir(root)
    return root


def test_a_config_compiles_to_the_wrangler_dict() -> None:
    image = Container(name="image", dockerfile="./Dockerfile", max_instances=3)
    config = Config(
        account_id="abc",
        worker=Worker(
            name="w",
            entrypoint="src/main.py",
            compatibility_date="2026-09-08",
            compatibility_flags=["python_workers", "nodejs_compat"],
            assets=Assets(directory="./public", not_found_handling="404-page"),
            domains=["images.example.com"],
            rules=[Rule(type="Data", globs=["**/*.onnx"], fallthrough=True)],
            env={
                "GREETING": bindings.Text("hello"),
                "FEATURES": bindings.Json({"resize": True}),
                "API_KEY": bindings.Secret(),
                "DATABASE": bindings.D1(name="db"),
                "CACHE": bindings.KV(id="k"),
                "UPLOADS": bindings.R2(name="uploads", jurisdiction="eu"),
                "JOBS": bindings.Queue(name="jobs"),
                "IMAGES": bindings.DurableObject("ImageProcessor"),
                "COUNTERS": bindings.DurableObject("Counter", worker="metrics"),
                "THUMBNAILS": bindings.Workflow("ThumbnailFlow"),
                "AUTH": bindings.Service("auth", export="Sessions"),
                "AI": bindings.AI(),
                "STATIC": bindings.Assets(),
                "OFF": None,
            },
            triggers=[
                triggers.Cron("0 * * * *"),
                triggers.Queue("jobs", max_retries=3),
                triggers.Fetch("example.com/*", zone="example.com"),
            ],
            exports={
                "ImageProcessor": exports.DurableObject(container=image),
                "Old": exports.DeletedDurableObject(),
                "Tally": exports.RenamedDurableObject(renamed_to="ImageProcessor"),
                "ThumbnailFlow": exports.Workflow(name="thumbs", limits_steps=50),
                "Default": exports.Worker(cache=True),
            },
        ),
    )

    assert to_wrangler(config) == {
        "name": "w",
        "account_id": "abc",
        "main": "src/main.py",
        "compatibility_date": "2026-09-08",
        "compatibility_flags": ["python_workers", "nodejs_compat"],
        "assets": {
            "directory": "./public",
            "not_found_handling": "404-page",
            "binding": "STATIC",
        },
        "routes": [
            {"pattern": "images.example.com", "custom_domain": True},
            {"pattern": "example.com/*", "zone_name": "example.com"},
        ],
        "rules": [{"type": "Data", "globs": ["**/*.onnx"], "fallthrough": True}],
        "exports": {
            "ImageProcessor": {
                "type": "durable-object",
                "storage": "sqlite",
                "container": "image",
            },
            "Old": {"type": "durable-object", "state": "deleted"},
            "Tally": {
                "type": "durable-object",
                "state": "renamed",
                "renamed_to": "ImageProcessor",
            },
            "Default": {"type": "worker", "cache": {"enabled": True}},
        },
        "containers": [
            {
                "class_name": "ImageProcessor",
                "name": "image",
                "image": "./Dockerfile",
                "max_instances": 3,
            }
        ],
        "vars": {"GREETING": "hello", "FEATURES": {"resize": True}},
        "secrets": {"required": ["API_KEY"]},
        "d1_databases": [{"binding": "DATABASE", "database_name": "db"}],
        "kv_namespaces": [{"binding": "CACHE", "id": "k"}],
        "r2_buckets": [
            {"binding": "UPLOADS", "bucket_name": "uploads", "jurisdiction": "eu"}
        ],
        "queues": {
            "producers": [{"binding": "JOBS", "queue": "jobs"}],
            "consumers": [{"queue": "jobs", "max_retries": 3}],
        },
        "durable_objects": {
            "bindings": [
                {"name": "IMAGES", "class_name": "ImageProcessor"},
                {"name": "COUNTERS", "class_name": "Counter", "script_name": "metrics"},
            ]
        },
        "workflows": [
            {
                "binding": "THUMBNAILS",
                "class_name": "ThumbnailFlow",
                "name": "thumbs",
                "limits": {"steps": 50},
            }
        ],
        "services": [{"binding": "AUTH", "service": "auth", "entrypoint": "Sessions"}],
        "ai": {"binding": "AI"},
        "triggers": {"crons": ["0 * * * *"]},
    }


def test_every_rule_across_objects_is_reported_at_once() -> None:
    shared = Container(name="c", image="registry/c:1")
    config = Config(
        worker=replace(
            WORKER,
            env={
                "GONE": bindings.DurableObject("Old"),
                "FLOW": bindings.Workflow("Missing"),
                "A": bindings.AI(),
                "B": bindings.AI(),
                "STATIC": bindings.Assets(),
                "BAD": "text",
            },
            exports={
                "Old": exports.DeletedDurableObject(),
                "Tally": exports.RenamedDurableObject(renamed_to="Nowhere"),
                "One": exports.DurableObject(container=shared),
                "Two": exports.DurableObject(container=shared),
                "Unbound": exports.Workflow(name="u"),
            },
        )
    )

    with pytest.raises(WyndError) as caught:
        to_wrangler(config)

    assert str(caught.value).splitlines()[1:] == [
        (
            "  exports['Tally'] is renamed to 'Nowhere', which isn't a live "
            "Durable Object in exports"
        ),
        "  exports['One'] and exports['Two'] both run a container named 'c'",
        "  env['GONE']: exports has no live Durable Object named 'Old'",
        "  env['FLOW']: exports has no Workflow named 'Missing'",
        "  env['B']: a Worker has one AI binding",
        "  env['STATIC']: an Assets binding needs Worker.assets",
        "  env['BAD'] isn't a binding",
        "  exports['Unbound'] needs exactly one bindings.Workflow in env, and has 0",
    ]


@pytest.mark.parametrize(
    "build",
    [
        lambda: Container(name="c"),
        lambda: Container(name="c", dockerfile="./D", image="r/c:1"),
        lambda: Container(name="c", image="r/c:1", build_context="."),
        lambda: exports.DurableObject(
            storage="legacy-kv", container=Container(name="c", image="r/c:1")
        ),
        lambda: replace(WORKER, compatibility_date="2026-W36-1"),
    ],
)
def test_a_rule_inside_one_object_fails_on_construction(
    build: Callable[[], object],
) -> None:
    with pytest.raises(ValueError):
        build()


def test_dev_renders_the_mode_and_runs_wrangler_on_it(
    tools: Tools, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLOUDFLARE_ENV", "staging")

    assert main(["dev", "--port", "8788"]) == 0

    rendered = project / ".wynd" / "run" / "cloudflare.config.py.staging.json"
    assert tools.wrangler() == [["dev", "--port", "8788", "--config", str(rendered)]]
    assert [call["cloudflare_env"] for call in tools.calls("npx")] == [None]
    compiled = json.loads(rendered.read_text())
    assert compiled["name"] == "w-staging"
    assert compiled["vars"] == {"DEBUG": "1"}
    assert compiled["main"] == "../../src/entry.py"
    assert (project / ".wynd" / "run" / "python_modules" / "workers").is_dir()
    env = (project / "src" / "env.py").read_text()
    assert "    DATABASE: D1Database\n" in env
    assert "    COUNTER: DurableObjectNamespace[Counter]\n" in env


def test_passthrough_runs_on_the_rendered_file_without_the_mode(
    tools: Tools, project: Path
) -> None:
    args = ["d1", "migrations", "apply", "DATABASE", "-e", "staging", "--local"]

    assert main(["-c", "cloudflare.config.py", *args]) == 0

    rendered = str(project / ".wynd" / "run" / "cloudflare.config.py.staging.json")
    assert tools.wrangler() == [
        ["d1", "migrations", "apply", "DATABASE", "--local", "--config", rendered]
    ]


def test_deploy_stages_the_compiled_config(tools: Tools, project: Path) -> None:
    assert main(["deploy", "--env=staging"]) == 0

    staged = project / ".wynd" / "build" / "wrangler.json"
    assert tools.wrangler() == [["deploy", "--config", str(staged)]]
    assert json.loads(staged.read_text())["d1_databases"] == [
        {
            "binding": "DATABASE",
            "database_name": "db-staging",
            "database_id": "1",
            "migrations_dir": str(project / "migrations"),
        }
    ]


def test_an_unchanged_config_leaves_the_rendered_file_alone(
    tools: Tools, project: Path
) -> None:
    assert main(["whoami"]) == 0
    rendered = project / ".wynd" / "run" / "cloudflare.config.py.json"
    os.utime(rendered, ns=(0, 0))

    assert main(["whoami"]) == 0
    assert rendered.stat().st_mtime_ns == 0


def test_required_secrets_are_typed(
    tools: Tools, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    text = CONFIG.replace(
        '"COUNTER":', '"API_KEY": bindings.Secret(),\n                "COUNTER":'
    )
    monkeypatch.chdir(python_project(tmp_path / "worker", text))

    assert main(["types"]) == 0

    assert "    API_KEY: str\n" in (Path.cwd() / "src" / "env.py").read_text()


def test_a_bad_value_names_the_config_line(
    tools: Tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    text = CONFIG.replace(
        'compatibility_date="2025-06-01"', 'compatibility_date="soon"'
    )
    monkeypatch.chdir(python_project(tmp_path / "worker", text))

    assert main(["dev"]) == 1
    line = text.splitlines().index("        worker=Worker(") + 1
    assert capsys.readouterr().err == (
        f"wynd: error: cloudflare.config.py:{line}: compatibility_date 'soon' "
        "isn't a YYYY-MM-DD date\n"
    )
    assert tools.wrangler() == []


def test_a_wrangler_file_beside_a_python_config_fails(
    tools: Tools, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (project / "wrangler.json").write_text("{}")

    assert main(["whoami"]) == 1
    assert "has cloudflare.config.py and wrangler.json. Keep one." in (
        capsys.readouterr().err
    )
    assert tools.wrangler() == []
