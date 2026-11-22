from dataclasses import dataclass
from typing import Literal

Method = Literal["one_click", "page", "email", "none"]


@dataclass(frozen=True)
class MessageMeta:
    id: str
    thread_id: str
    from_header: str | None
    date: int  # epoch seconds (Gmail internalDate // 1000)
    list_unsubscribe: str | None
    list_unsubscribe_post: str | None


@dataclass
class Sender:
    address: str
    display_name: str
    domain: str
    is_list: bool
    latest_date: int
    latest_thread_id: str
    latest_list_unsubscribe: str | None
    latest_list_unsubscribe_post: str | None
    status: str
    status_at: int | None
    method: str | None
    last_error: str | None
    skip_baseline: int | None
    acknowledged_at: int | None


@dataclass
class SenderRow:
    sender: Sender
    count_30d: int
    count_total: int
    method: Method


@dataclass
class StillSendingRow:
    sender: Sender
    count_since: int


@dataclass(frozen=True)
class UnsubOptions:
    https_urls: list[str]
    http_urls: list[str]
    mailto: str | None
    one_click: bool
