from __future__ import annotations

from collections.abc import Mapping
from dataclasses import KW_ONLY, dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from . import exports
from ._utils import to_table

if TYPE_CHECKING:
    from ._model import Worker

__all__ = (
    "AI",
    "D1",
    "JSON",
    "KV",
    "R2",
    "Assets",
    "Binding",
    "DurableObject",
    "Json",
    "Queue",
    "Secret",
    "Service",
    "Text",
    "VersionMetadata",
    "Workflow",
)

type JSON = None | bool | int | float | str | list[JSON] | dict[str, JSON]


class Binding:
    """One value of Worker.env, compiled into its Wrangler section.

    `_section` is the Wrangler key, dotted for a list inside a table
    ("queues.producers"). `_keys` maps a field to its Wrangler key when the two
    differ. `_name_key` is the entry key that holds the binding name. A
    `_single` section holds one table, not a list.
    """

    _section: ClassVar[str]
    _keys: ClassVar[Mapping[str, str]] = {}
    _name_key: ClassVar[str] = "binding"
    _single: ClassVar[bool] = False

    def _emit(self, name: str, out: dict[str, Any], worker: Worker) -> None:
        """Add this binding to the Wrangler dict `out` under `name`.

        Raises ValueError when the binding doesn't fit the rest of `worker`.
        """
        entry = self._entry(name, worker)
        section, _, nested = self._section.partition(".")
        table = out.setdefault(section, {}) if nested else out
        key = nested or section

        if not self._single:
            table.setdefault(key, []).append(entry)
        elif key in table:
            raise ValueError(f"a Worker has one {type(self).__name__} binding")
        else:
            table[key] = entry

    def _entry(self, name: str, worker: Worker) -> dict[str, Any]:
        return {self._name_key: name, **to_table(self, self._keys)}


@dataclass(frozen=True)
class Text(Binding):
    value: str

    def _emit(self, name: str, out: dict[str, Any], worker: Worker) -> None:
        out.setdefault("vars", {})[name] = self.value


@dataclass(frozen=True)
class Json(Binding):
    value: JSON

    def _emit(self, name: str, out: dict[str, Any], worker: Worker) -> None:
        out.setdefault("vars", {})[name] = self.value


@dataclass(frozen=True)
class Secret(Binding):
    """A secret set with `wrangler secret put`, or in .dev.vars for dev."""

    def _emit(self, name: str, out: dict[str, Any], worker: Worker) -> None:
        out.setdefault("secrets", {}).setdefault("required", []).append(name)


@dataclass(frozen=True, kw_only=True)
class D1(Binding):
    _section = "d1_databases"
    _keys: ClassVar[Mapping[str, str]] = {"name": "database_name", "id": "database_id"}

    name: str | None = None
    id: str | None = None


@dataclass(frozen=True, kw_only=True)
class KV(Binding):
    _section = "kv_namespaces"

    id: str | None = None


@dataclass(frozen=True, kw_only=True)
class R2(Binding):
    _section = "r2_buckets"
    _keys: ClassVar[Mapping[str, str]] = {"name": "bucket_name"}

    name: str | None = None
    jurisdiction: Literal["eu", "fedramp"] | None = None


@dataclass(frozen=True, kw_only=True)
class Queue(Binding):
    """A queue producer. triggers.Queue consumes a queue."""

    _section = "queues.producers"
    _keys: ClassVar[Mapping[str, str]] = {"name": "queue"}

    name: str
    delivery_delay: int | None = None


@dataclass(frozen=True)
class DurableObject(Binding):
    """A Durable Object namespace for the class exported as `export`.

    Without `worker`, the class is this Worker's and must be a live
    exports.DurableObject.
    """

    _section = "durable_objects.bindings"
    _keys: ClassVar[Mapping[str, str]] = {
        "export": "class_name",
        "worker": "script_name",
    }
    _name_key = "name"

    export: str
    _: KW_ONLY
    worker: str | None = None

    def _entry(self, name: str, worker: Worker) -> dict[str, Any]:
        live = isinstance(worker.exports.get(self.export), exports._LiveDurableObject)
        if self.worker is None and not live:
            raise ValueError(
                f"exports has no live Durable Object named {self.export!r}"
            )
        return super()._entry(name, worker)


@dataclass(frozen=True)
class Workflow(Binding):
    """A workflow for the class exported as `export`.

    Without `worker`, the class is this Worker's exports.Workflow, which holds
    the workflow's name. With `worker`, `name` is required.
    """

    _section = "workflows"
    _keys: ClassVar[Mapping[str, str]] = {
        "export": "class_name",
        "worker": "script_name",
    }

    export: str
    _: KW_ONLY
    worker: str | None = None
    name: str | None = None

    def _entry(self, name: str, worker: Worker) -> dict[str, Any]:
        entry = super()._entry(name, worker)
        if self.worker is not None:
            if self.name is None:
                raise ValueError("another Worker's workflow needs name=")
            return entry

        declared = worker.exports.get(self.export)
        if isinstance(declared, exports.Workflow):
            if self.name is not None:
                raise ValueError(f"exports[{self.export!r}] already names the workflow")
            return entry | declared._binding_fields()
        raise ValueError(f"exports has no Workflow named {self.export!r}")


@dataclass(frozen=True)
class Service(Binding):
    """A binding to another Worker, cf's bindings.worker(). `export` names one
    of its entrypoints."""

    _section = "services"
    _keys: ClassVar[Mapping[str, str]] = {"worker": "service", "export": "entrypoint"}

    worker: str
    _: KW_ONLY
    export: str | None = None
    props: Mapping[str, JSON] | None = None


@dataclass(frozen=True)
class AI(Binding):
    _section = "ai"
    _single = True


@dataclass(frozen=True)
class VersionMetadata(Binding):
    _section = "version_metadata"
    _single = True


@dataclass(frozen=True)
class Assets(Binding):
    """The Worker's static assets as a Fetcher. Needs Worker.assets."""

    def _emit(self, name: str, out: dict[str, Any], worker: Worker) -> None:
        assets = out.get("assets")
        if assets is None:
            raise ValueError("an Assets binding needs Worker.assets")
        if "binding" in assets:
            raise ValueError("a Worker has one Assets binding")
        assets["binding"] = name
