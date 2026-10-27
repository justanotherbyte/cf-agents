from collections.abc import Generator, MutableMapping
from typing import Any

import js

from ._js import js_name
from .blob import Blob, File

__all__ = ("FormData", "FormDataValue")

type FormDataValue = str | js.Blob | Blob


class FormData(MutableMapping[str, Any]):
    """Key/value pairs for forms, following multidict and the JS FormData API."""

    def __init__(self, form_data: "js.FormData | dict[str, Any] | None" = None) -> None:
        if not form_data:
            self._js_form_data = js.FormData.new()
        elif isinstance(form_data, dict):
            self._js_form_data = js.FormData.new()
            for key, value in form_data.items():
                self._js_form_data.append(key, _to_js(value))
        elif js_name(form_data) == "FormData":
            self._js_form_data = form_data
        else:
            raise TypeError(
                "Expected form_data to be a dict or an instance of FormData"
            )

    def __getitem__(self, key: str) -> Any:
        if not self._js_form_data.has(key):
            raise KeyError(key)
        return _to_python(self._js_form_data.get(key))

    def __setitem__(self, key: str, value: Any) -> None:
        if isinstance(value, list):
            raise TypeError("Expected single item in arguments to FormData.__setitem__")
        self._js_form_data.set(key, _to_js(value))

    def __delitem__(self, key: str) -> None:
        self.delete(key)

    def __contains__(self, key: object) -> bool:
        return self._js_form_data.has(key)

    def __iter__(self) -> Generator[str]:
        yield from self.keys()

    def __len__(self) -> int:
        return sum(1 for _ in self.keys())

    def append(self, key: str, value: Any, filename: str | None = None) -> None:
        self._js_form_data.append(key, _to_js(value), filename)

    def delete(self, key: str) -> None:
        self._js_form_data.delete(key)

    # keys, values and items return generators, as in the stock SDK, where
    # Mapping promises views.
    def keys(self) -> Generator[str]:  # ty: ignore[invalid-method-override]
        yield from self._js_form_data.keys()

    def values(self) -> Generator[Any]:  # ty: ignore[invalid-method-override]
        for value in self._js_form_data.values():
            yield _to_python(value)

    def items(self) -> Generator[tuple[str, Any]]:  # ty: ignore[invalid-method-override]
        for key, value in self._js_form_data.entries():
            yield key, _to_python(value)

    def get_all(self, key: str) -> list[Any]:
        return [_to_python(value) for value in self._js_form_data.getAll(key)]

    @property
    def js_object(self) -> "js.FormData":
        return self._js_form_data


def _to_js(value: Any) -> Any:
    return value.js_object if isinstance(value, Blob) else value


_CLASS_FOR_JS: dict[str | None, type[Blob]] = {"File": File, "Blob": Blob}


def _to_python(value: Any) -> Any:
    wrapper = _CLASS_FOR_JS.get(js_name(value))
    return value if wrapper is None else wrapper(value)
