import time

from unsubscriber import db
from unsubscriber.auth import AuthRequired
from unsubscriber.web import create_app

from .conftest import NOW, FakeGmailClient, make_meta

H = {"Origin": "http://localhost"}


def raises(exc):
    def factory():
        raise exc("expired")

    return factory


def make_app(tmp_path, creds=True, seed=(), client=None, factory=None, post=None):
    creds_path = tmp_path / "credentials.json"
    if creds:
        creds_path.write_text("{}")
    db_path = tmp_path / "app.db"
    conn = db.connect(db_path)
    for m in seed:
        db.record_message(conn, m)
    conn.close()
    client = client or FakeGmailClient([])
    app = create_app(
        db_path=db_path,
        credentials_path=creds_path,
        token_path=tmp_path / "token.json",
        client_factory=factory or (lambda: client),
        post=post or (lambda u: (True, None)),
        now_fn=lambda: NOW,
        allowed_hosts={"localhost"},
    )
    app.testing = True
    return app


def wait_until(pred, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met")


def test_setup_page_without_credentials(tmp_path):
    c = make_app(tmp_path, creds=False).test_client()
    assert b"Google Cloud" in c.get("/").data


def test_signin_page_when_auth_required(tmp_path):
    c = make_app(tmp_path, factory=raises(AuthRequired)).test_client()
    assert b"Sign in with Google" in c.get("/").data


def test_index_lists_review_rows(tmp_path, now):
    body = make_app(tmp_path, seed=[make_meta("1", now=now)]).test_client().get("/").data
    assert b"news@promo.com" in body and b"Automatic" in body
    assert b"https://mail.google.com/mail/u/0/#search/from%3Anews%40promo.com" in body  # View emails
    assert b"How this works" in body


def test_index_hides_non_lists(tmp_path, now):
    seed = [make_meta("1", frm="Mom <mom@home.com>", now=now, unsub=None, post=None)]
    body = make_app(tmp_path, seed=seed).test_client().get("/").data
    assert b"mom@home.com" not in body


def test_scan_runs_and_reports(tmp_path, now):
    c = make_app(tmp_path, client=FakeGmailClient([make_meta("1", now=now)])).test_client()
    assert c.post("/api/scan", json={}, headers=H).status_code == 200
    wait_until(lambda: not c.get("/api/scan").json["running"])
    assert c.get("/api/scan").json["new_messages"] == 1
    assert b"news@promo.com" in c.get("/").data


def test_scan_auth_failure_surfaces(tmp_path):
    class Expired(FakeGmailClient):
        def list_message_ids(self, q):
            raise AuthRequired("expired")

    c = make_app(tmp_path, client=Expired([])).test_client()
    c.post("/api/scan", json={}, headers=H)
    wait_until(lambda: not c.get("/api/scan").json["running"])
    s = c.get("/api/scan").json
    assert s["auth_required"] is True and s["running"] is False


def test_unsubscribe_endpoint(tmp_path, now):
    c = make_app(tmp_path, seed=[make_meta("1", now=now)]).test_client()
    r = c.post("/api/unsubscribe", json={"addresses": ["news@promo.com"]}, headers=H)
    assert r.status_code == 200 and r.json[0]["outcome"] == "done"


def test_keep_confirm_hide_undo_retry(tmp_path, now):
    seed = [make_meta("1", now=now), make_meta("2", frm="B <b@b.com>", now=now, unsub="<mailto:u@b.com>", post=None)]
    app = make_app(tmp_path, seed=seed)
    c = app.test_client()
    assert c.post("/api/keep", json={"addresses": ["news@promo.com"]}, headers=H).json == {"ok": True}
    assert c.post("/api/undo", json={"address": "news@promo.com"}, headers=H).json == {"ok": True}
    assert c.post("/api/confirm", json={"address": "b@b.com", "method": "email"}, headers=H).json == {"ok": True}
    assert c.post("/api/hide", json={"address": "b@b.com"}, headers=H).json == {"ok": True}
    assert c.post("/api/retry", json={"address": "b@b.com"}, headers=H).json["outcome"] == "manual"
    conn = db.connect(tmp_path / "app.db")
    assert db.get_sender(conn, "news@promo.com").status == "new"
    assert db.get_sender(conn, "b@b.com").status == "unsubscribed"


def test_unknown_address_is_harmless(tmp_path):
    c = make_app(tmp_path).test_client()
    assert c.post("/api/hide", json={"address": "nobody@x.com"}, headers=H).status_code == 200
    assert c.post("/api/confirm", json={"address": "nobody@x.com", "method": "email"}, headers=H).status_code == 200


def test_bad_payload_is_400(tmp_path):
    c = make_app(tmp_path).test_client()
    assert c.post("/api/unsubscribe", json={"addresses": "news@promo.com"}, headers=H).status_code == 400
    assert c.post("/api/confirm", json={"address": "a@a.com", "method": "bogus"}, headers=H).status_code == 400


def test_cross_site_post_rejected(tmp_path, now):
    calls = []
    c = make_app(tmp_path, seed=[make_meta("1", now=now)], post=lambda u: calls.append(u) or (True, None)).test_client()
    payload = {"addresses": ["news@promo.com"]}
    assert c.post("/api/unsubscribe", json=payload, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/unsubscribe", data="addresses=news@promo.com", headers=H).status_code == 403
    assert c.post("/api/unsubscribe", json=payload).status_code == 403  # no Origin
    assert calls == []


def test_foreign_host_header_rejected(tmp_path, now):
    """DNS-rebinding: a page on another domain reaching 127.0.0.1 sends its own Host."""
    calls = []
    c = make_app(tmp_path, seed=[make_meta("1", now=now)], post=lambda u: calls.append(u) or (True, None)).test_client()
    evil = {"Host": "evil.example:5000", "Origin": "http://evil.example:5000"}
    assert c.get("/", headers={"Host": "evil.example:5000"}).status_code == 403
    assert c.post("/api/unsubscribe", json={"addresses": ["news@promo.com"]}, headers=evil).status_code == 403
    assert calls == []


def test_signin_shown_after_scan_hits_auth_error(tmp_path):
    class Expired(FakeGmailClient):
        def list_message_ids(self, q):
            raise AuthRequired("expired")

    c = make_app(tmp_path, client=Expired([])).test_client()
    c.post("/api/scan", json={}, headers=H)
    wait_until(lambda: not c.get("/api/scan").json["running"])
    assert b"Sign in with Google" in c.get("/").data


def test_offline_shows_message_not_500(tmp_path):
    from google.auth.exceptions import TransportError

    r = make_app(tmp_path, factory=raises(TransportError)).test_client().get("/")
    assert r.status_code == 200 and b"reach Google" in r.data
