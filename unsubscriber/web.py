"""Flask app: one page plus a small JSON API. Bound to 127.0.0.1 by the launcher."""

import threading
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

from google.auth.exceptions import TransportError
from flask import Flask, abort, g, jsonify, render_template, request

from . import auth, db, review
from .auth import AuthRequired
from .gmail_client import GmailClient
from .scanner import ScanProgress, run_scan
from .unsubscribe import PostFn, confirm_manual, one_click_post, retry, unsubscribe

METHOD_LABELS = {
    "one_click": "One-click",
    "page": "Opens page",
    "email": "Email draft",
    "none": "Open in Gmail",
}
MANUAL_METHODS = {"page", "email", "none"}


def _fmt_date(epoch: int | None) -> str:
    if not epoch:
        return ""
    return datetime.fromtimestamp(epoch, UTC).strftime("%b %d, %Y").replace(" 0", " ")


def _ago(epoch: int, now: datetime) -> str:
    days = int((now.timestamp() - epoch) // 86400)
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 30:
        return f"{days} days ago"
    months = days // 30
    return "1 month ago" if months == 1 else f"{months} months ago"


def create_app(
    db_path: Path | str,
    credentials_path: Path,
    token_path: Path,
    client_factory: Callable[[], GmailClient],
    post: PostFn = one_click_post,
    now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
    sign_in_fn: Callable[[Path, Path], object] = auth.sign_in,
    *,
    allowed_hosts: set[str],
) -> Flask:
    """allowed_hosts: exact Host header values to accept (e.g. {"127.0.0.1:5123"}).
    Checking them on every request blocks DNS-rebinding pages."""
    app = Flask(__name__)
    progress = ScanProgress()
    scan_lock = threading.Lock()

    def conn():
        if "conn" not in g:
            g.conn = db.connect(db_path)
        return g.conn

    @app.teardown_appcontext
    def _close(_exc):
        c = g.pop("conn", None)
        if c is not None:
            c.close()

    @app.before_request
    def _guard():
        if request.host not in allowed_hosts:
            abort(403)
        if request.method != "POST":
            return
        origin = request.headers.get("Origin")
        if not request.is_json or origin != f"http://{request.host}":
            abort(403)

    def body() -> dict:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400)
        return data

    def addresses_arg() -> list[str]:
        value = body().get("addresses")
        if not isinstance(value, list) or not all(isinstance(a, str) for a in value):
            abort(400)
        return value

    def address_arg() -> str:
        value = body().get("address")
        if not isinstance(value, str):
            abort(400)
        return value

    app.jinja_env.globals.update(method_label=METHOD_LABELS.get, fmt_date=_fmt_date)

    @app.get("/")
    def index():
        if not Path(credentials_path).exists():
            return render_template("setup.html")
        if progress.auth_required and not progress.running:
            return render_template("signin.html")
        try:
            client_factory()
        except AuthRequired:
            return render_template("signin.html")
        except TransportError:
            return render_template(
                "message.html",
                title="Can't reach Google",
                text="Couldn't reach Google to check your sign-in. Check your internet connection and reload.",
            )
        now = now_fn()
        c = conn()
        last = db.get_state(c, "last_completed")
        return render_template(
            "index.html",
            still=review.still_sending(c, now),
            rows=review.to_review(c, now),
            kept=review.decided(c, "kept", now),
            unsubscribed=review.decided(c, "unsubscribed", now),
            skipped=review.decided(c, "skipped", now),
            last_scan=_fmt_date(int(last)) if last else None,
            last_new=db.get_state(c, "last_new_messages"),
            ago=lambda e: _ago(e, now),
            scanning=progress.running,
        )

    @app.post("/api/signin")
    def api_signin():
        sign_in_fn(credentials_path, token_path)
        progress.auth_required = False
        progress.error = None
        return jsonify(ok=True)

    def _scan_worker():
        scan_conn = db.connect(db_path)
        try:
            try:
                client = client_factory()
            except AuthRequired as e:
                progress.error, progress.auth_required = str(e) or "Sign-in required", True
                return
            except Exception as e:
                progress.error = str(e) or type(e).__name__
                return
            run_scan(scan_conn, client, now_fn(), progress)
        finally:
            progress.running = False
            scan_conn.close()

    @app.post("/api/scan")
    def api_scan_start():
        with scan_lock:
            if progress.running:
                return jsonify(error="A scan is already running"), 409
            progress.running = True
            progress.error = None
            progress.auth_required = False
            progress.done = progress.total = progress.new_messages = 0
            threading.Thread(target=_scan_worker, daemon=True).start()
        return jsonify(ok=True)

    @app.get("/api/scan")
    def api_scan_status():
        return jsonify(asdict(progress))

    @app.post("/api/unsubscribe")
    def api_unsubscribe():
        results = unsubscribe(conn(), addresses_arg(), now_fn(), post=post)
        return jsonify([asdict(r) for r in results])

    @app.post("/api/keep")
    def api_keep():
        review.keep(conn(), addresses_arg(), now_fn())
        return jsonify(ok=True)

    @app.post("/api/confirm")
    def api_confirm():
        address = address_arg()
        method = body().get("method")
        if method not in MANUAL_METHODS:
            abort(400)
        sender = db.get_sender(conn(), address)
        if sender is not None and sender.status != "kept":
            confirm_manual(conn(), address, method, now_fn())
        return jsonify(ok=True)

    @app.post("/api/retry")
    def api_retry():
        return jsonify(asdict(retry(conn(), address_arg(), now_fn(), post=post)))

    @app.post("/api/hide")
    def api_hide():
        review.hide(conn(), address_arg(), now_fn())
        return jsonify(ok=True)

    @app.post("/api/undo")
    def api_undo():
        review.undo(conn(), address_arg(), now_fn())
        return jsonify(ok=True)

    return app
