from dataclasses import dataclass
from pathlib import Path

from .errors import ProjectNotFoundError, WyndError
from .sources import ConfigParser, find_up, read
from .wrangler_config import WranglerConfig

__all__ = ("Project", "load_project")


@dataclass(frozen=True)
class Project:
    """A Worker config plus the pyproject settings Wynd reads beside it."""

    root: Path
    source: ConfigParser
    config: WranglerConfig
    mode: str | None
    dependencies: tuple[str, ...]
    allow_build: bool
    keep_modules: tuple[str, ...]
    keep_docstrings: bool

    @property
    def wynd_dir(self) -> Path:
        return self.root / ".wynd"

    @property
    def run_dir(self) -> Path:
        """Path: stands in for the config's directory in every dev and
        passthrough run, because Wrangler reads files beside its config."""
        return self.wynd_dir / "run" / self.source.path.parent.relative_to(self.root)

    @property
    def python_modules(self) -> Path:
        """Path: the full tree, beside every staged file."""
        return self.run_dir / "python_modules"


def load_project(cwd: Path, config: str | None, mode: str | None) -> Project:
    """Read the project the way Wrangler would for `-c config` and `-e mode`.

    Raises ProjectNotFoundError with no pyproject.toml or no config from cwd up, and
    WyndError for a config outside the pyproject directory, a bad [tool.wynd],
    or a config that can't be read.
    """
    root = find_up(cwd, "pyproject.toml")
    if root is None:
        raise ProjectNotFoundError(
            f"no pyproject.toml in {cwd} or any parent directory"
        )
    root = root.resolve()
    source = ConfigParser.find(cwd, config)
    if not source.path.is_relative_to(root):
        raise WyndError(
            f"{source.path} is outside {root}, the project that holds "
            "pyproject.toml. Move the config into the project."
        )

    pyproject = read(root / "pyproject.toml")
    wynd = pyproject.get("tool", {}).get("wynd", {})
    keep = wynd.get("keep", [])
    if not isinstance(keep, list) or not all(isinstance(k, str) for k in keep):
        raise WyndError("[tool.wynd] keep must be a list of module names")
    keep_docstrings = wynd.get("keep-docstrings", False)
    if not isinstance(keep_docstrings, bool):
        raise WyndError("[tool.wynd] keep-docstrings must be true or false")

    return Project(
        root=root,
        source=source,
        config=source.parse(mode),
        mode=mode,
        dependencies=tuple(pyproject.get("project", {}).get("dependencies", ())),
        allow_build=bool(wynd.get("allow-build")),
        keep_modules=tuple(keep),
        keep_docstrings=keep_docstrings,
    )
