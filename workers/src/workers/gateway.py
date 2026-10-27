"""Options shared by the ASGI and WSGI adapters, as in the stock SDK at eec1e54."""

from dataclasses import dataclass
from typing import Any, Literal, get_args

EncodeBody = Literal["automatic", "manual"]


@dataclass(frozen=True, kw_only=True)
class BaseOptions:
    """Options shared by the ASGI and WSGI adapters.

    Each adapter's `Options` subclasses this class.
    """

    # Whether the runtime encodes the body to match its `Content-Encoding`.
    # https://developers.cloudflare.com/workers/runtime-apis/response/#the-encodebody-option
    encode_body: EncodeBody = "automatic"

    def __post_init__(self) -> None:
        if self.encode_body not in get_args(EncodeBody):
            raise ValueError(
                f"encode_body must be one of {get_args(EncodeBody)}, "
                f"got {self.encode_body!r}"
            )

    def response_init(self) -> dict[str, Any]:
        """Extra keyword arguments for `Response.new`."""
        if self.encode_body == "automatic":
            return {}
        return {"encodeBody": self.encode_body}
