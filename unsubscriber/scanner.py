"""Full and incremental scans. Every chunk is committed, so an interrupted
scan resumes by re-listing and skipping messages already stored."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from . import db, review
from .auth import AuthRequired
from .gmail_client import GmailClient

BASE_QUERY = "-in:sent -in:draft"  # spam/trash excluded via includeSpamTrash=False
CHUNK = 100
OVERLAP_SECONDS = 86400


@dataclass
class ScanProgress:
    running: bool = False
    done: int = 0
    total: int = 0
    new_messages: int = 0
    error: str | None = None
    auth_required: bool = False


def build_query(conn: sqlite3.Connection) -> str:
    last = db.get_state(conn, "last_completed")
    if last is None:
        return BASE_QUERY
    return f"{BASE_QUERY} after:{int(last) - OVERLAP_SECONDS}"


def run_scan(conn: sqlite3.Connection, client: GmailClient, now: datetime, progress: ScanProgress) -> None:
    progress.running = True
    progress.error = None
    progress.auth_required = False
    progress.done = progress.total = progress.new_messages = 0
    try:
        shown = review.review_addresses(conn, now)
        ids = list(client.list_message_ids(build_query(conn)))
        known = db.known_ids(conn, ids)
        todo = [i for i in ids if i not in known]
        progress.total = len(todo)
        for start in range(0, len(todo), CHUNK):
            chunk = todo[start : start + CHUNK]
            for meta in client.get_metadata(chunk):
                if db.record_message(conn, meta):
                    progress.new_messages += 1
            progress.done += len(chunk)
        review.skip_undecided(conn, shown, now)
        review.revive_skipped(conn, now)
        db.set_state(conn, "last_completed", str(int(now.timestamp())))
        db.set_state(conn, "last_new_messages", str(progress.new_messages))
    except AuthRequired as e:
        progress.auth_required = True
        progress.error = str(e) or "Google sign-in expired"
    except Exception as e:  # surfaced in the UI, never raised
        progress.error = str(e) or type(e).__name__
    finally:
        progress.running = False
