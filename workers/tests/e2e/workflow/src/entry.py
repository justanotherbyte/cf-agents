import json
from urllib.parse import parse_qs, urlparse

from js import Error, Object
from pyodide.ffi import JsException, create_once_callable, to_js

from workers import Response, WorkerEntrypoint, WorkflowEntrypoint

ATTEMPTS = {"non_retryable": 0, "other_name": 0}
RETRIES = {"retries": {"limit": 2, "delay": "1 second", "backoff": "constant"}}


def js_object(value):
    return to_js(value, dict_converter=Object.fromEntries)


def rejecting_thenable(name, message):
    error = Error.new(message)
    error.name = name
    # A thenable, so no rejected promise exists before the engine adopts it.
    return js_object({"then": create_once_callable(lambda _res, rej: rej(error))})


class Flow(WorkflowEntrypoint):
    async def run(self, event, step):
        async def non_retryable(ctx=None):
            ATTEMPTS["non_retryable"] += 1
            return rejecting_thenable("NonRetryableError", "stop")

        # The control: the same thenable under another name.
        async def other_name(ctx=None):
            ATTEMPTS["other_name"] += 1
            return rejecting_thenable("OtherError", "stop")

        errors = {}
        for callback in (non_retryable, other_name):
            try:
                await step.do(callback.__name__, js_object(RETRIES), callback)
            except JsException as error:
                errors[callback.__name__] = str(error)
        return js_object({"attempts": ATTEMPTS, "errors": errors})


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        url = urlparse(request.url)
        if url.path == "/start":
            instance = await self.env.FLOW.create()
            return Response(instance.id)
        instance = await self.env.FLOW.get(parse_qs(url.query)["id"][0])
        return Response(json.dumps(await instance.status()))
