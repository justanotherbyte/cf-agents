import copy
import json
import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, time
from functools import cached_property
from pathlib import Path
from typing import Any

from .errors import WyndError

__all__ = ("JSON", "BindingRef", "WranglerConfig")

type JSON = None | bool | int | float | str | list[JSON] | dict[str, JSON]

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

# Fields Wrangler resolves from the config directory, by table. A list value
# holds one table per item.
PATH_FIELDS = {
    "assets": ("directory",),
    "build": ("watch_dir",),
    "containers": ("image_build_context",),
    "d1_databases": ("migrations_dir", "migrations_pattern"),
    "site": ("bucket", "entry-point"),
}

# Keys whose relative values must stay relative or aren't config-relative paths.
# main and base_dir point into the code root. build.cwd and build.command
# resolve from the process working directory.
LEFT_ALONE = {"$schema", "main", "base_dir", "build", "rules", "vars", "define"}

BINDINGS_SUBKEY = {
    "queues": "producers",
    "durable_objects": "bindings",
    "logfwdr": "bindings",
    "unsafe": "bindings",
}

KEYED_BY_NAME = {
    "durable_objects.bindings",
    "logfwdr.bindings",
    "unsafe.bindings",
    "ratelimits",
    "send_email",
}

NOT_BINDINGS = {"vars", "define", "env", "previews"}


@dataclass(frozen=True)
class BindingRef:
    """One binding of the effective config, as typegen needs it."""

    name: str
    # Dotted for nested lists, such as "queues.producers".
    section: str
    class_name: str | None = None
    script_name: str | None = None


@dataclass(frozen=True, eq=False)
class WranglerConfig:
    """One Worker config, as Wynd reads it and as Wrangler will receive it.

    `_payload` is the only state: the Wrangler dict exactly as the source gave
    it, env blocks included, with paths relative to source.parent. Every typed
    attribute is parsed from it on first access, so what Wynd reads comes from
    the dict Wrangler receives. Typed attributes raise WyndError on a malformed
    value, so a config Wynd can't read still stages for passthrough.
    """

    # The file the user wrote.
    source: Path
    # The -e env Wrangler will apply. Always None for a Python config.
    env: str | None
    _payload: dict[str, Any] = field(repr=False)

    @cached_property
    def _settings(self) -> dict[str, Any]:
        """The env's table over the top-level keys it inherits."""
        envs = self._payload.get("env") or {}
        # Without any env table, Wrangler warns and uses the top level, as Wynd does.
        if self.env is None or not envs:
            return self._payload
        if self.env not in envs:
            raise WyndError(f"{self.source.name} has no environment named {self.env!r}")

        inherited = {
            key: self._payload[key] for key in INHERITED if key in self._payload
        }
        return {**inherited, **envs[self.env]}

    @cached_property
    def main(self) -> Path:
        """Path: the entrypoint, absolute. WyndError when the config sets none."""
        main = self._settings.get("main")
        if not isinstance(main, str):
            raise WyndError(f"{self.source.name} sets no main")
        return (self.source.parent / main).resolve()

    @cached_property
    def source_dir(self) -> Path:
        """Path: the module root, base_dir or the entrypoint's directory."""
        base_dir = self._settings.get("base_dir")
        if not base_dir:
            return self.main.parent
        return (self.source.parent / base_dir).resolve()

    @cached_property
    def entry_module(self) -> str:
        """str: the entry module's dotted name below source_dir."""
        try:
            entry = self.main.relative_to(self.source_dir)
        except ValueError:
            raise WyndError(
                f"main {self._settings['main']} is outside {self.source_dir}"
            ) from None
        return ".".join(entry.with_suffix("").parts)

    @cached_property
    def compatibility_date(self) -> str | None:
        """str | None: the date as text. TOML parses a bare date as a date."""
        day = self._settings.get("compatibility_date")
        return None if day is None else str(day)

    @cached_property
    def compatibility_flags(self) -> tuple[str, ...]:
        return tuple(self._settings.get("compatibility_flags", ()))

    @cached_property
    def bindings(self) -> tuple[BindingRef, ...]:
        """tuple[BindingRef, ...]: every binding, in config order."""
        return tuple(_bindings(self._settings))

    @cached_property
    def vars(self) -> Mapping[str, JSON]:
        variables = self._settings.get("vars")
        return variables if isinstance(variables, dict) else {}

    @cached_property
    def required_secrets(self) -> tuple[str, ...]:
        secrets = self._settings.get("secrets")
        required = secrets.get("required") if isinstance(secrets, dict) else None
        names = required if isinstance(required, list) else []
        return tuple(name for name in names if isinstance(name, str))

    def to_wrangler(self, path: Path, *, code_root: Path) -> str:
        """Write the payload as JSON at `path` and return the text.

        Wrangler reading `path` resolves what it would resolve reading
        `source`. Known path keys become absolute against source.parent, a D1
        database with no migrations setting gets source.parent / "migrations",
        and main and base_dir point into `code_root`, relative to path.parent.
        Every other key is copied unchanged. The file is replaced atomically,
        and only when its text changes, so a running wrangler dev doesn't
        reload for nothing.

        Raises WyndError for a "./" or "../" string under a key Wynd doesn't
        know, and for a value JSON can't encode.
        """
        payload = copy.deepcopy(self._payload)
        _rewrite(payload, self.source.parent)
        _point_into(payload, code_root, path.parent)

        unknown = list(_dot_relative_paths(payload, ""))
        if unknown:
            raise WyndError(
                f"{self.source.name} has a relative path under {', '.join(unknown)}, "
                "which Wynd can't point back at the project from .wynd/. Use an "
                "absolute path."
            )

        try:
            text = json.dumps(payload, indent=2, default=_toml_value) + "\n"
        except TypeError as error:
            raise WyndError(
                f"{self.source.name} holds a value JSON can't encode: {error}"
            ) from None
        _replace(path, text)
        return text


def _bindings(settings: dict[str, Any]) -> Iterator[BindingRef]:
    for key, value in settings.items():
        if key in NOT_BINDINGS:
            continue

        section = key
        if key in BINDINGS_SUBKEY and isinstance(value, dict):
            section = f"{key}.{BINDINGS_SUBKEY[key]}"
            value = value.get(BINDINGS_SUBKEY[key])

        name_key = "name" if section in KEYED_BY_NAME else "binding"
        for entry in value if isinstance(value, list) else [value]:
            if isinstance(entry, dict) and isinstance(entry.get(name_key), str):
                yield BindingRef(
                    entry[name_key],
                    section,
                    _str_or_none(entry.get("class_name")),
                    _str_or_none(entry.get("script_name")),
                )


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _rewrite(section: dict[str, Any], base: Path) -> None:
    """Point a section's known path keys at the project, in place."""
    for key, path_keys in PATH_FIELDS.items():
        for table in _tables(section.get(key)):
            for path_key in path_keys:
                if path_key in table:
                    table[path_key] = _absolute(table[path_key], base)

    # Wrangler's default is beside the config, which is no longer beside it.
    for database in _tables(section.get("d1_databases")):
        if "migrations_dir" not in database and "migrations_pattern" not in database:
            database["migrations_dir"] = str(base / "migrations")

    for container in _tables(section.get("containers")):
        image = container.get("image")
        # Wrangler builds an image that names a file and pulls anything else. A
        # missing "./Dockerfile" is rewritten too, so Wrangler names the file.
        if isinstance(image, str) and (
            image.startswith(".") or (base / image).is_file()
        ):
            container["image"] = str(base / image)

    if "tsconfig" in section:
        section["tsconfig"] = _absolute(section["tsconfig"], base)

    aliases = section.get("alias")
    if isinstance(aliases, dict):
        for name, target in aliases.items():
            # A target that isn't a relative path names a package.
            if isinstance(target, str) and target.startswith("."):
                aliases[name] = str(base / target)

    envs = section.get("env")
    nested = list(envs.values()) if isinstance(envs, dict) else []
    for table in _tables(nested) + _tables(section.get("previews")):
        _rewrite(table, base)


def _point_into(payload: dict[str, Any], code_root: Path, directory: Path) -> None:
    """Point main and base_dir, at the top level and in every env, into
    `code_root`, relative to the staged file's `directory`."""
    envs = payload.get("env")
    nested = list(envs.values()) if isinstance(envs, dict) else []
    for section in [payload, *_tables(nested)]:
        for key in ("main", "base_dir"):
            if isinstance(section.get(key), str):
                section[key] = os.path.relpath(code_root / section[key], directory)


def _tables(value: Any) -> list[dict[str, Any]]:
    items = value if isinstance(value, list) else [value]
    return [item for item in items if isinstance(item, dict)]


def _absolute(value: Any, base: Path) -> Any:
    if isinstance(value, list):
        return [_absolute(item, base) for item in value]
    return str(base / value) if isinstance(value, str) else value


def _dot_relative_paths(node: Any, where: str) -> Iterator[str]:
    if isinstance(node, str) and node.startswith(("./", "../")):
        yield where
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from _dot_relative_paths(item, f"{where}[{index}]")
    elif isinstance(node, dict):
        for key, value in node.items():
            if key not in LEFT_ALONE:
                yield from _dot_relative_paths(
                    value, f"{where}.{key}" if where else key
                )


def _toml_value(value: Any) -> str:
    # TOML parses bare dates and times, which Wrangler reads as text.
    if isinstance(value, (date, time)):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _replace(path: Path, text: str) -> None:
    if path.is_file() and path.read_text() == text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(text)
    temporary.replace(path)
