"""SQLite storage. Only sender addresses, dates and unsubscribe headers are kept."""

import sqlite3
from dataclasses import fields
from pathlib import Path

from .headers import domain_of, parse_from
from .models import MessageMeta, Sender

SENDER_COLUMNS = [f.name for f in fields(Sender)]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS senders (
    address TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    domain TEXT NOT NULL,
    is_list INTEGER NOT NULL DEFAULT 0,
    latest_date INTEGER NOT NULL,
    latest_thread_id TEXT NOT NULL,
    latest_list_unsubscribe TEXT,
    latest_list_unsubscribe_post TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    status_at INTEGER,
    method TEXT,
    last_error TEXT,
    skip_baseline INTEGER,
    acknowledged_at INTEGER
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    address TEXT NOT NULL,
    date INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_address_date ON messages(address, date);
CREATE TABLE IF NOT EXISTS scan_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def record_message(conn: sqlite3.Connection, meta: MessageMeta) -> bool:
    parsed = parse_from(meta.from_header)
    if parsed is None:
        return False
    name, address = parsed
    cur = conn.execute(
        "INSERT OR IGNORE INTO messages(id, address, date) VALUES (?, ?, ?)",
        (meta.id, address, meta.date),
    )
    if cur.rowcount == 0:
        return False
    has_header = 1 if meta.list_unsubscribe is not None else 0
    conn.execute(
        """
        INSERT INTO senders(address, display_name, domain, is_list, latest_date,
                            latest_thread_id, latest_list_unsubscribe,
                            latest_list_unsubscribe_post)
        VALUES (:address, :name, :domain, :is_list, :date, :thread, :lu, :lup)
        ON CONFLICT(address) DO UPDATE SET
            is_list = MAX(senders.is_list, excluded.is_list),
            display_name = CASE WHEN excluded.latest_date >= senders.latest_date
                THEN excluded.display_name ELSE senders.display_name END,
            latest_thread_id = CASE WHEN excluded.latest_date >= senders.latest_date
                THEN excluded.latest_thread_id ELSE senders.latest_thread_id END,
            latest_list_unsubscribe = CASE WHEN excluded.latest_date >= senders.latest_date
                THEN excluded.latest_list_unsubscribe ELSE senders.latest_list_unsubscribe END,
            latest_list_unsubscribe_post = CASE WHEN excluded.latest_date >= senders.latest_date
                THEN excluded.latest_list_unsubscribe_post ELSE senders.latest_list_unsubscribe_post END,
            latest_date = MAX(senders.latest_date, excluded.latest_date)
        """,
        {
            "address": address,
            "name": name,
            "domain": domain_of(address),
            "is_list": has_header,
            "date": meta.date,
            "thread": meta.thread_id,
            "lu": meta.list_unsubscribe,
            "lup": meta.list_unsubscribe_post,
        },
    )
    conn.commit()
    return True


def known_ids(conn: sqlite3.Connection, ids: list[str]) -> set[str]:
    found: set[str] = set()
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        marks = ",".join("?" * len(chunk))
        rows = conn.execute(f"SELECT id FROM messages WHERE id IN ({marks})", chunk)
        found.update(r[0] for r in rows)
    return found


def _row_to_sender(row: sqlite3.Row) -> Sender:
    data = dict(row)
    data["is_list"] = bool(data["is_list"])
    return Sender(**data)


def get_sender(conn: sqlite3.Connection, address: str) -> Sender | None:
    row = conn.execute("SELECT * FROM senders WHERE address = ?", (address,)).fetchone()
    return _row_to_sender(row) if row else None


def all_senders(conn: sqlite3.Connection, where: str = "1", params: tuple = ()) -> list[Sender]:
    rows = conn.execute(f"SELECT * FROM senders WHERE {where}", params)
    return [_row_to_sender(r) for r in rows]


def count_messages(conn: sqlite3.Connection, address: str, since: int = 0) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE address = ? AND date > ?", (address, since)
    ).fetchone()
    return row[0]


def update_sender(conn: sqlite3.Connection, address: str, **values) -> None:
    unknown = set(values) - set(SENDER_COLUMNS)
    if unknown or "address" in values:
        raise ValueError(f"Unknown sender columns: {sorted(unknown or {'address'})}")
    if not values:
        return
    assignments = ", ".join(f"{k} = ?" for k in values)
    conn.execute(
        f"UPDATE senders SET {assignments} WHERE address = ?", (*values.values(), address)
    )
    conn.commit()


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM scan_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO scan_state(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
