import shutil
import signal
import subprocess
import sys

__all__ = ("VERSION", "run")

VERSION = "4.129.0"


def run(args: list[str]) -> int:
    """Run the pinned Wrangler with `args` and return its exit code."""
    # which() also finds npx.cmd on Windows, which Popen can't find by name.
    npx = shutil.which("npx")
    if npx is None:
        print(
            "wynd: npx not found on PATH. Install Node.js, which provides npx.",
            file=sys.stderr,
        )
        return 1

    process = subprocess.Popen([npx, "--yes", f"wrangler@{VERSION}", *args])
    # Ignore Ctrl-C after spawning, so Wrangler gets it from the terminal and
    # shuts down in its own time instead of being killed by subprocess.
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        code = process.wait()
    finally:
        signal.signal(signal.SIGINT, previous)

    # Popen reports death by signal N as -N. A shell reports 128 + N.
    return 128 - code if code < 0 else code
