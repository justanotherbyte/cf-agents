import re
from urllib.parse import quote, unquote

__all__ = ("decode_uri_component", "encode_uri_component", "is_absolute_path")

_MALFORMED_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")


def decode_uri_component(value: str) -> str:
    """
    Decode like JavaScript's ``decodeURIComponent``.

    Raises
    ------
    ValueError
        A ``%`` isn't followed by two hex digits, or the bytes aren't UTF-8.
    """
    if _MALFORMED_ESCAPE.search(value):
        raise ValueError(f"malformed URI component: {value!r}")
    return unquote(value, errors="strict")


def encode_uri_component(value: str, *, safe: str = "") -> str:
    """Percent-encode everything but ``A-Z a-z 0-9 - _ . ~`` and ``safe``."""
    return quote(value, safe=safe)


def is_absolute_path(value: object) -> bool:
    return isinstance(value, str) and value.startswith("/") and "\0" not in value
