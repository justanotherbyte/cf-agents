from fake_js import (
    CLOUDFLARE_WAIT_UNTIL,
    CLOUDFLARE_WORKERS,
    JS_MODULES,
    JsValue,
    js_request,
)

import workers
from workers import Request, handler, import_from_javascript


def test_module_env_reads_the_cloudflare_workers_env() -> None:
    assert workers.env.TEXT == "hi"


def test_wait_until_hands_workerd_a_promise_and_waituntil_is_the_same() -> None:
    js_promise = JsValue("Promise")

    workers.wait_until(js_promise)

    assert CLOUDFLARE_WAIT_UNTIL.calls[-1] is js_promise
    assert workers.waitUntil is workers.wait_until


def test_the_preloaded_modules_import_without_jspi() -> None:
    workers_module = import_from_javascript("cloudflare:workers")
    sockets_module = import_from_javascript("cloudflare:sockets")

    assert workers_module is CLOUDFLARE_WORKERS
    assert sockets_module.specifier == "cloudflare:sockets"


def test_other_modules_import_through_do_an_import() -> None:
    JS_MODULES["node:buffer"] = buffer = JsValue("Module")

    assert import_from_javascript("node:buffer") is buffer


def test_handler_converts_the_request_and_wraps_env() -> None:
    seen = []

    @handler
    def on_fetch(request: Request, env: object) -> str:
        seen.append((request, env))
        return "ok"

    js_env = JsValue("Object", KV=JsValue("KvNamespace"))
    assert on_fetch(js_request("https://x/"), js_env) == "ok"
    request, env = seen[0]
    assert isinstance(request, Request)
    assert env.KV.js_object is not None
