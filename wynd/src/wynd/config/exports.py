"""Builders for a Worker's `exports`: how the platform manages each class the
entrypoint exports. The map replaces Wrangler's `migrations`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from ._utils import present

if TYPE_CHECKING:
    from .context import Input
    from .definitions import Container

__all__ = (
    "Export",
    "Retention",
    "WorkflowConcurrency",
    "WorkflowLimits",
    "durable_object",
    "worker",
    "workflow",
)

type Storage = Literal["sqlite", "legacy-kv"]
type State = Literal[
    "created", "deleted", "renamed", "expecting-transfer", "transferred"
]

# The options each Durable Object state takes, all required.
_STATE_OPTIONS: dict[str, set[str]] = {
    "created": {"storage"},
    "expecting-transfer": {"storage", "transfer_from"},
    "deleted": set(),
    "renamed": {"renamed_to"},
    "transferred": {"transferred_to"},
}


@dataclass(frozen=True, slots=True)
class Export:
    """An export's Wrangler entry, and the Container it links, if any."""

    options: dict[str, Any]
    container: Input[Container] | None = None


class WorkflowLimits(TypedDict, total=False):
    steps: int


class WorkflowConcurrency(TypedDict, total=False):
    limit: int


class Retention(TypedDict, total=False):
    # Milliseconds, or a duration such as "3 days".
    success_retention: int | str
    error_retention: int | str


def durable_object(
    *,
    storage: Storage | None = None,
    state: State = "created",
    container: Input[Container] | None = None,
    renamed_to: str | None = None,
    transferred_to: str | None = None,
    transfer_from: str | None = None,
) -> Export:
    """A Durable Object class and its namespace's lifecycle.

    Raises ValueError when the options don't match `state`, or `container`
    comes without SQLite storage.
    """
    given = present(
        storage=storage,
        renamed_to=renamed_to,
        transferred_to=transferred_to,
        transfer_from=transfer_from,
    )
    if given.keys() != _STATE_OPTIONS[state]:
        needed = ", ".join(sorted(_STATE_OPTIONS[state])) or "no options"
        raise ValueError(f"a {state!r} Durable Object takes {needed}")
    if container is not None and storage != "sqlite":
        raise ValueError("a Durable Object with a container needs storage='sqlite'")

    options = {"type": "durable-object", **given}
    if state != "created":
        options["state"] = state
    return Export(options, container)


def worker(*, cache: bool | None = None) -> Export:
    """A WorkerEntrypoint, or `default`. `cache` turns its cache on or off."""
    return Export(
        {
            "type": "worker",
            **present(cache=None if cache is None else {"enabled": cache}),
        }
    )


def workflow(
    *,
    name: str,
    limits: WorkflowLimits | None = None,
    concurrency: WorkflowConcurrency | None = None,
    schedules: str | list[str] | None = None,
    default_retention: Retention | None = None,
) -> Export:
    return Export(
        {
            "type": "workflow",
            "name": name,
            **present(
                limits=limits,
                concurrency=concurrency,
                schedules=schedules,
                default_retention=default_retention,
            ),
        }
    )
