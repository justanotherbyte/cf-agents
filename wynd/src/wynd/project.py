import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonc

from .config import Context
from .config._convert import load_config
from .errors import WyndError

__all__ = ("Project", "WyndError", "find_config", "load_project")

# The config files Wynd reads. The nearest directory with one of them holds
# the project's config, and it may hold only one.
CONFIG_NAMES = (
    "cloudflare.config.py",
    "wrangler.json",
    "wrangler.jsonc",
    "wrangler.toml",
)

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
        """Whether the config is a cloudflare.config.py, which Wynd converts."""
        return self.config_path.suffix == ".py"

    @property
    def python_modules(self) -> Path:
        # Wrangler collects python_modules/ beside the config file, and Wrangler
        # reads a Python config's conversion in .wynd/dev/.
        if self.python_config:
            return self.wynd_dir / "dev" / "python_modules"
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


def load_project(
    cwd: Path, config: str | None, env: str | None, *, preview: bool = False
) -> Project:
    """Read the project for `-c config` and `-e env`, choosing the environment
    the way Wrangler would.

    A cloudflare.config.py has no environments. It runs with `env` as its mode,
    and with `preview` for a `wynd preview`.
    """
    root = _find_up(cwd, "pyproject.toml")
    if root is None:
        raise WyndError(f"no pyproject.toml in {cwd} or any parent directory")

    config_path = cwd / config if config else find_config(cwd)
    if config_path is None:
        raise WyndError(f"no config ({', '.join(CONFIG_NAMES)}) found from {cwd}")
    if config_path.suffix == ".py":
        raw = settings = load_config(config_path, Context(env, preview))
    else:
        raw = _read(config_path)
        settings = _environment(raw, env, config_path.name)

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
    """The config in the nearest directory that has one, or None.

    Raises WyndError when that directory has more than one.
    """
    for directory in (cwd, *cwd.parents):
        found = [
            directory / name for name in CONFIG_NAMES if (directory / name).is_file()
        ]
        if len(found) > 1:
            names = ", ".join(path.name for path in found)
            raise WyndError(f"{directory} has {names}. Keep one config.")
        if found:
            return found[0]
    return None


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
