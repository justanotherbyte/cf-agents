"""The ``cf_agents_mcp_servers`` table: one row per registered server.

The table, the schema version key and the ``server_options`` JSON are
exactly upstream's (``client/storage.ts``, ``client/index.ts:357``), so a
Durable Object can move between the TypeScript and Python SDKs
(``.design/mcp_engine.md`` §6.2).
"""

import json
from typing import Any, TypedDict

from ..core.sql import Sql
from ..core.timing import to_milliseconds
from ..core.types import RetryOptions
from .types import MCPServerRecord, SessionSnapshot

__all__ = ("McpServerStore", "encode_options")

_SCHEMA_VERSION_KEY = "cf_agents:mcp_schema_version"
_SCHEMA_VERSION = 1

_MODERN_VERSION = "2026-07-28"


class _Row(TypedDict):
    id: str
    name: str
    server_url: str
    callback_url: str
    client_id: str | None
    auth_url: str | None
    server_options: str | None


class McpServerStore:
    """Reads and writes stored servers.

    Parameters
    ----------
    sql
        The host's typed SQL.
    """

    __slots__ = ("_sql",)

    def __init__(self, sql: Sql) -> None:
        self._sql = sql

    async def ensure_schema(self, storage: Any) -> None:
        """Create the table on first use (``storage`` is the host's KV)."""
        version = await storage.get(_SCHEMA_VERSION_KEY) or 0
        if version >= _SCHEMA_VERSION:
            return
        self._sql(
            "CREATE TABLE IF NOT EXISTS cf_agents_mcp_servers ("
            " id TEXT PRIMARY KEY NOT NULL,"
            " name TEXT NOT NULL,"
            " server_url TEXT NOT NULL,"
            " callback_url TEXT NOT NULL,"
            " client_id TEXT,"
            " auth_url TEXT,"
            " server_options TEXT)"
        )
        await storage.put(_SCHEMA_VERSION_KEY, _SCHEMA_VERSION)

    def records(self) -> list[MCPServerRecord]:
        """Return every stored server, in insertion order."""
        rows = self._sql(
            "SELECT id, name, server_url, callback_url, client_id, auth_url,"
            " server_options FROM cf_agents_mcp_servers ORDER BY rowid",
            row=_Row,
        )
        return [_record(row) for row in rows]

    def get(self, server_id: str) -> MCPServerRecord | None:
        """Return the stored server ``server_id``, if there is one."""
        rows = self._sql(
            "SELECT id, name, server_url, callback_url, client_id, auth_url,"
            " server_options FROM cf_agents_mcp_servers WHERE id = ?",
            server_id,
            row=_Row,
        )
        return _record(rows[0]) if rows else None

    def save(self, record: MCPServerRecord) -> None:
        """Insert or replace ``record``'s row."""
        self._sql(
            "INSERT OR REPLACE INTO cf_agents_mcp_servers"
            " (id, name, server_url, client_id, auth_url, callback_url,"
            " server_options) VALUES (?, ?, ?, ?, ?, ?, ?)",
            record.id,
            record.name,
            record.server_url,
            record.client_id,
            record.auth_url,
            record.callback_url,
            json.dumps(record.options, separators=(",", ":")),
        )

    def delete(self, server_id: str) -> None:
        """Delete the server's row."""
        self._sql("DELETE FROM cf_agents_mcp_servers WHERE id = ?", server_id)

    def set_auth(self, server_id: str, *, auth_url: str, client_id: str | None) -> None:
        """Record a pending authorization."""
        self._sql(
            "UPDATE cf_agents_mcp_servers SET auth_url = ?, client_id = ? WHERE id = ?",
            auth_url,
            client_id,
            server_id,
        )

    def clear_auth_url(self, server_id: str) -> None:
        """Forget the pending authorization URL."""
        self._sql(
            "UPDATE cf_agents_mcp_servers SET auth_url = NULL WHERE id = ?", server_id
        )

    def update_session(self, server_id: str, snapshot: SessionSnapshot | None) -> None:
        """Store or clear the negotiated session (upstream ``withMcpSession``)."""
        record = self.get(server_id)
        if record is None:
            return
        transport = dict(record.options.get("transport") or {})
        transport.pop("sessionId", None)
        transport.pop("protocolVersion", None)
        record.options.pop("discoverResult", None)
        if snapshot is not None:
            transport["protocolVersion"] = snapshot.protocol_version
            if snapshot.session_id is not None:
                transport["sessionId"] = snapshot.session_id
            if snapshot.discover_result is not None:
                record.options["discoverResult"] = snapshot.discover_result
        record.options["transport"] = transport
        self.save(record)


def encode_options(
    *,
    transport_type: str,
    headers: dict[str, str],
    retry: RetryOptions | None,
) -> dict[str, Any]:
    """Return a new server's ``server_options``, in upstream's shape."""
    transport: dict[str, Any] = {"type": transport_type}
    if headers:
        transport["headers"] = headers
    options: dict[str, Any] = {"transport": transport}
    if retry is not None:
        options["retry"] = {
            "maxAttempts": retry.max_attempts,
            "baseDelayMs": to_milliseconds(retry.base_delay),
            "maxDelayMs": to_milliseconds(retry.max_delay),
        }
    return options


def _record(row: _Row) -> MCPServerRecord:
    return MCPServerRecord(
        id=row["id"],
        name=row["name"],
        server_url=row["server_url"],
        callback_url=row["callback_url"],
        client_id=row["client_id"],
        auth_url=row["auth_url"],
        options=_decode_options(row["server_options"]),
    )


def _decode_options(value: str | None) -> dict[str, Any]:
    # Upstream's drop rule (storage.ts:141): a session id without a version,
    # or a 2026-07-28 session without its discover result, can't be resumed.
    options = json.loads(value) if value else {}
    transport = options.get("transport") or {}
    version = transport.get("protocolVersion")
    stateless_without_prior = (
        version == _MODERN_VERSION and "discoverResult" not in options
    )
    if transport.get("sessionId") and (not version or stateless_without_prior):
        transport.pop("sessionId", None)
        transport.pop("protocolVersion", None)
        options.pop("discoverResult", None)
    return options
