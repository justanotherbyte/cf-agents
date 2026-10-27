from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from ._model import Container
from ._utils import to_table

__all__ = (
    "DeletedDurableObject",
    "DurableObject",
    "ExpectingTransferDurableObject",
    "Export",
    "RenamedDurableObject",
    "Storage",
    "TransferredDurableObject",
    "Worker",
    "Workflow",
)

type Storage = Literal["sqlite", "legacy-kv"]


class Export(ABC):
    """A class the Worker's entrypoint exports, keyed by its name in
    Worker.exports."""

    @abstractmethod
    def _entry(self) -> dict[str, Any] | None:
        """The Wrangler `exports` entry, or None for an export Wrangler
        declares in another section."""


@dataclass(frozen=True, kw_only=True)
class _DurableObjectExport(Export):
    """One state of a Durable Object class, compiled to the `exports` entry
    Wrangler 4.129 reconciles on upload."""

    _state: ClassVar[str | None] = None

    def _entry(self) -> dict[str, Any]:
        entry: dict[str, Any] = {"type": "durable-object", **to_table(self)}
        if self._state is not None:
            entry["state"] = self._state
        if isinstance(container := entry.get("container"), Container):
            entry["container"] = container.name
        return entry


@dataclass(frozen=True, kw_only=True)
class _LiveDurableObject(_DurableObjectExport):
    """A Durable Object class this Worker runs, so a binding can name it.

    Raises ValueError for a container on storage other than sqlite.
    """

    storage: Storage = "sqlite"
    container: Container | None = None

    def __post_init__(self) -> None:
        if self.container is not None and self.storage != "sqlite":
            raise ValueError(
                f"Container {self.container.name!r} needs storage='sqlite'"
            )


@dataclass(frozen=True, kw_only=True)
class DurableObject(_LiveDurableObject):
    pass


@dataclass(frozen=True, kw_only=True)
class ExpectingTransferDurableObject(_LiveDurableObject):
    _state = "expecting-transfer"

    transfer_from: str


@dataclass(frozen=True, kw_only=True)
class DeletedDurableObject(_DurableObjectExport):
    _state = "deleted"


@dataclass(frozen=True, kw_only=True)
class RenamedDurableObject(_DurableObjectExport):
    _state = "renamed"

    renamed_to: str


@dataclass(frozen=True, kw_only=True)
class TransferredDurableObject(_DurableObjectExport):
    _state = "transferred"

    transferred_to: str


@dataclass(frozen=True, kw_only=True)
class Worker(Export):
    """A WorkerEntrypoint class."""

    cache: bool | None = None

    def _entry(self) -> dict[str, Any]:
        entry: dict[str, Any] = {"type": "worker"}
        if self.cache is not None:
            entry["cache"] = {"enabled": self.cache}
        return entry


@dataclass(frozen=True, kw_only=True)
class Workflow(Export):
    """A WorkflowEntrypoint class.

    Wrangler 4.129 declares a workflow by its workflows[] binding, so these
    fields go into the entry of the one bindings.Workflow that names this
    export.
    """

    name: str
    limits_steps: int | None = None

    def _entry(self) -> None:
        return None

    def _binding_fields(self) -> dict[str, Any]:
        fields: dict[str, Any] = {"name": self.name}
        if self.limits_steps is not None:
            fields["limits"] = {"steps": self.limits_steps}
        return fields
