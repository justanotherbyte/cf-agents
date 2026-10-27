import asyncio
import json

from fake_runtime import FakeStorage

from agents.core.sql import Sql
from agents.core.types import RetryOptions
from agents.mcp.storage import McpServerStore, encode_options
from agents.mcp.types import MCPServerRecord, SessionSnapshot


def make_store() -> tuple[McpServerStore, FakeStorage]:
    storage = FakeStorage()
    store = McpServerStore(Sql(storage))
    asyncio.run(store.ensure_schema(storage))
    return store, storage


def test_schema_and_version_key() -> None:
    store, storage = make_store()
    assert storage.kv["cf_agents:mcp_schema_version"] == 1
    columns = [
        row["name"]
        for row in storage.sql.exec(
            "PRAGMA table_info(cf_agents_mcp_servers)"
        ).toArray()
    ]
    assert columns == [
        "id",
        "name",
        "server_url",
        "callback_url",
        "client_id",
        "auth_url",
        "server_options",
    ]
    asyncio.run(store.ensure_schema(storage))  # idempotent


def test_round_trip_keeps_unknown_keys() -> None:
    store, storage = make_store()
    # A row as the TypeScript SDK writes it.
    upstream = {
        "client": {"capabilities": {"elicitation": {}}},
        "transport": {"type": "streamable-http", "headers": {"x": "1"}},
        "retry": {"maxAttempts": 5, "baseDelayMs": 100, "maxDelayMs": 2000},
        "capabilities": {"roots": {}},
    }
    storage.sql.exec(
        "INSERT INTO cf_agents_mcp_servers VALUES (?, ?, ?, ?, ?, ?, ?)",
        "gh",
        "GitHub",
        "https://mcp.example.com/mcp",
        "https://agent.example.com/cb",
        None,
        None,
        json.dumps(upstream),
    )
    record = store.get("gh")
    assert record is not None
    assert record.transport_type == "streamable-http"
    assert record.headers == {"x": "1"}
    assert record.retry == RetryOptions(max_attempts=5, base_delay=0.1, max_delay=2.0)

    store.update_session(
        "gh", SessionSnapshot(protocol_version="2025-11-25", session_id="s1")
    )
    saved = json.loads(_raw(storage))
    assert saved["client"] == upstream["client"]
    assert saved["capabilities"] == upstream["capabilities"]
    assert saved["transport"] == {
        "type": "streamable-http",
        "headers": {"x": "1"},
        "protocolVersion": "2025-11-25",
        "sessionId": "s1",
    }


def _raw(storage: FakeStorage) -> str:
    return storage.sql.exec(
        "SELECT server_options FROM cf_agents_mcp_servers"
    ).toArray()[0]["server_options"]


def test_snapshot_and_clearing() -> None:
    store, _ = make_store()
    store.save(
        MCPServerRecord(
            id="a",
            name="A",
            server_url="https://a.example.com/mcp",
            options=encode_options(transport_type="auto", headers={}, retry=None),
        )
    )
    modern = SessionSnapshot(protocol_version="2026-07-28", discover_result={"x": 1})
    store.update_session("a", modern)
    record = store.get("a")
    assert record is not None
    assert record.snapshot == modern

    store.update_session("a", None)
    record = store.get("a")
    assert record is not None
    assert record.snapshot is None
    assert record.options == {"transport": {"type": "auto"}}


def test_drop_rule() -> None:
    store, storage = make_store()
    options = {"transport": {"sessionId": "s", "protocolVersion": "2026-07-28"}}
    storage.sql.exec(
        "INSERT INTO cf_agents_mcp_servers VALUES (?, ?, ?, ?, ?, ?, ?)",
        "a",
        "A",
        "https://a.example.com",
        "",
        None,
        None,
        json.dumps(options),
    )
    record = store.get("a")
    assert record is not None
    assert record.snapshot is None


def test_auth_columns_and_retry_encoding() -> None:
    store, _ = make_store()
    retry = RetryOptions(max_attempts=2, base_delay=0.25, max_delay=1)
    store.save(
        MCPServerRecord(
            id="a",
            name="A",
            server_url="https://a.example.com",
            options=encode_options(
                transport_type="auto", headers={"h": "v"}, retry=retry
            ),
        )
    )
    store.set_auth("a", auth_url="https://auth/x", client_id="c")
    record = store.get("a")
    assert record is not None
    assert (record.auth_url, record.client_id) == ("https://auth/x", "c")
    assert record.options["retry"] == {
        "maxAttempts": 2,
        "baseDelayMs": 250,
        "maxDelayMs": 1000,
    }
    store.clear_auth_url("a")
    store.delete("a")
    assert store.records() == []
