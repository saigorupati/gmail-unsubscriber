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


@dataclass(frozen=True)
class UnsubOptions:
    https_urls: list[str]
    http_urls: list[str]
    mailto: str | None
    one_click: bool
