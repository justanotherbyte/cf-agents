import asgi
from fastapi import FastAPI

# pydantic loads field_serializer's module lazily, by a computed name.
from pydantic import BaseModel, field_serializer
from workers import WorkerEntrypoint


class Item(BaseModel):
    name: str

    @field_serializer("name")
    def shout(self, name: str) -> str:
        return name.upper()


app = FastAPI()


@app.get("/")
async def root() -> Item:
    return Item(name="pruned")


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return await asgi.fetch(app, request, self.env)

    # The stock SDK has no alias for the name workerd calls before 2025-08-14.
    on_fetch = fetch
