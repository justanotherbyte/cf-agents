"""AIChatAgent waits for restoring MCP servers before each turn."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from fake_runtime import FakeCtx

from agents import (
    AIChatAgent,
    AIChatAgentOptions,
    ChatMessageOptions,
    MCPOptions,
    TextPart,
    UIMessage,
)

HI = [UIMessage(id="u1", role="user", parts=[TextPart(text="hi")])]


class Waiting(AIChatAgent):
    options = AIChatAgentOptions(mcp=MCPOptions(), wait_for_mcp_connections=True)

    def __init__(self, ctx: Any, env: Any) -> None:
        super().__init__(ctx, env)
        self.log: list[str] = []
        self.release = asyncio.Event()
        self.timeouts: list[Any] = []

        async def wait_for_connections(*, timeout: Any = None) -> None:
            self.timeouts.append(timeout)
            self.log.append("waiting")
            await self.release.wait()

        self.mcp.wait_for_connections = wait_for_connections  # ty: ignore[invalid-assignment]

    async def on_chat_message(self, options: ChatMessageOptions) -> AsyncIterator[Any]:
        self.log.append("turn")
        return
        yield


class Bounded(Waiting):
    options = AIChatAgentOptions(mcp=MCPOptions(), wait_for_mcp_connections=2.5)


class NotWaiting(Waiting):
    options = AIChatAgentOptions(mcp=MCPOptions())


def test_a_turn_waits_for_connections() -> None:
    agent = Waiting(FakeCtx("chat"), env=None)

    async def main() -> None:
        await agent.lifecycle.start()
        turn = asyncio.ensure_future(agent.save_messages(HI))
        await asyncio.sleep(0.05)
        assert agent.log == ["waiting"]
        agent.release.set()
        await turn
        assert agent.log == ["waiting", "turn"]
        assert agent.timeouts == [None]

    asyncio.run(main())


def test_a_duration_bounds_the_wait_and_false_skips_it() -> None:
    bounded = Bounded(FakeCtx("chat"), env=None)
    plain = NotWaiting(FakeCtx("chat"), env=None)

    async def main() -> None:
        for agent in (bounded, plain):
            agent.release.set()
            await agent.lifecycle.start()
            await agent.save_messages(HI)

    asyncio.run(main())
    assert bounded.timeouts == [2.5]
    assert plain.log == ["turn"]


def test_a_stop_ends_the_wait() -> None:
    agent = Waiting(FakeCtx("chat"), env=None)

    async def main() -> Any:
        await agent.lifecycle.start()
        turn = asyncio.ensure_future(agent.save_messages(HI))
        await asyncio.sleep(0.05)
        agent.abort_request(next(iter(agent._consuming)))
        return await turn

    result = asyncio.run(main())
    assert result.status == "aborted"
    assert agent.log == ["waiting"]
