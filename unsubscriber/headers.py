"""Pure parsing of email headers and Gmail URL helpers."""

import re
from email.header import decode_header, make_header
from email.utils import parseaddr
from urllib.parse import parse_qs, unquote, urlencode, urlsplit

from .models import Method, UnsubOptions

_URI_RE = re.compile(r"<\s*([^>]+?)\s*>")


def _decode(value: str) -> str:
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def parse_from(value: str | None) -> tuple[str, str] | None:
    """Return (display_name, lowercased address) or None if no usable address."""
    if not value:
        return None
    name, address = parseaddr(_decode(value))
    address = address.strip().lower()
    if "@" not in address or address.startswith("@") or address.endswith("@"):
        return None
    name = name.strip() or address
    return name, address


def domain_of(address: str) -> str:
    return address.rsplit("@", 1)[-1]


def parse_list_unsubscribe(value: str | None, post: str | None) -> UnsubOptions:
    https_urls: list[str] = []
    http_urls: list[str] = []
    mailto: str | None = None
    if value:
        unfolded = re.sub(r"\s+", " ", value)
        for uri in _URI_RE.findall(unfolded):
            lower = uri.lower()
            if lower.startswith("https://"):
                https_urls.append(uri)
            elif lower.startswith("http://"):
                http_urls.append(uri)
            elif lower.startswith("mailto:") and mailto is None:
                mailto = uri
    one_click = bool(
        https_urls and post and post.strip().lower() == "list-unsubscribe=one-click"
    )
    return UnsubOptions(https_urls, http_urls, mailto, one_click)


def choose_method(opts: UnsubOptions) -> Method:
    if opts.one_click:
        return "one_click"
    if opts.https_urls or opts.http_urls:
        return "page"
    if opts.mailto:
        return "email"
    return "none"


def gmail_compose_url(mailto: str) -> str:
    parts = urlsplit(mailto)
    params = {"view": "cm", "to": unquote(parts.path)}
    query = parse_qs(parts.query)
    for src, dst in (("subject", "su"), ("body", "body")):
        values = query.get(src) or query.get(src.capitalize())
        if values:
            params[dst] = values[0]
    return "https://mail.google.com/mail/?" + urlencode(params)


def gmail_thread_url(thread_id: str) -> str:
    return f"https://mail.google.com/mail/u/0/#all/{thread_id}"
