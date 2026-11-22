# Gmail Unsubscriber — Design

Date: 2026-11-21
Status: Approved 2026-11-22

## Intent

The owner gets a lot of junk and marketing email in their personal Gmail. They
want a tool they come back to **about once a month**: it scans their mail,
shows which senders send the most, and lets them tick the ones to unsubscribe
from in one go. Over time, each visit should mostly show *new* senders, not
the ones they've already dealt with.

Single user (the owner), single Gmail account, runs on their Windows PC.

## Goals

1. Rank senders by how much mail they send, so the worst offenders are on top.
2. Unsubscribe from many senders at once with as little clicking as possible.
3. Remember decisions: senders marked **keep** or **unsubscribed** don't
   clutter the next month's review.
4. Show senders who are **still sending** after an unsubscribe, so the owner
   knows which ones ignored it.
5. Keep monthly scans fast by only reading mail that arrived since the last scan.

## Non-goals (v1)

- Deleting, archiving, labeling or filtering existing mail. The owner chose to
  **leave existing mail alone**.
- Digging unsubscribe links out of email bodies.
- Multiple Gmail accounts, other providers, hosting it online, mobile use.
- Running automatically on a schedule. The owner starts it when they want it.

## Anti-goals (failure even if it "works")

- Any change to the owner's mailbox. The app must only have read access.
- Mail content leaving the owner's computer. The only outbound traffic besides
  Google is the unsubscribe requests the owner explicitly picked.
- Unsubscribing from something the owner didn't tick, or from a sender marked keep.
- Having to re-review the same senders every month.

## Constraints

- **Gmail access scope: `gmail.readonly` only.** Nothing else.
- Python 3.13 is installed on the machine (3.14 is also present).
- The owner sets up their own Google Cloud project with a "Desktop app" OAuth
  client and downloads `credentials.json`. The app stays in **Testing** mode
  with the owner added as a test user, so Google doesn't need to review it.
  - Known catch: in Testing mode Google expires refresh tokens after 7 days.
    Used monthly, that means a browser sign-in on most runs. The app must
    handle an expired or revoked token by re-running the sign-in flow cleanly,
    not crashing. (Switching the project to "In production" unverified avoids
    the expiry at the cost of a scary warning screen. Documented in the README
    as an option, not the default.)
- Gmail API quota is about 250 units/user/second and `messages.get` costs 5,
  so roughly 50 messages/second. A first scan of tens of thousands of messages
  can take 10–20 minutes. The UI must show progress, and an interrupted first
  scan should resume rather than start over.

## Approaches considered

1. **Local Python app with a browser page (chosen).** One command starts a
   small local web server and opens the checkbox page. State lives in a local
   SQLite file. Mail data stays local, there are no run-time limits, and the UI
   can be pleasant. Costs: needs Python plus the one-time Cloud setup, and you
   start it from a command.
2. **Google Apps Script web app.** Hosted by Google, state in a Google Sheet,
   no install or Cloud project. Rejected because of the 6-minute execution
   limit (the first scan would need chunking) and a clunkier UI, which matters
   for something used monthly.
3. **Command-line script.** Rejected because ticking through a long list every
   month is much nicer with checkboxes than typing in a terminal.

The owner picked option 1.

## The design

### Running it

- `python -m unsubscriber` (plus a `run.ps1` shortcut) starts a local server
  bound to `127.0.0.1` only and opens the page in the default browser.
- First run: no `credentials.json` → the page shows setup instructions (Cloud
  project, enable Gmail API, OAuth consent screen in Testing with the owner as
  test user, Desktop OAuth client, download the JSON into the app folder).
  Also covered in the README.
- `credentials.json`, `token.json` and the SQLite file sit in the project
  folder and are listed in `.gitignore`.

### Scanning

- **What gets scanned:** all mail except Sent, Drafts, Spam and Trash, not just
  the Inbox, so junk that was archived or landed in the Promotions tab still
  counts. *(My call: the owner said "inbox", but archived junk is still junk.)*
- For each message, fetch metadata only (`format=metadata`) with the headers
  `From`, `Date`, `List-Unsubscribe`, `List-Unsubscribe-Post`, plus the
  thread ID. Bodies are never downloaded. Use batch requests within the quota.
- **First scan:** the past 30 days only (`FIRST_SCAN_DAYS`; changed from full
  history at the owner's request on 2026-11-22), with a progress bar (n of N).
  Saves progress as it goes so a closed window or crash resumes where it stopped.
- **Later scans:** only messages newer than the last completed scan. Using
  Gmail's `history` API or an `after:` date query is the builder's call.
- A "Scan now" button starts a scan, and the page shows when the last scan ran.

### Grouping

- Group by the **sender's email address** (lowercased), showing the most recent
  display name and the domain. *(My call: grouping by domain would wrongly
  merge something like `alerts@bank.com` with `offers@bank.com`.)*
- Per sender, store: total count, count in the last 30 days, first and last
  seen, the unsubscribe headers from the **most recent** message, and the
  thread ID of the most recent message.
- **Only mailing lists are shown (owner's decision).** A sender is a mailing
  list if any of its messages carries a `List-Unsubscribe` header. Everyone
  else (real people, most one-off receipts) is stored but never appears on
  the page. Accepted trade-off: junk senders that don't include the header
  won't be listed.

### The page

Sections, top to bottom:

1. **Still sending after unsubscribe**: senders marked unsubscribed who have
   sent mail more than **10 days** after the unsubscribe (US law gives senders
   10 business days). Shows how many since the unsubscribe date, a link to
   open their latest email in Gmail, and actions:
   - **Retry**: re-run unsubscribe using the latest headers, offering the
     manual methods if one-click was tried before.
   - **Hide**: records an "acknowledged at" date. The sender stays hidden
     until it sends again after that date. (Useful after the owner blocks or
     filters them in Gmail.)
   - **Keep**: changed your mind.

   *(My call: without retry/hide, a stubborn sender would sit at the top
   every month.)*
2. **To review**: senders with no decision yet, sorted by **emails in the
   last 30 days** (highest first), with the all-time count shown alongside.
   Senders with nothing in the last 90 days are left out, since unsubscribing
   from a list that's gone quiet does nothing. *(My call: this matches a
   monthly cadence and ranks what's filling the inbox now.)* Each row has a
   checkbox, name/address, 30-day and all-time counts, last seen, and a badge
   for the unsubscribe method (see below). Buttons:
   **Unsubscribe selected** and **Keep selected**.
   - **Skipping (owner's decision):** a sender left in "To review" without a
     decision becomes **skipped** when the next scan completes, and its
     30-day count at that moment is saved as a baseline. A skipped sender
     comes back to "To review" only if a later scan finds its 30-day count
     **above** that baseline (it's sending more than before).
3. **Kept**, **Unsubscribed** and **Skipped** (collapsed by default): lists of
   past decisions, each with an undo that moves the sender back to "To review".

Sketch:

```
Last scan: Oct 21 · 214 new emails        [ Scan now ]

Still sending after unsubscribe
  Deals Daily  deals@dealsdaily.com   6 since Oct 3   [open in Gmail] [keep]

To review                                  emails  last seen
[x] Promo Store      news@promostore.com     38    2 days ago   One-click
[ ] Bank Alerts      alerts@bank.com         22    today        One-click
[x] Webinar Co       hi@webinar.co           15    1 week ago   Opens page
[ ] Old Forum        noreply@forum.org        9    1 month ago  Email draft
        [ Unsubscribe selected ]   [ Keep selected ]

▸ Kept (41)    ▸ Unsubscribed (87)
```

### Unsubscribing

The method is chosen from the most recent message's headers, in this order:

| Method | When | What happens |
|---|---|---|
| **One-click** | `List-Unsubscribe` has an `https:` URL **and** `List-Unsubscribe-Post: List-Unsubscribe=One-Click` (RFC 8058) | Server sends `POST` to that URL with body `List-Unsubscribe=One-Click`, no cookies, 10 s timeout. A 2xx response counts as success. |
| **Opens page** | `https:` URL without the one-click header | The URL opens in a new browser tab for the owner to finish. |
| **Email draft** | `mailto:` only | Opens a pre-filled Gmail compose window (`mail.google.com/mail/?view=cm&to=…&su=…&body=…`, using the subject/body from the mailto if given). The owner clicks Send. |
| **None** | No usable header | Opens the latest email in Gmail so the owner can find a link themselves. |

- After **Unsubscribe selected**, one-click senders are processed on the
  server and each row shows its result (done / failed with reason). Senders
  that need the owner (opening a page or a draft) go into a short "finish
  these" list with a button each, because browsers block a pile of tabs
  opening at once.
- A sender is marked **unsubscribed** (with the date and method) when a
  one-click request succeeds, or when the owner clicks through a manual one.
  If it didn't really work, the "still sending" check catches it later.
- A failed one-click leaves the sender in "To review" with the error shown,
  and offers the manual methods if the headers have them.
- Never act on a sender the owner didn't tick. Never send a `GET` to an
  unsubscribe URL automatically (some confirm your address is real or
  trigger tracking). Only the RFC 8058 `POST`.

### Data (SQLite)

- `senders`: address (key), display name, domain, counts, first/last seen,
  latest unsubscribe headers, latest thread ID, is-mailing-list flag, status
  (`new` / `kept` / `unsubscribed` / `skipped`), status date, unsubscribe
  method, last error, skip baseline (30-day count when skipped),
  acknowledged-at date (for hidden still-sending senders).
- `messages`: message ID (key), sender address, date. Used for counts,
  "still sending" and resuming scans. No subjects or bodies stored.
- `scan_state`: last completed scan time/marker, in-progress resume point.

### Tech choices (my calls)

- Python 3.13, Flask, `google-api-python-client`, `google-auth-oauthlib`,
  `requests`, stdlib `sqlite3`. Server-rendered HTML with a little vanilla JS
  for the progress bar and button actions. No front-end build step.
- Dependencies in `requirements.txt`, installed into a local virtual environment.

### Testing

- Unit tests (pytest) for parsing `List-Unsubscribe` (multiple URIs, angle
  brackets, mailto with subject/body), choosing the method, `From` header
  parsing and grouping, and the still-sending rule.
- Gmail is reached through a thin client wrapper so tests use a fake with
  canned message metadata. The one-click POST is tested against a local stub
  server.

## Left to the builder

- `history` API vs `after:` query for incremental scans.
- Batch sizes, retry/backoff on 429s and 5xx errors.
- Exact page styling, as long as it's clean and readable.
- Module and file layout.
