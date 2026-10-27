"""The Images binding. `hosted`, for stored images, passes through."""

from typing import Any, Literal, TypedDict, Unpack

import js

from ..response import Response
from ..rpc import python_from_rpc, python_to_rpc
from .base import Binding, _js_options

__all__ = ("ImageTransformationResult", "ImageTransformer", "ImagesBinding")

type _ImageFormat = Literal[
    "image/jpeg", "image/png", "image/gif", "image/webp", "image/avif", "rgb", "rgba"
]


class _ImageTransform(TypedDict, total=False):
    # Every key is a single JS word, so keywords pass to JS unchanged.
    width: int
    height: int
    background: str
    blur: int
    border: dict[str, Any]
    brightness: float
    contrast: float
    fit: Literal["scale-down", "contain", "pad", "squeeze", "cover", "crop"]
    flip: Literal["h", "v", "hv"]
    gamma: float
    segment: Literal["foreground"]
    gravity: (
        Literal["face", "left", "right", "top", "bottom", "center", "auto", "entropy"]
        | dict[str, Any]
    )
    rotate: Literal[0, 90, 180, 270]
    saturation: float
    sharpen: float
    trim: Literal["border"] | dict[str, Any]


class ImageTransformationResult(Binding):
    def response(self) -> Response:
        return Response(self.js_object.response())

    def content_type(self) -> str:
        return self.js_object.contentType()

    def image(
        self,
        options: Any = None,
        *,
        encoding: Literal["base64"] | None = None,
        **extra: Any,
    ) -> "js.ReadableStream":
        return self.js_object.image(_js_options(options, extra, encoding=encoding))


class ImageTransformer(Binding):
    def transform(
        self, transform: Any = None, /, **options: Unpack[_ImageTransform]
    ) -> "ImageTransformer":
        return ImageTransformer(
            self.js_object.transform(_js_options(transform, dict(options)))
        )

    def draw(
        self,
        image: "js.ReadableStream | ImageTransformer",
        options: Any = None,
        *,
        opacity: float | None = None,
        repeat: bool | str | None = None,
        top: int | None = None,
        left: int | None = None,
        bottom: int | None = None,
        right: int | None = None,
        **extra: Any,
    ) -> "ImageTransformer":
        js_options = _js_options(
            options,
            extra,
            opacity=opacity,
            repeat=repeat,
            top=top,
            left=left,
            bottom=bottom,
            right=right,
        )
        return ImageTransformer(self.js_object.draw(python_to_rpc(image), js_options))

    async def output(
        self,
        options: Any = None,
        *,
        format: _ImageFormat | None = None,
        quality: int | None = None,
        background: str | None = None,
        anim: bool | None = None,
        **extra: Any,
    ) -> ImageTransformationResult:
        js_options = _js_options(
            options,
            extra,
            format=format,
            quality=quality,
            background=background,
            anim=anim,
        )
        return ImageTransformationResult(await self.js_object.output(js_options))


class ImagesBinding(Binding):
    async def info(
        self,
        stream: "js.ReadableStream",
        options: Any = None,
        *,
        encoding: Literal["base64"] | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        js_options = _js_options(options, extra, encoding=encoding)
        return python_from_rpc(await self.js_object.info(stream, js_options))

    def input(
        self,
        stream: "js.ReadableStream",
        options: Any = None,
        *,
        encoding: Literal["base64"] | None = None,
        **extra: Any,
    ) -> ImageTransformer:
        js_options = _js_options(options, extra, encoding=encoding)
        return ImageTransformer(self.js_object.input(stream, js_options))
