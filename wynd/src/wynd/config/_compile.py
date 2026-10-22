import re
import runpy
import sys
import traceback
from collections import Counter
from pathlib import Path
from typing import Any

from ..errors import WyndError
from . import exports
from ._model import Config
from ._utils import to_table
from .bindings import Binding
from .exports import Export
from .triggers import Trigger

__all__ = ("load", "run", "to_wrangler")

# The mode becomes part of the staged file's name.
MODE = re.compile(r"[A-Za-z0-9_-]+")


def run(path: Path, mode: str | None) -> dict[str, Any]:
    """Compile cloudflare.config.py for `mode` into its Wrangler dict.

    Raises WyndError for a mode that can't be part of a file name, and as
    `load` and `to_wrangler` do.
    """
    if mode is not None and not MODE.fullmatch(mode):
        raise WyndError(f"mode {mode!r} may hold only letters, digits, _ and -")
    return to_wrangler(load(path, mode))


def load(path: Path, mode: str | None) -> Config:
    """Run cloudflare.config.py and return its Config for `mode`.

    The file runs with its directory on sys.path, so it can import modules
    beside it. A ValueError or TypeError from building the config becomes a
    WyndError naming the file and line, and other errors in the user's code
    keep their traceback. Raises WyndError when `config` isn't a Config or a
    function of the mode returning one.
    """
    sys.path.insert(0, str(path.parent))
    try:
        value = runpy.run_path(str(path)).get("config")
        if callable(value):
            value = value(mode)
    except (TypeError, ValueError) as error:
        raise WyndError(f"{_where(path, error)}: {error}") from None
    finally:
        sys.path.remove(str(path.parent))

    if not isinstance(value, Config):
        raise WyndError(
            f"{path.name} must set `config` to a Config, or to a function that "
            "takes the mode and returns one"
        )
    return value


def to_wrangler(config: Config) -> dict[str, Any]:
    """The Wrangler 4.129 dict for one mode, with no env blocks.

    Paths stay as written, relative to cloudflare.config.py. WranglerConfig
    points them back at the project when it stages the file. Raises one
    WyndError listing every rule that spans objects and fails.
    """
    return _Compiler(config).run()


class _Compiler:
    """Builds one Wrangler dict from a Config, collecting every error."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._worker = config.worker
        self._out: dict[str, Any] = {}
        self._errors: list[str] = []

    def run(self) -> dict[str, Any]:
        self._add_settings()
        self._add_exports()
        self._add_env()
        self._add_triggers()

        if self._errors:
            lines = "".join(f"\n  {error}" for error in self._errors)
            raise WyndError(f"cloudflare.config.py has errors:{lines}")
        return self._out

    def _add_settings(self) -> None:
        worker, config = self._worker, self._config
        flags = [
            flag for flag in worker.compatibility_flags if flag != "python_workers"
        ]
        settings = {
            "name": worker.name,
            "account_id": config.account_id,
            "compliance_region": config.compliance_region,
            "main": worker.entrypoint,
            "base_dir": worker.base_dir,
            "compatibility_date": worker.compatibility_date,
            "compatibility_flags": ["python_workers", *flags],
            "workers_dev": worker.workers_dev,
            "preview_urls": worker.preview_urls,
            "logpush": worker.logpush,
            "assets": _table(worker.assets),
            "observability": _table(worker.observability),
            "placement": _table(worker.placement),
            "limits": _table(worker.limits),
            "tail_consumers": [
                {"service": consumer.worker} for consumer in worker.tail_consumers
            ]
            or None,
            "routes": [
                {"pattern": domain, "custom_domain": True} for domain in worker.domains
            ]
            or None,
            "rules": [to_table(rule) for rule in worker.rules] or None,
        }
        self._out.update((k, v) for k, v in settings.items() if v is not None)

    def _add_exports(self) -> None:
        holders: dict[str, str] = {}
        for name, export in self._worker.exports.items():
            if not isinstance(export, Export):
                self._errors.append(f"exports[{name!r}] isn't an export")
                continue

            entry = export._entry()
            if entry is not None:
                self._out.setdefault("exports", {})[name] = entry
            if isinstance(export, exports.RenamedDurableObject) and not isinstance(
                self._worker.exports.get(export.renamed_to), exports._LiveDurableObject
            ):
                self._errors.append(
                    f"exports[{name!r}] is renamed to {export.renamed_to!r}, which "
                    "isn't a live Durable Object in exports"
                )

            if not isinstance(export, exports._LiveDurableObject):
                continue
            container = export.container
            if container is None:
                continue
            if container.name in holders:
                self._errors.append(
                    f"exports[{holders[container.name]!r}] and exports[{name!r}] "
                    f"both run a container named {container.name!r}"
                )
            holders[container.name] = name
            self._out.setdefault("containers", []).append(container._entry(name))

    def _add_env(self) -> None:
        for name, binding in self._worker.env.items():
            if binding is None or binding is False:
                continue
            if not isinstance(binding, Binding):
                self._errors.append(f"env[{name!r}] isn't a binding")
                continue
            try:
                binding._emit(name, self._out, self._worker)
            except ValueError as error:
                self._errors.append(f"env[{name!r}]: {error}")

        # workflows[] declares the workflow, so each needs exactly one binding.
        bound = Counter(
            entry["class_name"]
            for entry in self._out.get("workflows", [])
            if "script_name" not in entry
        )
        for name, export in self._worker.exports.items():
            if isinstance(export, exports.Workflow) and bound[name] != 1:
                self._errors.append(
                    f"exports[{name!r}] needs exactly one bindings.Workflow in "
                    f"env, and has {bound[name]}"
                )

    def _add_triggers(self) -> None:
        # Wrangler leaves a missing list alone and clears an empty one, and the
        # config is the whole truth.
        self._out["triggers"] = {"crons": []}
        for index, trigger in enumerate(self._worker.triggers):
            if isinstance(trigger, Trigger):
                trigger._emit(self._out)
            else:
                self._errors.append(f"triggers[{index}] isn't a trigger")


def _table(settings: Any) -> dict[str, Any] | None:
    return None if settings is None else to_table(settings)


def _where(path: Path, error: Exception) -> str:
    """`path:line` for the innermost frame of `error` in the config file."""
    frames = traceback.extract_tb(error.__traceback__)
    lines = [frame.lineno for frame in frames if frame.filename == str(path)]
    return f"{path.name}:{lines[-1]}" if lines else path.name
