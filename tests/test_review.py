from datetime import timedelta

import pytest

from unsubscriber.db import get_sender, record_message
from unsubscriber.review import (
    decided,
    hide,
    keep,
    mark_unsubscribed,
    review_addresses,
    revive_skipped,
    skip_undecided,
    still_sending,
    to_review,
    undo,
)

from .conftest import make_meta


def test_to_review_ranks_by_30d_and_hides_quiet_and_non_lists(conn, now):
    for i in range(5):
        record_message(conn, make_meta(f"a{i}", frm="A <a@a.com>", days_ago=60, now=now))  # 5 total, 0 recent
    for i in range(2):
        record_message(conn, make_meta(f"b{i}", frm="B <b@b.com>", days_ago=3, now=now))  # 2 recent
    record_message(conn, make_meta("c", frm="C <c@c.com>", days_ago=100, now=now))  # quiet >90d
    record_message(conn, make_meta("p", frm="Mom <mom@home.com>", days_ago=1, now=now, unsub=None, post=None))
    rows = to_review(conn, now)
    assert [r.sender.address for r in rows] == ["b@b.com", "a@a.com"]
    assert (rows[0].count_30d, rows[0].count_total, rows[0].method) == (2, 2, "one_click")
    assert (rows[1].count_30d, rows[1].count_total) == (0, 5)


def test_still_sending_respects_grace_and_hide(conn, now):
    record_message(conn, make_meta("1", days_ago=30, now=now))
    mark_unsubscribed(conn, "news@promo.com", "one_click", now - timedelta(days=20))
    record_message(conn, make_meta("2", days_ago=15, now=now))  # within 10-day grace → ignored
    assert still_sending(conn, now) == []
    record_message(conn, make_meta("3", days_ago=5, now=now))  # after grace
    assert [r.count_since for r in still_sending(conn, now)] == [1]
    hide(conn, "news@promo.com", now)
    assert still_sending(conn, now) == []
    record_message(conn, make_meta("4", days_ago=-0.01, now=now))  # sends again after hide
    assert len(still_sending(conn, now + timedelta(days=1))) == 1


def test_skip_then_revive_only_when_volume_rises(conn, now):
    record_message(conn, make_meta("1", days_ago=2, now=now))
    skip_undecided(conn, review_addresses(conn, now), now)
    s = get_sender(conn, "news@promo.com")
    assert (s.status, s.skip_baseline) == ("skipped", 1)
    assert to_review(conn, now) == []
    revive_skipped(conn, now)
    assert get_sender(conn, "news@promo.com").status == "skipped"
    record_message(conn, make_meta("2", days_ago=1, now=now))
    revive_skipped(conn, now)
    s = get_sender(conn, "news@promo.com")
    assert (s.status, s.skip_baseline) == ("new", None)


def test_skip_ignores_decided_senders(conn, now):
    record_message(conn, make_meta("1", now=now))
    addrs = review_addresses(conn, now)
    keep(conn, ["news@promo.com"], now)
    skip_undecided(conn, addrs, now)
    assert get_sender(conn, "news@promo.com").status == "kept"


def test_cannot_unsubscribe_kept(conn, now):
    record_message(conn, make_meta("1", now=now))
    keep(conn, ["news@promo.com"], now)
    with pytest.raises(ValueError):
        mark_unsubscribed(conn, "news@promo.com", "one_click", now)


def test_mark_unsubscribed_records_method_and_clears_error(conn, now):
    from unsubscriber.db import update_sender

    record_message(conn, make_meta("1", now=now))
    update_sender(conn, "news@promo.com", last_error="HTTP 500")
    mark_unsubscribed(conn, "news@promo.com", "page", now)
    s = get_sender(conn, "news@promo.com")
    assert (s.status, s.method, s.last_error, s.status_at) == ("unsubscribed", "page", None, int(now.timestamp()))


def test_undo_resets(conn, now):
    record_message(conn, make_meta("1", now=now))
    keep(conn, ["news@promo.com"], now)
    undo(conn, "news@promo.com", now)
    assert [r.sender.address for r in to_review(conn, now)] == ["news@promo.com"]


def test_decided_lists(conn, now):
    record_message(conn, make_meta("1", frm="Zed <z@z.com>", now=now))
    record_message(conn, make_meta("2", frm="Abe <a@a.com>", now=now))
    keep(conn, ["z@z.com", "a@a.com"], now)
    assert [r.sender.address for r in decided(conn, "kept", now)] == ["a@a.com", "z@z.com"]
    assert decided(conn, "unsubscribed", now) == []
