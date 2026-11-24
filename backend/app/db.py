"""SQLite storage layer.

- Single-file database, schema created automatically on startup.
- Lives under a Docker volume (e.g. /data/app.db) via DATABASE_URL so history
  survives container restarts.
- Write-ahead logging (WAL) for safe concurrent reads while writing.
- History is only written after a successful completion; failed requests
  (422 / 503) never leave a trace.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,          -- 'user' or 'assistant'
    content TEXT NOT NULL,
    metadata TEXT,               -- JSON string, assistant messages only
    created_at TEXT NOT NULL     -- server time, ISO-8601
);
CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages (session_id, id);
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    system_prompt TEXT,          -- effective system prompt for this session
    created_at TEXT NOT NULL
);
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_sqlite_path(database_url: str) -> Path:
    """Parse DATABASE_URL into a filesystem path.

    Supports "sqlite:////data/app.db" (absolute), "sqlite:///./app.db"
    (relative), "file:/data/app.db" (Prisma-style URI) and a plain filesystem
    path.
    """
    url = database_url.strip()
    if url.startswith("sqlite:///"):
        path = url[len("sqlite:///"):]
    elif url.startswith("sqlite://"):
        path = url[len("sqlite://"):]
    elif url.startswith("file:"):
        path = url[len("file:"):]
    else:
        path = url
    p = Path(path)
    if not p.is_absolute():
        p = Path.cwd() / p
    return p


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA)
            conn.commit()
        logger.info("SQLite ready at %s", self.path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield conn
        finally:
            conn.close()

    def healthy(self) -> bool:
        try:
            with self.connect() as conn:
                conn.execute("SELECT 1")
            return True
        except sqlite3.Error:
            return False

    # -- sessions ----------------------------------------------------------

    def get_session_prompt(self, session_id: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT system_prompt FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return row["system_prompt"] if row else None

    def session_exists(self, session_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return row is not None

    # -- messages ----------------------------------------------------------

    def get_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Stored history of one session, oldest first (role/content only)."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    def get_history(self, session_id: str) -> list[dict[str, Any]]:
        """History for the API endpoint: assistant rows carry parsed metadata."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT role, content, metadata FROM messages WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            item: dict[str, Any] = {"role": r["role"], "content": r["content"]}
            if r["role"] == "assistant" and r["metadata"]:
                try:
                    item["metadata"] = json.loads(r["metadata"])
                except (TypeError, ValueError):
                    logger.warning("Corrupt metadata JSON in session %s", session_id)
            out.append(item)
        return out

    def record_turn(
        self,
        session_id: str,
        system_prompt: str,
        user_message: str,
        assistant_reply: str,
        metadata: dict[str, Any],
    ) -> None:
        """Persist one successful turn (session row + user row + assistant row).

        Called only after a successful completion, so failed requests (503)
        never appear in history and unknown sessions stay unknown (404).
        """
        now = utc_now_iso()
        meta_json = json.dumps(metadata, ensure_ascii=False)
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (session_id, system_prompt, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET system_prompt = excluded.system_prompt
                """,
                (session_id, system_prompt, now),
            )
            conn.execute(
                """
                INSERT INTO messages (session_id, role, content, metadata, created_at)
                VALUES (?, 'user', ?, NULL, ?)
                """,
                (session_id, user_message, now),
            )
            conn.execute(
                """
                INSERT INTO messages (session_id, role, content, metadata, created_at)
                VALUES (?, 'assistant', ?, ?, ?)
                """,
                (session_id, assistant_reply, meta_json, now),
            )
            conn.commit()
