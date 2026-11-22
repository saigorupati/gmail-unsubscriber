"""Start the local app: `python -m unsubscriber`."""

import socket
import threading
import webbrowser

from .auth import load_credentials
from .config import CREDENTIALS_PATH, DB_PATH, TOKEN_PATH
from .gmail_client import RealGmailClient
from .web import create_app


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    app = create_app(
        db_path=DB_PATH,
        credentials_path=CREDENTIALS_PATH,
        token_path=TOKEN_PATH,
        client_factory=lambda: RealGmailClient(load_credentials(CREDENTIALS_PATH, TOKEN_PATH)),
    )
    port = _free_port()
    url = f"http://127.0.0.1:{port}/"
    print(f"Unsubscriber running at {url}  (press Ctrl+C to stop)")
    threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    app.run(host="127.0.0.1", port=port, threaded=True)


if __name__ == "__main__":
    main()
