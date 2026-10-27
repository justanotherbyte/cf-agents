# pyodide-py ships no stub for `js`, the JS global scope.
from typing import Any

def __getattr__(name: str) -> Any: ...
