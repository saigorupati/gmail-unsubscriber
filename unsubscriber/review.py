"""Review rules: what to show, and status changes."""

import sqlite3
from datetime import datetime, timedelta

from . import db
from .config import GRACE_DAYS, RANK_DAYS, REVIEW_WINDOW_DAYS
from .headers import choose_method, parse_list_unsubscribe
from .models import Method, Sender, SenderRow, StillSendingRow


def _epoch(dt: datetime) -> int:
    return int(dt.timestamp())


def _days_ago(now: datetime, days: int) -> int:
    return _epoch(now - timedelta(days=days))


def method_for(sender: Sender) -> Method:
    return choose_method(
        parse_list_unsubscribe(sender.latest_list_unsubscribe, sender.latest_list_unsubscribe_post)
    )


def count_30d(conn: sqlite3.Connection, address: str, now: datetime) -> int:
    return db.count_messages(conn, address, since=_days_ago(now, RANK_DAYS))


def _row(conn: sqlite3.Connection, sender: Sender, now: datetime) -> SenderRow:
    return SenderRow(
        sender=sender,
        count_30d=count_30d(conn, sender.address, now),
        count_total=db.count_messages(conn, sender.address),
        method=method_for(sender),
    )


def to_review(conn: sqlite3.Connection, now: datetime) -> list[SenderRow]:
    senders = db.all_senders(
        conn,
        "is_list = 1 AND status = 'new' AND latest_date >= ?",
        (_days_ago(now, REVIEW_WINDOW_DAYS),),
    )
    rows = [_row(conn, s, now) for s in senders]
    rows.sort(key=lambda r: (-r.count_30d, -r.count_total, r.sender.address))
    return rows


def review_addresses(conn: sqlite3.Connection, now: datetime) -> set[str]:
    return {r.sender.address for r in to_review(conn, now)}


def still_sending(conn: sqlite3.Connection, now: datetime) -> list[StillSendingRow]:
    result = []
    for s in db.all_senders(conn, "status = 'unsubscribed'"):
        cutoff = max((s.status_at or 0) + GRACE_DAYS * 86400, s.acknowledged_at or 0)
        n = db.count_messages(conn, s.address, since=cutoff)
        if n >= 1:
            result.append(StillSendingRow(sender=s, count_since=n))
    result.sort(key=lambda r: (-r.count_since, r.sender.address))
    return result


def decided(conn: sqlite3.Connection, status: str, now: datetime) -> list[SenderRow]:
    senders = db.all_senders(conn, "is_list = 1 AND status = ?", (status,))
    rows = [_row(conn, s, now) for s in senders]
    rows.sort(key=lambda r: (r.sender.display_name.lower(), r.sender.address))
    return rows


def skip_undecided(conn: sqlite3.Connection, addresses: set[str], now: datetime) -> None:
    for address in addresses:
        s = db.get_sender(conn, address)
        if s and s.status == "new":
            db.update_sender(
                conn,
                address,
                status="skipped",
                status_at=_epoch(now),
                skip_baseline=count_30d(conn, address, now),
            )


def revive_skipped(conn: sqlite3.Connection, now: datetime) -> None:
    for s in db.all_senders(conn, "status = 'skipped'"):
        if count_30d(conn, s.address, now) > (s.skip_baseline or 0):
            db.update_sender(conn, s.address, status="new", status_at=_epoch(now), skip_baseline=None)


def keep(conn: sqlite3.Connection, addresses: list[str], now: datetime) -> None:
    for address in addresses:
        if db.get_sender(conn, address):
            db.update_sender(conn, address, status="kept", status_at=_epoch(now), last_error=None)


def hide(conn: sqlite3.Connection, address: str, now: datetime) -> None:
    if db.get_sender(conn, address):
        db.update_sender(conn, address, acknowledged_at=_epoch(now))


def undo(conn: sqlite3.Connection, address: str, now: datetime) -> None:
    if db.get_sender(conn, address):
        db.update_sender(
            conn,
            address,
            status="new",
            status_at=_epoch(now),
            method=None,
            last_error=None,
            skip_baseline=None,
            acknowledged_at=None,
        )


def mark_unsubscribed(conn: sqlite3.Connection, address: str, method: Method, now: datetime) -> None:
    s = db.get_sender(conn, address)
    if s is None:
        raise ValueError(f"Unknown sender {address}")
    if s.status == "kept":
        raise ValueError(f"{address} is marked keep")
    db.update_sender(
        conn,
        address,
        status="unsubscribed",
        status_at=_epoch(now),
        method=method,
        last_error=None,
        acknowledged_at=None,
    )
