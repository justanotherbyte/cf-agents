"""Runs binding_tests.py inside workerd against real local bindings."""

import json
import traceback

import binding_tests

from workers import (
    DurableObject,
    Response,
    WorkerEntrypoint,
    WorkflowEntrypoint,
)


class Counter(DurableObject):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.value = 0

    async def increment(self, by):
        self.value += by
        return self.value


class Other(WorkerEntrypoint):
    async def fetch(self, request):
        return Response("from other")

    async def add(self, a, b):
        return a + b


class Flow(WorkflowEntrypoint):
    async def run(self, event, step):
        return "done"


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        names = [name for name in dir(binding_tests) if name.startswith("test_")]
        failures = {}
        for name in names:
            try:
                await getattr(binding_tests, name)(self.env)
            except BaseException:  # noqa: BLE001
                failures[name] = traceback.format_exc()
        return Response(json.dumps({"ran": names, "failures": failures}))
