from collections.abc import Generator, Iterable
from contextlib import ExitStack, contextmanager
from enum import StrEnum
from typing import Any

import js
from pyodide.ffi import to_js

from ._js import js_name, lend_buffer, managed_pyproxies

__all__ = ["Blob", "BlobEnding", "BlobValue", "File"]

type BlobValue = (
    str | bytes | js.ArrayBuffer | js.TypedArray | js.DataView | js.Blob | Blob | File
)


class BlobEnding(StrEnum):
    TRANSPARENT = "transparent"
    NATIVE = "native"


class Blob:
    def __init__(
        self,
        blob_parts: Iterable[BlobValue] | BlobValue,
        content_type: str | None = None,
        endings: BlobEnding | str | None = None,
    ) -> None:
        if not _is_part_list(blob_parts):
            # A single Blob or File passes on its type. A File's name is lost.
            if not content_type:
                content_type = _blob_type(blob_parts)
            blob_parts = [blob_parts]
        with _js_parts(blob_parts) as parts:
            self._js_blob = js.Blob.new(
                parts, type=content_type, endings=str(endings) if endings else None
            )

    @property
    def size(self) -> int:
        return self._js_blob.size

    @property
    def content_type(self) -> str:
        return self._js_blob.type

    @property
    def js_object(self) -> "js.Blob":
        return self._js_blob

    async def text(self) -> str:
        return await self.js_object.text()

    async def bytes(self) -> bytes:
        return (await self.js_object.arrayBuffer()).to_bytes()

    def slice(
        self,
        start: int | None = None,
        end: int | None = None,
        content_type: str | None = None,
    ) -> "Blob":
        return Blob([self.js_object.slice(start, end, content_type)])


class File(Blob):
    def __init__(
        self,
        blob_parts: Iterable[BlobValue] | BlobValue,
        filename: str | None = None,
        content_type: str | None = None,
        endings: BlobEnding | str | None = None,
        last_modified: int | None = None,
    ) -> None:
        if not _is_part_list(blob_parts):
            # A single Blob or File passes on its type, and a File its name and
            # time, so a JS File received over RPC wraps without a filename.
            if not content_type:
                content_type = _blob_type(blob_parts)
            source: Any = blob_parts
            if isinstance(source, File):
                source = source.js_object
            if js_name(source) == "File":
                filename = source.name if filename is None else filename
                last_modified = last_modified or source.lastModified
            blob_parts = [blob_parts]
        if filename is None:
            raise TypeError("File() needs a filename unless it wraps a single File")
        with _js_parts(blob_parts) as parts:
            self._js_blob = js.File.new(
                parts,
                filename,
                type=content_type,
                endings=str(endings) if endings else None,
                lastModified=last_modified,
            )

    @property
    def name(self) -> str:
        return self._js_blob.name

    @property
    def last_modified(self) -> int:
        return self._js_blob.lastModified


def _is_part_list(parts: Any) -> bool:
    # bytes-like values are one part, even though Python can iterate them.
    if isinstance(parts, (str, bytes, bytearray, memoryview)):
        return False
    try:
        iter(parts)
    except TypeError:
        return False
    return True


def _blob_type(part: Any) -> str | None:
    if isinstance(part, Blob):
        return part.content_type
    if js_name(part) in ("Blob", "File"):
        return part.type
    return None


@contextmanager
def _js_parts(parts: Any) -> Generator[Any]:
    with ExitStack() as stack:
        entries = [stack.enter_context(_js_part(part)) for part in parts]
        with managed_pyproxies() as pyproxies:
            yield to_js(entries, pyproxies=pyproxies)


@contextmanager
def _js_part(part: Any) -> Generator[Any]:
    if isinstance(part, str) or js_name(part) in ("Blob", "File"):
        yield part
    elif isinstance(part, Blob):
        yield part.js_object
    elif _is_buffer(part):
        with lend_buffer(part) as view:
            yield view
    else:
        raise TypeError(f"Don't know how to handle {type(part)} for Blob()")


def _is_buffer(value: Any) -> bool:
    try:
        with memoryview(value):
            return True
    except TypeError:
        return False
