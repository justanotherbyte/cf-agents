import json
import sys
from collections.abc import Iterator
from pathlib import Path
from textwrap import dedent
from typing import Any

import pytest
from conftest import Tools

from wynd.cli import main
from wynd.config import Context, exports
from wynd.config._convert import load_config
from wynd.errors import WyndError

ENV = """\
from typing import TypedDict

from wynd.config import bindings, context


class Job(TypedDict):
    id: str


class Env:
    GREETING = bindings.text("hi")
    CACHE = bindings.kv(id="kv1") if context().mode == "staging" else None
"""

CONFIG = """\
from env import Env
from wynd.config import Config, Worker


def config(ctx):
    return Config(
        worker=Worker(
            name=f"app-{ctx.mode}" if ctx.mode else "app",
            compatibility_date="2025-06-01",
            compatibility_flags=["python_workers"],
            entrypoint="src/main.py",
            env=Env,
        )
    )
"""

MAIN = """\
from typing import TYPE_CHECKING

from workers import WorkerEntrypoint

if TYPE_CHECKING:
    from env import Env


class Default(WorkerEntrypoint["Env"]):
    pass
"""


@pytest.fixture(autouse=True)
def fresh_modules(tmp_path: Path) -> Iterator[None]:
    # Each test's project has its own env module, and Wynd loads once a process.
    yield
    for name, module in list(sys.modules.items()):
        if str(getattr(module, "__file__", None)).startswith(str(tmp_path)):
            del sys.modules[name]


def python_project(
    root: Path, config: str = CONFIG, env: str = ENV, main: str = MAIN
) -> Path:
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "w"\ndependencies = ["crosswind-workers"]\n'
    )
    (root / "cloudflare.config.py").write_text(config)
    (root / "src" / "env.py").write_text(env)
    (root / "src" / "main.py").write_text(main)
    return root


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = python_project(tmp_path / "worker")
    monkeypatch.chdir(root)
    return root


def convert(root: Path, config: str, env: str = "", mode: str | None = None) -> Any:
    python_project(root, config=dedent(config), env=dedent(env))
    return load_config(root / "cloudflare.config.py", Context(mode))


def test_every_section_converts_to_its_wrangler_keys(tmp_path: Path) -> None:
    env = """
        from wynd.config import bindings
        from definitions import other

        class Env:
            TEXT = bindings.text("hi")
            FLAGS = bindings.json({"beta": True})
            TOKEN = bindings.secret()
            KV = bindings.kv(id="kv1", remote=True)
            DB = bindings.d1(id="d1", name="db")
            BUCKET = bindings.r2(
                name="b",
                jurisdiction="eu",
                experimental_s3_credentials={
                    "access_key_id": "a", "secret_access_key": "s"
                },
            )
            JOBS = bindings.queue(dict, name="jobs", delivery_delay=5)
            COUNTER = bindings.DurableObject["Counter"](export_name="Counter")
            ROOM = bindings.DurableObject(export_name="Room", worker=other)
            API = bindings.worker(worker="api", export_name="Admin", props={"a": 1})
            FLOW = bindings.workflow(name="flow", export_name="Flow")
            AI = bindings.ai(remote=True)
            ASSETS = bindings.assets()
            VERSION = bindings.version_metadata()
            HD = bindings.hyperdrive(id="h", local_connection_string="postgres://")
            LIMIT = bindings.rate_limit(
                namespace="1", simple={"limit": 10, "period": 60}
            )
            MAIL = bindings.send_email(destination_address="a@b.c")
            NET = bindings.vpc_network(tunnel_id="t")
            DISPATCH = bindings.dispatch_namespace(
                namespace="n", outbound={"worker": "out"}
            )
            LOGS = bindings.logfwdr(destination="d")
            SEARCH = bindings.ai_search(name="s")
            RAW = bindings.unsafe("ratelimit", namespace_id="9")
            OFF = None
            ALSO_OFF = False
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "definitions.py").write_text(
        dedent("""
            from wynd.config import Worker, exports

            other = Worker(
                name="other",
                compatibility_date="2025-06-01",
                exports={"Room": exports.durable_object(storage="sqlite")},
            )
        """)
    )
    config = """
        from env import Env
        from wynd.config import Config, Container, Worker, exports, triggers

        box = Container(name="box", image={"dockerfile": "./Dockerfile"})
        sandbox = Container(
            name="sandbox",
            scheduling_policy="durable-object",
            images={"main": {"reference": "r:1"}},
        )

        async def worker(ctx):
            return Worker(
                name="app",
                compatibility_date="2025-06-01",
                compatibility_flags=["python_workers"],
                entrypoint="src/main.py",
                env=Env,
                assets={"directory": "./public", "not_found_handling": "404-page"},
                limits={"cpu_ms": 50},
                observability={"logs": {"head_sampling_rate": 0.5}},
                workers_dev=False,
                exports={
                    "Counter": exports.durable_object(storage="sqlite", container=box),
                    "Room": exports.durable_object(
                        state="renamed", renamed_to="Counter"
                    ),
                    "Flow": exports.workflow(name="flow", limits={"steps": 10}),
                    "Default": exports.worker(cache=True),
                },
                triggers=[
                    triggers.fetch("example.com/*", zone="example.com"),
                    triggers.fetch("a.dev/*"),
                    triggers.queue("jobs", max_retries=3),
                    triggers.scheduled("0 * * * *"),
                    triggers.email(["a@example.com"]),
                    triggers.connect("udp", 53, idle_timeout_ms=10),
                ],
                domains=[
                    "app.example.com",
                    {"hostname": "pre.example.com", "production": False,
                     "previews": True},
                ],
                tail_consumers=[{"worker": "t"}, {"worker": "s", "streaming": True}],
            )

        config = Config(
            worker=worker,
            containers=[box, sandbox],
            account_id="acc",
            compliance_region="fedramp-high",
        )
    """

    assert convert(tmp_path, config, env) == {
        "name": "app",
        "main": "src/main.py",
        "compatibility_date": "2025-06-01",
        "compatibility_flags": ["python_workers"],
        "workers_dev": False,
        "limits": {"cpu_ms": 50},
        "observability": {"logs": {"head_sampling_rate": 0.5}},
        "vars": {"TEXT": "hi", "FLAGS": {"beta": True}},
        "secrets": {"required": ["TOKEN"]},
        "kv_namespaces": [{"binding": "KV", "id": "kv1", "remote": True}],
        "d1_databases": [{"binding": "DB", "database_id": "d1", "database_name": "db"}],
        "r2_buckets": [
            {
                "binding": "BUCKET",
                "bucket_name": "b",
                "jurisdiction": "eu",
                "local_dev": {
                    "experimental_s3_credentials": {
                        "accessKeyId": "a",
                        "secretAccessKey": "s",
                    }
                },
            }
        ],
        "queues": {
            "producers": [{"binding": "JOBS", "queue": "jobs", "delivery_delay": 5}],
            "consumers": [{"queue": "jobs", "max_retries": 3}],
        },
        "durable_objects": {
            "bindings": [
                # This Worker's own class has no script_name.
                {"name": "COUNTER", "class_name": "Counter"},
                {"name": "ROOM", "class_name": "Room", "script_name": "other"},
            ]
        },
        "services": [
            {
                "binding": "API",
                "entrypoint": "Admin",
                "props": {"a": 1},
                "service": "api",
            }
        ],
        "workflows": [{"binding": "FLOW", "name": "flow", "class_name": "Flow"}],
        "ai": {"binding": "AI", "remote": True},
        "assets": {
            "binding": "ASSETS",
            "directory": "./public",
            "not_found_handling": "404-page",
        },
        "version_metadata": {"binding": "VERSION"},
        "hyperdrive": [
            {"binding": "HD", "id": "h", "localConnectionString": "postgres://"}
        ],
        "ratelimits": [
            {
                "name": "LIMIT",
                "namespace_id": "1",
                "simple": {"limit": 10, "period": 60},
            }
        ],
        "send_email": [{"name": "MAIL", "destination_address": "a@b.c"}],
        "vpc_networks": [{"binding": "NET", "tunnel_id": "t"}],
        "dispatch_namespaces": [
            {"binding": "DISPATCH", "namespace": "n", "outbound": {"service": "out"}}
        ],
        "logfwdr": {"bindings": [{"name": "LOGS", "destination": "d"}]},
        "ai_search": [{"binding": "SEARCH", "instance_name": "s"}],
        "unsafe": {
            "bindings": [{"name": "RAW", "type": "ratelimit", "namespace_id": "9"}]
        },
        "exports": {
            "Counter": {
                "type": "durable-object",
                "storage": "sqlite",
                "container": "box",
            },
            "Room": {
                "type": "durable-object",
                "renamed_to": "Counter",
                "state": "renamed",
            },
            "Flow": {"type": "workflow", "name": "flow", "limits": {"steps": 10}},
            "Default": {"type": "worker", "cache": {"enabled": True}},
        },
        "routes": [
            {"pattern": "app.example.com", "custom_domain": True},
            {
                "pattern": "pre.example.com",
                "custom_domain": True,
                "enabled": False,
                "previews_enabled": True,
            },
            {"pattern": "example.com/*", "zone_name": "example.com"},
            "a.dev/*",
        ],
        "triggers": {"crons": ["0 * * * *"]},
        "addresses": ["a@example.com"],
        "connect": [{"protocol": "udp", "port": 53, "idle_timeout_ms": 10}],
        "tail_consumers": [{"service": "t"}],
        "streaming_tail_consumers": [{"service": "s"}],
        "account_id": "acc",
        "compliance_region": "fedramp_high",
        "containers": [
            {"name": "box", "image": "./Dockerfile", "max_instances": 20},
            {
                "name": "sandbox",
                "scheduling_policy": "durable_object",
                "images": {"main": {"image": "r:1"}},
            },
        ],
    }


def test_errors_are_reported_together(tmp_path: Path) -> None:
    env = """
        from wynd.config import bindings

        class Env:
            AI = bindings.ai()
            AI_AGAIN = bindings.ai()
            COUNTER = bindings.DurableObject(export_name="Counter")
            NAME = "not a binding"
    """
    config = """
        from env import Env
        from wynd.config import Config, Container, Worker, exports

        box = Container(name="box", image={"reference": "r"})
        config = Config(
            worker=Worker(
                name="app",
                compatibility_date="2025-06-01",
                env=Env,
                exports={
                    "Gone": exports.durable_object(storage="sqlite", container=box)
                },
            ),
        )
    """

    with pytest.raises(WyndError) as error:
        convert(tmp_path, config, env)

    assert str(error.value) == (
        "cloudflare.config.py is invalid:\n"
        "  worker.env.AI_AGAIN: a Worker has only one ai binding\n"
        "  worker.env.COUNTER: Worker 'app' has no live durable-object export named "
        "'Counter'\n"
        "  worker.env.NAME is a str, not a binding from wynd.config.bindings\n"
        "  worker.exports.Gone: the Container 'box' is not in containers"
    )


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"state": "deleted", "storage": "sqlite"},
        {"state": "renamed"},
        {"storage": "legacy-kv", "container": "box"},
    ],
)
def test_a_durable_object_export_takes_the_options_of_its_state(
    options: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        exports.durable_object(**options)


def test_deploy_runs_the_config_for_the_mode(
    tools: Tools, project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLOUDFLARE_ENV", "staging")

    assert main(["deploy", "--dry-run"]) == 0

    build = project / ".wynd" / "build"
    staged = json.loads((build / "wrangler.json").read_text())
    assert staged["name"] == "app-staging"
    assert staged["kv_namespaces"] == [{"binding": "CACHE", "id": "kv1"}]
    # The mode is Wynd's. Wrangler would add it to the Worker's name.
    (call,) = tools.calls("npx")
    assert call["args"][2:] == [
        "deploy",
        "--dry-run",
        "--config",
        str(build / "wrangler.json"),
    ]
    assert call["cloudflare_env"] is None


def test_dev_runs_wrangler_on_a_converted_config_in_wynd(
    tools: Tools, project: Path
) -> None:
    (project / ".dev.vars").write_text("TOKEN=dev\n")
    (project / ".dev.vars.staging").write_text("TOKEN=staging\n")

    assert main(["dev", "-e", "staging", "--port", "8788"]) == 0

    dev = project / ".wynd" / "dev"
    config = json.loads((dev / "wrangler.json").read_text())
    assert config["main"] == str(project / "src" / "main.py")
    assert "CACHE" in json.dumps(config)
    # Wrangler reads python_modules/ and .dev.vars beside its config.
    assert (dev / "python_modules" / "workers" / "__init__.py").is_file()
    assert (dev / ".dev.vars").read_text() == "TOKEN=staging\n"
    assert (dev / ".gitignore").read_text() == "*\n"
    assert tools.wrangler() == [
        ["dev", "--port", "8788", "--config", str(dev / "wrangler.json")]
    ]


def test_passthrough_commands_run_on_the_converted_config(
    tools: Tools, project: Path
) -> None:
    assert main(["tail", "--format", "json"]) == 0

    # Beside the dev config, but not over it, which a running dev would reload.
    path = project / ".wynd" / "dev" / "wrangler.passthrough.json"
    assert json.loads(path.read_text())["name"] == "app"
    assert not (project / ".wynd" / "dev" / "wrangler.json").exists()
    assert tools.wrangler() == [["tail", "--format", "json", "--config", str(path)]]
    assert tools.uv() == []


def test_types_has_nothing_to_generate(
    tools: Tools, project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["types", "--check"]) == 0
    assert "declares Env itself" in capsys.readouterr().out
    assert not (project / "src" / "env.py").read_text().startswith("# Generated")


def test_deploy_refuses_a_worker_that_imports_wynd(
    tools: Tools,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = python_project(tmp_path / "worker", main="from env import Env\n")
    monkeypatch.chdir(root)

    assert main(["deploy"]) == 1
    assert "Import Env under `if TYPE_CHECKING:`" in capsys.readouterr().err
    assert tools.wrangler() == []
