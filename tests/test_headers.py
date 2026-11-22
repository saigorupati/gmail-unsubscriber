from urllib.parse import parse_qs, urlparse

from unsubscriber.headers import (
    choose_method,
    domain_of,
    gmail_compose_url,
    gmail_thread_url,
    parse_from,
    parse_list_unsubscribe,
)


def test_parse_from_plain():
    assert parse_from('"Promo Store" <News@PromoStore.com>') == ("Promo Store", "news@promostore.com")


def test_parse_from_encoded_name():
    assert parse_from("=?UTF-8?B?Q2Fmw6k=?= <hi@cafe.com>") == ("Café", "hi@cafe.com")


def test_parse_from_bare_address_uses_address_as_name():
    assert parse_from("deals@x.com") == ("deals@x.com", "deals@x.com")


def test_parse_from_unusable():
    assert parse_from("") is None
    assert parse_from(None) is None
    assert parse_from("Undisclosed recipients") is None


def test_domain_of():
    assert domain_of("news@promo.com") == "promo.com"


def test_list_unsub_one_click():
    o = parse_list_unsubscribe("<https://x.com/u?a=1>, <mailto:u@x.com>", "List-Unsubscribe=One-Click")
    assert o.https_urls == ["https://x.com/u?a=1"] and o.mailto == "mailto:u@x.com" and o.one_click
    assert choose_method(o) == "one_click"


def test_list_unsub_folded_header():
    o = parse_list_unsubscribe("<mailto:u@x.com>,\r\n\t<https://x.com/u>", None)
    assert o.https_urls == ["https://x.com/u"] and not o.one_click
    assert choose_method(o) == "page"


def test_http_url_is_never_one_click():
    o = parse_list_unsubscribe("<http://x.com/u>", "List-Unsubscribe=One-Click")
    assert o.http_urls == ["http://x.com/u"]
    assert not o.one_click and choose_method(o) == "page"


def test_one_click_header_case_and_whitespace():
    o = parse_list_unsubscribe("<https://x.com/u>", "  list-unsubscribe=one-click ")
    assert o.one_click


def test_mailto_only_and_none():
    assert choose_method(parse_list_unsubscribe("<mailto:u@x.com>", None)) == "email"
    assert choose_method(parse_list_unsubscribe(None, None)) == "none"
    assert choose_method(parse_list_unsubscribe("garbage", None)) == "none"


def test_compose_url_carries_subject_and_body():
    url = gmail_compose_url("mailto:u@x.com?subject=Unsub%20me&body=bye")
    q = parse_qs(urlparse(url).query)
    assert url.startswith("https://mail.google.com/mail/?")
    assert q["to"] == ["u@x.com"] and q["su"] == ["Unsub me"] and q["body"] == ["bye"] and q["view"] == ["cm"]


def test_compose_url_without_subject():
    q = parse_qs(urlparse(gmail_compose_url("mailto:u@x.com")).query)
    assert q == {"view": ["cm"], "to": ["u@x.com"]}


def test_thread_url():
    assert gmail_thread_url("abc") == "https://mail.google.com/mail/u/0/#all/abc"


def test_parse_from_encoded_name_with_comma():
    assert parse_from("=?UTF-8?B?RG9lLCBKb2hu?= <a@b.com>") == ("Doe, John", "a@b.com")


def test_parse_from_encoded_name_with_angle_brackets():
    assert parse_from("=?UTF-8?Q?Shop_=3Cdeals=3E?= <a@b.com>") == ("Shop <deals>", "a@b.com")
