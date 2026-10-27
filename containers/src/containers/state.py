import time
from typing import Any, Literal, NotRequired, TypedDict

CONTAINER_STATE_KEY = "__CF_CONTAINER_STATE"

type Status = Literal["running", "healthy", "stopping", "stopped", "stopped_with_code"]


class State(TypedDict):
    """
    The container state.

    ``running`` means that the container is trying to start and is transitioning
    to a healthy status. ``on_stop`` might be triggered if there is an exit code,
    and it will transition to ``stopped``.
    """

    status: Status
    last_change: int
    exit_code: NotRequired[int]


class ContainerState:
    """
    A wrapper around Durable Object storage to store and get the container state.

    It's useful to track which kind of events have been handled by the user,
    a transition to a new state won't be successful unless the user's hook has
    been triggered and waited for.
    A user hook might be repeated multiple times if they throw errors.
    """

    def __init__(self, storage: Any) -> None:
        self.status: State | None = None
        self._storage = storage

    async def set_running(self) -> None:
        await self._set_status_and_update("running")

    async def set_healthy(self) -> None:
        await self._set_status_and_update("healthy")

    async def set_stopping(self) -> None:
        await self._set_status_and_update("stopping")

    async def set_stopped(self) -> None:
        await self._set_status_and_update("stopped")

    async def set_stopped_if_unchanged(self, previous_state: State) -> None:
        if self.status is not previous_state:
            return

        await self.set_stopped()

    async def set_stopped_with_code(self, exit_code: int) -> None:
        self.status = {
            "status": "stopped_with_code",
            "last_change": int(time.time() * 1000),
            "exit_code": exit_code,
        }
        await self._update()

    async def get_state(self) -> State:
        if self.status is None:
            state = await self._storage.get(CONTAINER_STATE_KEY)
            if not state:
                self.status = {
                    "status": "stopped",
                    "last_change": int(time.time() * 1000),
                }
                await self._update()
            else:
                self.status = state

        assert self.status is not None
        return self.status

    async def _set_status_and_update(self, status: Status) -> None:
        self.status = {"status": status, "last_change": int(time.time() * 1000)}
        await self._update()

    async def _update(self) -> None:
        if self.status is None:
            raise RuntimeError("status should be init")
        await self._storage.put(CONTAINER_STATE_KEY, self.status)
