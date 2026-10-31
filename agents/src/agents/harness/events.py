"""Fanning each session's events out to its watchers."""

import asyncio

from .types import HarnessEvent

__all__ = ("EventHub",)


class EventHub:
    """In-memory watchers per session; events aren't stored."""

    __slots__ = ("_watchers",)

    def __init__(self) -> None:
        self._watchers: dict[str, set[asyncio.Queue[HarnessEvent]]] = {}

    def subscribe(self, session: str) -> "asyncio.Queue[HarnessEvent]":
        """Return a queue that receives the session's events from now on."""
        queue: asyncio.Queue[HarnessEvent] = asyncio.Queue()
        self._watchers.setdefault(session, set()).add(queue)
        return queue

    def unsubscribe(self, session: str, queue: "asyncio.Queue[HarnessEvent]") -> None:
        """Stop sending the session's events to ``queue``."""
        watchers = self._watchers.get(session)
        if watchers is None:
            return

        watchers.discard(queue)
        if not watchers:
            del self._watchers[session]

    def publish(self, session: str, event: HarnessEvent) -> None:
        """Send ``event`` to every watcher of the session."""
        for queue in self._watchers.get(session, ()):
            queue.put_nowait(event)
