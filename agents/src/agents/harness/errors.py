"""Errors raised by harnesses."""

from ..core.errors import AgentsException

__all__ = ("OperationNotFoundError", "SessionBusyError", "SessionNotFoundError")


class SessionBusyError(AgentsException):
    """A session has open work and the call refuses to queue behind it."""

    def __init__(self, session: str) -> None:
        super().__init__(f"Harness session {session!r} has an operation open")
        self.session = session


class SessionNotFoundError(AgentsException):
    """No session has this id."""

    def __init__(self, session: str) -> None:
        super().__init__(f"Harness session {session!r} doesn't exist")
        self.session = session


class OperationNotFoundError(AgentsException):
    """No operation in the session has this id."""

    def __init__(self, session: str, operation_id: str) -> None:
        super().__init__(
            f"Harness session {session!r} has no operation {operation_id!r}"
        )
        self.session = session
        self.operation_id = operation_id
