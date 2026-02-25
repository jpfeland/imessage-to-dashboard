"""
Read-only SQLite access to a COPY of chat.db.

We NEVER open the original chat.db directly.  Every entry point here:
  1. Copies the live chat.db to /tmp/chat_copy.db.
  2. Opens /tmp/chat_copy.db with URI mode read-only (uri=True, mode=ro).
  3. Returns rows; the caller is responsible for closing the connection.
"""

import logging
import shutil
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

CHAT_DB_PATH = Path.home() / "Library" / "Messages" / "chat.db"
COPY_PATH = Path("/tmp/chat_copy.db")


def copy_chat_db() -> None:
    """Copy chat.db to /tmp/chat_copy.db.  Raises if the source does not exist."""
    if not CHAT_DB_PATH.exists():
        raise FileNotFoundError(
            f"chat.db not found at {CHAT_DB_PATH}. "
            "Ensure iMessage is configured and Full Disk Access is granted."
        )
    shutil.copy2(str(CHAT_DB_PATH), str(COPY_PATH))
    logger.debug("Copied chat.db → %s", COPY_PATH)


def open_copy() -> sqlite3.Connection:
    """Open /tmp/chat_copy.db in read-only URI mode."""
    uri = f"file:{COPY_PATH}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

LIVE_QUERY = """
SELECT
    m.ROWID          AS rowid,
    m.text           AS text,
    m.is_from_me     AS is_from_me,
    m.date           AS date,
    h.id             AS handle,
    c.ROWID          AS chat_id
FROM message m
LEFT JOIN handle h          ON m.handle_id = h.ROWID
LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
LEFT JOIN chat c            ON c.ROWID = cmj.chat_id
WHERE m.ROWID > ?
ORDER BY m.ROWID ASC;
"""


def fetch_messages_since(conn: sqlite3.Connection, last_rowid: int) -> list[sqlite3.Row]:
    """Return all messages with ROWID > last_rowid, ordered ascending."""
    cur = conn.execute(LIVE_QUERY, (last_rowid,))
    return cur.fetchall()


HANDLE_QUERY = "SELECT ROWID, id FROM handle;"


def fetch_all_handles(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Return every row from the handle table."""
    cur = conn.execute(HANDLE_QUERY)
    return cur.fetchall()


def build_backfill_query(handle_ids: list[int]) -> str:
    """Build a parameterized SELECT for messages belonging to specific handle IDs."""
    placeholders = ", ".join("?" * len(handle_ids))
    return f"""
SELECT
    m.ROWID          AS rowid,
    m.text           AS text,
    m.is_from_me     AS is_from_me,
    m.date           AS date,
    h.id             AS handle,
    c.ROWID          AS chat_id
FROM message m
JOIN handle h               ON m.handle_id = h.ROWID
LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
LEFT JOIN chat c            ON c.ROWID = cmj.chat_id
WHERE m.handle_id IN ({placeholders})
ORDER BY m.ROWID ASC;
"""


def fetch_messages_for_handles(
    conn: sqlite3.Connection, handle_ids: list[int]
) -> list[sqlite3.Row]:
    """Return all messages sent or received by the given handle ROWIDs."""
    if not handle_ids:
        return []
    query = build_backfill_query(handle_ids)
    cur = conn.execute(query, handle_ids)
    return cur.fetchall()
