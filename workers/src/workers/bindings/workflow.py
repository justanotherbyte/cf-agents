"""The Workflow binding, which starts and controls instances. The Workflow
code itself lives in `workers.workflows`."""

from collections.abc import Iterable
from typing import Any

from ..rpc import python_from_rpc, python_to_rpc
from .base import Binding, _js_options

__all__ = ("Workflow", "WorkflowInstance")


class WorkflowInstance(Binding):
    @property
    def id(self) -> str:
        return self.js_object.id

    async def status(self) -> dict[str, Any]:
        return python_from_rpc(await self.js_object.status())

    async def pause(self) -> None:
        await self.js_object.pause()

    async def resume(self) -> None:
        await self.js_object.resume()

    async def restart(self) -> None:
        await self.js_object.restart()

    async def terminate(self) -> None:
        await self.js_object.terminate()

    async def send_event(
        self,
        event: Any = None,
        /,
        *,
        type: str | None = None,
        payload: Any = None,
        **extra: Any,
    ) -> None:
        js_event = _js_options(event, extra, type=type, payload=payload)
        await self.js_object.sendEvent(js_event)


class Workflow(Binding):
    async def get(self, id: str) -> WorkflowInstance:
        return WorkflowInstance(await self.js_object.get(id))

    async def create(
        self,
        options: Any = None,
        /,
        *,
        id: str | None = None,
        params: Any = None,
        **extra: Any,
    ) -> WorkflowInstance:
        js_options = _js_options(options, extra, id=id, params=params)
        return WorkflowInstance(await self.js_object.create(js_options))

    async def create_batch(self, batch: Iterable[Any]) -> list[WorkflowInstance]:
        instances = await self.js_object.createBatch(python_to_rpc(list(batch)))
        return [WorkflowInstance(instance) for instance in instances]
