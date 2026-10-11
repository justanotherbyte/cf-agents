import sys

import workers
from workers import Request, Response, WorkerEntrypoint


class Default(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        version = f"{sys.version_info.major}.{sys.version_info.minor}"
        return Response(f"{version} {request.method} {workers.__file__}", status=201)
