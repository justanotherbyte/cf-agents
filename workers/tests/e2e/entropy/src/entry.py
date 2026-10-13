import numpy
import pydantic

from workers import Response, WorkerEntrypoint


class Item(pydantic.BaseModel):
    name: str


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        item = Item.model_validate({"name": "ok"})
        return Response(f"{item.name} {numpy.random.random()}")
