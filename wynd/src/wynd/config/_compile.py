import re
import runpy
import sys
import traceback
from collections import Counter
from pathlib import Path
from typing import Any

from ..errors import WyndError
from . import exports
from .bindings import Binding
from .exports import Export
from .model import Config
from .triggers import Trigger
from .utils import to_table

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
        raise WyndError(f"{where(path, error)}: {error}") from None
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
    return Compiler(config).run()


class Compiler:
    """Builds one Wrangler dict from a Config, collecting every error."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.worker = config.worker
        self.out: dict[str, Any] = {}
        self.errors: list[str] = []

    def run(self) -> dict[str, Any]:
        self.add_settings()
        self.add_exports()
        self.add_env()
        self.add_triggers()

        if self.errors:
            lines = "".join(f"\n  {error}" for error in self.errors)
            raise WyndError(f"cloudflare.config.py has errors:{lines}")
        return self.out

    def add_settings(self) -> None:
        worker, config = self.worker, self.config
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
            "assets": table(worker.assets),
            "observability": table(worker.observability),
            "placement": table(worker.placement),
            "limits": table(worker.limits),
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
        self.out.update((k, v) for k, v in settings.items() if v is not None)

    def add_exports(self) -> None:
        holders: dict[str, str] = {}
        for name, export in self.worker.exports.items():
            if not isinstance(export, Export):
                self.errors.append(f"exports[{name!r}] isn't an export")
                continue

            entry = export.entry()
            if entry is not None:
                self.out.setdefault("exports", {})[name] = entry
            if isinstance(export, exports.RenamedDurableObject) and not isinstance(
                self.worker.exports.get(export.renamed_to), exports.LiveDurableObject
            ):
                self.errors.append(
                    f"exports[{name!r}] is renamed to {export.renamed_to!r}, which "
                    "isn't a live Durable Object in exports"
                )

            if not isinstance(export, exports.LiveDurableObject):
                continue
            container = export.container
            if container is None:
                continue
            if container.name in holders:
                self.errors.append(
                    f"exports[{holders[container.name]!r}] and exports[{name!r}] "
                    f"both run a container named {container.name!r}"
                )
            holders[container.name] = name
            self.out.setdefault("containers", []).append(container.entry(name))

    def add_env(self) -> None:
        for name, binding in self.worker.env.items():
            if binding is None or binding is False:
                continue
            if not isinstance(binding, Binding):
                self.errors.append(f"env[{name!r}] isn't a binding")
                continue
            try:
                binding.emit(name, self.out, self.worker)
            except ValueError as error:
                self.errors.append(f"env[{name!r}]: {error}")

        # workflows[] declares the workflow, so each needs exactly one binding.
        bound = Counter(
            entry["class_name"]
            for entry in self.out.get("workflows", [])
            if "script_name" not in entry
        )
        for name, export in self.worker.exports.items():
            if isinstance(export, exports.Workflow) and bound[name] != 1:
                self.errors.append(
                    f"exports[{name!r}] needs exactly one bindings.Workflow in "
                    f"env, and has {bound[name]}"
                )

    def add_triggers(self) -> None:
        # Wrangler leaves a missing list alone and clears an empty one, and the
        # config is the whole truth.
        self.out["triggers"] = {"crons": []}
        for index, trigger in enumerate(self.worker.triggers):
            if isinstance(trigger, Trigger):
                trigger.emit(self.out)
            else:
                self.errors.append(f"triggers[{index}] isn't a trigger")


def table(settings: Any) -> dict[str, Any] | None:
    return None if settings is None else to_table(settings)


def where(path: Path, error: Exception) -> str:
    """`path:line` for the innermost frame of `error` in the config file."""
    frames = traceback.extract_tb(error.__traceback__)
    lines = [frame.lineno for frame in frames if frame.filename == str(path)]
    return f"{path.name}:{lines[-1]}" if lines else path.name
