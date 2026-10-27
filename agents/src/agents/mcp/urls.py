"""Server ids and the private-address check on server URLs.

Ports of upstream ``normalizeServerId`` and ``isBlockedUrl``
(``client/index.ts:119-262``).
"""

import ipaddress
import re
from urllib.parse import urlsplit

__all__ = ("MCP_SERVER_ID_MAX_LENGTH", "is_blocked_url", "normalize_server_id")

MCP_SERVER_ID_MAX_LENGTH = 64

_BLOCKED_HOSTNAMES = frozenset({"metadata.google.internal"})

# Loopback stays allowed for local development, as upstream.
_BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in (
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",  # link-local, cloud metadata
        "0.0.0.0/8",
        "::/128",
        "fc00::/7",  # unique local
        "fe80::/10",  # link-local
    )
)


def normalize_server_id(value: str) -> str:
    """Return ``value`` as a storage- and tool-name-safe server id.

    Lowercase, runs of characters outside ``[a-z0-9_-]`` become one ``-``,
    leading and trailing ``-`` / ``_`` are trimmed, ids that don't start with
    a letter get an ``id-`` prefix, and the result is cut to 64 characters.

    Examples
    --------
    >>> normalize_server_id("GitHub MCP!")
    'github-mcp'
    >>> normalize_server_id("42-things")
    'id-42-things'
    """
    normalized = re.sub(r"[^a-z0-9_-]+", "-", value.lower())
    normalized = re.sub(r"-+", "-", normalized).strip("-_")
    if not re.match(r"[a-z]", normalized):
        normalized = f"id-{normalized}".rstrip("-")
    return normalized[:MCP_SERVER_ID_MAX_LENGTH].rstrip("-")


def is_blocked_url(url: str) -> bool:
    """Return whether ``url`` is malformed or points at a private address.

    Blocks RFC 1918, link-local, unique-local and unspecified addresses
    (including IPv4-mapped IPv6 forms) and cloud metadata hosts. Loopback is
    allowed.
    """
    if "\\" in url:  # fetch() reads a backslash as "/", urlsplit doesn't
        return True
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        address = _parse_ip(host)
    except ValueError:  # a bracketed host that isn't an IPv6 address
        return True
    if parts.scheme not in ("http", "https") or not host:
        return True
    if host in _BLOCKED_HOSTNAMES:
        return True

    if address is None:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return any(address in network for network in _BLOCKED_NETWORKS)


def _parse_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    if ":" in host:  # urlsplit has already dropped the brackets
        return ipaddress.IPv6Address(host.partition("%")[0])
    return _parse_whatwg_ipv4(host)


def _parse_whatwg_ipv4(host: str) -> ipaddress.IPv4Address | None:
    # fetch() parses hosts the WHATWG way, where "2130706433", "0x7f.1" and
    # "010.0.0.1" are all IPv4 addresses; the check must read them the same.
    parts = host.split(".")
    if parts[-1] == "" and len(parts) > 1:
        parts.pop()
    if len(parts) > 4:
        return None
    numbers = [_parse_ipv4_number(part) for part in parts]
    if any(number is None for number in numbers):
        return None

    *head, last = [number for number in numbers if number is not None]
    if any(number > 255 for number in head) or last >= 256 ** (5 - len(numbers)):
        return None
    value = last
    for index, number in enumerate(head):
        value += number * 256 ** (3 - index)
    return ipaddress.IPv4Address(value)


def _parse_ipv4_number(part: str) -> int | None:
    if part[:2].lower() == "0x":
        digits, base = part[2:], 16
    elif len(part) > 1 and part.startswith("0"):
        digits, base = part[1:], 8
    else:
        digits, base = part, 10
    if digits == "":
        return 0 if base == 16 else None
    allowed = "0123456789abcdef"[:base]
    if any(char not in allowed for char in digits.lower()):
        return None
    return int(digits, base)
