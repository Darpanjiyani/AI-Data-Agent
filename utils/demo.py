"""
Settings and limits for the public demo (DEMO_MODE=1).

The demo lets anyone ask questions with the project's API key, so it:
  - answers with the SQL Analyst only: the ETL agent is switched off (no downloads, files or loads),
  - limits each visitor to a number of questions per browser session,
  - limits all visitors together to a number of questions per day (the hard stop on cost),
  - limits the length of a question.
Running locally without DEMO_MODE, nothing here changes how the app behaves.
"""

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
USAGE_DB = Path(os.getenv("DEMO_USAGE_DB", PROJECT_ROOT / "agent_state" / "demo_usage.db"))

MAX_QUESTION_CHARS = 500

ETL_OFF_MESSAGE = (
    "Fetching, transforming and loading data is switched off in this public demo, so nobody can "
    "change what's in the database. You can still ask questions about the data, including the daily "
    "weather that was loaded for the 8 cities. To try the ETL agent, run the project yourself "
    "(see the GitHub README)."
)


def demo_mode() -> bool:
    return os.getenv("DEMO_MODE", "").strip().lower() in ("1", "true", "yes", "on")


def per_session_limit() -> int:
    return int(os.getenv("DEMO_MAX_QUESTIONS_PER_SESSION", "15"))


def per_day_limit() -> int:
    return int(os.getenv("DEMO_MAX_QUESTIONS_PER_DAY", "200"))


def _usage() -> sqlite3.Connection:
    USAGE_DB.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(USAGE_DB, timeout=10, isolation_level=None)
    connection.execute("CREATE TABLE IF NOT EXISTS usage (day TEXT PRIMARY KEY, questions INTEGER NOT NULL)")
    return connection


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def questions_today() -> int:
    connection = _usage()
    try:
        row = connection.execute("SELECT questions FROM usage WHERE day = ?", (_today(),)).fetchone()
        return row[0] if row else 0
    finally:
        connection.close()


def take_daily_slot() -> bool:
    """Count one question against today's limit for all visitors; False when the limit is reached.
    One transaction, so two visitors asking at the same moment can't both take the last slot."""
    connection = _usage()
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT questions FROM usage WHERE day = ?", (_today(),)).fetchone()
        used = row[0] if row else 0
        if used >= per_day_limit():
            connection.execute("ROLLBACK")
            return False
        connection.execute("INSERT INTO usage (day, questions) VALUES (?, 1) "
                           "ON CONFLICT(day) DO UPDATE SET questions = questions + 1", (_today(),))
        connection.execute("COMMIT")
        return True
    finally:
        connection.close()
