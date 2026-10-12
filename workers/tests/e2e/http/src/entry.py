"""Runs http_tests.py inside workerd and serves the routes its fetches hit."""

import json
import traceback
from urllib.parse import urlparse

import http_tests
import js
from pyodide.ffi import to_js

from workers import FormData, Response, WorkerEntrypoint


def sub(request):
    return Response(
        "Hi there!", headers={"Custom-Header-That-Should-Passthrough": "true"}
    )


def redirect(request):
    return Response.redirect(request.url.replace("/redirect", "/sub"), 301)


def form_data(request):
    return Response(FormData({"field": "value"}))


def form_data_blob(request):
    data = FormData({"field": "value"})
    data.append(
        "blob.py",
        js.Blob.new(to_js(["print(42)"]), type="text/python"),
        "blob.py",
    )
    data.append("metadata", js.Blob.new(to_js(["{}"])), "metadata.json")
    return Response(data)


ROUTES = {
    "/sub": sub,
    "/redirect": redirect,
    "/formdata": form_data,
    "/formdatablob": form_data_blob,
}


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        url = urlparse(request.url)
        if url.path in ROUTES:
            return ROUTES[url.path](request)
        http_tests.ORIGIN = f"{url.scheme}://{url.netloc}"
        names = [name for name in dir(http_tests) if name.startswith("test_")]
        failures = {}
        for name in names:
            try:
                await getattr(http_tests, name)()
            except BaseException:  # noqa: BLE001
                failures[name] = traceback.format_exc()
        return Response(json.dumps({"ran": names, "failures": failures}))
