"""Thin read-only wrapper over the Gmail API."""

import time
from collections.abc import Iterator
from typing import Protocol

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


class RealGmailClient:
    def __init__(self, creds):
        self._service = build("gmail", "v1", credentials=creds, cache_discovery=False)

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
            except RefreshError as e:
                raise AuthRequired("Google sign-in expired") from e
            except HttpError as e:
                if _status(e) == 401:
                    raise AuthRequired("Google sign-in expired") from e
                raise
            for m in resp.get("messages", []):
                yield m["id"]
            page_token = resp.get("nextPageToken")
            if not page_token:
                return

    def get_metadata(self, ids: list[str]) -> list[MessageMeta]:
        results: dict[str, MessageMeta] = {}
        for i in range(0, len(ids), BATCH_SIZE):
            pending = ids[i : i + BATCH_SIZE]
            for attempt in range(len(BACKOFF) + 1):
                failed: list[str] = []
                fatal: list[Exception] = []

                def callback(request_id, response, exception, _failed=failed, _fatal=fatal):
                    if exception is None:
                        results[request_id] = meta_from_message(response)
                        return
                    status = _status(exception)
                    if status == 401 or isinstance(exception, RefreshError):
                        _fatal.append(AuthRequired("Google sign-in expired"))
                    elif status == 404:
                        pass  # message deleted since listing
                    elif status in (429, 500, 502, 503, 504) or status is None:
                        _failed.append(request_id)
                    else:
                        _fatal.append(exception)

                batch = self._service.new_batch_http_request(callback=callback)
                for mid in pending:
                    batch.add(
                        self._service.users().messages().get(
                            userId="me",
                            id=mid,
                            format="metadata",
                            metadataHeaders=METADATA_HEADERS,
                        ),
                        request_id=mid,
                    )
                try:
                    batch.execute()
                except RefreshError as e:
                    raise AuthRequired("Google sign-in expired") from e
                if fatal:
                    raise fatal[0]
                if not failed:
                    break
                if attempt == len(BACKOFF):
                    raise RuntimeError(f"Gmail kept refusing {len(failed)} requests; try again later")
                time.sleep(BACKOFF[attempt])
                pending = failed
        return [results[i] for i in ids if i in results]
