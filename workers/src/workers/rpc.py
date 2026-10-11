from typing import Any

from .request import Request


# shortcut: wraps a JS Request and passes everything else through. Slice 2 ports
# the stock conversion rules.
def python_from_rpc(obj: Any) -> Any:
    if getattr(getattr(obj, "constructor", None), "name", None) == "Request":
        return Request(obj)
    return obj


def python_to_rpc(value: Any) -> Any:
    return getattr(value, "js_object", value)
