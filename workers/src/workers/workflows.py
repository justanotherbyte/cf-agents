"""Workflow steps in pure Python.

The stock SDK ships one JavaScript helper, which renames a Python
`NonRetryableError` so the Workflows engine stops retrying the step. A Python
`raise` can't do that, because Pyodide wraps it in a fresh `PythonError`. Here
the step callback instead returns a thenable that rejects with a JS `Error` of
that name, and the engine adopts the rejection.
"""

import functools
import inspect
from asyncio import Task, create_task, gather
from collections.abc import Callable, Coroutine
from typing import Any

import js
from pyodide.ffi import JsException, create_once_callable, to_js

from ._js import compat_flag
from .bindings import Binding
from .rpc import python_from_rpc, python_to_rpc

__all__ = ("NonRetryableError", "WorkflowStep")

NON_RETRYABLE = "NonRetryableError"

type Step = Callable[[], Coroutine[Any, Any, Any]]


class NonRetryableError(Exception):
    """Raised inside a step, stops the Workflows engine from retrying it."""


class WorkflowStep(Binding):
    """The `step` a Workflow's `run` receives. Other names pass through."""

    def __init__(self, js_object: Any) -> None:
        if js_object is self:
            return
        super().__init__(js_object)
        self._results: dict[Step, Any] = {}
        self._in_flight: dict[Step, Task[Any]] = {}
        self._steps_by_name: dict[str, Step] = {}

    def do(
        self,
        name: str | None = None,
        depends: list[Step] | None = None,
        concurrent: bool = False,
        config: dict[str, Any] | None = None,
    ) -> Callable[[Callable[..., Any]], Step]:
        """Decorate a function as a step and return an async callable that runs it.

        With `python_workflows_implicit_dependencies`, a parameter named after
        an earlier step receives its result. Without it, `depends` lists them.
        A parameter named `ctx` receives the step context either way.
        """
        implicit = compat_flag("python_workflows_implicit_dependencies")
        if implicit and depends is not None:
            raise TypeError(
                "depends is not accepted with python_workflows_implicit_dependencies;"
                " name parameters after the steps they depend on"
            )

        def decorator(func: Callable[..., Any]) -> Step:
            func_name: str = getattr(func, "__name__", name or "")
            step_name = func_name if name is None else name
            parameters = list(inspect.signature(func).parameters)
            dependencies = self._dependencies(parameters, depends, implicit)

            async def step() -> Any:
                if concurrent:
                    results = await gather(*map(self._resolve, dependencies))
                else:
                    results = [await self._resolve(d) for d in dependencies]
                return await self._run(
                    step, step_name, config, func, parameters, results
                )

            self._steps_by_name[step_name] = self._steps_by_name[func_name] = step
            return step

        return decorator

    async def sleep(self, *args: Any, **kwargs: Any) -> Any:
        return await self.js_object.sleep(*args, **kwargs)

    async def sleep_until(self, name: str, timestamp: Any) -> Any:
        if not isinstance(timestamp, str):
            timestamp = python_to_rpc(timestamp)
        return await self.js_object.sleepUntil(name, timestamp)

    async def wait_for_event(
        self, name: str, event_type: str, /, timeout: str = "24 hours"
    ) -> Any:
        options = _js_object({"type": event_type, "timeout": timeout})
        return python_from_rpc(await self.js_object.waitForEvent(name, options))

    def _dependencies(
        self, parameters: list[str], depends: list[Step] | None, implicit: bool
    ) -> list[Step]:
        """The steps whose results fill the parameters other than ctx."""
        others = [p for p in parameters if p != "ctx"]
        if not implicit:
            return self._listed_dependencies(others, depends)

        for parameter in others:
            if parameter not in self._steps_by_name:
                raise TypeError(f"Received unexpected parameter {parameter}")

        return [self._steps_by_name[p] for p in others]

    def _listed_dependencies(
        self, others: list[str], depends: list[Step] | None
    ) -> list[Step]:
        """The steps `depends` lists, checked against the parameters they fill."""
        if depends is None and others:
            raise TypeError(
                f"Step has {len(others)} non-ctx parameter(s) but no 'depends' "
                "list provided"
            )
        depends = list(depends or [])

        if len(depends) != len(others):
            raise TypeError(
                f"Step declares {len(others)} non-ctx parameter(s) but 'depends' "
                f"has {len(depends)} item(s)"
            )

        for parameter, dependency in zip(others, depends, strict=True):
            if dependency not in self._steps_by_name.values():
                raise TypeError(
                    f"'depends' entry for parameter {parameter!r} is not a "
                    "function decorated with step.do"
                )

        return depends

    async def _resolve(self, dependency: Step) -> Any:
        if dependency in self._results:
            return self._results[dependency]

        if dependency not in self._in_flight:
            self._in_flight[dependency] = create_task(dependency())
        return await self._in_flight[dependency]

    async def _run(
        self,
        key: Step,
        name: str,
        config: dict[str, Any] | None,
        func: Callable[..., Any],
        parameters: list[str],
        results: list[Any],
    ) -> Any:
        async def callback(ctx: Any = None) -> Any:
            given = iter(results)
            args = [
                python_from_rpc(ctx) if p == "ctx" else next(given) for p in parameters
            ]

            try:
                result = func(*args)
                if inspect.isawaitable(result):
                    result = await result
            except NonRetryableError as error:
                return _rejecting_thenable(error)

            return python_to_rpc(result)

        async def call() -> Any:
            args = [name] if config is None else [name, _js_object(config)]

            try:
                return python_from_rpc(await self.js_object.do(*args, callback))
            except JsException as error:
                python_error = _python_error(error)
                if python_error is error:
                    raise
                raise python_error from error

        task = create_task(call())
        self._in_flight[key] = task
        try:
            self._results[key] = result = await task
        finally:
            self._in_flight.pop(key, None)

        return result


def wrap_run(run: Callable[..., Any]) -> Callable[..., Any]:
    """Convert the event and step workerd passes `run` as raw JS values."""

    @functools.wraps(run)
    async def wrapped(
        self: Any, event: Any = None, step: Any = None, /, *args: Any, **kwargs: Any
    ) -> Any:
        if event is not None:
            event = python_from_rpc(event)
        if step is not None:
            step = WorkflowStep(step)

        result = run(self, event, step, *args, **kwargs)
        if inspect.isawaitable(result):
            result = await result

        return None if result is None else python_to_rpc(result)

    return wrapped


def _js_object(value: dict[str, Any]) -> Any:
    return to_js(value, dict_converter=js.Object.fromEntries)


def _rejecting_thenable(error: NonRetryableError) -> Any:
    js_error = js.Error.new(str(error))
    js_error.name = NON_RETRYABLE
    # A thenable, so no rejected promise exists before the engine adopts it.
    return _js_object(
        {"then": create_once_callable(lambda _resolve, reject: reject(js_error))}
    )


_TRACEBACK_ERRORS: dict[str, type[Exception]] = {
    "TypeError": TypeError,
    "ValueError": ValueError,
    "workers.workflows.NonRetryableError": NonRetryableError,
}

_JS_ERRORS: dict[str | None, type[Exception]] = {
    "RangeError": ValueError,
    "TypeError": TypeError,
}


def _python_error(error: JsException) -> Exception:
    """The Python exception for a step error the engine rethrew."""
    message = error.message or ""

    # The name survives the engine, or is folded into the message.
    if error.name == NON_RETRYABLE:
        return NonRetryableError(message)
    if message == NON_RETRYABLE or message.startswith(f"{NON_RETRYABLE}: "):
        return NonRetryableError(message[len(NON_RETRYABLE) + 2 :])

    # A Python exception that escaped a step comes back as a PythonError whose
    # message is the traceback, ending with the exception's own line.
    lines = message.rstrip().split("\n")
    if len(lines) > 1 and (
        error.name == "PythonError" or message.startswith("PythonError")
    ):
        for prefix, error_type in _TRACEBACK_ERRORS.items():
            if lines[-1].startswith(prefix):
                return error_type(lines[-1])

    error_type = _JS_ERRORS.get(error.name)
    return error if error_type is None else error_type(message)
