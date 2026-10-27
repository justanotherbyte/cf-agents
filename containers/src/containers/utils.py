import random
import re
import secrets
from collections.abc import Awaitable
from typing import Any, Protocol

from workers import Request, Response

SINGLETON_CONTAINER_ID = "cf-singleton-container"

_ID_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
_TIME_EXPRESSION = re.compile(r"^(\d+)([smh])$")


class DurableObjectStub(Protocol):
    """A Durable Object stub, as returned by a Durable Object namespace binding."""

    def fetch(
        self, request: Request | str, /, **kwargs: Any
    ) -> Awaitable[Response]: ...

    def __getattr__(self, name: str) -> Any: ...


class DurableObjectNamespace(Protocol):
    """A Durable Object namespace binding, e.g. ``self.env.MY_CONTAINER``."""

    def idFromName(self, name: str, /) -> Any: ...  # noqa: N802

    def get(self, id: Any, /) -> DurableObjectStub: ...


def generate_id(length: int = 9) -> str:
    """
    Generate a random ID of a specified length using a url-friendly alphabet.

    Parameters
    ----------
    length
        The length of the ID to generate.

    Returns
    -------
    str
        A random string ID.
    """
    return "".join(secrets.choice(_ID_ALPHABET) for _ in range(length))


def parse_time_expression(time_expression: str | int) -> int:
    """
    Parse a time expression into seconds.

    Parameters
    ----------
    time_expression
        A number of seconds, or a string like ``"5m"``, ``"30s"`` or ``"1h"``.

    Returns
    -------
    int
        Number of seconds.

    Raises
    ------
    ValueError
        If the string is not a valid time expression.
    TypeError
        If the value is neither a string nor an int.
    """
    if isinstance(time_expression, int):
        # If it's already a number, assume it's in seconds
        return time_expression

    if isinstance(time_expression, str):
        match = _TIME_EXPRESSION.match(time_expression)
        if not match:
            raise ValueError(f"invalid time expression {time_expression}")

        value = int(match[1])
        unit = match[2]

        match unit:
            case "s":
                return value
            case "m":
                return value * 60
            case "h":
                return value * 60 * 60
            case _:
                raise ValueError(f"unknown time unit {unit}")

    raise TypeError(
        f"invalid type for a time expression: {type(time_expression).__name__}"
    )


async def get_random(
    binding: DurableObjectNamespace, instances: int = 3
) -> DurableObjectStub:
    """
    Get a random container instance across N instances.

    This is useful for load balancing.

    Parameters
    ----------
    binding
        The Container's Durable Object binding.
    instances
        Number of instances to load balance across.

    Returns
    -------
    DurableObjectStub
        A container stub ready to handle requests.
    """
    id = random.randrange(instances)

    # Always use idFromName for consistent behavior
    # idFromString requires a 64-hex digit string which is hard to generate
    object_id = binding.idFromName(f"instance-{id}")

    return binding.get(object_id)


def get_container(
    binding: DurableObjectNamespace, name: str = SINGLETON_CONTAINER_ID
) -> DurableObjectStub:
    """
    Get a container stub.

    Parameters
    ----------
    binding
        The Container's Durable Object binding.
    name
        The name of the instance to get, ``"cf-singleton-container"`` by default.

    Returns
    -------
    DurableObjectStub
        A container stub ready to handle requests.
    """
    object_id = binding.idFromName(name)
    return binding.get(object_id)


def switch_port(request: Request, port: int) -> Request:
    """
    Return a request with the port target set correctly.

    Use this when you have to use ``fetch`` and not ``container_fetch``, as
    ``container_fetch`` is an RPC method and can't pass WebSockets.

    Parameters
    ----------
    request
        The request to target.
    port
        The container port to send the request to.

    Returns
    -------
    Request
        A copy of ``request`` with the ``cf-container-target-port`` header set.

    Examples
    --------
    >>> await container.fetch(switch_port(request, 8090))
    """
    headers = [
        (key, value)
        for key, value in request.headers.items()
        if key.lower() != "cf-container-target-port"
    ]
    headers.append(("cf-container-target-port", str(port)))
    return Request(request, headers=headers)
