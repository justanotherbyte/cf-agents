"""The real ``agents._ffi`` must import on the Python 3.12 Workers runtime.

The other tests replace ``agents._ffi``, so this one loads the real module, in
a fresh interpreter, under a ``pyodide.ffi`` shaped like Pyodide 0.27's.
"""

import subprocess
import sys
from pathlib import Path

TESTS = Path(__file__).parent
FFI = TESTS.parent / "src" / "agents" / "_ffi.py"

CODE = f"""
import importlib.util, sys, types
sys.path.insert(0, {str(TESTS)!r})
import conftest

ffi = types.ModuleType("pyodide.ffi")  # Pyodide 0.27: no jsnull
for name in ("JsException", "JsProxy", "create_once_callable", "create_proxy", "to_js"):
    setattr(ffi, name, type(name, (Exception,), {{}}))
sys.modules["js"] = types.ModuleType("js")
sys.modules["pyodide"] = types.ModuleType("pyodide")
sys.modules["pyodide.ffi"] = ffi

spec = importlib.util.spec_from_file_location("agents._ffi", {str(FFI)!r})
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print(module._prepare(None), module.js_to_py(None))
"""


def test_imports_without_jsnull() -> None:
    result = subprocess.run(
        [sys.executable, "-c", CODE], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "None None"
