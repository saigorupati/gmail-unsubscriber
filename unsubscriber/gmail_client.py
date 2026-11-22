"""Thin read-only wrapper over the Gmail API."""

import json
import time
from collections.abc import Callable, Iterator
from typing import Protocol

import httplib2

from google.auth.exceptions import RefreshError
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .auth import AuthRequired
from .models import MessageMeta

METADATA_HEADERS = ["From", "Date", "List-Unsubscribe", "List-Unsubscribe-Post"]
BATCH_SIZE = 50
BACKOFF = [1, 2, 4, 8, 16]


class GmailClient(Protocol):
    def list_message_ids(self, query: str) -> Iterator[str]: ...

    def get_metadata(self, ids: list[str]) -> list[MessageMeta]: ...


def meta_from_message(msg: dict) -> MessageMeta:
    headers = {
        h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])
    }
    return MessageMeta(
        id=msg["id"],
        thread_id=msg.get("threadId", ""),
        from_header=headers.get("from"),
        date=int(msg.get("internalDate", "0")) // 1000,
        list_unsubscribe=headers.get("list-unsubscribe"),
        list_unsubscribe_post=headers.get("list-unsubscribe-post"),
    )


def _status(err: Exception) -> int | None:
    return getattr(getattr(err, "resp", None), "status", None)


RATE_LIMIT_REASONS = {"rateLimitExceeded", "userRateLimitExceeded"}
PACE_SECONDS = 1.0  # one batch of 50 gets ~= 250 quota units; Gmail allows ~250/user/second


def _reasons(err: HttpError) -> set[str]:
    try:
        data = json.loads(err.content.decode("utf-8", "replace"))
        return {e.get("reason", "") for e in data.get("error", {}).get("errors", [])}
    except (ValueError, AttributeError):
        return set()


def is_retryable(err: Exception) -> bool:
    if isinstance(err, HttpError):
        status = _status(err)
        if status in (429, 500, 502, 503, 504):
            return True
        return status == 403 and bool(_reasons(err) & RATE_LIMIT_REASONS)
    return isinstance(err, (OSError, httplib2.HttpLib2Error))


def _is_auth_error(err: Exception) -> bool:
    return isinstance(err, RefreshError) or _status(err) == 401


class RealGmailClient:
    def __init__(self, creds, service=None, sleep: Callable[[float], None] = time.sleep):
        self._service = service or build("gmail", "v1", credentials=creds, cache_discovery=False)
        self._sleep = sleep

    def list_message_ids(self, query: str) -> Iterator[str]:
        messages = self._service.users().messages()
        page_token = None
        while True:
            try:
                resp = messages.list(
                    userId="me",
                    q=query,
                    maxResults=500,
                    includeSpamTrash=False,
                    pageToken=page_token,
                ).execute(num_retries=5)
            except Exception as e:
                if _is_auth_error(e):
                    raise AuthRequired("Google sign-in expired") from e
                raise
            for m in resp.get("messages", []):
                yield m["id"]
            page_token = resp.get("nextPageToken")
            if not page_token:
                return

    def _run_batch(self, ids: list[str], results: dict[str, MessageMeta]) -> list[str]:
        """Fetch one batch. Returns ids that should be retried; raises on fatal errors."""
        failed: list[str] = []
        fatal: list[Exception] = []

        def callback(request_id, response, exception):
            if exception is None:
                results[request_id] = meta_from_message(response)
            elif _is_auth_error(exception):
                fatal.append(AuthRequired("Google sign-in expired"))
            elif _status(exception) == 404:
                pass  # message deleted since listing
            elif is_retryable(exception) or _status(exception) is None:
                failed.append(request_id)
            else:
                fatal.append(exception)

        batch = self._service.new_batch_http_request(callback=callback)
        for mid in ids:
            batch.add(
                self._service.users().messages().get(
                    userId="me", id=mid, format="metadata", metadataHeaders=METADATA_HEADERS
                ),
                request_id=mid,
            )
        try:
            batch.execute()
        except Exception as e:
            if _is_auth_error(e):
                raise AuthRequired("Google sign-in expired") from e
            if is_retryable(e):
                return list(ids)
            raise
        if fatal:
            raise fatal[0]
        return failed

    def get_metadata(self, ids: list[str]) -> list[MessageMeta]:
        results: dict[str, MessageMeta] = {}
        for i in range(0, len(ids), BATCH_SIZE):
            if i:
                self._sleep(PACE_SECONDS)
            pending = ids[i : i + BATCH_SIZE]
            for attempt in range(len(BACKOFF) + 1):
                pending = self._run_batch(pending, results)
                if not pending:
                    break
                if attempt == len(BACKOFF):
                    raise RuntimeError(
                        f"Gmail kept refusing {len(pending)} requests; click Scan now to try again later"
                    )
                self._sleep(BACKOFF[attempt])
        return [results[i] for i in ids if i in results]
