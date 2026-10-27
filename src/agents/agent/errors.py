"""Exceptions raised by the Agent class."""

from ..core.errors import AgentsException

__all__ = ("MCPNotEnabledError", "ReadonlyConnectionError")


class ReadonlyConnectionError(AgentsException):
    """``set_state`` was called while serving a readonly connection."""

    def __init__(self) -> None:
        super().__init__("Connection is readonly")


class MCPNotEnabledError(AgentsException):
    """An MCP member was used on an agent without ``AgentOptions(mcp=...)``."""

    def __init__(self) -> None:
        super().__init__(
            "MCP isn't enabled on this agent; "
            "set options = AgentOptions(mcp=MCPOptions())"
        )
