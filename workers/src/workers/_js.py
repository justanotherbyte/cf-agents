from typing import Any


def js_name(value: Any) -> str | None:
    """The JS constructor name of a JS value, or None for a Python value."""
    return value.constructor.name if hasattr(value, "constructor") else None
