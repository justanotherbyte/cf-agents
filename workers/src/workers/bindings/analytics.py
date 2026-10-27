from collections.abc import Iterable
from typing import Any

from .base import Binding, _js_options

__all__ = ("AnalyticsEngineDataset",)


class AnalyticsEngineDataset(Binding):
    def write_data_point(
        self,
        event: Any = None,
        *,
        indexes: Iterable[str | bytes | None] | None = None,
        doubles: Iterable[float] | None = None,
        blobs: Iterable[str | bytes | None] | None = None,
        **extra: Any,
    ) -> None:
        for name, values in (("indexes", indexes), ("blobs", blobs)):
            if isinstance(values, (str, bytes)):
                raise TypeError(f"{name} must be a list, not {type(values).__name__}")

        js_event = _js_options(
            event,
            extra,
            indexes=None if indexes is None else list(indexes),
            doubles=None if doubles is None else list(doubles),
            blobs=None if blobs is None else list(blobs),
        )
        self.js_object.writeDataPoint(js_event)
