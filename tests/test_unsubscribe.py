import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from unsubscriber.db import get_sender, record_message
from unsubscriber.review import keep
from unsubscriber.unsubscribe import confirm_manual, one_click_post, retry, unsubscribe

from .conftest import make_meta


class Stub:
    def __init__(self):
        self.status = 200
        self.delay = 0.0
        self.last = None
        self.location = None
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
                stub.last = ("POST", body, self.headers.get("Cookie"))
                time.sleep(stub.delay)
                self.send_response(stub.status)
                if stub.location:
                    self.send_header("Location", stub.location)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):
                stub.last = ("GET", "", self.headers.get("Cookie"))
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.server.server_port}{path}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def stub():
    s = Stub()
    yield s
    s.close()


def ok(url):
    return (True, None)


def test_one_click_post_success(stub):
    assert one_click_post(stub.url("/u")) == (True, None)
    assert stub.last == ("POST", "List-Unsubscribe=One-Click", None)


def test_one_click_post_http_error(stub):
    stub.status = 404
    assert one_click_post(stub.url("/u")) == (False, "HTTP 404")


def test_one_click_post_timeout(stub):
    stub.delay = 2
    assert one_click_post(stub.url("/slow"), timeout=0.5) == (False, "Timed out")


def test_one_click_post_refused():
    assert one_click_post("https://127.0.0.1:1/u", timeout=2) == (False, "Could not connect")


def test_unsubscribe_mixed(conn, now):
    record_message(conn, make_meta("1", frm="A <a@a.com>", now=now))  # one-click
    record_message(conn, make_meta("2", frm="B <b@b.com>", now=now, unsub="<mailto:u@b.com?subject=stop>", post=None))
    record_message(conn, make_meta("3", frm="K <k@k.com>", now=now))
    keep(conn, ["k@k.com"], now)
    calls = []
    res = {
        r.address: r
        for r in unsubscribe(conn, ["a@a.com", "b@b.com", "k@k.com", "zz@nope.com"], now,
                             post=lambda u: calls.append(u) or (True, None))
    }
    assert calls == ["https://promo.com/u"]  # only the one-click sender, never the kept one
    assert res["a@a.com"].outcome == "done" and get_sender(conn, "a@a.com").status == "unsubscribed"
    assert get_sender(conn, "a@a.com").method == "one_click"
    assert res["b@b.com"].outcome == "manual" and res["b@b.com"].method == "email"
    assert res["b@b.com"].action_url.startswith("https://mail.google.com/mail/?view=cm")
    assert get_sender(conn, "b@b.com").status == "new"
    assert res["k@k.com"].outcome == "skipped" and get_sender(conn, "k@k.com").status == "kept"
    assert res["zz@nope.com"].outcome == "skipped"


def test_page_method_returns_url_without_requesting_it(conn, now):
    record_message(conn, make_meta("1", now=now, unsub="<https://promo.com/page>", post=None))
    calls = []
    [r] = unsubscribe(conn, ["news@promo.com"], now, post=lambda u: calls.append(u) or (True, None))
    assert (r.outcome, r.method, r.action_url) == ("manual", "page", "https://promo.com/page")
    assert calls == []


def test_failed_one_click_offers_manual_and_stays_new(conn, now):
    record_message(conn, make_meta("1", now=now, unsub="<https://promo.com/u>, <mailto:u@promo.com>"))
    [r] = unsubscribe(conn, ["news@promo.com"], now, post=lambda u: (False, "HTTP 500"))
    assert (r.outcome, r.error, r.action_url) == ("failed", "HTTP 500", "https://promo.com/u")
    s = get_sender(conn, "news@promo.com")
    assert (s.status, s.last_error) == ("new", "HTTP 500")


def test_confirm_manual_marks_unsubscribed(conn, now):
    record_message(conn, make_meta("1", now=now, unsub="<mailto:u@promo.com>", post=None))
    confirm_manual(conn, "news@promo.com", "email", now)
    s = get_sender(conn, "news@promo.com")
    assert (s.status, s.method) == ("unsubscribed", "email")


def test_retry_after_one_click_goes_manual(conn, now):
    record_message(conn, make_meta("1", now=now))
    unsubscribe(conn, ["news@promo.com"], now, post=ok)
    calls = []
    r = retry(conn, "news@promo.com", now, post=lambda u: calls.append(u) or (True, None))
    assert r.outcome == "manual" and r.action_url == "https://promo.com/u" and calls == []


def test_no_header_opens_thread(conn, now):
    record_message(conn, make_meta("old", days_ago=5, now=now))  # makes it a list
    record_message(conn, make_meta("new", days_ago=1, now=now, unsub=None, post=None, thread="tLatest"))
    [r] = unsubscribe(conn, ["news@promo.com"], now, post=ok)
    assert (r.outcome, r.method, r.action_url) == ("manual", "none", "https://mail.google.com/mail/u/0/#all/tLatest")


def test_one_click_redirect_is_not_followed(stub):
    stub.status = 302
    stub.location = "/elsewhere"
    ok, err = one_click_post(stub.url("/u"))
    assert (ok, err) == (False, "Redirected (HTTP 302)")
    assert stub.last[0] == "POST"  # no follow-up GET was sent
