"""Module-level helpers with the stock SDK's names and import paths."""

from typing import Any, NoReturn

import _pyodide_entrypoint_helper
from pyodide.ffi import JsException

__all__ = ("import_from_javascript", "patch_env")

# JS modules workerd imports ahead of time, by the helper attribute holding each.
_PRELOADED = {
    "cloudflare:workers": "cloudflareWorkersModule",
    "cloudflare:sockets": "cloudflareSocketsModule",
}


def import_from_javascript(module_name: str) -> Any:
    """Import a JS module, such as "cloudflare:sockets", from Python.

    "cloudflare:workers" and "cloudflare:sockets" are imported ahead of time,
    so they work everywhere. Any other module goes through run_sync, which
    Python 3.12 Workers lack. On 3.13 and 3.14 it works at top level too.
    """
    if module_name in _PRELOADED:
        return getattr(_pyodide_entrypoint_helper, _PRELOADED[module_name])

    failed = f"Failed to import '{module_name}'"
    try:
        from pyodide.ffi import run_sync
    except ImportError as error:
        raise ImportError(
            f"{failed}: Only 'cloudflare:workers' and 'cloudflare:sockets' are "
            "available until the next python runtime version."
        ) from error

    try:
        return run_sync(_pyodide_entrypoint_helper.doAnImport(module_name))
    except JsException as error:
        raise ImportError(f"{failed}: {error}") from error
    except RuntimeError as error:
        if error.args and error.args[0] == "No suspender":
            raise ImportError(
                f"{failed}: Only 'cloudflare:workers' and 'cloudflare:sockets' "
                "are available in the global scope."
            ) from error
        raise


def patch_env(*args: Any, **kwargs: Any) -> NoReturn:
    """Removed from workerd. The stock SDK still exports it, so crosswind does."""
    raise NotImplementedError(
        "patch_env needs a workerd helper that workerd removed on 2026-06-30 "
        "(commit 6581cf0), so it can't swap env. Pass the values you need "
        "explicitly instead."
    )
