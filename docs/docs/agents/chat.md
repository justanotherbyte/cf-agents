---
title: "Build a streaming chat agent in Python"
description: "Subclass AIChatAgent, stream replies from on_chat_message as text or typed chunks, connect useAgentChat, and control stored history and overlapping sends."
---

`AIChatAgent` is an agent that runs chat turns. When a user sends a message,
the SDK stores it, calls your `on_chat_message`, streams what it yields to
every connected client, and stores the finished reply. A client that
reconnects in the middle of a reply receives the part it missed.

The React client is `useAgentChat` from
[`@cloudflare/ai-chat`](https://www.npmjs.com/package/@cloudflare/ai-chat). It
works with a Python agent unchanged.

## Write the agent

```python title="src/entry.py"
from collections.abc import AsyncIterator

from workers import Request, Response, WorkerEntrypoint

from agents import AIChatAgent, ChatMessageOptions, TextPart, route_agent_request


class EchoChat(AIChatAgent):
    async def on_chat_message(self, options: ChatMessageOptions) -> AsyncIterator[str]:
        last = self.messages[-1]
        text = "".join(part.text for part in last.parts if isinstance(part, TextPart))
        for word in f"You said: {text}".split():
            yield word + " "


class Default(WorkerEntrypoint):
    async def fetch(self, request: Request) -> Response:
        response = await route_agent_request(request, self.env)
        if response is None:
            return Response("Not found", status=404)
        return response
```

Add a binding and a migration for `EchoChat`, as in
[Getting started](getting-started.md#3-configure-wrangler):

=== "wrangler.jsonc"

    ```jsonc
    {
      "durable_objects": {
        "bindings": [{ "class_name": "EchoChat", "name": "EchoChat" }]
      },
      "migrations": [{ "tag": "v1", "new_sqlite_classes": ["EchoChat"] }]
    }
    ```

=== "wrangler.toml"

    ```toml
    [[durable_objects.bindings]]
    class_name = "EchoChat"
    name = "EchoChat"

    [[migrations]]
    tag = "v1"
    new_sqlite_classes = ["EchoChat"]
    ```

=== "cloudflare.config.py"

    ```python
    from wynd.config import bindings, exports

    env={"EchoChat": bindings.DurableObject("EchoChat")},
    exports={"EchoChat": exports.DurableObject()},
    ```

`self.messages` is the conversation, oldest first, including the message the
user just sent. Each message is a `UIMessage` with `id`, `role` and `parts`.

## Connect a React client

```sh
npm install agents @cloudflare/ai-chat
```

```tsx
import { useAgent } from "agents/react";
import { useAgentChat } from "@cloudflare/ai-chat/react";

export function Chat() {
  const agent = useAgent({ agent: "EchoChat", name: "my-chat" });
  const { messages, sendMessage } = useAgentChat({ agent });

  return (
    <div>
      {messages.map((message) => (
        <p key={message.id}>
          <strong>{message.role}: </strong>
          {message.parts.map((part) => (part.type === "text" ? part.text : "")).join("")}
        </p>
      ))}
      <button
        onClick={() =>
          sendMessage({ role: "user", parts: [{ type: "text", text: "hello there" }] })
        }
      >
        Send
      </button>
    </div>
  );
}
```

Clicking Send streams back `You said: hello there`. Reload the page and the
conversation is still there: `useAgentChat` loads it from
`/agents/echo-chat/my-chat/get-messages`, which the SDK answers.

## Stream text or chunks

`on_chat_message` is an async generator. It can yield one of two kinds of
value, not both:

- `str`. The SDK wraps the text in a single text part.
- Typed chunks from `agents.chat.chunks`. Use these for reasoning, tool calls,
  sources, files, custom data or metadata.

Mixing them fails the turn with `TypeError: on_chat_message yielded a str
after chunks`, or `... yielded a chunk after str text`.

```python
from agents.chat import chunks


class Assistant(AIChatAgent):
    async def on_chat_message(self, options: ChatMessageOptions):
        yield chunks.ReasoningStart(id="r1")
        yield chunks.ReasoningDelta(id="r1", delta="The user greeted me.")
        yield chunks.ReasoningEnd(id="r1")
        yield chunks.TextStart(id="t1")
        yield chunks.TextDelta(id="t1", delta="Hello!")
        yield chunks.TextEnd(id="t1")
```

The chunk classes match the
[AI SDK v6 stream chunks](https://ai-sdk.dev/docs/ai-sdk-ui/stream-protocol),
with `snake_case` fields. The SDK sends `Start` and `Finish` for you if you
don't. Every delta and end chunk needs a start chunk with the same `id` first,
or the turn fails with `TypeError`.

## Call a model

The SDK doesn't call a model or ship provider adapters. Inside
`on_chat_message`, convert `self.messages` to your provider's format, call it
with your own client, and yield its text as it streams. A converter for
providers that take `role` and `content` strings:

```python
from collections.abc import Sequence

from agents import TextPart, UIMessage


def to_model_messages(messages: Sequence[UIMessage]) -> list[dict[str, str]]:
    return [
        {
            "role": message.role,
            "content": "".join(p.text for p in message.parts if isinstance(p, TextPart)),
        }
        for message in messages
    ]
```

Keep API keys in
[Worker secrets](https://developers.cloudflare.com/workers/configuration/secrets/),
set with `npx wrangler secret put <NAME>`, and read them from `self.env.<NAME>`.
For `pywrangler dev`, put `<NAME>=...` in a `.dev.vars` file and keep that
file out of git.

`ChatMessageOptions` carries the turn's details:

| Field          | What it holds                                                                       |
| -------------- | ----------------------------------------------------------------------------------- |
| `request_id`   | The turn's id.                                                                      |
| `body`         | Extra fields the client sent with `useAgentChat({ body })`, such as a model choice. |
| `continuation` | `True` when the turn appends to the last assistant message.                         |
| `client_tools` | Tool schemas the client sent.                                                       |

## Handle errors and stops

- An exception, or a yielded `chunks.Error(error_text=...)`, ends the turn as
  failed. The client shows the error and the partial reply is saved. The SDK
  passes the exception, or a `ChatStreamError` for an `Error` chunk, to
  `on_error(None, error)`.
- The client receives the exception's message as the error text. Don't put
  secrets or provider responses you haven't checked in exception messages.
  To trigger this path locally, raise `RuntimeError("model unavailable")`
  in `on_chat_message`. The client shows `model unavailable`, and
  `on_chat_response` gets `status == "error"`.
- When the user presses stop, the SDK cancels the turn. Your generator gets
  `asyncio.CancelledError` at its next `await`, and the partial reply is saved.
- `self.abort_request(request_id)` stops one turn from server code, and
  `self.abort_all_requests()` stops all of them.

## Run code after each reply

`on_chat_response` runs after a reply is saved:

```python
from agents import ChatResponseResult


class Assistant(AIChatAgent):
    async def on_chat_response(self, result: ChatResponseResult) -> None:
        if result.status == "error":
            print(f"turn {result.request_id} failed: {result.error}")
```

`result.status` is `"completed"`, `"error"` or `"aborted"`, and
`result.message` is the saved assistant message.

## Add messages from the server

`save_messages` stores messages and runs a turn to answer them, as if a client
had sent them. It waits for a running turn first:

```python
from agents import TextPart, UIMessage

result = await self.save_messages([
    *self.messages,
    UIMessage(id="reminder-1", role="user", parts=[TextPart(text="Summarize today")]),
])
print(result.status)  # "completed", "error", "skipped" or "aborted"
```

To change the stored conversation without running a turn, use
`persist_messages(messages)`. To remove messages, use
`delete_messages(ids)`. Don't change `self.messages` in place.

## Overlapping sends

`options.message_concurrency` decides what happens when a user sends a message
while a reply is still streaming:

```python
from agents import AIChatAgent, AIChatAgentOptions, Debounce


class Assistant(AIChatAgent):
    options = AIChatAgentOptions(message_concurrency=Debounce(seconds=1.0))
```

| Value               | Effect                                                                    |
| ------------------- | ------------------------------------------------------------------------- |
| `"queue"`           | Default. Answers every send, in order.                                    |
| `"latest"`          | Answers only the newest send. The others are still stored.                |
| `"merge"`           | Combines the overlapping sends into one user message and answers it once. |
| `"drop"`            | Rejects overlapping sends. The client removes them.                       |
| `Debounce(seconds)` | Like `"latest"`, after no new send for `seconds`. The default is 0.75.    |

The policy applies only to user sends. Regenerations and turns from
`save_messages` always queue.

## Limit stored history

- `max_persisted_messages=200` deletes the oldest stored messages after each
  save, keeping 200.
- `hydration_byte_budget` caps how much history `self.messages` loads when the
  agent wakes, 32 MiB by default. Older messages stay stored and
  `get-messages` still serves them.

Both go in `AIChatAgentOptions`. What you send to the model is up to
`on_chat_message`. Slice `self.messages` there to send less.

## Not ported yet

- Client-side tools and tool approvals. `client_tools` reaches
  `on_chat_message`, but the SDK doesn't handle tool results or approvals
  from the client. Those frames reach `on_message` instead.
- Recovery of a turn whose isolate stopped mid-reply. The SDK doesn't resume
  or retry that turn.
- `chatStreamStallTimeoutMs`, the watchdog that aborts a turn when chunks stop
  arriving.
- MCP tools.
