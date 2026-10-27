from rich.console import Console
from rich.text import Text

__all__ = ("choices", "error", "info", "warn")

# soft_wrap, so a long path never breaks across lines in a pipe or a log.
# Messages are Text, so brackets in them aren't read as Rich markup.
out = Console(soft_wrap=True, highlight=False)
err = Console(stderr=True, soft_wrap=True, highlight=False)


def info(message: str) -> None:
    """Report progress on stdout."""
    out.print(Text.assemble(("wynd:", "bold cyan"), " ", message))


def warn(message: str) -> None:
    """Report something to fix on stderr. The command still runs."""
    err.print(Text.assemble(("wynd: warning:", "bold yellow"), " ", message))


def error(message: str) -> None:
    """Report why the command stopped on stderr."""
    err.print(Text.assemble(("wynd: error:", "bold red"), " ", message))


def choices(items: list[str] | tuple[str, ...]) -> None:
    """List `items` as numbered choices for a prompt."""
    for number, item in enumerate(items, 1):
        out.print(Text.assemble((f"  {number})", "bold"), " ", item))
