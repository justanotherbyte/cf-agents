import sys

import workers
from workers import Response, WorkerEntrypoint


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        version = f"{sys.version_info.major}.{sys.version_info.minor}"
        return Response(f"{version} {workers.__file__}")

    # The stock SDK has no alias for the name workerd calls before 2025-08-14.
    on_fetch = fetch
