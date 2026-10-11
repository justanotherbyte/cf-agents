from typing import Any


class Request:
    def __init__(self, js_object: Any) -> None:
        self.js_object = js_object

    @property
    def method(self) -> str:
        return self.js_object.method

    @property
    def url(self) -> str:
        return self.js_object.url
