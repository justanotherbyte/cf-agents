import json
from urllib.parse import parse_qs, urlparse

from workers import Response, WorkerEntrypoint, WorkflowEntrypoint
from workers.workflows import NonRetryableError

ATTEMPTS = {"non_retryable": 0, "retryable": 0}
RETRIES = {"retries": {"limit": 2, "delay": "1 second", "backoff": "constant"}}


class Flow(WorkflowEntrypoint):
    async def run(self, event, step):
        @step.do("non_retryable", config=RETRIES)
        async def non_retryable():
            ATTEMPTS["non_retryable"] += 1
            raise NonRetryableError("stop")

        @step.do("retryable", config=RETRIES)
        async def retryable():
            ATTEMPTS["retryable"] += 1
            raise ValueError("again")

        errors = {}
        for name, run_step in [
            ("non_retryable", non_retryable),
            ("retryable", retryable),
        ]:
            try:
                await run_step()
            except (NonRetryableError, ValueError) as error:
                errors[name] = [type(error).__name__, str(error)]
        return {"attempts": ATTEMPTS, "errors": errors}


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        url = urlparse(request.url)
        if url.path == "/start":
            instance = await self.env.FLOW.create()
            return Response(instance.id)
        instance = await self.env.FLOW.get(parse_qs(url.query)["id"][0])
        return Response(json.dumps(await instance.status()))
