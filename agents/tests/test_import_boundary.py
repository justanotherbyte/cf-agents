"""The MCP client must never load the MCP SDK or its dependency tree.

Each check runs in a fresh interpreter, since this test process has the SDK
loaded for the contract tests (.design/mcp_engine.md §10).
"""

import subprocess
import sys
from pathlib import Path

TESTS = Path(__file__).parent

HEAVY = ("mcp", "mcp_types", "pydantic", "pydantic_core", "httpx2", "anyio")

PRELUDE = f"""
import sys
sys.path.insert(0, {str(TESTS)!r})
import conftest
"""


def run(code: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", PRELUDE + code],
        capture_output=True,
        text=True,
        check=False,
    )


def test_client_imports_load_no_sdk() -> None:
    result = run(
        f"""
import agents, agents.chat, agents.mcp
loaded = sorted({{name.partition(".")[0] for name in sys.modules}} & set({HEAVY!r}))
print(loaded)
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_an_agent_with_mcp_on_loads_no_sdk() -> None:
    result = run(
        f"""
import asyncio
from fake_runtime import FakeCtx
from agents import Agent, AgentOptions, MCPOptions

class WithMcp(Agent):
    options = AgentOptions(mcp=MCPOptions())

agent = WithMcp(FakeCtx("a"), env=None)
asyncio.run(agent.lifecycle.start())
assert "agents.mcp.manager" in sys.modules
loaded = sorted({{name.partition(".")[0] for name in sys.modules}} & set({HEAVY!r}))
print(loaded)
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "[]"


def test_agents_without_mcp_skip_the_client() -> None:
    result = run(
        """
from fake_runtime import FakeCtx
from agents import Agent

Agent(FakeCtx("a"), env=None)
print("agents.mcp" in sys.modules)
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"


def test_server_needs_the_extra() -> None:
    result = run(
        """
sys.modules["mcp"] = None
try:
    import agents.mcp.server
except ImportError as error:
    print("mcp-server" in str(error))
"""
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True"
