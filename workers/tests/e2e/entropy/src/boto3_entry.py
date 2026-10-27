# botocore does `from urllib3.util.ssl_ import ssl`, but crosswind imports
# urllib3.util.ssl_ without ssl to avoid entropy calls, which leaves it unbound.
import boto3

from workers import Response, WorkerEntrypoint


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return Response(boto3.__name__)
