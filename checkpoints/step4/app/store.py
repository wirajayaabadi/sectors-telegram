import sqlite3
from contextlib import closing
from pathlib import Path

from app.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS subscribers (
    chat_id INTEGER PRIMARY KEY,
    title TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _conn() -> sqlite3.Connection:
    Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(settings.db_path)


def init_db() -> None:
    with closing(_conn()) as c:
        c.executescript(SCHEMA)


def subscribe(chat_id: int, title: str | None) -> bool:
    """True if this chat was not subscribed before."""
    with closing(_conn()) as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO subscribers (chat_id, title) VALUES (?, ?)",
            (chat_id, title),
        )
        c.commit()
        return cur.rowcount > 0


def unsubscribe(chat_id: int) -> bool:
    with closing(_conn()) as c:
        cur = c.execute("DELETE FROM subscribers WHERE chat_id = ?", (chat_id,))
        c.commit()
        return cur.rowcount > 0


def is_subscribed(chat_id: int) -> bool:
    with closing(_conn()) as c:
        return (
            c.execute(
                "SELECT 1 FROM subscribers WHERE chat_id = ?", (chat_id,)
            ).fetchone()
            is not None
        )


def active_chat_ids() -> list[int]:
    with closing(_conn()) as c:
        return [row[0] for row in c.execute("SELECT chat_id FROM subscribers")]
