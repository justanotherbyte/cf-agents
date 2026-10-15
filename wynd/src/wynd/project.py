import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonc

__all__ = ["Project", "WyndError", "load_project"]

# Wrangler walks every parent for one name before it tries the next.
CONFIG_NAMES = ("wrangler.json", "wrangler.jsonc", "wrangler.toml")

# Top-level keys Wynd reads that an environment inherits when it leaves them out.
# Bindings, vars and the rest stay with the table that sets them.
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


class WyndError(Exception):
    """An error Wynd reports to the user as one message."""


@dataclass(frozen=True)
class Project:
    root: Path
    config_path: Path
    # The config file as parsed, every environment included.
    config: dict[str, Any]
    # The chosen environment's table over the top-level keys it inherits, or
    # the top level without an environment.
    settings: dict[str, Any]
    main: str | None
    base_dir: str | None
    compatibility_date: str | None
    compatibility_flags: tuple[str, ...]
    dependencies: tuple[str, ...]
    allow_build: bool
    # Modules the shaker keeps although nothing imports them by a literal name.
    keep: tuple[str, ...]
    # Ship vendored docstrings, for a library that breaks when they're None.
    keep_docstrings: bool

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
        # Wynd's builds and state.
        return self.root / ".wynd"


def load_project(cwd: Path, config: str | None, env: str | None) -> Project:
    """Read the project the way Wrangler would for `-c config` and `-e env`."""
    root = _find_up(cwd, "pyproject.toml")
    if root is None:
        raise WyndError(f"no pyproject.toml in {cwd} or any parent directory")

    config_path = cwd / config if config else _find_config(cwd)
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

    date = settings.get("compatibility_date")
    return Project(
        root=root,
        config_path=config_path,
        config=raw,
        settings=settings,
        main=settings.get("main"),
        base_dir=settings.get("base_dir"),
        # TOML parses a bare date as datetime.date. Wrangler reads it as text.
        compatibility_date=None if date is None else str(date),
        compatibility_flags=tuple(settings.get("compatibility_flags", ())),
        dependencies=tuple(pyproject.get("project", {}).get("dependencies", ())),
        allow_build=bool(wynd.get("allow-build")),
        keep=tuple(keep),
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

    # An environment's value replaces the top-level one; nothing merges.
    inherited = {key: config[key] for key in INHERITED if key in config}
    return {**inherited, **envs[env]}


def _find_up(start: Path, name: str) -> Path | None:
    for directory in (start, *start.parents):
        if (directory / name).is_file():
            return directory
    return None


def _find_config(cwd: Path) -> Path:
    for name in CONFIG_NAMES:
        directory = _find_up(cwd, name)
        if directory is not None:
            return directory / name
    raise WyndError(f"no Wrangler config ({', '.join(CONFIG_NAMES)}) found from {cwd}")


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
