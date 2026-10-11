from typing import Any

cloudflareWorkersModule: Any
cloudflareSocketsModule: Any

def doAnImport(module_name: str) -> Any: ...
def patchWaitUntil(ctx: Any) -> None: ...
