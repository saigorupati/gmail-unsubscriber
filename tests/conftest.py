from datetime import UTC, datetime, timedelta

import pytest

from unsubscriber import db
from unsubscriber.models import MessageMeta

NOW = datetime(2026, 11, 22, 12, tzinfo=UTC)

_DEFAULT = object()


def ts(now: datetime, days_ago: float) -> int:
    return int((now - timedelta(days=days_ago)).timestamp())


def make_meta(
    id: str,
    frm: str = "Promo <news@promo.com>",
    days_ago: float = 1,
    now: datetime = NOW,
    unsub=_DEFAULT,
    post=_DEFAULT,
    thread: str | None = None,
) -> MessageMeta:
    return MessageMeta(
        id=id,
        thread_id=thread or f"t-{id}",
        from_header=frm,
        date=ts(now, days_ago),
        list_unsubscribe="<https://promo.com/u>" if unsub is _DEFAULT else unsub,
        list_unsubscribe_post="List-Unsubscribe=One-Click" if post is _DEFAULT else post,
    )


class FakeGmailClient:
    """In-memory GmailClient. Honors an `after:<epoch>` term in queries."""

    def __init__(self, messages: list[MessageMeta], fail_after: int | None = None):
        self.messages = {m.id: m for m in messages}
        self.fail_after = fail_after
        self.returned = 0
        self.queries: list[str] = []

    def list_message_ids(self, query: str):
        self.queries.append(query)
        after = 0
        for term in query.split():
            if term.startswith("after:"):
                after = int(term[len("after:"):])
        return iter([m.id for m in self.messages.values() if m.date > after])

    def get_metadata(self, ids: list[str]) -> list[MessageMeta]:
        if self.fail_after is not None and self.returned >= self.fail_after:
            raise RuntimeError("boom")
        out = [self.messages[i] for i in ids if i in self.messages]
        self.returned += len(out)
        return out


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    yield c
    c.close()
