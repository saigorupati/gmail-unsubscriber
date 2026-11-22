from unittest.mock import MagicMock, patch

import pytest
from google.auth.exceptions import RefreshError

from unsubscriber.auth import AuthRequired, load_credentials
from unsubscriber.config import SCOPES
from unsubscriber.gmail_client import meta_from_message


@pytest.fixture
def paths(tmp_path):
    creds = tmp_path / "credentials.json"
    creds.write_text("{}")
    return creds, tmp_path / "token.json"


def test_missing_credentials_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_credentials(tmp_path / "credentials.json", tmp_path / "token.json")


def test_missing_token_requires_sign_in(paths):
    with pytest.raises(AuthRequired):
        load_credentials(*paths)


def test_valid_token_returned(paths):
    creds, tok = paths
    tok.write_text("{}")
    fake = MagicMock(valid=True, scopes=SCOPES)
    with patch("unsubscriber.auth.Credentials.from_authorized_user_file", return_value=fake):
        assert load_credentials(creds, tok) is fake


def test_revoked_token_is_deleted_and_requires_sign_in(paths):
    creds, tok = paths
    tok.write_text("{}")
    fake = MagicMock(valid=False, expired=True, refresh_token="r", scopes=SCOPES)
    fake.refresh.side_effect = RefreshError("invalid_grant")
    with patch("unsubscriber.auth.Credentials.from_authorized_user_file", return_value=fake):
        with pytest.raises(AuthRequired):
            load_credentials(creds, tok)
    assert not tok.exists()


def test_refreshed_token_is_saved(paths):
    creds, tok = paths
    tok.write_text("{}")
    fake = MagicMock(valid=False, expired=True, refresh_token="r", scopes=SCOPES)
    fake.to_json.return_value = '{"refreshed": true}'
    with patch("unsubscriber.auth.Credentials.from_authorized_user_file", return_value=fake):
        assert load_credentials(creds, tok) is fake
    assert tok.read_text() == '{"refreshed": true}'


def test_wrong_scope_requires_sign_in(paths):
    creds, tok = paths
    tok.write_text("{}")
    fake = MagicMock(valid=True, scopes=["https://mail.google.com/"])
    with patch("unsubscriber.auth.Credentials.from_authorized_user_file", return_value=fake):
        with pytest.raises(AuthRequired):
            load_credentials(creds, tok)


def test_meta_from_message_headers_case_insensitive():
    msg = {
        "id": "m1",
        "threadId": "t1",
        "internalDate": "1763812800123",
        "payload": {
            "headers": [
                {"name": "FROM", "value": "A <a@a.com>"},
                {"name": "list-unsubscribe", "value": "<https://a.com/u>"},
            ]
        },
    }
    m = meta_from_message(msg)
    assert (m.id, m.thread_id, m.from_header, m.date) == ("m1", "t1", "A <a@a.com>", 1763812800)
    assert m.list_unsubscribe == "<https://a.com/u>" and m.list_unsubscribe_post is None
