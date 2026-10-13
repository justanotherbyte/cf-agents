"""What Request and Response share on top of their JS object."""

from typing import TYPE_CHECKING, Any

from ._js import headers_to_message, python_errors
from .blob import Blob
from .formdata import FormData


class BodyMixin:
    """Headers and the body readers pyodide's FetchResponse lacks.

    Built on `js_object`, a JS Request or Response, and `_raise_if_failed`.
    """

    if TYPE_CHECKING:

        @property
        def js_object(self) -> Any: ...

        def _raise_if_failed(self) -> None: ...

    @property
    def headers(self) -> Any:
        return headers_to_message(self.js_object.headers)

    async def form_data(self) -> FormData:
        self._raise_if_failed()
        with python_errors():
            return FormData(await self.js_object.formData())

    async def blob(self) -> Blob:
        self._raise_if_failed()
        return Blob(await self.js_object.blob())
