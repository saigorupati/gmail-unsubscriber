"""Unsubscribe actions. The only automatic request is the RFC 8058 one-click POST."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Literal

import requests

from . import db
from .config import ONE_CLICK_TIMEOUT
from .headers import gmail_compose_url, gmail_thread_url, parse_list_unsubscribe
from .models import Method, Sender
from .review import mark_unsubscribed, method_for

PostFn = Callable[[str], tuple[bool, str | None]]


@dataclass
class ActionResult:
    address: str
    outcome: Literal["done", "failed", "manual", "skipped"]
    method: Method | None
    error: str | None = None
    action_url: str | None = None


def one_click_post(url: str, timeout: float = ONE_CLICK_TIMEOUT) -> tuple[bool, str | None]:
    try:
        resp = requests.post(
            url,
            data="List-Unsubscribe=One-Click",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=timeout,
            allow_redirects=False,  # following would turn the POST into an automatic GET
        )
    except requests.ConnectTimeout:  # Windows reports refused connections this way
        return False, "Could not connect"
    except requests.Timeout:
        return False, "Timed out"
    except requests.ConnectionError:
        return False, "Could not connect"
    except requests.RequestException as e:
        return False, f"Request failed: {type(e).__name__}"
    if 200 <= resp.status_code < 300:
        return True, None
    if 300 <= resp.status_code < 400:
        return False, f"Redirected (HTTP {resp.status_code})"
    return False, f"HTTP {resp.status_code}"


def _manual_from_headers(sender: Sender) -> tuple[Method, str] | None:
    opts = parse_list_unsubscribe(sender.latest_list_unsubscribe, sender.latest_list_unsubscribe_post)
    urls = opts.https_urls or opts.http_urls
    if urls:
        return "page", urls[0]
    if opts.mailto:
        return "email", gmail_compose_url(opts.mailto)
    return None


def manual_action(sender: Sender) -> tuple[Method, str]:
    return _manual_from_headers(sender) or ("none", gmail_thread_url(sender.latest_thread_id))


def _one(conn, address: str, now: datetime, post: PostFn, prefer_manual: bool) -> ActionResult:
    sender = db.get_sender(conn, address)
    if sender is None or sender.status == "kept":
        return ActionResult(address, "skipped", None)
    method = method_for(sender)
    if method == "one_click" and not prefer_manual:
        opts = parse_list_unsubscribe(sender.latest_list_unsubscribe, sender.latest_list_unsubscribe_post)
        ok, err = post(opts.https_urls[0])
        if ok:
            mark_unsubscribed(conn, address, "one_click", now)
            return ActionResult(address, "done", "one_click")
        db.update_sender(conn, address, last_error=err)
        fallback = _manual_from_headers(sender)
        return ActionResult(
            address,
            "failed",
            fallback[0] if fallback else "one_click",
            error=err,
            action_url=fallback[1] if fallback else None,
        )
    manual_method, url = manual_action(sender)
    return ActionResult(address, "manual", manual_method, action_url=url)


def unsubscribe(
    conn: sqlite3.Connection,
    addresses: list[str],
    now: datetime,
    post: PostFn = one_click_post,
    prefer_manual: bool = False,
) -> list[ActionResult]:
    return [_one(conn, a, now, post, prefer_manual) for a in addresses]


def confirm_manual(conn: sqlite3.Connection, address: str, method: Method, now: datetime) -> None:
    mark_unsubscribed(conn, address, method, now)


def retry(conn: sqlite3.Connection, address: str, now: datetime, post: PostFn = one_click_post) -> ActionResult:
    sender = db.get_sender(conn, address)
    prefer_manual = bool(sender and sender.method == "one_click")
    return _one(conn, address, now, post, prefer_manual)
