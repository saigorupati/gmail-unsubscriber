from datetime import timedelta

from unsubscriber.auth import AuthRequired
from unsubscriber.db import count_messages, get_sender, get_state
from unsubscriber.scanner import ScanProgress, run_scan

from .conftest import FakeGmailClient, make_meta


def test_first_scan_records_everything(conn, now):
    msgs = [make_meta(str(i), days_ago=i, now=now) for i in range(250)]
    p = ScanProgress()
    run_scan(conn, FakeGmailClient(msgs), now, p)
    assert (p.done, p.total, p.new_messages, p.error, p.running) == (250, 250, 250, None, False)
    assert get_state(conn, "last_completed") == str(int(now.timestamp()))
    assert get_state(conn, "last_new_messages") == "250"


def test_bad_from_does_not_abort(conn, now):
    msgs = [make_meta("1", frm="", now=now), make_meta("2", now=now)]
    p = ScanProgress()
    run_scan(conn, FakeGmailClient(msgs), now, p)
    assert p.error is None and count_messages(conn, "news@promo.com") == 1
    assert p.new_messages == 1


def test_crash_then_resume_skips_known(conn, now):
    msgs = [make_meta(str(i), now=now) for i in range(250)]
    p = ScanProgress()
    run_scan(conn, FakeGmailClient(msgs, fail_after=100), now, p)
    assert p.error == "boom" and not p.running and get_state(conn, "last_completed") is None
    p2 = ScanProgress()
    run_scan(conn, FakeGmailClient(msgs), now, p2)
    assert p2.total == 150 and p2.error is None and count_messages(conn, "news@promo.com") == 250


def test_incremental_uses_after_query(conn, now):
    run_scan(conn, FakeGmailClient([make_meta("1", now=now)]), now, ScanProgress())
    client = FakeGmailClient([make_meta("1", now=now)])
    run_scan(conn, client, now + timedelta(days=30), ScanProgress())
    assert client.queries[-1] == f"-in:sent -in:draft after:{int(now.timestamp()) - 86400}"


def test_first_query_has_no_after(conn, now):
    client = FakeGmailClient([])
    run_scan(conn, client, now, ScanProgress())
    assert client.queries == ["-in:sent -in:draft"]


def test_undecided_become_skipped_after_next_scan(conn, now):
    run_scan(conn, FakeGmailClient([make_meta("1", now=now)]), now, ScanProgress())
    assert get_sender(conn, "news@promo.com").status == "new"  # newly found: not skipped yet
    run_scan(conn, FakeGmailClient([make_meta("1", now=now)]), now + timedelta(days=30), ScanProgress())
    assert get_sender(conn, "news@promo.com").status == "skipped"


def test_failed_scan_does_not_skip(conn, now):
    run_scan(conn, FakeGmailClient([make_meta("1", now=now)]), now, ScanProgress())
    msgs = [make_meta("1", now=now), make_meta("2", days_ago=-5, now=now)]  # new mail after last scan
    p = ScanProgress()
    run_scan(conn, FakeGmailClient(msgs, fail_after=0), now + timedelta(days=30), p)
    assert p.error == "boom"
    assert get_sender(conn, "news@promo.com").status == "new"


def test_auth_failure_reported(conn, now):
    class Expired(FakeGmailClient):
        def list_message_ids(self, q):
            raise AuthRequired("expired")

    p = ScanProgress()
    run_scan(conn, Expired([]), now, p)
    assert p.auth_required and not p.running and p.error


def test_resumed_first_scan_does_not_skip_unreviewed_senders(conn, now):
    msgs = [make_meta(str(i), frm=f"S{i} <s{i}@x.com>", now=now) for i in range(250)]
    run_scan(conn, FakeGmailClient(msgs, fail_after=100), now, ScanProgress())
    p = ScanProgress()
    run_scan(conn, FakeGmailClient(msgs), now, p)
    assert p.error is None
    assert get_sender(conn, "s0@x.com").status == "new"


def test_retried_monthly_scan_does_not_skip_new_senders(conn, now):
    run_scan(conn, FakeGmailClient([make_meta("1", now=now)]), now, ScanProgress())
    later = now + timedelta(days=30)
    msgs = [make_meta("1", now=now)] + [
        make_meta(f"n{i}", frm=f"N{i} <n{i}@y.com>", days_ago=-29, now=now) for i in range(150)
    ]
    run_scan(conn, FakeGmailClient(msgs, fail_after=100), later, ScanProgress())  # fails part-way
    run_scan(conn, FakeGmailClient(msgs), later, ScanProgress())  # retry succeeds
    assert get_sender(conn, "n0@y.com").status == "new"  # found during this month's scans
    assert get_sender(conn, "news@promo.com").status == "skipped"  # shown last month, undecided
