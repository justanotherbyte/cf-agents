"""The env an entrypoint sees, which picks each binding's class."""

from collections.abc import Callable
from typing import Any

from .._js import js_name
from ..rpc import python_from_rpc
from .analytics import AnalyticsEngineDataset
from .base import Binding
from .d1 import D1Database
from .images import ImagesBinding
from .kv import KVNamespace
from .queues import Queue
from .r2 import R2Bucket
from .ratelimit import RateLimit
from .services import DurableObjectNamespace, Fetcher
from .vectorize import Vectorize
from .workflow import Workflow

__all__ = ()

_CLASS_FOR_JS: dict[str, Callable[[Any], Any]] = {
    "Fetcher": Fetcher,
    "DurableObjectNamespace": DurableObjectNamespace,
    "WorkflowImpl": Workflow,
    "KvNamespace": KVNamespace,
    "R2Bucket": R2Bucket,
    "D1Database": D1Database,
    "WorkerQueue": Queue,
    "VectorizeIndexImpl": Vectorize,
    "AnalyticsEngine": AnalyticsEngineDataset,
    "AnalyticsEngineDataset": AnalyticsEngineDataset,
    "LocalAnalyticsEngineDataset": AnalyticsEngineDataset,
    "ImagesBindingImpl": ImagesBinding,
    "Ratelimit": RateLimit,
}


class _EnvWrapper(Binding):
    """The env an entrypoint sees: each binding wrapped by its JS class."""

    @property
    def _env(self) -> Any:
        # The stock SDK's private name for the JS env, which its tests read.
        return self.js_object

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
