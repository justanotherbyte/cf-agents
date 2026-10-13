from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import asgi
from workers import WorkerEntrypoint

app = FastAPI()


class Item(BaseModel):
    name: str


@app.get("/")
async def root(request: Request):
    return {"greeting": request.scope["env"].GREETING}


@app.get("/env")
async def env_dependency(env=asgi.env):
    return {"greeting": env.GREETING}


@app.post("/items")
async def create(item: Item):
    return {"name": item.name.upper()}


@app.get("/stream")
async def stream():
    async def chunks():
        for i in range(3):
            yield f"{i}\n"

    return StreamingResponse(chunks(), media_type="text/plain")


@app.get("/sync")
def sync_route():
    # FastAPI runs a sync route through anyio.to_thread, which crosswind's
    # package patch runs inline.
    return {"sync": True}


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return await asgi.fetch(app, request, self.env, self.ctx)
