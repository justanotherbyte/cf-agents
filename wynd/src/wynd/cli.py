import sys

from wynd import wrangler


def main(argv: list[str] | None = None) -> int:
    """Run a `wynd` command line. Every command passes through to Wrangler."""
    return wrangler.run(sys.argv[1:] if argv is None else argv)
