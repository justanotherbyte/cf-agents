"""Load cloudflare.config.py and convert it to a Wrangler config, as
@cloudflare/config 0.27.0's resolveAndParseConfig and convertToWranglerConfig
do for cloudflare.config.ts.

shortcut: Wynd checks what spans objects (bindings, exports, Containers) and
leaves the shape of each option dictionary to the type checker and Wrangler's
own validation, which warns about unknown keys where zod rejects them. Upgrade
if a misspelled option reaches a deploy.
"""

import asyncio
import importlib.util
import inspect
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..errors import WyndError
from ._utils import present
from .bindings import Spec
from .context import Context, _current
from .definitions import Config, Container, Worker
from .exports import Export
from .triggers import Trigger

__all__ = ("load_config",)

# Sections that name each binding with `name` instead of `binding`.
NAMED = {
    "durable_objects.bindings",
    "logfwdr.bindings",
    "ratelimits",
    "send_email",
    "unsafe.bindings",
}

# Sections that hold one binding, not a list.
SINGLETONS = {
    "ai",
    "analytics",
    "assets",
    "browser",
    "images",
    "media",
    "stream",
    "version_metadata",
}

# Where a binding to another Worker writes that Worker's name.
WORKER_KEYS = {
    "durable_objects.bindings": "script_name",
    "services": "service",
    "workflows": "script_name",
}

# The export type a binding's class_name must name in its Worker's exports.
EXPORT_TYPES = {
    "durable_objects.bindings": "durable-object",
    "workflows": "workflow",
}

# Durable Object states whose class no binding can reach.
RETIRED = {"deleted", "renamed", "transferred"}


def load_config(path: Path, context: Context) -> dict[str, Any]:
    """Run cloudflare.config.py for `context` and return its Wrangler config.

    Raises WyndError when the file is missing, sets no `config`, or fails a
    check. Errors in the user's own code propagate with their traceback.
    """
    token = _current.set(context)
    try:
        module = _import(path)
        if not hasattr(module, "config"):
            raise WyndError(f"{path.name} sets no module-level `config`")
        return asyncio.run(_Converter(path.name, context).convert(module.config))
    finally:
        _current.reset(token)


def _import(path: Path) -> Any:
    if not path.is_file():
        raise WyndError(f"cannot read {path}: no such file")

    # shortcut: the root and src/ are where type checkers resolve the config's
    # imports, such as `from env import Env`. Upgrade if a project needs base_dir.
    roots = [str(path.parent / "src"), str(path.parent)]
    sys.path[:0] = roots
    try:
        spec = importlib.util.spec_from_file_location("cloudflare_config", path)
        assert spec is not None and spec.loader is not None, "a .py path has a loader"
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for root in roots:
            sys.path.remove(root)

    return module


class _Converter:
    """One load: each definition resolves once, and every error is collected."""

    def __init__(self, name: str, context: Context) -> None:
        self.name = name
        self.context = context
        self.errors: list[str] = []
        # Keyed by id, holding the input so its id stays unique.
        self._resolved: dict[int, tuple[Any, Any]] = {}

    async def convert(self, value: Any) -> dict[str, Any]:
        config = await self.resolve(value, Config, "config")
        containers = [
            await self.resolve(container, Container, f"containers[{index}]")
            for index, container in enumerate(config.containers)
        ]

        result: dict[str, Any] = {}
        if config.worker is not None:
            worker = await self.resolve(config.worker, Worker, "worker")
            result = await self.worker(worker, containers)

        region = config.compliance_region
        result |= present(
            account_id=config.account_id,
            compliance_region=region and region.replace("-", "_"),
        )
        if containers:
            result["containers"] = [_container(c) for c in containers]
        self.check_container_names(containers)

        if self.errors:
            lines = "".join(f"\n  {error}" for error in self.errors)
            raise WyndError(f"{self.name} is invalid:{lines}")
        return result

    async def resolve[T](self, value: Any, kind: type[T], where: str) -> T:
        """A value, an awaitable, or a factory of the context, resolved once.

        Raises WyndError when the result isn't a `kind`.
        """
        key = id(value)
        if key not in self._resolved:
            result = value(self.context) if callable(value) else value
            if inspect.isawaitable(result):
                result = await result
            self._resolved[key] = (value, result)

        result = self._resolved[key][1]
        if not isinstance(result, kind):
            raise WyndError(
                f"{self.name}: {where} is a {type(result).__name__}, "
                f"not a {kind.__name__}"
            )
        return result

    async def worker(
        self, worker: Worker, containers: list[Container]
    ) -> dict[str, Any]:
        result = present(
            name=worker.name,
            main=worker.entrypoint,
            base_dir=worker.base_dir,
            compatibility_date=worker.compatibility_date,
            compatibility_flags=worker.compatibility_flags
            and list(worker.compatibility_flags),
            workers_dev=worker.workers_dev,
            preview_urls=worker.preview_urls,
            logpush=worker.logpush,
            first_party_worker=worker.first_party_worker,
            placement=worker.placement,
            limits=worker.limits,
            observability=worker.observability,
            access=worker.access,
            cache=worker.cache,
            unsafe=worker.unsafe and dict(worker.unsafe),
        )

        for name, spec in _env(worker.env).items():
            if spec is not None and spec is not False:
                await self.binding(result, name, spec, worker)
        if worker.assets:
            result["assets"] = {**result.get("assets", {}), **worker.assets}

        exports = await self.exports(worker, containers)
        if exports:
            result["exports"] = exports

        for domain in worker.domains or ():
            _append(result, "routes", self.domain(domain))
        for index, trigger in enumerate(worker.triggers or ()):
            if not isinstance(trigger, Trigger):
                self.errors.append(f"worker.triggers[{index}] is not a trigger")
                continue
            _append(result, trigger.section, *trigger.values)
        for consumer in worker.tail_consumers or ():
            streaming = "streaming_" if consumer.get("streaming") else ""
            _append(
                result, f"{streaming}tail_consumers", {"service": consumer["worker"]}
            )

        return result

    async def binding(
        self, result: dict[str, Any], name: str, spec: Any, worker: Worker
    ) -> None:
        where = f"worker.env.{name}"
        if not isinstance(spec, Spec):
            self.errors.append(
                f"{where} is a {type(spec).__name__}, not a binding from "
                "wynd.config.bindings"
            )
            return

        section = spec.section
        if section == "vars":
            value = spec.options.get("value")
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                self.errors.append(f"{where} has a value JSON can't hold: {value!r}")
                return
            result.setdefault("vars", {})[name] = value
            return
        if section == "secrets.required":
            _append(result, section, name)
            return

        entry = {"name" if section in NAMED else "binding": name, **spec.options}
        if section in WORKER_KEYS:
            await self.link(entry, spec, worker, where)

        if section not in SINGLETONS:
            _append(result, section, entry)
        elif section in result:
            self.errors.append(f"{where}: a Worker has only one {section} binding")
        else:
            result[section] = entry

    async def link(
        self, entry: dict[str, Any], spec: Spec[Any], worker: Worker, where: str
    ) -> None:
        """Name the Worker a binding points at, and check the class it binds."""
        target: Worker | None = worker
        if isinstance(spec.target, str):
            entry[WORKER_KEYS[spec.section]] = spec.target
            # Another config's Worker, whose exports Wynd can't see.
            target = worker if spec.target == worker.name else None
        elif spec.target is not None:
            target = await self.resolve(spec.target, Worker, f"{where} worker")
            entry[WORKER_KEYS[spec.section]] = target.name

        export_type = EXPORT_TYPES.get(spec.section)
        if export_type is None or target is None:
            return
        export = (target.exports or {}).get(entry["class_name"])
        if (
            not isinstance(export, Export)
            or export.options["type"] != export_type
            or export.options.get("state") in RETIRED
        ):
            self.errors.append(
                f"{where}: Worker {target.name!r} has no live {export_type} export "
                f"named {entry['class_name']!r}"
            )

    async def exports(
        self, worker: Worker, containers: list[Container]
    ) -> dict[str, Any]:
        converted: dict[str, Any] = {}
        linked: dict[str, str] = {}
        for name, export in (worker.exports or {}).items():
            where = f"worker.exports.{name}"
            if not isinstance(export, Export):
                self.errors.append(f"{where} is not an export from wynd.config.exports")
                continue
            converted[name] = dict(export.options)
            if export.container is None:
                continue

            container = await self.resolve(export.container, Container, where)
            if not any(container is listed for listed in containers):
                self.errors.append(
                    f"{where}: the Container {container.name!r} is not in containers"
                )
            elif container.name in linked:
                self.errors.append(
                    f"{where}: the Container {container.name!r} is already linked to "
                    f"worker.exports.{linked[container.name]}"
                )
            else:
                linked[container.name] = name
                converted[name]["container"] = container.name

        return converted

    def domain(self, domain: Any) -> Any:
        if isinstance(domain, str):
            return {"pattern": domain, "custom_domain": True}
        if not (domain["production"] or domain["previews"]):
            self.errors.append(
                f"worker.domains: {domain['hostname']} serves neither production "
                "nor previews"
            )
        return {
            "pattern": domain["hostname"],
            "custom_domain": True,
            "enabled": domain["production"],
            "previews_enabled": domain["previews"],
        }

    def check_container_names(self, containers: list[Container]) -> None:
        seen: set[str] = set()
        for index, container in enumerate(containers):
            if container.name in seen:
                self.errors.append(
                    f"containers[{index}]: another Container is named "
                    f"{container.name!r}"
                )
            seen.add(container.name)


def _env(env: type | Mapping[str, Any] | None) -> dict[str, Any]:
    """An Env's bindings by name: a mapping as given, or a class's public
    attributes, base classes first."""
    if env is None:
        return {}
    if isinstance(env, Mapping):
        return dict(env)
    return {
        name: value
        for cls in reversed(env.__mro__)
        for name, value in vars(cls).items()
        if not name.startswith("_")
    }


def _append(result: dict[str, Any], section: str, *values: Any) -> None:
    """Add `values` to the list at a dotted section, creating its tables."""
    *tables, key = section.split(".")
    for table in tables:
        result = result.setdefault(table, {})
    result.setdefault(key, []).extend(values)


def _container(container: Container) -> dict[str, Any]:
    result = {
        "name": container.name,
        **present(
            observability=container.observability,
            unsafe=container.unsafe,
            ssh=container.ssh,
            authorized_keys=container.authorized_keys
            and list(container.authorized_keys),
        ),
    }
    if container.is_durable_object():
        result["scheduling_policy"] = "durable_object"
        if container.images is not None:
            result["images"] = {
                name: {"image": image["reference"]} if "reference" in image else image
                for name, image in container.images.items()
            }
        return result

    image: Any = container.image
    rollout = container.rollout or {}
    kind = rollout.get("kind")
    max_instances = container.max_instances
    return result | present(
        image=image.get("dockerfile") or image.get("reference"),
        max_instances=20 if max_instances is None else max_instances,
        image_build_context=image.get("build_context"),
        image_vars=image.get("build_vars"),
        instance_type=container.instance_type,
        scheduling_policy=container.scheduling_policy,
        constraints=container.constraints,
        rollout_kind=kind and kind.replace("-", "_"),
        rollout_step_percentage=rollout.get("step_percentage"),
        rollout_active_grace_period=rollout.get("active_grace_period"),
    )
