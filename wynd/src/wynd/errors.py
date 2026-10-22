__all__ = ("ProjectNotFoundError", "WyndError")


class WyndError(Exception):
    """An error Wynd reports to the user as one message."""


class ProjectNotFoundError(WyndError):
    """No pyproject.toml or no Worker config, so there is no project to read."""
