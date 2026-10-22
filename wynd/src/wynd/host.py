import re
import sys

from .project import Project
from .targets import Target

__all__ = ("warn_on_venv_mismatch",)


def warn_on_venv_mismatch(project: Project, target: Target) -> None:
    """Warn when the editor's .venv runs another Python minor than the target."""
    try:
        pyvenv = (project.root / ".venv" / "pyvenv.cfg").read_text()
    except FileNotFoundError:
        return

    match = re.search(r"^version_info\s*=\s*(\d+\.\d+)", pyvenv, re.MULTILINE)
    if match is None or match.group(1) == target.version:
        return

    config = project.config
    flags = ", ".join(f for f in config.compatibility_flags if "python_workers" in f)
    print(
        f"warning: .venv is Python {match.group(1)} but {config.source.name}"
        f" targets {target.version} ({flags}, {config.compatibility_date}).\n"
        "Editor types may differ from production."
        f" To match: uv venv --python {target.version} && uv sync",
        file=sys.stderr,
    )
