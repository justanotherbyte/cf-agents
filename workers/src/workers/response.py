from typing import Any

import js
from pyodide.ffi import to_js

from ._js import js_name


class Response:
    def __init__(self, body: Any = None, status: int = 200) -> None:
        # shortcut: a JS Response, or a str body with a status. crosswind 3 adds
        # the stock SDK's other bodies, headers and options.
        if js_name(body) == "Response":
            self.js_object = body
            return
        init = to_js({"status": status}, dict_converter=js.Object.fromEntries)
        self.js_object = js.Response.new(body, init)
