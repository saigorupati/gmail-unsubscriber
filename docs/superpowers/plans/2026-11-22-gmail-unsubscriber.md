# Gmail Unsubscriber Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local Python app the owner runs monthly that scans their Gmail (read-only), ranks mailing-list senders by recent volume, and unsubscribes from the ones they tick, remembering every decision.

**Architecture:** A Flask server bound to `127.0.0.1` serves one page. A scanner pulls message metadata (never bodies) through a thin Gmail client wrapper into SQLite. Review rules (to-review, still-sending, skipping) are pure queries over that database. Unsubscribing runs RFC 8058 one-click POSTs on the server, and anything manual becomes a link the owner clicks.

**Tech Stack:** Python 3.13, Flask, google-api-python-client, google-auth-oauthlib, requests, sqlite3 (stdlib), pytest. Vanilla JS, no build step.

**Spec:** `docs/superpowers/specs/2026-11-21-gmail-unsubscriber-design.md`

## Global Constraints

- Gmail OAuth scope is exactly `https://www.googleapis.com/auth/gmail.readonly`. No other Google scope anywhere.
- No mail body or subject is fetched or stored. Fetch with `format="metadata"` and `metadataHeaders=["From","Date","List-Unsubscribe","List-Unsubscribe-Post"]`.
- The server binds to `127.0.0.1` only.
- `credentials.json`, `token.json`, `unsubscriber.db` and `.venv/` live in the project root and are gitignored.
- The only automatic outbound request to a sender is the one-click `POST` (body `List-Unsubscribe=One-Click`, no cookies, 10 s timeout, 2xx = success). Never send an automatic `GET` to an unsubscribe URL.
- Never unsubscribe a sender the owner didn't select, or one whose status is `kept`.
- Windows/PowerShell. Commands are run from the project root with `.venv\Scripts\python`.
- All time logic takes an injected `now: datetime` (UTC, tz-aware) so tests are deterministic. Store times as integer epoch seconds.
- Numbers from the spec: still-sending grace **10 days**, to-review window **90 days**, ranking window **30 days**.

## Review Focus

1. **Messy `List-Unsubscribe` headers** (folded across lines, several URIs, `http:` rather than `https:`, mailto with percent-encoded subject) must parse without crashing. `http:` is never one-click; it's treated as "Opens page". Tests: Task 1.
2. **Encoded or broken `From` headers** (`=?UTF-8?B?...?=` names, no angle brackets, empty, no `@`): decode the name, lowercase the address, and skip messages with no usable address without aborting the scan. Tests: Tasks 1 and 5.
3. **A one-click endpoint that times out, returns 4xx/5xx, or refuses the connection** must count as failed with a readable reason. The sender stays in To review and is offered manual methods. Tests: Task 6.
4. **A Google token that expires or is revoked between runs or mid-scan** must lead to the sign-in screen or a visible scan error, never a crash or a hung progress bar. Tests: Tasks 4 and 7.
5. **Another website in the owner's browser POSTing to the local server** (e.g. to `/api/unsubscribe`) must be rejected: every POST requires a JSON body and an `Origin` that matches the server's own host, otherwise 403. Tests: Task 7.

---

## File Structure

```
unsubscriber/
  __init__.py
  __main__.py        # entry point: pick port, open browser, run server
  config.py          # paths, scope, constants
  headers.py         # pure parsing: From, List-Unsubscribe, method choice, Gmail URLs
  models.py          # dataclasses shared across modules
  db.py              # schema + data access
  review.py          # review rules: to_review, still_sending, skip/revive, status changes
  auth.py            # OAuth load/refresh/sign-in
  gmail_client.py    # GmailClient protocol + real implementation
  scanner.py         # full/incremental scan with resume
  unsubscribe.py     # one-click POST + manual action building
  web.py             # Flask app factory and routes
  templates/index.html, templates/setup.html, templates/signin.html
  static/app.js, static/style.css
tests/
  conftest.py        # fixtures: tmp db, fixed `now`, FakeGmailClient, make_meta()
  test_headers.py  test_db.py  test_review.py  test_auth.py
  test_scanner.py  test_unsubscribe.py  test_web.py
requirements.txt  .gitignore  run.ps1  README.md  pytest.ini
```

---

### Task 1: Project setup and header parsing

**Files:**
- Create: `.gitignore`, `requirements.txt`, `pytest.ini`, `unsubscriber/__init__.py`, `unsubscriber/config.py`, `unsubscriber/models.py`, `unsubscriber/headers.py`
- Test: `tests/test_headers.py`

**Interfaces:**
- Produces in `config.py`: `ROOT: Path` (project root), `CREDENTIALS_PATH = ROOT/"credentials.json"`, `TOKEN_PATH = ROOT/"token.json"`, `DB_PATH = ROOT/"unsubscriber.db"`, `SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]`, `RANK_DAYS = 30`, `REVIEW_WINDOW_DAYS = 90`, `GRACE_DAYS = 10`, `ONE_CLICK_TIMEOUT = 10`.
- Produces in `models.py`:
  - `@dataclass(frozen=True) MessageMeta: id: str; thread_id: str; from_header: str; date: int` (epoch seconds, taken from Gmail `internalDate // 1000`, which is more reliable than the Date header) `; list_unsubscribe: str | None; list_unsubscribe_post: str | None`
  - `@dataclass(frozen=True) UnsubOptions: https_urls: list[str]; http_urls: list[str]; mailto: str | None; one_click: bool`
  - `Method = Literal["one_click", "page", "email", "none"]`
- Produces in `headers.py`:
  - `parse_from(value: str | None) -> tuple[str, str] | None`: returns `(display_name, address_lowercased)`, or `None` if there's no address with `@`. Decode RFC 2047 names with `email.header` / `email.utils.parseaddr`. Fall back to the address as the name.
  - `domain_of(address: str) -> str`
  - `parse_list_unsubscribe(value: str | None, post: str | None) -> UnsubOptions`: unfold whitespace, extract each `<...>` URI, sort by scheme. `one_click` is true only if `post` (case-insensitive, stripped) equals `list-unsubscribe=one-click` **and** there's at least one https URL.
  - `choose_method(opts: UnsubOptions) -> Method`: `one_click` if `opts.one_click`, else `page` if any https or http URL, else `email` if mailto, else `none`.
  - `gmail_compose_url(mailto: str) -> str`: `https://mail.google.com/mail/?view=cm&to=<addr>&su=<subject>&body=<body>`, with subject/body taken from the mailto query (percent-decoded, then re-encoded with `urllib.parse.urlencode`). Omit `su`/`body` when absent.
  - `gmail_thread_url(thread_id: str) -> str`: `https://mail.google.com/mail/u/0/#all/{thread_id}`

- [ ] **Step 1: Set up the project**

```powershell
git init
py -3.13 -m venv .venv
```
`requirements.txt`: `flask`, `google-api-python-client`, `google-auth-oauthlib`, `requests`, `pytest`. `.gitignore`: `.venv/`, `__pycache__/`, `credentials.json`, `token.json`, `unsubscriber.db`, `*.db-journal`. `pytest.ini`: `[pytest]` with `testpaths = tests`.
Run: `.venv\Scripts\python -m pip install -r requirements.txt`. Expected: installs without error.

- [ ] **Step 2: Write the failing tests** in `tests/test_headers.py`

```python
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
    assert not o.one_click and choose_method(o) == "page"

def test_mailto_only_and_none():
    assert choose_method(parse_list_unsubscribe("<mailto:u@x.com>", None)) == "email"
    assert choose_method(parse_list_unsubscribe(None, None)) == "none"
    assert choose_method(parse_list_unsubscribe("garbage", None)) == "none"

def test_compose_url_carries_subject_and_body():
    url = gmail_compose_url("mailto:u@x.com?subject=Unsub%20me&body=bye")
    q = parse_qs(urlparse(url).query)
    assert q["to"] == ["u@x.com"] and q["su"] == ["Unsub me"] and q["body"] == ["bye"] and q["view"] == ["cm"]

def test_thread_url():
    assert gmail_thread_url("abc") == "https://mail.google.com/mail/u/0/#all/abc"
```

- [ ] **Step 3: Run the tests to make sure they fail**
Run: `.venv\Scripts\python -m pytest tests/test_headers.py -v`. Expected: FAIL (ImportError).

- [ ] **Step 4: Implement `config.py`, `models.py`, `headers.py`** with the interfaces above.

- [ ] **Step 5: Run the tests to make sure they pass**
Run: `.venv\Scripts\python -m pytest tests/test_headers.py -v`. Expected: all PASS.

- [ ] **Step 6: Commit**
`git add -A; git commit -m "feat: project setup and header parsing"`

---

### Task 2: Database layer

**Files:**
- Create: `unsubscriber/db.py`, `tests/conftest.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `MessageMeta`, `parse_from`, `domain_of`.
- Produces in `models.py`: `@dataclass Sender: address: str; display_name: str; domain: str; is_list: bool; latest_date: int; latest_thread_id: str; latest_list_unsubscribe: str | None; latest_list_unsubscribe_post: str | None; status: str; status_at: int | None; method: str | None; last_error: str | None; skip_baseline: int | None; acknowledged_at: int | None`
- Produces in `db.py`:
  - `connect(path: Path | str) -> sqlite3.Connection`: creates tables if missing, `row_factory = sqlite3.Row`, `check_same_thread=False`. Tables:
    - `senders` (columns as in `Sender`; `status` defaults to `'new'`)
    - `messages(id TEXT PRIMARY KEY, address TEXT NOT NULL, date INTEGER NOT NULL)` with an index on `(address, date)`
    - `scan_state(key TEXT PRIMARY KEY, value TEXT)`
  - `record_message(conn, meta: MessageMeta) -> bool`: returns False and writes nothing if `parse_from` gives None or the id already exists. Otherwise inserts the message and upserts the sender: `is_list = is_list OR meta.list_unsubscribe is not None`; display name, thread and unsubscribe headers are replaced only when `meta.date >= latest_date`.
  - `known_ids(conn, ids: list[str]) -> set[str]`
  - `get_sender(conn, address: str) -> Sender | None`
  - `count_messages(conn, address: str, since: int = 0) -> int`: messages with `date > since`. (Counts are computed, not stored; this refines the spec's "store counts".)
  - `update_sender(conn, address: str, **fields) -> None`: sets only the named `Sender` columns. Raises `ValueError` on an unknown column.
  - `get_state(conn, key: str) -> str | None`, `set_state(conn, key: str, value: str) -> None`
  - All writers commit.
- Produces in `tests/conftest.py`: fixture `conn` (a temp-file db via `tmp_path`), fixture `now` = `datetime(2026, 11, 22, 12, tzinfo=UTC)`, helper `ts(now, days_ago: float) -> int`, helper `make_meta(id, frm="Promo <news@promo.com>", days_ago=1, now=..., unsub="<https://promo.com/u>", post="List-Unsubscribe=One-Click", thread="t-"+id) -> MessageMeta`.

- [ ] **Step 1: Write the failing tests** in `tests/test_db.py`

```python
def test_record_and_count(conn, now):
    assert record_message(conn, make_meta("1", days_ago=40, now=now))
    assert record_message(conn, make_meta("2", days_ago=5, now=now))
    assert count_messages(conn, "news@promo.com") == 2
    assert count_messages(conn, "news@promo.com", since=ts(now, 30)) == 1

def test_duplicate_ignored(conn, now):
    record_message(conn, make_meta("1", now=now))
    assert record_message(conn, make_meta("1", now=now)) is False
    assert count_messages(conn, "news@promo.com") == 1

def test_bad_from_skipped(conn, now):
    assert record_message(conn, make_meta("1", frm="", now=now)) is False
    assert count_messages(conn, "") == 0

def test_latest_headers_win_and_is_list_sticks(conn, now):
    record_message(conn, make_meta("new", days_ago=1, now=now, unsub=None, post=None, thread="tNew"))
    record_message(conn, make_meta("old", days_ago=9, now=now, unsub="<mailto:u@promo.com>", post=None))
    s = get_sender(conn, "news@promo.com")
    assert s.is_list is True                      # an older message had the header
    assert s.latest_thread_id == "tNew" and s.latest_list_unsubscribe is None
    assert s.status == "new" and s.domain == "promo.com"

def test_update_sender_rejects_unknown_column(conn, now):
    record_message(conn, make_meta("1", now=now))
    with pytest.raises(ValueError):
        update_sender(conn, "news@promo.com", bogus=1)

def test_state_roundtrip(conn):
    assert get_state(conn, "last_completed") is None
    set_state(conn, "last_completed", "123")
    assert get_state(conn, "last_completed") == "123"
```

- [ ] **Step 2: Run the tests to make sure they fail.** `.venv\Scripts\python -m pytest tests/test_db.py -v` → FAIL.
- [ ] **Step 3: Implement `db.py`, `conftest.py` and the `Sender` dataclass** as specified.
- [ ] **Step 4: Run the tests to make sure they pass.** Same command → PASS.
- [ ] **Step 5: Commit.** `git commit -am "feat: sqlite data layer"` (after `git add -A`).

---

### Task 3: Review rules

**Files:**
- Create: `unsubscriber/review.py`
- Test: `tests/test_review.py`

**Interfaces:**
- Consumes: `db.*`, `headers.parse_list_unsubscribe`, `headers.choose_method`, config constants.
- Produces in `models.py`: `@dataclass SenderRow: sender: Sender; count_30d: int; count_total: int; method: Method` and `@dataclass StillSendingRow: sender: Sender; count_since: int`.
- Produces in `review.py` (each `now: datetime`):
  - `to_review(conn, now) -> list[SenderRow]`: `is_list` and `status == 'new'` and `latest_date >= now - 90d`. Sorted by `count_30d` desc, then `count_total` desc, then address.
  - `still_sending(conn, now) -> list[StillSendingRow]`: `status == 'unsubscribed'`. Let `cutoff = max(status_at + 10d, acknowledged_at or 0)`, then `count_since = count_messages(since=cutoff)`. Include rows where `count_since >= 1`. Sorted by `count_since` desc.
  - `decided(conn, status: str, now) -> list[SenderRow]` for `kept` / `unsubscribed` / `skipped`, is_list only, sorted by display name.
  - `review_addresses(conn, now) -> set[str]`: addresses currently in `to_review`.
  - `skip_undecided(conn, addresses: set[str], now) -> None`: for each address still `status == 'new'`, set `status='skipped'`, `status_at=now`, `skip_baseline=count_30d`.
  - `revive_skipped(conn, now) -> None`: any `skipped` sender with `count_30d > skip_baseline` goes back to `status='new'` with the baseline cleared.
  - `keep(conn, addresses: list[str], now)`, `hide(conn, address, now)` (sets `acknowledged_at=now`), `undo(conn, address, now)` (back to `new`, clearing `method`, `last_error`, `skip_baseline`, `acknowledged_at`).
  - `mark_unsubscribed(conn, address: str, method: Method, now)` (clears `last_error` and `acknowledged_at`). Raises `ValueError` if the sender is `kept`.

- [ ] **Step 1: Write the failing tests** in `tests/test_review.py`. Seed data with `record_message` and `make_meta`.

```python
def test_to_review_ranks_by_30d_and_hides_quiet_and_non_lists(conn, now):
    for i in range(5): record_message(conn, make_meta(f"a{i}", frm="A <a@a.com>", days_ago=60, now=now))   # 5 total, 0 recent
    for i in range(2): record_message(conn, make_meta(f"b{i}", frm="B <b@b.com>", days_ago=3, now=now))    # 2 recent
    record_message(conn, make_meta("c", frm="C <c@c.com>", days_ago=100, now=now))                         # quiet >90d
    record_message(conn, make_meta("p", frm="Mom <mom@home.com>", days_ago=1, now=now, unsub=None, post=None))  # not a list
    rows = to_review(conn, now)
    assert [r.sender.address for r in rows] == ["b@b.com", "a@a.com"]
    assert (rows[0].count_30d, rows[0].count_total, rows[0].method) == (2, 2, "one_click")

def test_still_sending_respects_grace_and_hide(conn, now):
    record_message(conn, make_meta("1", days_ago=30, now=now))
    mark_unsubscribed(conn, "news@promo.com", "one_click", now - timedelta(days=20))
    record_message(conn, make_meta("2", days_ago=15, now=now))       # within 10-day grace → ignored
    assert still_sending(conn, now) == []
    record_message(conn, make_meta("3", days_ago=5, now=now))        # after grace
    assert [r.count_since for r in still_sending(conn, now)] == [1]
    hide(conn, "news@promo.com", now)
    assert still_sending(conn, now) == []
    record_message(conn, make_meta("4", days_ago=-0.01, now=now))    # sends again after hide
    assert len(still_sending(conn, now + timedelta(days=1))) == 1

def test_skip_then_revive_only_when_volume_rises(conn, now):
    record_message(conn, make_meta("1", days_ago=2, now=now))
    skip_undecided(conn, review_addresses(conn, now), now)
    s = get_sender(conn, "news@promo.com"); assert (s.status, s.skip_baseline) == ("skipped", 1)
    revive_skipped(conn, now); assert get_sender(conn, "news@promo.com").status == "skipped"
    record_message(conn, make_meta("2", days_ago=1, now=now))
    revive_skipped(conn, now); assert get_sender(conn, "news@promo.com").status == "new"

def test_skip_ignores_decided_senders(conn, now):
    record_message(conn, make_meta("1", now=now))
    addrs = review_addresses(conn, now); keep(conn, ["news@promo.com"], now)
    skip_undecided(conn, addrs, now)
    assert get_sender(conn, "news@promo.com").status == "kept"

def test_cannot_unsubscribe_kept(conn, now):
    record_message(conn, make_meta("1", now=now)); keep(conn, ["news@promo.com"], now)
    with pytest.raises(ValueError): mark_unsubscribed(conn, "news@promo.com", "one_click", now)

def test_undo_resets(conn, now):
    record_message(conn, make_meta("1", now=now)); keep(conn, ["news@promo.com"], now)
    undo(conn, "news@promo.com", now)
    assert [r.sender.address for r in to_review(conn, now)] == ["news@promo.com"]
```

- [ ] **Step 2: Run the tests to make sure they fail.** `.venv\Scripts\python -m pytest tests/test_review.py -v` → FAIL.
- [ ] **Step 3: Implement `review.py`** as specified.
- [ ] **Step 4: Run the tests to make sure they pass.** → PASS.
- [ ] **Step 5: Commit.** `git add -A; git commit -m "feat: review rules (ranking, still-sending, skipping)"`

---

### Task 4: Auth and Gmail client

**Files:**
- Create: `unsubscriber/auth.py`, `unsubscriber/gmail_client.py`
- Modify: `tests/conftest.py` (add `FakeGmailClient`)
- Test: `tests/test_auth.py`

**Interfaces:**
- Produces in `auth.py`:
  - `class AuthRequired(Exception)`
  - `load_credentials(credentials_path: Path, token_path: Path) -> Credentials`: raises `FileNotFoundError` if there's no credentials file. Raises `AuthRequired` if there's no token, or if refreshing raises `google.auth.exceptions.RefreshError` (in which case it deletes `token.json` first). Saves a refreshed token. Verifies the token's scopes equal `SCOPES`, else `AuthRequired`.
  - `sign_in(credentials_path: Path, token_path: Path) -> Credentials`: `InstalledAppFlow.from_client_secrets_file(..., SCOPES).run_local_server(port=0, open_browser=True)`, then writes `token.json`.
- Produces in `gmail_client.py`:
  - `class GmailClient(Protocol)`: `list_message_ids(query: str) -> Iterator[str]`; `get_metadata(ids: list[str]) -> list[MessageMeta]`.
  - `class RealGmailClient(creds)`: `list_message_ids` pages through `users().messages().list(userId="me", q=query, maxResults=500, includeSpamTrash=False)`. `get_metadata` batches `messages().get(..., format="metadata", metadataHeaders=[...])`, at most 50 per HTTP batch. It retries 429/5xx per message with exponential backoff (1 s, 2 s, 4 s, 8 s, 16 s) and then raises. Header lookup is case-insensitive. Missing headers → `None`. A token failure mid-call is re-raised as `AuthRequired`. (Batching and backoff are the builder's call per the spec. No unit test against live Google.)
- Produces in `tests/conftest.py`: `class FakeGmailClient(messages: list[MessageMeta], fail_after: int | None = None)`. `list_message_ids(query)` returns ids filtered by an `after:<epoch>` term if present and records `self.queries`. `get_metadata` raises `RuntimeError("boom")` once `fail_after` metas have been returned.

- [ ] **Step 1: Write the failing tests** in `tests/test_auth.py`, using `unittest.mock.patch` on `google.oauth2.credentials.Credentials.from_authorized_user_file`.

```python
def test_missing_credentials_file(tmp_path):
    with pytest.raises(FileNotFoundError): load_credentials(tmp_path/"credentials.json", tmp_path/"token.json")

def test_missing_token_requires_sign_in(tmp_path):
    (tmp_path/"credentials.json").write_text("{}")
    with pytest.raises(AuthRequired): load_credentials(tmp_path/"credentials.json", tmp_path/"token.json")

def test_revoked_token_is_deleted_and_requires_sign_in(tmp_path):
    (tmp_path/"credentials.json").write_text("{}"); tok = tmp_path/"token.json"; tok.write_text("{}")
    fake = MagicMock(valid=False, expired=True, refresh_token="r", scopes=SCOPES)
    fake.refresh.side_effect = RefreshError("invalid_grant")
    with patch("unsubscriber.auth.Credentials.from_authorized_user_file", return_value=fake):
        with pytest.raises(AuthRequired): load_credentials(tmp_path/"credentials.json", tok)
    assert not tok.exists()

def test_wrong_scope_requires_sign_in(tmp_path):
    ...  # token with scopes ["https://mail.google.com/"] → AuthRequired
```

- [ ] **Step 2: Run the tests to make sure they fail.** `.venv\Scripts\python -m pytest tests/test_auth.py -v` → FAIL.
- [ ] **Step 3: Implement `auth.py`, `gmail_client.py`, and `FakeGmailClient`.**
- [ ] **Step 4: Run the tests to make sure they pass.** → PASS.
- [ ] **Step 5: Commit.** `git add -A; git commit -m "feat: oauth handling and gmail client wrapper"`

---

### Task 5: Scanner

**Files:**
- Create: `unsubscriber/scanner.py`
- Test: `tests/test_scanner.py`

**Interfaces:**
- Consumes: `GmailClient`, `db.record_message`, `db.known_ids`, `db.get_state/set_state`, `review.review_addresses/skip_undecided/revive_skipped`.
- Produces:
  - `BASE_QUERY = "-in:sent -in:draft"` (spam and trash are already excluded by `includeSpamTrash=False`)
  - `@dataclass ScanProgress: running: bool = False; done: int = 0; total: int = 0; new_messages: int = 0; error: str | None = None; auth_required: bool = False`
  - `run_scan(conn, client: GmailClient, now: datetime, progress: ScanProgress) -> None`:
    1. `shown = review_addresses(conn, now)`.
    2. `query = BASE_QUERY`. If state `last_completed` exists, append `f" after:{last_completed - 86400}"` (one day of overlap; already-known ids are dropped).
    3. List all ids, drop `known_ids`, set `progress.total`.
    4. Fetch metadata in chunks of 100, `record_message` each, and update `done`/`new_messages` after every chunk. Since every chunk is committed, a crash resumes naturally: the next run re-lists and skips known ids. While no scan has completed yet, `last_completed` stays unset, so the resume re-lists full history.
    5. Only on success: `skip_undecided(conn, shown, now)`, `revive_skipped(conn, now)`, `set_state("last_completed", str(int(now.timestamp())))`, and set state `last_new_messages`.
    6. On `AuthRequired`: set `progress.error` and `progress.auth_required = True`. On any other exception: set `progress.error = str(e)`. In every case finish with `progress.running = False`, and never raise.

- [ ] **Step 1: Write the failing tests** in `tests/test_scanner.py`

```python
def test_first_scan_records_everything(conn, now):
    msgs = [make_meta(str(i), days_ago=i, now=now) for i in range(250)]
    p = ScanProgress(); run_scan(conn, FakeGmailClient(msgs), now, p)
    assert (p.done, p.total, p.new_messages, p.error, p.running) == (250, 250, 250, None, False)
    assert get_state(conn, "last_completed") == str(int(now.timestamp()))

def test_bad_from_does_not_abort(conn, now):
    msgs = [make_meta("1", frm="", now=now), make_meta("2", now=now)]
    p = ScanProgress(); run_scan(conn, FakeGmailClient(msgs), now, p)
    assert p.error is None and count_messages(conn, "news@promo.com") == 1

def test_crash_then_resume_skips_known(conn, now):
    msgs = [make_meta(str(i), now=now) for i in range(250)]
    p = ScanProgress(); run_scan(conn, FakeGmailClient(msgs, fail_after=100), now, p)
    assert p.error == "boom" and get_state(conn, "last_completed") is None
    client = FakeGmailClient(msgs); p2 = ScanProgress(); run_scan(conn, client, now, p2)
    assert p2.total == 150 and count_messages(conn, "news@promo.com") == 250

def test_incremental_uses_after_query(conn, now):
    run_scan(conn, FakeGmailClient([make_meta("1", now=now)]), now, ScanProgress())
    client = FakeGmailClient([make_meta("1", now=now)]); later = now + timedelta(days=30)
    run_scan(conn, client, later, ScanProgress())
    assert client.queries[-1] == f"-in:sent -in:draft after:{int(now.timestamp()) - 86400}"

def test_undecided_become_skipped_after_next_scan(conn, now):
    run_scan(conn, FakeGmailClient([make_meta("1", now=now)]), now, ScanProgress())
    assert get_sender(conn, "news@promo.com").status == "new"   # newly found: not skipped yet
    run_scan(conn, FakeGmailClient([make_meta("1", now=now)]), now + timedelta(days=30), ScanProgress())
    assert get_sender(conn, "news@promo.com").status == "skipped"

def test_auth_failure_reported(conn, now):
    class Expired(FakeGmailClient):
        def list_message_ids(self, q): raise AuthRequired()
    p = ScanProgress(); run_scan(conn, Expired([]), now, p)
    assert p.auth_required and not p.running
```

- [ ] **Step 2: Run the tests to make sure they fail.** `.venv\Scripts\python -m pytest tests/test_scanner.py -v` → FAIL.
- [ ] **Step 3: Implement `scanner.py`.**
- [ ] **Step 4: Run the tests to make sure they pass.** → PASS.
- [ ] **Step 5: Commit.** `git add -A; git commit -m "feat: full and incremental scanning with resume"`

---

### Task 6: Unsubscribe actions

**Files:**
- Create: `unsubscriber/unsubscribe.py`
- Test: `tests/test_unsubscribe.py`

**Interfaces:**
- Consumes: `headers.*`, `review.mark_unsubscribed`, `db.get_sender/update_sender`.
- Produces:
  - `one_click_post(url: str, timeout: float = ONE_CLICK_TIMEOUT) -> tuple[bool, str | None]`: a fresh `requests.post(url, data="List-Unsubscribe=One-Click", headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=timeout, allow_redirects=True)` with no session and no cookies. Returns `(True, None)` on 2xx, `(False, "HTTP 404")` on other statuses, and `(False, "Timed out")` / `(False, "Could not connect")` on exceptions.
  - `@dataclass ActionResult: address: str; outcome: Literal["done", "failed", "manual", "skipped"]; method: Method; error: str | None = None; action_url: str | None = None`
  - `manual_action(sender: Sender) -> tuple[Method, str]`: page → the first https URL (or http if that's all there is); email → `gmail_compose_url(mailto)`; none → `gmail_thread_url(latest_thread_id)`.
  - `unsubscribe(conn, addresses: list[str], now, post=one_click_post, prefer_manual: bool = False) -> list[ActionResult]`: per address, a missing or `kept` sender → `skipped`. If the method is `one_click` and not `prefer_manual`, POST: success → `mark_unsubscribed(method="one_click")` and `done`; failure → `update_sender(last_error=...)`, `failed` with `action_url` from `manual_action` (only when the headers have an https/http URL or mailto). Otherwise → `manual` with `action_url`. Nothing is marked unsubscribed yet.
  - `confirm_manual(conn, address: str, method: Method, now) -> None` → `mark_unsubscribed`.
  - `retry(conn, address: str, now, post=one_click_post) -> ActionResult`: `unsubscribe([address], prefer_manual=(sender.method == "one_click"))`.

- [ ] **Step 1: Write the failing tests** in `tests/test_unsubscribe.py`. Use a local stub HTTP server in a thread (`http.server` on port 0) that records the method, body and Cookie header, and returns a status set per test.

```python
def test_one_click_post_success(stub):          # stub returns 200
    ok, err = one_click_post(stub.url("/u"))
    assert ok and err is None
    assert stub.last == ("POST", "List-Unsubscribe=One-Click", None)   # method, body, cookie

def test_one_click_post_http_error(stub):       # stub returns 404
    assert one_click_post(stub.url("/u")) == (False, "HTTP 404")

def test_one_click_post_timeout(stub):          # stub sleeps 2s
    assert one_click_post(stub.url("/slow"), timeout=0.5) == (False, "Timed out")

def test_one_click_post_refused():
    assert one_click_post("https://127.0.0.1:1/u", timeout=2) == (False, "Could not connect")

def test_unsubscribe_mixed(conn, now):
    record_message(conn, make_meta("1", frm="A <a@a.com>", now=now))                                       # one-click
    record_message(conn, make_meta("2", frm="B <b@b.com>", now=now, unsub="<mailto:u@b.com?subject=stop>", post=None))
    record_message(conn, make_meta("3", frm="K <k@k.com>", now=now)); keep(conn, ["k@k.com"], now)
    res = {r.address: r for r in unsubscribe(conn, ["a@a.com", "b@b.com", "k@k.com"], now, post=lambda u: (True, None))}
    assert res["a@a.com"].outcome == "done" and get_sender(conn, "a@a.com").status == "unsubscribed"
    assert res["b@b.com"].outcome == "manual" and res["b@b.com"].action_url.startswith("https://mail.google.com/mail/?view=cm")
    assert get_sender(conn, "b@b.com").status == "new"
    assert res["k@k.com"].outcome == "skipped"

def test_failed_one_click_offers_manual_and_stays_new(conn, now):
    record_message(conn, make_meta("1", now=now, unsub="<https://promo.com/u>, <mailto:u@promo.com>"))
    [r] = unsubscribe(conn, ["news@promo.com"], now, post=lambda u: (False, "HTTP 500"))
    assert (r.outcome, r.error, r.action_url) == ("failed", "HTTP 500", "https://promo.com/u")
    s = get_sender(conn, "news@promo.com"); assert (s.status, s.last_error) == ("new", "HTTP 500")

def test_retry_after_one_click_goes_manual(conn, now):
    record_message(conn, make_meta("1", now=now))
    unsubscribe(conn, ["news@promo.com"], now, post=lambda u: (True, None))
    calls = []
    r = retry(conn, "news@promo.com", now, post=lambda u: calls.append(u) or (True, None))
    assert r.outcome == "manual" and calls == []

def test_no_header_opens_thread(conn, now):
    ...  # sender with is_list from an older message but latest has no header → manual, gmail_thread_url
```

- [ ] **Step 2: Run the tests to make sure they fail.** `.venv\Scripts\python -m pytest tests/test_unsubscribe.py -v` → FAIL.
- [ ] **Step 3: Implement `unsubscribe.py`** (plus the `stub` fixture in the test file).
- [ ] **Step 4: Run the tests to make sure they pass.** → PASS.
- [ ] **Step 5: Commit.** `git add -A; git commit -m "feat: one-click and manual unsubscribe actions"`

---

### Task 7: Web app

**Files:**
- Create: `unsubscriber/web.py`, `unsubscriber/templates/{index,setup,signin}.html`, `unsubscriber/static/{app.js,style.css}`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `create_app(db_path, credentials_path, token_path, client_factory: Callable[[], GmailClient], post=one_click_post, now_fn=lambda: datetime.now(UTC)) -> Flask`. `client_factory` raises `FileNotFoundError` / `AuthRequired` the same way `load_credentials` does. In production it is `lambda: RealGmailClient(load_credentials(...))`.
- Routes:
  - `GET /` → `setup.html` if there's no credentials file. → `signin.html` (a "Sign in with Google" button) if `client_factory()` raises `AuthRequired`. → otherwise `index.html`, rendering the still-sending rows, the to-review rows, the three decided lists with counts, the last scan date with its new-message count, and each row's `last_error` if set.
  - `POST /api/signin` → calls `sign_in` (blocking; Google's page opens in the browser), returns `{"ok": true}`.
  - `POST /api/scan` → starts `run_scan` in a background `threading.Thread` with its own `db.connect`. Returns 409 if a scan is already running.
  - `GET /api/scan` → `asdict(progress)`.
  - `POST /api/unsubscribe {addresses}` → `[asdict(ActionResult)]`
  - `POST /api/keep {addresses}`, `POST /api/confirm {address, method}`, `POST /api/retry {address}`, `POST /api/hide {address}`, `POST /api/undo {address}` → `{"ok": true}` / the result.
  - **Every POST:** `request.is_json` must be true and the `Origin` header must equal `request.host_url` minus the trailing slash, otherwise 403. Unknown addresses are ignored, never a 500.
- Page behavior (`app.js`): Scan now polls `GET /api/scan` every 1 s and shows a progress bar `done / total`. When it finishes it reloads; if it errors it shows the message, and on `auth_required` it reloads to the sign-in page. Unsubscribe selected posts the checked rows and replaces each row with its result. Manual results go into a "Finish these" list, where each button opens `action_url` in a new tab (`window.open`, from a click) and then posts `/api/confirm`. Layout follows the spec sketch, with method badges labeled "One-click", "Opens page", "Email draft" and "Open in Gmail".

- [ ] **Step 1: Write the failing tests** in `tests/test_web.py`, using Flask's `test_client` and a `FakeGmailClient` factory. Test-file helpers: `make_app(tmp_path, creds=True, seed=[], client=None, factory=None, post=None)` writes a dummy `credentials.json` when `creds`, records the `seed` metas into the db, and builds the app with `now_fn` fixed. `raises(exc)` returns a factory that raises `exc()`. `wait_until(pred, timeout=5)` polls every 0.05 s.

```python
H = {"Origin": "http://localhost"}

def test_setup_page_without_credentials(tmp_path):
    c = make_app(tmp_path, creds=False).test_client()
    assert b"Google Cloud" in c.get("/").data

def test_signin_page_when_auth_required(tmp_path):
    c = make_app(tmp_path, factory=raises(AuthRequired)).test_client()
    assert b"Sign in with Google" in c.get("/").data

def test_index_lists_review_rows(tmp_path, now):
    app = make_app(tmp_path, seed=[make_meta("1", now=now)]); body = app.test_client().get("/").data
    assert b"news@promo.com" in body and b"One-click" in body

def test_scan_runs_and_reports(tmp_path, now):
    c = make_app(tmp_path, client=FakeGmailClient([make_meta("1", now=now)])).test_client()
    assert c.post("/api/scan", json={}, headers=H).status_code == 200
    wait_until(lambda: not c.get("/api/scan").json["running"])
    assert c.get("/api/scan").json["new_messages"] == 1

def test_unsubscribe_endpoint(tmp_path, now):
    c = make_app(tmp_path, seed=[make_meta("1", now=now)], post=lambda u: (True, None)).test_client()
    r = c.post("/api/unsubscribe", json={"addresses": ["news@promo.com"]}, headers=H)
    assert r.json[0]["outcome"] == "done"

def test_cross_site_post_rejected(tmp_path, now):
    c = make_app(tmp_path, seed=[make_meta("1", now=now)], post=lambda u: (True, None)).test_client()
    assert c.post("/api/unsubscribe", json={"addresses": ["news@promo.com"]}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/unsubscribe", data="addresses=news@promo.com", headers=H).status_code == 403
    assert c.post("/api/unsubscribe", json={"addresses": ["news@promo.com"]}).status_code == 403   # no Origin

def test_scan_auth_failure_surfaces(tmp_path):
    ...  # client whose list_message_ids raises AuthRequired → GET /api/scan shows auth_required true, running false
```

- [ ] **Step 2: Run the tests to make sure they fail.** `.venv\Scripts\python -m pytest tests/test_web.py -v` → FAIL.
- [ ] **Step 3: Implement `web.py`, the templates and the static files.** `setup.html` holds the setup steps from the spec: create a Cloud project, enable the Gmail API, set the OAuth consent screen to External + Testing, add yourself as a test user, create a Desktop OAuth client, and save the JSON as `credentials.json` in the project folder.
- [ ] **Step 4: Run the full suite.** `.venv\Scripts\python -m pytest -v` → all PASS.
- [ ] **Step 5: Commit.** `git add -A; git commit -m "feat: web ui and api"`

---

### Task 8: Entry point, README, and real-world check

**Files:**
- Create: `unsubscriber/__main__.py`, `run.ps1`, `README.md`

**Interfaces:**
- Consumes: `create_app`, `config`, `RealGmailClient`, `load_credentials`.
- Produces: `python -m unsubscriber` picks a free port (bind a socket to `127.0.0.1:0`), opens `http://127.0.0.1:<port>/` with `webbrowser.open` once the server is listening, and runs `app.run(host="127.0.0.1", port=port)`. `run.ps1`: `& "$PSScriptRoot\.venv\Scripts\python.exe" -m unsubscriber`.

- [ ] **Step 1: Write `__main__.py` and `run.ps1`.**
- [ ] **Step 2: Write `README.md`.** It covers install (venv + pip), the Cloud setup steps (the same as `setup.html`), monthly use, the 7-day Testing-mode sign-in note with the "In production (unverified)" alternative and its warning screen, and a statement that the app only has read access.
- [ ] **Step 3: Smoke test without credentials.** Run `.\run.ps1`. Expected: the browser opens on the setup page, and the console shows it listening on `127.0.0.1` only.
- [ ] **Step 4: Real check with the owner (needs the owner's Google sign-in, which they do themselves).** The owner puts `credentials.json` in place and signs in, then runs a scan. Expected: the progress bar advances, the To review list fills with mailing lists only, and no personal contacts appear. Unsubscribe from one sender the owner picks, and confirm it shows as done.
- [ ] **Step 5: Commit.** `git add -A; git commit -m "feat: launcher and readme"`
