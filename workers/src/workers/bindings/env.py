"""The env an entrypoint sees, which picks each binding's class."""

from collections.abc import Callable
from typing import Any

from .._js import js_name
from ..rpc import python_from_rpc
from .base import Binding
from .d1 import D1Database
from .kv import KVNamespace
from .queues import Queue
from .r2 import R2Bucket
from .services import DurableObjectNamespace, Fetcher
from .workflow import Workflow

__all__: list[str] = []

_CLASS_FOR_JS: dict[str, Callable[[Any], Any]] = {
    "Fetcher": Fetcher,
    "DurableObjectNamespace": DurableObjectNamespace,
    "WorkflowImpl": Workflow,
    "KvNamespace": KVNamespace,
    "R2Bucket": R2Bucket,
    "D1Database": D1Database,
    "WorkerQueue": Queue,
}


class _EnvWrapper(Binding):
    """The env an entrypoint sees: each binding wrapped by its JS class."""

    @property
    def _env(self) -> Any:
        # The stock SDK's private name for the JS env, which its tests read.
        return self.js_object

    def __getattr__(self, name: str) -> Any:
        try:
            return super().__getattr__(name)
        except AttributeError:
            if name.startswith("__") or name == "js_object":
                raise
            # A binding this mode's config leaves out, which a Wynd Env types
            # as `X | None`. The stock SDK raises AttributeError.
            return None

    def _pass_through(self, name: str, value: Any) -> Any:
        js_class = js_name(value)
        if js_class is None:
            binding = value
        elif js_class in ("Object", "Array"):
            binding = python_from_rpc(value)
        else:
            binding = _CLASS_FOR_JS.get(js_class, Binding)(value)

        setattr(self, name, binding)

        return binding
