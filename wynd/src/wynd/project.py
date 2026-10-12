import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonc

# Wrangler walks every parent for one name before it tries the next.
CONFIG_NAMES = ("wrangler.json", "wrangler.jsonc", "wrangler.toml")


class WyndError(Exception):
    """An error Wynd reports to the user as one message."""


@dataclass(frozen=True)
class Project:
    root: Path
    config_path: Path
    # The config file as parsed, every environment included.
    config: dict[str, Any]
    main: str | None
    base_dir: str | None
    compatibility_date: str | None
    compatibility_flags: tuple[str, ...]
    dependencies: tuple[str, ...]
    allow_build: bool

    @property
    def python_modules(self) -> Path:
        # Wrangler collects python_modules/ beside the config file.
        return self.config_path.parent / "python_modules"


def load_project(cwd: Path, config: str | None, env: str | None) -> Project:
    """Read the project the way Wrangler would for `-c config` and `-e env`."""
    root = _find_up(cwd, "pyproject.toml")
    if root is None:
        raise WyndError(f"no pyproject.toml in {cwd} or any parent directory")
    config_path = cwd / config if config else _find_config(cwd)
    raw = settings = _read(config_path)
    envs = settings.get("env") or {}
    # Without any env table, Wrangler warns and uses the top level, as Wynd does.
    if env is not None and envs:
        if env not in envs:
            raise WyndError(f"{config_path.name} has no environment named {env!r}")
        # An environment's value replaces the top-level one; nothing merges.
        settings = {**settings, **envs[env]}
    pyproject = _read(root / "pyproject.toml")
    return Project(
        root=root,
        config_path=config_path,
        config=raw,
        main=settings.get("main"),
        base_dir=settings.get("base_dir"),
        compatibility_date=settings.get("compatibility_date"),
        compatibility_flags=tuple(settings.get("compatibility_flags", ())),
        dependencies=tuple(pyproject.get("project", {}).get("dependencies", ())),
        allow_build=bool(pyproject.get("tool", {}).get("wynd", {}).get("allow-build")),
    )


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
