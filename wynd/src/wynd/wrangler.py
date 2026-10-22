import shutil
import signal
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

__all__ = ("VERSION", "run")

VERSION = "4.129.0"


def run(
    args: list[str],
    *,
    environ: dict[str, str] | None = None,
    watch: Path | None = None,
    restage: Callable[[], object] | None = None,
) -> int:
    """Run the pinned Wrangler with `args` and return its exit code.

    `environ` replaces the environment Wrangler inherits. While Wrangler runs,
    `restage` is called within a second of each change to `watch`, because
    wrangler dev watches only the staged file, not the one the user edits.
    """
    # which() also finds npx.cmd on Windows, which Popen can't find by name.
    npx = shutil.which("npx")
    if npx is None:
        print(
            "wynd: npx not found on PATH. Install Node.js, which provides npx.",
            file=sys.stderr,
        )
        return 1

    process = subprocess.Popen(
        [npx, "--yes", f"wrangler@{VERSION}", *args], env=environ
    )
    # Ignore Ctrl-C after spawning, so Wrangler gets it from the terminal and
    # shuts down in its own time instead of being killed by subprocess.
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        code = _wait(process, watch, restage)
    finally:
        signal.signal(signal.SIGINT, previous)

    # Popen reports death by signal N as -N. A shell reports 128 + N.
    return 128 - code if code < 0 else code


def _wait(
    process: subprocess.Popen[bytes],
    watch: Path | None,
    restage: Callable[[], object] | None,
) -> int:
    if watch is None or restage is None:
        return process.wait()

    seen = _mtime(watch)
    while True:
        try:
            return process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        if (mtime := _mtime(watch)) != seen:
            seen = mtime
            restage()


def _mtime(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except FileNotFoundError:
        return None
