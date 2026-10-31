"""SQL storage for harnesses: sessions and their operations.

Rows are keyed by the harness's capability id, so several harnesses can share
one object. History and checkpoints are opaque bytes the subclass owns.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..core.sql import Sql
from ..core.timing import now_ms
from ..core.types import JSONValue
from .types import (
    Approval,
    HarnessInput,
    OperationResult,
    OperationStatus,
    PendingOperation,
    ToolAnswer,
    ToolOutput,
    ToolRequest,
)

__all__ = ("HarnessStore", "OperationRow")

_SESSIONS = """CREATE TABLE IF NOT EXISTS cf_agents_harness_sessions (
  harness TEXT NOT NULL,
  id TEXT NOT NULL,
  history BLOB,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (harness, id)
) WITHOUT ROWID"""
_OPERATIONS = """CREATE TABLE IF NOT EXISTS cf_agents_harness_operations (
  harness TEXT NOT NULL,
  session TEXT NOT NULL,
  id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  input TEXT NOT NULL,
  status TEXT NOT NULL,
  checkpoint BLOB,
  attempts INTEGER NOT NULL DEFAULT 0,
  result TEXT,
  reason TEXT,
  created_at INTEGER NOT NULL,
  PRIMARY KEY (harness, session, id)
) WITHOUT ROWID"""


@dataclass(slots=True, frozen=True, kw_only=True)
class OperationRow:
    """One stored operation, parsed."""

    session: str
    id: str
    input: HarnessInput
    status: OperationStatus
    checkpoint: bytes | None
    attempts: int
    text: str | None
    requests: Sequence[ToolRequest]
    reason: str | None

    @classmethod
    def from_sql(cls, row: dict[str, Any]) -> "OperationRow":
        """Parse a row of the operations table."""
        result = json.loads(row["result"]) if row["result"] is not None else {}

        return cls(
            session=row["session"],
            id=row["id"],
            input=_input_from_json(json.loads(row["input"])),
            status=row["status"],
            checkpoint=row["checkpoint"],
            attempts=row["attempts"],
            text=result.get("text"),
            requests=[ToolRequest(**r) for r in result.get("requests", ())],
            reason=row["reason"],
        )

    @property
    def is_open(self) -> bool:
        """Whether the operation is queued or running."""
        return self.status in ("queued", "running")

    def result(self) -> OperationResult:
        """Return the settled operation's result."""
        return OperationResult(
            session=self.session,
            operation_id=self.id,
            status=self.status,
            text=self.text,
            requests=self.requests,
            reason=self.reason,
        )

    def pending(self) -> PendingOperation:
        """Return the open operation's summary."""
        return PendingOperation(
            session=self.session,
            operation_id=self.id,
            status="running" if self.status == "running" else "queued",
        )


class HarnessStore:
    """Every SQL statement a harness runs; callers wrap multi-step writes."""

    __slots__ = ("harness", "sql")

    def __init__(self, sql: Sql, harness: str) -> None:
        self.sql = sql
        self.harness = harness

    def ensure_tables(self) -> None:
        """Create both tables (idempotent)."""
        self.sql(_SESSIONS)
        self.sql(_OPERATIONS)

    def ensure_session(self, session: str) -> None:
        """Create the session if it doesn't exist."""
        self.sql(
            "INSERT OR IGNORE INTO cf_agents_harness_sessions"
            " (harness, id, created_at) VALUES (?, ?, ?)",
            self.harness,
            session,
            now_ms(),
        )

    def has_session(self, session: str) -> bool:
        """Return whether the session exists."""
        return bool(
            self.sql(
                "SELECT id FROM cf_agents_harness_sessions"
                " WHERE harness = ? AND id = ?",
                self.harness,
                session,
            )
        )

    def session_ids(self) -> list[str]:
        """Return every session id, oldest first."""
        rows = self.sql(
            "SELECT id FROM cf_agents_harness_sessions WHERE harness = ?"
            " ORDER BY created_at, id",
            self.harness,
        )
        return [row["id"] for row in rows]

    def fork_session(self, source: str, session: str) -> None:
        """Create ``session`` with a copy of ``source``'s history."""
        self.sql(
            "INSERT INTO cf_agents_harness_sessions (harness, id, history, created_at)"
            " SELECT harness, ?, history, ? FROM cf_agents_harness_sessions"
            " WHERE harness = ? AND id = ?",
            session,
            now_ms(),
            self.harness,
            source,
        )

    def history(self, session: str) -> bytes | None:
        """Return the session's history, or ``None`` if it has none."""
        rows = self.sql(
            "SELECT history FROM cf_agents_harness_sessions"
            " WHERE harness = ? AND id = ?",
            self.harness,
            session,
        )
        return rows[0]["history"] if rows else None

    def set_history(self, session: str, history: bytes | None) -> None:
        """Replace the session's history."""
        self.sql(
            "UPDATE cf_agents_harness_sessions SET history = ?"
            " WHERE harness = ? AND id = ?",
            history,
            self.harness,
            session,
        )

    def insert(self, session: str, id: str, input: HarnessInput) -> bool:
        """Queue an operation at the end of the session; ``False`` if ``id`` exists."""
        rows = self.sql(
            "INSERT INTO cf_agents_harness_operations"
            " (harness, session, id, seq, input, status, created_at)"
            " VALUES (?, ?, ?, (SELECT COALESCE(MAX(seq), 0) + 1"
            "   FROM cf_agents_harness_operations WHERE harness = ? AND session = ?),"
            " ?, 'queued', ?)"
            " ON CONFLICT DO NOTHING RETURNING id",
            self.harness,
            session,
            id,
            self.harness,
            session,
            json.dumps(_input_to_json(input)),
            now_ms(),
        )
        return bool(rows)

    def get(self, session: str, id: str) -> OperationRow | None:
        """Return one operation, or ``None``."""
        rows = self.sql(
            "SELECT * FROM cf_agents_harness_operations"
            " WHERE harness = ? AND session = ? AND id = ?",
            self.harness,
            session,
            id,
        )
        return OperationRow.from_sql(rows[0]) if rows else None

    def open(self, session: str) -> list[OperationRow]:
        """Return the session's open operations, oldest first."""
        rows = self.sql(
            "SELECT * FROM cf_agents_harness_operations"
            " WHERE harness = ? AND session = ? AND status IN ('queued', 'running')"
            " ORDER BY seq",
            self.harness,
            session,
        )
        return [OperationRow.from_sql(row) for row in rows]

    def running(self) -> list[OperationRow]:
        """Return every running operation, in any session."""
        rows = self.sql(
            "SELECT * FROM cf_agents_harness_operations"
            " WHERE harness = ? AND status = 'running'",
            self.harness,
        )
        return [OperationRow.from_sql(row) for row in rows]

    def sessions_with_open_work(self) -> list[str]:
        """Return the sessions that have open operations."""
        rows = self.sql(
            "SELECT DISTINCT session FROM cf_agents_harness_operations"
            " WHERE harness = ? AND status IN ('queued', 'running')",
            self.harness,
        )
        return [row["session"] for row in rows]

    def start(self, session: str, id: str, *, attempts: int) -> None:
        """Mark the operation running with this many interruptions so far."""
        self.sql(
            "UPDATE cf_agents_harness_operations SET status = 'running', attempts = ?"
            " WHERE harness = ? AND session = ? AND id = ?",
            attempts,
            self.harness,
            session,
            id,
        )

    def save_checkpoint(self, session: str, id: str, checkpoint: bytes) -> None:
        """Replace a running operation's checkpoint."""
        self.sql(
            "UPDATE cf_agents_harness_operations SET checkpoint = ?"
            " WHERE harness = ? AND session = ? AND id = ? AND status = 'running'",
            checkpoint,
            self.harness,
            session,
            id,
        )

    def settle(
        self,
        session: str,
        id: str,
        status: OperationStatus,
        *,
        text: str | None,
        requests: Sequence[ToolRequest],
        reason: str | None,
    ) -> bool:
        """Settle an open operation; ``False`` if it was already settled.

        An ``unanswered`` operation keeps its checkpoint, so ``retry`` resumes.
        """
        result: dict[str, JSONValue] = {
            "text": text,
            "requests": [_request_to_json(request) for request in requests],
        }

        rows = self.sql(
            "UPDATE cf_agents_harness_operations"
            " SET status = ?, result = ?, reason = ?,"
            "   checkpoint = CASE WHEN ? = 'unanswered' THEN checkpoint END"
            " WHERE harness = ? AND session = ? AND id = ?"
            " AND status IN ('queued', 'running') RETURNING id",
            status,
            json.dumps(result),
            reason,
            status,
            self.harness,
            session,
            id,
        )
        return bool(rows)

    def reopen(self, session: str, id: str) -> None:
        """Queue an unanswered operation again, resuming from its checkpoint."""
        self.sql(
            "UPDATE cf_agents_harness_operations"
            " SET status = 'queued', attempts = 0, result = NULL, reason = NULL"
            " WHERE harness = ? AND session = ? AND id = ?",
            self.harness,
            session,
            id,
        )


def _input_to_json(input: HarnessInput) -> JSONValue:
    if isinstance(input, str):
        return {"prompt": input}
    return {"answers": [_answer_to_json(answer) for answer in input]}


def _input_from_json(data: dict[str, Any]) -> HarnessInput:
    if "prompt" in data:
        return data["prompt"]
    return [_answer_from_json(answer) for answer in data["answers"]]


def _answer_to_json(answer: ToolAnswer) -> JSONValue:
    if isinstance(answer, Approval):
        return {
            "type": "approval",
            "id": answer.id,
            "approved": answer.approved,
            "message": answer.message,
        }
    return {"type": "output", "id": answer.id, "value": answer.value}


def _answer_from_json(data: dict[str, Any]) -> ToolAnswer:
    if data["type"] == "approval":
        return Approval(
            id=data["id"], approved=data["approved"], message=data["message"]
        )
    return ToolOutput(id=data["id"], value=data["value"])


def _request_to_json(request: ToolRequest) -> JSONValue:
    return {
        "id": request.id,
        "name": request.name,
        "args": request.args,
        "kind": request.kind,
    }
