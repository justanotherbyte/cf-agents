"""Builders for a Worker's `triggers`, the events that invoke it."""

from dataclasses import dataclass
from typing import Any, Literal

from ._utils import present

__all__ = ("Trigger", "connect", "email", "fetch", "queue", "scheduled")


@dataclass(frozen=True, slots=True)
class Trigger:
    """`values` to add to the Wrangler list at `section`, dotted when nested."""

    section: str
    values: tuple[Any, ...]


def fetch(pattern: str, *, zone: str | None = None) -> Trigger:
    """A route. `zone` is a zone name if it has a dot, otherwise a zone ID."""
    if zone is None:
        return Trigger("routes", (pattern,))
    key = "zone_name" if "." in zone else "zone_id"
    return Trigger("routes", ({"pattern": pattern, key: zone},))


def queue(
    name: str,
    *,
    dead_letter_queue: str | None = None,
    max_batch_size: int | None = None,
    max_batch_timeout: int | None = None,
    max_concurrency: int | None = None,
    max_retries: int | None = None,
    retry_delay: int | None = None,
    visibility_timeout_ms: int | None = None,
) -> Trigger:
    consumer = present(
        queue=name,
        dead_letter_queue=dead_letter_queue,
        max_batch_size=max_batch_size,
        max_batch_timeout=max_batch_timeout,
        max_concurrency=max_concurrency,
        max_retries=max_retries,
        retry_delay=retry_delay,
        visibility_timeout_ms=visibility_timeout_ms,
    )
    return Trigger("queues.consumers", (consumer,))


def scheduled(schedule: str) -> Trigger:
    """One cron schedule."""
    return Trigger("triggers.crons", (schedule,))


def email(addresses: list[str]) -> Trigger:
    """Literal addresses, or `*@domain` for every address of a domain."""
    return Trigger("addresses", tuple(addresses))


def connect(
    protocol: Literal["tcp", "udp"],
    port: int,
    *,
    address: str | None = None,
    idle_timeout_ms: int | None = None,
    max_pending_bytes: int | None = None,
) -> Trigger:
    """Raises ValueError for UDP-only options on TCP."""
    udp = present(idle_timeout_ms=idle_timeout_ms, max_pending_bytes=max_pending_bytes)
    if udp and protocol != "udp":
        raise ValueError(f"{', '.join(udp)} only apply to UDP")
    handler = present(protocol=protocol, port=port, address=address, **udp)
    return Trigger("connect", (handler,))
