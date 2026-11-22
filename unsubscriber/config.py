from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CREDENTIALS_PATH = ROOT / "credentials.json"
TOKEN_PATH = ROOT / "token.json"
DB_PATH = ROOT / "unsubscriber.db"

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

RANK_DAYS = 30
REVIEW_WINDOW_DAYS = 90
GRACE_DAYS = 10
ONE_CLICK_TIMEOUT = 10
