import js
from pyodide.ffi import to_js


class Response:
    def __init__(self, body: str | None = None, status: int = 200) -> None:
        init = to_js({"status": status}, dict_converter=js.Object.fromEntries)
        self.js_object = js.Response.new(body, init)
