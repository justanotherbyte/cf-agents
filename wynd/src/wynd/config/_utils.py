from typing import Any

__all__ = ("present",)


def present(**values: Any) -> dict[str, Any]:
    """The keyword arguments that aren't None."""
    return {key: value for key, value in values.items() if value is not None}
