---
title: "Cloudflare Agents SDK for Python Workers"
description: "What cf-agents does, how an Agent maps to a Durable Object, which parts of the TypeScript Agents SDK it ports, and what isn't ported yet."
---

`cf-agents` is a Python port of the
[Cloudflare Agents SDK](https://developers.cloudflare.com/agents/). An agent
is a Python class that keeps state, talks to browsers over WebSockets, runs
scheduled and background work, and can stream chat replies from a model.

```python
from agents import Agent, callable


class Counter(Agent):
    initial_state = {"count": 0}

    @callable
    async def increment(self, by: int = 1) -> int:
        self.set_state({"count": self.state["count"] + by})
        return self.state["count"]
```

A browser connected with the `useAgent` React hook can call
`agent.stub.increment(1)`. Every connected client then receives the new state.

## How it works

Each agent instance is a
[Durable Object](https://developers.cloudflare.com/durable-objects/): a class
instance with its own SQLite database and a unique name. Every request for one
name reaches the same instance, so it can hold state between requests and
connections without a separate database.

A request reaches an agent in three steps:

1. A browser opens a WebSocket with `useAgent`, or `curl` or `fetch` sends an
   HTTP request, to your Worker.
2. The Worker calls `route_agent_request`, which forwards the request to the
   Durable Object with that name, such as `room-7`.
3. The Durable Object runs your `Agent` subclass. It holds the state it syncs
   to clients, its SQLite database (`self.sql(...)`), its schedules and queue,
   and its durable tasks.

`route_agent_request` forwards `/agents/{agent}/{name}` to the instance called
`{name}`. `{agent}` is the binding name in kebab case, so the binding `Counter`
answers at `/agents/counter/{name}`.

When no request, message or alarm is running, the runtime can evict the agent
from memory, even while clients are connected. Their WebSockets stay open
while the agent hibernates. The next message, request or alarm wakes it and
calls `on_start()` again, so keep anything you need between events in state
or SQL, not in instance attributes.

## Works with the JavaScript clients

`cf-agents` speaks the same WebSocket protocol as the TypeScript SDK. The
browser packages connect to a Python agent unchanged:

- `useAgent` and `AgentClient` from the
  [`agents`](https://www.npmjs.com/package/agents) npm package, for state and
  `@callable` methods.
- `useAgentChat` from
  [`@cloudflare/ai-chat`](https://www.npmjs.com/package/@cloudflare/ai-chat),
  for chat agents.

## What the SDK covers

| Area                  | Python API                                                 |
| --------------------- | ---------------------------------------------------------- |
| State                 | `initial_state`, `self.state`, `set_state()`               |
| Client RPC            | `@callable` methods, including streaming ones              |
| WebSocket connections | `on_connect`, `on_message`, `broadcast()`, connection tags |
| HTTP                  | `on_request`, `route_agent_request`, `get_agent_by_name`   |
| Storage               | `self.sql(...)` over the agent's SQLite database           |
| Timed work            | `schedule()`, `schedule_every()`, cron expressions         |
| Background work       | `queue()`, `@task` with durable steps, fibers              |
| Chat                  | `AIChatAgent` with `on_chat_message`                       |
| Sub-agents            | `self.dynamic_agents`, child agents with their own SQLite  |

`AIChatAgent` doesn't call a model for you. You call any model client you
like in `on_chat_message` and yield text or typed chunks.

## Not ported yet

- MCP clients and servers.
- Chat client tools, tool approvals and automatic recovery of interrupted chat
  turns.
- Voice, email, workflows, channels and browser automation.
- The experimental Cap'n Web transport.
- Tracing. Structured events are ported.

## Install

Add the package to a Python Worker project:

```sh
uv add cf-agents
```

It needs Python 3.12 or later. To create a project first, follow
[Getting started](getting-started.md).

## Status

These pages cover `cf-agents` 0.1.3, which is alpha. Its API may change
between minor releases. It is an independent community project, not an
official Cloudflare SDK. It ports `agents` v0.25.0 and the `AIChatAgent` from
`@cloudflare/ai-chat`.

## Next steps

- [Getting started](getting-started.md) builds a counter agent and calls it
  over HTTP.
- [Agent class](agent-class.md) lists every hook and method.
