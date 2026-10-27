from collections.abc import Mapping
from dataclasses import fields
from typing import Any

__all__ = ("to_table",)


def to_table(obj: Any, keys: Mapping[str, str] | None = None) -> dict[str, Any]:
    """A dataclass's fields that aren't None, under their Wrangler keys."""
    keys = keys or {}
    return {
        keys.get(field.name, field.name): value
        for field in fields(obj)
        if (value := getattr(obj, field.name)) is not None
    }
