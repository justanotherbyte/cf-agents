"""`import asgi`, the stock SDK's older import path for `workers.asgi`."""

import sys

from workers import asgi as _asgi

sys.modules[__name__] = _asgi
