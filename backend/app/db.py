"""SQLite storage for session history.

Deliberately stdlib-only (``sqlite3``): the assignment forbids external stores
and the access pattern is a handful of small reads/writes per request, so an
ORM would add dependencies without buying anything.

A fresh connection is opened per operation. SQLite handles this well, it keeps
the code thread-safe under FastAPI's worker threads, and WAL mode keeps
concurrent readers from blocking the writer.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,          -- 'user' or 'assistant'
    content     TEXT NOT NULL,
    metadata    TEXT,                   -- JSON string, assistant messages only
    created_at  TEXT NOT NULL           -- server time, ISO-8601
);

CREATE INDEX IF NOT EXISTS idx_messages_session_id
    ON messages (session_id, id);

-- Per-session system prompt, so a prompt set once keeps applying to the rest
-- of the conversation without the client having to resend it.
CREATE TABLE IF NOT EXISTS sessions (
    session_id    TEXT PRIMARY KEY,
    system_prompt TEXT,
    created_at    TEXT NOT NULL
);
"""


def utc_now_iso() -> str:
    """Server time as ISO-8601 with a UTC offset."""
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str) -> None:
        self.path = path

    # ------------------------------------------------------------- lifecycle
    def connect(self) -> sqlite3.Connection:
        directory = os.path.dirname(os.path.abspath(self.path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    @contextmanager
    def cursor(self) -> Iterator[sqlite3.Cursor]:
        conn = self.connect()
        try:
            yield conn.cursor()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        """Create the schema on startup. Idempotent."""
        conn = self.connect()
        try:
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def healthy(self) -> bool:
        try:
            with self.cursor() as cur:
                cur.execute("SELECT 1 FROM messages LIMIT 1;")
            return True
        except Exception:
            return False

    # ---------------------------------------------------------------- reads
    def session_exists(self, session_id: str) -> bool:
        with self.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM messages WHERE session_id = ? LIMIT 1;", (session_id,)
            )
            return cur.fetchone() is not None

    def get_history(self, session_id: str) -> List[Dict[str, Any]]:
        """Stored turns for a session, oldest first. Never includes the system prompt."""
        with self.cursor() as cur:
            cur.execute(
                "SELECT role, content, metadata FROM messages "
                "WHERE session_id = ? ORDER BY id ASC;",
                (session_id,),
            )
            rows = cur.fetchall()

        history: List[Dict[str, Any]] = []
        for row in rows:
            item: Dict[str, Any] = {"role": row["role"], "content": row["content"]}
            if row["metadata"]:
                try:
                    item["metadata"] = json.loads(row["metadata"])
                except json.JSONDecodeError:  # pragma: no cover - defensive
                    item["metadata"] = None
            history.append(item)
        return history

    def get_system_prompt(self, session_id: str) -> Optional[str]:
        with self.cursor() as cur:
            cur.execute(
                "SELECT system_prompt FROM sessions WHERE session_id = ?;",
                (session_id,),
            )
            row = cur.fetchone()
        return row["system_prompt"] if row else None

    # --------------------------------------------------------------- writes
    def set_system_prompt(self, session_id: str, system_prompt: str) -> None:
        with self.cursor() as cur:
            cur.execute(
                "INSERT INTO sessions (session_id, system_prompt, created_at) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET system_prompt = excluded.system_prompt;",
                (session_id, system_prompt, utc_now_iso()),
            )

    def append_turn(
        self,
        session_id: str,
        user_message: str,
        assistant_message: str,
        assistant_metadata: Dict[str, Any],
    ) -> None:
        """Persist a completed turn.

        Both rows are written in a single transaction, and only after the LLM
        call succeeded, so a failed request (422/503) leaves no trace in history.
        """
        now = utc_now_iso()
        with self.cursor() as cur:
            cur.execute(
                "INSERT INTO messages (session_id, role, content, metadata, created_at) "
                "VALUES (?, 'user', ?, NULL, ?);",
                (session_id, user_message, now),
            )
            cur.execute(
                "INSERT INTO messages (session_id, role, content, metadata, created_at) "
                "VALUES (?, 'assistant', ?, ?, ?);",
                (
                    session_id,
                    assistant_message,
                    json.dumps(assistant_metadata, separators=(",", ":")),
                    utc_now_iso(),
                ),
            )
