"""Package patches that workerd leaves to the SDK.

With `python_process_pth_files` on, workerd stops bundling its per-package
entropy patches and runs `.pth` files instead, and `crosswind_workers_patches.pth`
imports this package at startup. The patches are ported from the stock SDK at
cb2db3c. They hook imports through workerd's `_cloudflare` package, so a patch
applies only when its package is imported, and nothing happens outside workerd.
"""

import importlib.util

if importlib.util.find_spec("_cloudflare") is not None:
    from . import entropy, runtime  # noqa: F401
