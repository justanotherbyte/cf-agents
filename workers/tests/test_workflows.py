import asyncio
from typing import Any

import fake_js
import pytest
from fake_js import JsValue
from pyodide.ffi import JsException

from workers import WorkflowEntrypoint
from workers.workflows import NonRetryableError, WorkflowStep


class Engine:
    """Stands in for the Workflows engine's step: runs each callback once."""

    def __init__(self) -> None:
        self.steps: list[str] = []

    async def do(self, name: str, *rest: Any) -> Any:
        self.steps.append(name)
        result = await rest[-1](JsValue("Object", {"attempt": 1}))
        then = result.get("then") if isinstance(result, dict) else None
        if then is not None:
            # JS promise resolution adopts a thenable's rejection.
            rejections: list[Any] = []
            then(lambda value: None, rejections.append)
            raise JsException(rejections[0].name, rejections[0].message)
        return result


@pytest.fixture
def implicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        fake_js.COMPAT_FLAGS, "python_workflows_implicit_dependencies", True, False
    )


def run_workflow(workflow: type[Any], engine: Engine) -> Any:
    instance = workflow(JsValue("ExecutionContext"), JsValue("Object", {}))
    return asyncio.run(instance.run(JsValue("Object", {"n": 2}), engine))


def test_a_non_retryable_error_reaches_the_engine_by_name() -> None:
    class Flow(WorkflowEntrypoint):
        async def run(self, event: Any, step: WorkflowStep) -> Any:
            @step.do("fails")
            async def fails() -> None:
                raise NonRetryableError("stop")

            try:
                await fails()
            except NonRetryableError as error:
                return str(error)

    assert run_workflow(Flow, Engine()) == "stop"


def test_a_python_error_inside_step_do_is_not_remapped() -> None:
    class BrokenEngine(Engine):
        async def do(self, name: str, *rest: Any) -> Any:
            raise KeyError("not a JS error")

    class Flow(WorkflowEntrypoint):
        async def run(self, event: Any, step: WorkflowStep) -> Any:
            @step.do("one")
            async def one() -> int:
                return 1

            await one()

    with pytest.raises(KeyError, match="not a JS error"):
        run_workflow(Flow, BrokenEngine())


@pytest.mark.usefixtures("implicit")
def test_implicit_dependencies_resolve_once_by_step_or_function_name() -> None:
    engine = Engine()

    class Flow(WorkflowEntrypoint):
        async def run(self, event: Any, step: WorkflowStep) -> Any:
            @step.do("first-step")
            async def first() -> int:
                return event["n"]

            @step.do()
            async def second(first: int, ctx: Any) -> int:
                return first * 10 + ctx["attempt"]

            @step.do(concurrent=True)
            async def third(first: int, second: int) -> list[int]:
                return [first, second]

            return await third()

    assert run_workflow(Flow, engine) == [2, 21]
    assert engine.steps == ["first-step", "second", "third"]


@pytest.mark.usefixtures("implicit")
def test_a_none_result_still_feeds_a_dependent_step() -> None:
    class Flow(WorkflowEntrypoint):
        async def run(self, event: Any, step: WorkflowStep) -> Any:
            @step.do()
            async def nothing() -> None:
                return None

            @step.do()
            async def after(nothing: None, ctx: Any) -> list[Any]:
                return [nothing is None, ctx["attempt"]]

            return await after()

    assert run_workflow(Flow, Engine()) == [True, 1]


def test_legacy_steps_name_dependencies_with_depends() -> None:
    class Flow(WorkflowEntrypoint):
        async def run(self, event: Any, step: WorkflowStep) -> Any:
            @step.do("first")
            async def first() -> int:
                return 3

            @step.do("second", depends=[first])
            async def second(value: int) -> int:
                return value + 1

            return await second()

    assert run_workflow(Flow, Engine()) == 4


def test_a_legacy_parameter_without_depends_is_refused() -> None:
    class Flow(WorkflowEntrypoint):
        async def run(self, event: Any, step: WorkflowStep) -> Any:
            @step.do("orphan")
            async def orphan(value: int) -> int:
                return value

    with pytest.raises(TypeError, match="no 'depends' list"):
        run_workflow(Flow, Engine())


@pytest.mark.usefixtures("implicit")
def test_concurrent_branches_run_a_shared_step_once() -> None:
    class YieldingEngine(Engine):
        async def do(self, name: str, *rest: Any) -> Any:
            await asyncio.sleep(0)
            return await super().do(name, *rest)

    engine = YieldingEngine()

    class Flow(WorkflowEntrypoint):
        async def run(self, event: Any, step: WorkflowStep) -> Any:
            @step.do()
            async def a() -> int:
                return 1

            @step.do()
            async def x(a: int) -> int:
                return a

            @step.do()
            async def b(x: int) -> int:
                return x

            @step.do()
            async def c(x: int) -> int:
                return x

            @step.do(concurrent=True)
            async def d(b: int, c: int) -> int:
                return b + c

            return await d()

    assert run_workflow(Flow, engine) == 2
    assert engine.steps == ["a", "x", "b", "c", "d"]
