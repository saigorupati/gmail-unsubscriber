"""Google OAuth: load/refresh the saved token, or run the browser sign-in."""

from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from .config import SCOPES


class AuthRequired(Exception):
    """The owner needs to sign in with Google (again)."""


def load_credentials(credentials_path: Path, token_path: Path) -> Credentials:
    if not Path(credentials_path).exists():
        raise FileNotFoundError(str(credentials_path))
    token_path = Path(token_path)
    if not token_path.exists():
        raise AuthRequired("No saved sign-in")
    try:
        creds = Credentials.from_authorized_user_file(str(token_path))
    except (ValueError, KeyError) as e:
        token_path.unlink(missing_ok=True)
        raise AuthRequired(f"Saved sign-in is unreadable: {e}") from e
    if set(creds.scopes or []) != set(SCOPES):
        raise AuthRequired("Saved sign-in has the wrong permissions")
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as e:
            token_path.unlink(missing_ok=True)
            raise AuthRequired("Google sign-in expired") from e
        token_path.write_text(creds.to_json())
        return creds
    raise AuthRequired("Saved sign-in is not usable")


def sign_in(credentials_path: Path, token_path: Path) -> Credentials:
    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    Path(token_path).write_text(creds.to_json())
    return creds
