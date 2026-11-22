import pytest

from unsubscriber.db import (
    count_messages,
    get_sender,
    get_state,
    known_ids,
    record_message,
    set_state,
    update_sender,
)

from .conftest import make_meta, ts


def test_record_and_count(conn, now):
    assert record_message(conn, make_meta("1", days_ago=40, now=now))
    assert record_message(conn, make_meta("2", days_ago=5, now=now))
    assert count_messages(conn, "news@promo.com") == 2
    assert count_messages(conn, "news@promo.com", since=ts(now, 30)) == 1


def test_duplicate_ignored(conn, now):
    record_message(conn, make_meta("1", now=now))
    assert record_message(conn, make_meta("1", now=now)) is False
    assert count_messages(conn, "news@promo.com") == 1


def test_bad_from_skipped(conn, now):
    assert record_message(conn, make_meta("1", frm="", now=now)) is False
    assert known_ids(conn, ["1"]) == set()


def test_known_ids(conn, now):
    record_message(conn, make_meta("1", now=now))
    assert known_ids(conn, ["1", "2"]) == {"1"}
    assert known_ids(conn, []) == set()


def test_latest_headers_win_and_is_list_sticks(conn, now):
    record_message(conn, make_meta("new", days_ago=1, now=now, unsub=None, post=None, thread="tNew"))
    record_message(conn, make_meta("old", days_ago=9, now=now, unsub="<mailto:u@promo.com>", post=None))
    s = get_sender(conn, "news@promo.com")
    assert s.is_list is True  # an older message had the header
    assert s.latest_thread_id == "tNew" and s.latest_list_unsubscribe is None
    assert s.status == "new" and s.domain == "promo.com"


def test_display_name_from_latest(conn, now):
    record_message(conn, make_meta("a", frm="Old Name <news@promo.com>", days_ago=5, now=now))
    record_message(conn, make_meta("b", frm="New Name <news@promo.com>", days_ago=1, now=now))
    assert get_sender(conn, "news@promo.com").display_name == "New Name"


def test_get_sender_missing(conn):
    assert get_sender(conn, "nobody@x.com") is None


def test_update_sender(conn, now):
    record_message(conn, make_meta("1", now=now))
    update_sender(conn, "news@promo.com", status="kept", status_at=5)
    s = get_sender(conn, "news@promo.com")
    assert (s.status, s.status_at) == ("kept", 5)


def test_update_sender_rejects_unknown_column(conn, now):
    record_message(conn, make_meta("1", now=now))
    with pytest.raises(ValueError):
        update_sender(conn, "news@promo.com", bogus=1)


def test_state_roundtrip(conn):
    assert get_state(conn, "last_completed") is None
    set_state(conn, "last_completed", "123")
    set_state(conn, "last_completed", "456")
    assert get_state(conn, "last_completed") == "456"
