# Unsubscriber

A small app that runs on your own computer. Once a month, it scans your Gmail, shows which
mailing lists send you the most mail, and unsubscribes you from the ones you tick.

- **Read-only.** The app only asks Google for permission to *read* your mail. It can't delete,
  move, label, or send anything.
- **Stays on your computer.** It stores sender addresses, dates, and unsubscribe headers in a
  local file (`unsubscriber.db`). It never downloads email bodies or subjects. The only requests
  it sends anywhere besides Google are the unsubscribe requests for senders you picked.
- **Remembers your choices.** Senders you keep, unsubscribe from, or skip don't come back
  next month. A skipped list returns only if it starts sending more than before.

## Install (once)

Requires Python 3.13 or newer.

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

## Google setup (once, about 10 minutes)

1. In [Google Cloud Console](https://console.cloud.google.com/projectcreate), create a project.
2. Enable the [Gmail API](https://console.cloud.google.com/apis/library/gmail.googleapis.com).
3. Open [Google Auth Platform](https://console.cloud.google.com/auth/overview) (the OAuth consent
   screen). Choose **External**, enter an app name and your email, and leave it in **Testing**.
4. Under **Audience → Test users**, add your own Gmail address.
5. Under **Clients**, create a client of type **Desktop app** and download its JSON.
6. Save the file as `credentials.json` in this folder.

## Monthly use

```powershell
.\run.ps1
```

Your browser opens the app.

1. Click **Scan now**. The first scan reads the past 30 days of mail (change `FIRST_SCAN_DAYS`
   in `unsubscriber/config.py` to go further back). If it stops partway, click Scan again and it
   picks up where it left off. Later scans only read mail that arrived since the last scan.
2. Tick the senders you're done with, then click **Unsubscribe selected**.
   - **One-click** senders are handled immediately.
   - Others show up under **Finish these**. Click each one to open the sender's unsubscribe page
     or a pre-filled Gmail draft (you click Send).
3. Use **Keep selected** for lists you want. Anything you leave unticked is skipped at the next
   scan.
4. **Still sending after unsubscribe** lists senders who kept emailing more than 10 days after
   you unsubscribed. Use **Retry**, block them in Gmail and click **Hide**, or **Keep** them.

Press `Ctrl+C` in the terminal to stop the app.

## Signing in again

While your Google project is in **Testing** mode, Google expires your sign-in after 7 days. Since
you use this monthly, you'll usually click **Sign in with Google** again. That's expected.

To avoid it, you can switch the project to **In production** under Google Auth Platform →
Audience without submitting it for verification. Google will then show an "app isn't verified"
warning when you sign in (click *Advanced → Go to app*), but your sign-in will stop expiring
weekly.

## Tests

```powershell
.venv\Scripts\python -m pytest
```
