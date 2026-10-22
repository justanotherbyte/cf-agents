import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonc

from .config._compile import render
from .errors import WyndError

__all__ = ("Project", "WyndError", "find_config", "load_project")

# Wrangler walks every parent for one name before it tries the next.
CONFIG_NAMES = ("wrangler.json", "wrangler.jsonc", "wrangler.toml")
PYTHON_CONFIG = "cloudflare.config.py"

INHERITED = (
    "main",
    "base_dir",
    "compatibility_date",
    "compatibility_flags",
    "rules",
    "assets",
    "build",
    "python_modules",
    # Wrangler 4.129 inherits logfwdr, although it holds bindings.
    "logfwdr",
)


@dataclass(frozen=True)
class Project:
    root: Path
    # The file the user wrote. config_path is the file Wrangler reads, which
    # for a cloudflare.config.py is the Wrangler file rendered beside it.
    source_path: Path
    config_path: Path
    config: dict[str, Any]
    environment: dict[str, Any]
    main: str | None
    base_dir: str | None
    compatibility_date: str | None
    compatibility_flags: tuple[str, ...]
    dependencies: tuple[str, ...]
    allow_build: bool
    keep_modules: tuple[str, ...]
    keep_docstrings: bool

    @property
    def python_config(self) -> bool:
        return self.source_path.suffix == ".py"

    @property
    def python_modules(self) -> Path:
        # Wrangler collects python_modules/ beside the config file.
        return self.config_path.parent / "python_modules"

    @property
    def source_dir(self) -> Path:
        """The module root: base_dir, or the entrypoint's directory."""
        if self.main is None:
            raise WyndError(f"{self.config_path.name} sets no main")
        config_dir = self.config_path.parent.resolve()
        return (config_dir / (self.base_dir or Path(self.main).parent)).resolve()

    @property
    def wynd_dir(self) -> Path:
        return self.root / ".wynd"


def load_project(cwd: Path, config: str | None, env: str | None) -> Project:
    """Read the project the way Wrangler would for `-c config` and `-e env`.

    For a cloudflare.config.py, `env` is the mode it runs with, and the config
    is the rendered file as Wrangler will read it.
    """
    root = _find_up(cwd, "pyproject.toml")
    if root is None:
        raise WyndError(f"no pyproject.toml in {cwd} or any parent directory")

    source_path = cwd / config if config else find_config(cwd)
    if source_path is None:
        names = ", ".join((PYTHON_CONFIG, *CONFIG_NAMES))
        raise WyndError(f"no config ({names}) found from {cwd}")
    if source_path.suffix == ".py":
        config_path = render(source_path, env)
        raw = environment = _read(config_path)
    else:
        config_path = source_path
        raw = _read(config_path)
        environment = _environment(raw, env, config_path.name)

    pyproject = _read(root / "pyproject.toml")
    wynd = pyproject.get("tool", {}).get("wynd", {})
    keep = wynd.get("keep", [])
    if not isinstance(keep, list) or not all(isinstance(k, str) for k in keep):
        raise WyndError("[tool.wynd] keep must be a list of module names")
    keep_docstrings = wynd.get("keep-docstrings", False)
    if not isinstance(keep_docstrings, bool):
        raise WyndError("[tool.wynd] keep-docstrings must be true or false")

    date = environment.get("compatibility_date")
    return Project(
        root=root,
        source_path=source_path,
        config_path=config_path,
        config=raw,
        environment=environment,
        main=environment.get("main"),
        base_dir=environment.get("base_dir"),
        # TOML parses a bare date as datetime.date. Wrangler reads it as text.
        compatibility_date=None if date is None else str(date),
        compatibility_flags=tuple(environment.get("compatibility_flags", ())),
        dependencies=tuple(pyproject.get("project", {}).get("dependencies", ())),
        allow_build=bool(wynd.get("allow-build")),
        keep_modules=tuple(keep),
        keep_docstrings=keep_docstrings,
    )


def _environment(config: dict[str, Any], env: str | None, name: str) -> dict[str, Any]:
    """The chosen environment's table over the top-level keys it inherits."""
    envs = config.get("env") or {}
    # Without any env table, Wrangler warns and uses the top level, as Wynd does.
    if env is None or not envs:
        return config
    if env not in envs:
        raise WyndError(f"{name} has no environment named {env!r}")

    inherited = {key: config[key] for key in INHERITED if key in config}
    return {**inherited, **envs[env]}


def _find_up(start: Path, name: str) -> Path | None:
    for directory in (start, *start.parents):
        if (directory / name).is_file():
            return directory
    return None


def find_config(cwd: Path) -> Path | None:
    """The config Wrangler would find, or a deeper cloudflare.config.py.

    Raises WyndError when one directory holds both kinds.
    """
    found = (
        directory / name
        for name in CONFIG_NAMES
        if (directory := _find_up(cwd, name)) is not None
    )
    wrangler = next(found, None)
    python = _find_up(cwd, PYTHON_CONFIG)
    if python is None:
        return wrangler
    if wrangler is not None and wrangler.parent == python:
        raise WyndError(f"{python} has {PYTHON_CONFIG} and {wrangler.name}. Keep one.")
    if wrangler is not None and wrangler.parent.is_relative_to(python):
        return wrangler
    return python / PYTHON_CONFIG


def _read(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text()
        if path.suffix == ".toml":
            return tomllib.loads(text)
        return jsonc.loads(text)
    except OSError as error:
        raise WyndError(f"cannot read {path}: {error.strerror}") from None
    except (tomllib.TOMLDecodeError, ValueError) as error:
        raise WyndError(f"cannot parse {path}: {error}") from None
