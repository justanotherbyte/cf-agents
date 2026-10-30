<h1 align="center">
<sub>
    <img src="https://raw.githubusercontent.com/justanotherbyte/crosswind/refs/heads/main/agents/.github/agents.svg" height="36">
</sub>
&nbsp;
cf-agents
</h1>
<p align="center">
<sup>
Cloudflare Agents for Python Workers
</sup>
</p>


[![PyPI - Version](https://img.shields.io/pypi/v/cf-agents.svg)](https://pypi.org/project/cf-agents)
[![pypi](https://img.shields.io/pypi/pyversions/cf-agents.svg)](https://pypi.org/pypi/cf-agents)

> [!note]
> This is an unofficial SDK and not affiliated with Cloudflare. I started a port of the official [Agents SDK](https://agents.cloudflare.com) during my internship at Cloudflare, and this project continues that work.
>
> This project is part of [Crosswind](https://crosswind.viswa.space).

Use `cf-agents` to keep state, schedule work and serve WebSocket clients from a Python Worker.

# What it does

- The SDK provides a typed, `async` API for Python 3.12 or later.
- The SDK makes the JavaScript runtime calls from your Python code.
- The API and behaviour match the TypeScript [Agents SDK](https://github.com/cloudflare/agents), so TypeScript clients such as `useAgent` connect unchanged.

# Install

Create a project with [uv](https://docs.astral.sh/uv/) and [pywrangler](https://developers.cloudflare.com/workers/languages/python/), then add the package. [Wynd](https://crosswind.viswa.space/wynd/overview/) also works.

```shell
$ uvx --from workers-py pywrangler init
$ uv add cf-agents
```

# Quickstart

This example runs a team's daily lunch vote. Its `Agent` keeps state, runs schedules and exposes `@callable` methods to WebSocket clients. Replace `search_menus_by_agent` and `choose_winners` with your own code.

```python
from typing import TypedDict

from agents import Agent, Schedule, callable, route_agent_request
from workers import WorkerEntrypoint, Request, Response

from utils import search_menus_by_agent, choose_winners

MODEL = "@cf/zai-org/glm-4.7-flash"
SYSTEM_PROMPT = """
You help deliver results to a bunch of co-workers who are choosing lunch together.
The user is going to provide you with the options.
Your task is to make the choice sound exciting so people who voted for something
else feel validated.
"""


class Restaurant(TypedDict):
    cuisine: str
    name: str
    address: str


class Vote(TypedDict):
    username: str
    restaurant_name: str


class LunchState(TypedDict):
    office_address: str
    todays_votes: list[Vote]
    todays_ruling: str | None
    restaurants: list[Restaurant]


class LunchAgent(Agent[LunchState]):
    initial_state = LunchState(
        office_address="County Hall, London",
        todays_votes=[],
        todays_ruling=None,
        restaurants=[],
    )

    async def on_start(self) -> None:
        # Cron expressions, evaluated in UTC
        await self.schedule("30 23 * * 1-5", self.choose_lunch)
        await self.schedule("0 17 * * *", self.reset_lunch)

    @callable
    async def nominate_restaurant(self, restaurant_name: str) -> None:
        # A Workflow researches the restaurant, stores its menu in
        # Vectorize, then updates this agent's list of restaurants
        await self.env.RESTAURANT_RESEARCHER_WORKFLOW.create(
            {
                "params": {
                    "restaurantName": restaurant_name,
                    "agent": self.name,
                    "near": self.state["office_address"],
                }
            }
        )

    @callable
    async def search_restaurants(self, query: str) -> list[str]:
        # Vector search, filtered by metadata to this agent's menus
        results = await search_menus_by_agent(query, self.name)
        return [result["metadata"]["restaurantName"] for result in results]

    @callable
    async def vote(self, username: str, restaurant_name: str) -> None:
        state = self.state
        vote = Vote(username=username, restaurant_name=restaurant_name)
        votes = [*state["todays_votes"], vote]
        # Broadcasts the new state to every connected eater
        self.set_state({**state, "todays_votes": votes})

    async def reset_lunch(self, payload: None, schedule: Schedule) -> None:
        self.set_state({**self.state, "todays_votes": [], "todays_ruling": None})

    async def choose_lunch(self, payload: None, schedule: Schedule) -> None:
        winners = choose_winners(self.state["todays_votes"])
        result = await self.env.AI.run(
            MODEL,
            {
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": ", ".join(winners)},
                ]
            },
        )
        self.set_state({**self.state, "todays_ruling": result.response})


class Default(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        response = await route_agent_request(request, self.env)
        return response or Response("Not Found", status=404)
```

For a chat agent that stores messages and streams model replies, use [`AIChatAgent`](https://crosswind.viswa.space/agents/chat/).

# Goals

`cf-agents` has one maintainer, so the scope is limited. Planned:

- [ ] MCP
- [ ] Channels
- [ ] Integration with [Agent Traces](https://developers.cloudflare.com/agents/runtime/operations/observability/tracing/)
- [ ] A harness/opinionated framework, similar to [Think](https://developers.cloudflare.com/agents/harnesses/think/)
- [ ] Integrations with popular Agent libraries in the Python eco-system (such as [LangChain](https://www.langchain.com/langchain) or [CrewAI](https://crewai.com/))

Some of these need changes to the Python Workers runtime first. A Python Worker also exceeds the free plan's limits easily today.


# Links

- [Documentation](https://crosswind.viswa.space/agents/getting-started/)
- [Crosswind](https://crosswind.viswa.space)
