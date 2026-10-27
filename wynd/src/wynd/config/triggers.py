from abc import ABC, abstractmethod
from dataclasses import KW_ONLY, dataclass
from typing import Any

from ._utils import to_table

__all__ = ("Cron", "Fetch", "Queue", "Trigger")


class Trigger(ABC):
    """One value of Worker.triggers, compiled into its Wrangler section."""

    @abstractmethod
    def _emit(self, out: dict[str, Any]) -> None:
        """Add this trigger to the Wrangler dict `out`."""


@dataclass(frozen=True)
class Cron(Trigger):
    schedule: str

    def _emit(self, out: dict[str, Any]) -> None:
        out.setdefault("triggers", {}).setdefault("crons", []).append(self.schedule)


@dataclass(frozen=True)
class Fetch(Trigger):
    """A route. Worker.domains holds custom domains."""

    pattern: str
    _: KW_ONLY
    zone: str | None = None

    def _emit(self, out: dict[str, Any]) -> None:
        route = to_table(self, {"zone": "zone_name"})
        out.setdefault("routes", []).append(route)


@dataclass(frozen=True)
class Queue(Trigger):
    """A queue consumer. Each field is the Wrangler key of the same name."""

    name: str
    _: KW_ONLY
    max_batch_size: int | None = None
    max_batch_timeout: int | None = None
    max_retries: int | None = None
    max_concurrency: int | None = None
    retry_delay: int | None = None
    dead_letter_queue: str | None = None

    def _emit(self, out: dict[str, Any]) -> None:
        consumer = to_table(self, {"name": "queue"})
        out.setdefault("queues", {}).setdefault("consumers", []).append(consumer)
