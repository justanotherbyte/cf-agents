from dataclasses import dataclass
from datetime import date, datetime

from .project import Project, WyndError

__all__ = ("Target", "select_target")


@dataclass(frozen=True)
class Target:
    version: str
    flag: str
    since: date | None
    interpreter: str
    index: str

    def matches(self, flags: tuple[str, ...], day: date) -> bool:
        """Whether a config with these flags and date runs this Python."""
        return self.flag in flags or (self.since is not None and day >= self.since)


# Copied from pywrangler at cb2db3c (metadata.py and utils.py), newest first.
TARGETS = (
    Target(
        "3.14",
        "python_workers_314",
        date(2026, 9, 8),
        "cpython-3.14.2-emscripten-wasm32-musl",
        "https://index.pyodide.org/314.0.7",
    ),
    Target(
        "3.13",
        "python_workers_20250116",
        date(2025, 9, 29),
        "cpython-3.13.2-emscripten-wasm32-musl",
        "https://index.pyodide.org/0.28.3",
    ),
    Target(
        "3.12",
        "python_workers",
        None,
        "cpython-3.12.7-emscripten-wasm32-musl",
        "https://index.pyodide.org/0.27.7",
    ),
)


def select_target(project: Project) -> Target:
    flags = project.compatibility_flags
    found = (
        f"{project.config_path.name} has compatibility_date "
        f"{project.compatibility_date!r} and compatibility_flags {list(flags)}"
    )

    if project.compatibility_date is None:
        raise WyndError(f"{found}. Set a compatibility_date.")
    if "python_workers" not in flags:
        raise WyndError(f"{found}. Add python_workers to compatibility_flags.")
    try:
        day = datetime.strptime(project.compatibility_date, "%Y-%m-%d").date()
    except ValueError:
        raise WyndError(f"{found}. Write the date as YYYY-MM-DD.") from None

    for target in TARGETS:
        if target.matches(flags, day):
            return target
    raise AssertionError("the 3.12 row matches every python_workers config")
