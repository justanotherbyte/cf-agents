"""Module globals, used at top level and inside a handler."""

import asyncio
import json
import sys
from urllib.parse import urlparse

import workers
from workers import Response, WorkerEntrypoint, env, import_from_javascript, patch_env
from workers.bindings import KVNamespace

# Top level: env and the two preloaded modules work here, as in the stock SDK.
TOP_TEXT = env.TEXT
SOCKETS = import_from_javascript("cloudflare:sockets")
try:
    import_from_javascript("node:buffer")
    TOP_LEVEL_IMPORT = "imported"
except ImportError as error:
    TOP_LEVEL_IMPORT = str(error)


async def write_later():
    await asyncio.sleep(0.3)
    await workers.env.KV.put("deferred", "yes")


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        path = urlparse(request.url).path
        if path == "/defer":
            # Responds at once; only ctx.wait_until keeps the write alive.
            self.ctx.wait_until(write_later())
            return Response("scheduled")
        if path == "/deferred":
            return Response(str(await workers.env.KV.get("deferred")))
        result = {"python": f"{sys.version_info.major}.{sys.version_info.minor}"}
        result["top_text"] = TOP_TEXT
        result["sockets_connect"] = hasattr(SOCKETS, "connect")
        result["top_level_import"] = TOP_LEVEL_IMPORT
        result["module_env_kv"] = isinstance(workers.env.KV, KVNamespace)
        try:
            buffer = import_from_javascript("node:buffer")
            result["handler_import"] = hasattr(buffer, "Buffer")
        except ImportError as error:
            result["handler_import"] = str(error)
        finished = []

        async def later():
            await asyncio.sleep(0.05)
            finished.append(True)

        # In-request only: the module-level wait_until doesn't outlive the
        # response under wrangler dev (see utils and the tracker).
        workers.wait_until(later())
        await asyncio.sleep(0.2)
        result["module_wait_until_runs"] = finished == [True]
        try:
            patch_env(TEXT="x")
        except NotImplementedError as error:
            result["patch_env"] = "2026-06-30" in str(error)
        return Response(json.dumps(result))
