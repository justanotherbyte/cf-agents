"""Typed classes for Worker bindings, and `Binding` for every other kind.

A name a class doesn't define passes through to the JS object under its exact
JS name, with arguments and results converted as for RPC. Typed methods take
snake_case keywords and forward any keyword they don't know into the JS
options object unchanged, so stock calls like `kv.put(k, v, expirationTtl=60)`
keep working.
"""

from .base import Binding
from .base import _convert as _convert
from .base import _js_options as _js_options
from .base import _RpcStub as _RpcStub
from .d1 import D1Database, D1PreparedStatement
from .env import _EnvWrapper as _EnvWrapper
from .kv import KVNamespace
from .queues import Queue
from .r2 import R2Bucket, R2Object, R2ObjectBody
from .services import DurableObjectNamespace, Fetcher
from .workflow import Workflow, WorkflowInstance

# The private names above are for the rest of the SDK and rpc's stock aliases.
__all__ = (
    "Binding",
    "D1Database",
    "D1PreparedStatement",
    "DurableObjectNamespace",
    "Fetcher",
    "KVNamespace",
    "Queue",
    "R2Bucket",
    "R2Object",
    "R2ObjectBody",
    "Workflow",
    "WorkflowInstance",
)
