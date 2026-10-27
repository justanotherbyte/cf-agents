"""Exceptions for sub-agents."""

from ..core.errors import AgentsException

__all__ = (
    "InvalidSubAgentNameError",
    "SubAgentAbortedError",
    "UnknownSubAgentClassError",
)


class SubAgentAbortedError(AgentsException):
    """A sub-agent was aborted (``dynamic_agents.abort``) while a call was pending.

    The default reason ``abort`` raises in pending callers.
    """


class InvalidSubAgentNameError(ValueError):
    """A sub-agent name can't be used (it contains NUL)."""


class UnknownSubAgentClassError(ValueError):
    """A sub-agent class isn't exported from the Worker under that name."""
