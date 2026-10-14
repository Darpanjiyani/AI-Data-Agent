"""
Saved chats for the Streamlit app: the list in the sidebar and every message in each chat.

Chats are kept in a small SQLite file (CHAT_DB, default chat_history/chats.db, which is
ignored by git). The agent's own memory of each chat, used to understand follow-ups,
is kept by the LangGraph checkpointer in the same file, keyed by the same chat id.

Every chat belongs to a visitor_id. Run locally, that is always "local". A public demo
can pass a per-browser id instead, so visitors never see each other's chats.
"""

import json
import os
import sqlite3
import time
import uuid
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHAT_DB = Path(os.getenv("CHAT_DB", PROJECT_ROOT / "chat_history" / "chats.db"))
TITLE_LENGTH = 48


def _connect() -> sqlite3.Connection:
    CHAT_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(CHAT_DB)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def setup() -> None:
    with closing(_connect()) as conn, conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS chats (
                chat_id    TEXT PRIMARY KEY,
                visitor_id TEXT NOT NULL,
                title      TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_chats_visitor ON chats (visitor_id, updated_at);
            CREATE TABLE IF NOT EXISTS chat_messages (
                message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id    TEXT NOT NULL REFERENCES chats (chat_id) ON DELETE CASCADE,
                role       TEXT NOT NULL,          -- 'user' or 'assistant'
                content    TEXT NOT NULL,          -- the question, or the reply as JSON
                created_at REAL NOT NULL
            );
        """)


def make_title(question: str) -> str:
    title = " ".join(question.split())
    return title if len(title) <= TITLE_LENGTH else title[:TITLE_LENGTH - 1].rstrip() + "…"


def create_chat(visitor_id: str, first_question: str) -> str:
    chat_id = uuid.uuid4().hex
    now = time.time()
    with closing(_connect()) as conn, conn:
        conn.execute("INSERT INTO chats VALUES (?, ?, ?, ?, ?)",
                     (chat_id, visitor_id, make_title(first_question), now, now))
    return chat_id


def list_chats(visitor_id: str, limit: int = 50) -> list:
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT chat_id, title, updated_at FROM chats WHERE visitor_id = ? "
                            "ORDER BY updated_at DESC LIMIT ?", (visitor_id, limit)).fetchall()
    return [dict(row) for row in rows]


def add_message(chat_id: str, role: str, content: str) -> None:
    now = time.time()
    with closing(_connect()) as conn, conn:
        conn.execute("INSERT INTO chat_messages (chat_id, role, content, created_at) "
                     "VALUES (?, ?, ?, ?)", (chat_id, role, content, now))
        conn.execute("UPDATE chats SET updated_at = ? WHERE chat_id = ?", (now, chat_id))


def add_reply(chat_id: str, reply) -> None:
    add_message(chat_id, "assistant", json.dumps(asdict(reply), default=str))


def load_messages(chat_id: str, visitor_id: str) -> list:
    """Messages of one chat, oldest first. Empty if the chat isn't this visitor's."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT m.role, m.content FROM chat_messages m JOIN chats c USING (chat_id) "
            "WHERE m.chat_id = ? AND c.visitor_id = ? ORDER BY m.message_id",
            (chat_id, visitor_id)).fetchall()
    return [dict(row) for row in rows]


def delete_chat(chat_id: str, visitor_id: str) -> bool:
    with closing(_connect()) as conn, conn:
        deleted = conn.execute("DELETE FROM chats WHERE chat_id = ? AND visitor_id = ?",
                               (chat_id, visitor_id)).rowcount
    return bool(deleted)
