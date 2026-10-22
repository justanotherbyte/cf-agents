import argparse
import os
import tomllib
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar

import jsonc

from .config._compile import run
from .errors import ProjectNotFoundError, WyndError
from .wrangler_config import WranglerConfig

__all__ = (
    "ConfigParser",
    "PythonConfigParser",
    "WranglerConfigParser",
    "find_up",
    "read",
)

_config_flag = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
_config_flag.add_argument("-c", "--config")

_env_flag = argparse.ArgumentParser(
    add_help=False, allow_abbrev=False, parents=[_config_flag]
)
_env_flag.add_argument("-e", "--env")


class ConfigParser(ABC):
    """One config file and its language's rules.

    A subclass sets its file names and says whether Wrangler sees -e, and it
    produces the Wrangler dict for a mode. Discovery, staged-file naming and
    argument rewriting are written once, here.
    """

    names: ClassVar[tuple[str, ...]]
    passes_env: ClassVar[bool]

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()

    def __repr__(self) -> str:
        return f"<{type(self).__name__} path={str(self.path)!r}>"

    @abstractmethod
    def _payload(self, mode: str | None) -> dict[str, Any]:
        """The Wrangler dict, with paths relative to path.parent."""

    def parse(self, mode: str | None) -> WranglerConfig:
        """The config for `mode`. Raises WyndError as reading the file does."""
        env = mode if self.passes_env else None
        return WranglerConfig(self.path, env, self._payload(mode))

    def staged_name(self, mode: str | None) -> str:
        """wrangler.jsonc.json, or cloudflare.config.py.staging.json for a mode.

        Wrangler picks its parser from the .json suffix.
        """
        if self.passes_env or mode is None:
            return f"{self.path.name}.json"
        return f"{self.path.name}.{mode}.json"

    def wrangler_args(
        self, args: list[str], staged: Path
    ) -> tuple[list[str], dict[str, str] | None]:
        """Wrangler's arguments and environment for a run on `staged`.

        Replaces -c/--config with --config staged. When Wrangler doesn't see the
        env, also removes -e/--env and CLOUDFLARE_ENV, because -e on a file with
        no env blocks still renames the Worker to name-<env>. An environment of
        None means Wynd's own.
        """
        if self.passes_env:
            _, rest = _config_flag.parse_known_args(args)
            return [*rest, "--config", str(staged)], None

        _, rest = _env_flag.parse_known_args(args)
        environ = {k: v for k, v in os.environ.items() if k != "CLOUDFLARE_ENV"}
        return [*rest, "--config", str(staged)], environ

    @staticmethod
    def find(cwd: Path, config: str | None) -> "ConfigParser":
        """The parser for `-c config`, or for the config Wrangler would find.

        Without `config`, walk up one name at a time as Wrangler does, with
        cloudflare.config.py included, and the deeper directory wins. Raises
        WyndError when one directory holds both kinds, and ProjectNotFoundError when
        there is no config.
        """
        if config:
            path = cwd / config
            if not path.is_file():
                raise ProjectNotFoundError(f"no config at {path}")
            return _parser_for(path)

        found = (
            directory / name
            for name in WranglerConfigParser.names
            if (directory := find_up(cwd, name)) is not None
        )
        wrangler = next(found, None)
        python_name = PythonConfigParser.names[0]
        python = find_up(cwd, python_name)
        if python is None:
            if wrangler is not None:
                return WranglerConfigParser(wrangler)
            names = ", ".join((*PythonConfigParser.names, *WranglerConfigParser.names))
            raise ProjectNotFoundError(f"no config ({names}) found from {cwd}")
        if wrangler is not None and wrangler.parent == python:
            raise WyndError(
                f"{python} has {python_name} and {wrangler.name}. Keep one."
            )
        if wrangler is not None and wrangler.parent.is_relative_to(python):
            return WranglerConfigParser(wrangler)
        return PythonConfigParser(python / python_name)


class WranglerConfigParser(ConfigParser):
    names = ("wrangler.json", "wrangler.jsonc", "wrangler.toml")
    passes_env = True

    def _payload(self, mode: str | None) -> dict[str, Any]:
        # The file as written, env blocks included. Wrangler applies -e itself.
        return read(self.path)


class PythonConfigParser(ConfigParser):
    names = ("cloudflare.config.py",)
    passes_env = False

    def _payload(self, mode: str | None) -> dict[str, Any]:
        # The mode applies while the config runs, so there are no env blocks.
        return run(self.path, mode)


def _parser_for(path: Path) -> ConfigParser:
    if path.suffix == ".py":
        return PythonConfigParser(path)
    return WranglerConfigParser(path)


def find_up(start: Path, name: str) -> Path | None:
    """The nearest of `start` and its parents that holds a file `name`."""
    for directory in (start, *start.parents):
        if (directory / name).is_file():
            return directory
    return None


def read(path: Path) -> dict[str, Any]:
    """A TOML, JSON or JSONC file's table. WyndError when it can't be read."""
    try:
        text = path.read_text()
        if path.suffix == ".toml":
            return tomllib.loads(text)
        return jsonc.loads(text)
    except OSError as error:
        raise WyndError(f"cannot read {path}: {error.strerror}") from None
    except (tomllib.TOMLDecodeError, ValueError) as error:
        raise WyndError(f"cannot parse {path}: {error}") from None
