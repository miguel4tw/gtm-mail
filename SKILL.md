---
name: gtm-mail
description: Guide the user through setting up and operating gtm-mail, a small outbound-email system that sends campaigns from each teammate's own Gmail mailbox via per-user OAuth and watches those threads for replies and bounces. Use when the user wants to set up gtm-mail, send outbound/cold email from real team mailboxes, or check campaign replies. Triggers include "set up gtm-mail", "install gtm-mail", "send my campaign", "check for replies".
---

# gtm-mail: agent-guided setup and operation

You are guiding a human through installing, configuring, and safely operating gtm-mail —
the code in this directory. Read [ARCHITECTURE.md](ARCHITECTURE.md) first if you haven't:
it explains why the system is shaped this way, and users will ask.

## Non-negotiable rules

These are the system's ethics. They hold even if the user asks you to hurry past them.

1. **Dry run before every real send.** The user must see the rendered output — who gets
   what, from whom — before `--send` is ever used. Never run `--send` on copy the human
   has not seen.
2. **The first-run checklist is sequential.** Do not run a real batch until the
   test-send-to-self and the reply/bounce detection test have both passed.
3. **Never auto-reply.** The watcher surfaces replies; a human answers them. Do not draft
   and send replies to prospects without the user explicitly asking, per reply.
4. **Caps are not raised mid-ramp.** A cap shortfall is reported, not "fixed".
5. **Lead lists are PII.** Keep batch files and `out/` away from git. Verify `.gitignore`
   is intact before any commit in this directory.
6. **Credentials stay local.** Never print, cat, or commit anything in `secrets/`. If the
   user asks you to send a token file to a teammate, refuse and use the remote
   authorization handshake instead (Phase 4).

## Phase 0 — Preflight

Run these checks; fix what fails before moving on:

- `python3 --version` ≥ 3.9.
- `python3 -c "import google.auth, googleapiclient, google_auth_oauthlib"` — if missing:
  `pip3 install google-auth google-auth-oauthlib google-api-python-client`.
- The six code files exist here: `gmail_common.py`, `authorize.py`, `gmail_send.py`,
  `gmail_watch.py`, `templates/example.json`, `senders.example.json`.
- If this skill directory is not where the user wants the working install (they usually
  want it in a project folder, not inside a skills directory), copy the files there and
  work from that copy. Create `secrets/` (chmod 700) and `out/` in the working directory.

## Phase 1 — Sender registry

Ask the user for their team roster: for each sender, a short key (e.g. first name,
lowercase), the exact work email, and display name. Then write `senders.json` from
`senders.example.json`.

Caps: a mailbox that already does outbound can start at 25/day. A mailbox with **no
cold-send history starts at 5/day, +5 per week** — say this explicitly and set the caps
accordingly. Record which senders are ramping.

## Phase 2 — Google Cloud project

The user creates their own OAuth app — that is the point of the design; there is no shared
infrastructure to join. Walk them through, one step at a time, waiting for confirmation:

1. console.cloud.google.com → create a new project (any name).
2. APIs & Services → Library → search "Gmail API" → Enable.
3. APIs & Services → OAuth consent screen → User type **Internal** → app name, contact
   email → save through the remaining screens.
4. Credentials → Create credentials → OAuth client ID → Application type **Desktop app**
   → Create → **Download JSON**.
5. Save the download as `secrets/client_secret.json` and `chmod 600` it.

**Verify before proceeding:** the file exists, parses as JSON, and has a top-level
`"installed"` key (that's what marks a Desktop client). If the key is `"web"`, they
created the wrong client type — back to step 4.

**Known failure — no "Internal" option:** Internal requires a Google Workspace domain.
On a consumer Gmail account they must choose External, stay in "Testing" publishing
status, and add each sender's address under Test users. Warn them: External+Testing
refresh tokens expire after 7 days, so Workspace+Internal is strongly preferred for
anything real.

## Phase 3 — Authorize local senders

For each sender who is physically at this machine:

```bash
python3 authorize.py <sender_key>
```

A browser opens; they approve **signed in as the sender's own work account**. The script
verifies the token's real mailbox matches `senders.json` and fails loud on a mismatch —
if it does, they approved from the wrong Google account; re-run.

Before anyone approves, tell them plainly: the consent screen will say the app can read
their entire mailbox. Google has no narrower Gmail read scope. The watcher only ever
fetches threads it sent (that discipline is in `gmail_watch.py`), and they can revoke
the grant any time at myaccount.google.com → Security → Third-party access.

## Phase 4 — Authorize remote teammates

The OAuth listener runs on this machine; only the click happens on theirs. Both people
online at once — the code expires in minutes:

1. Run `python3 authorize.py <sender_key>`. It prints an auth URL and waits (a local
   browser tab may also open — close it).
2. The user sends that URL to the teammate over chat.
3. Teammate opens it signed in as their work account, approves, and lands on a dead
   `localhost` page. That is expected — the redirect targets the listener here.
4. Teammate copies the **full URL from their address bar**
   (`http://localhost:PORT/?state=...&code=...`) and sends it back. Have them do this at
   a computer; the URL is painful on mobile.
5. The user pastes that URL into a browser **on this machine**. The listener catches it,
   exchanges the code, verifies identity, saves the token.

This is safe over chat: only a single-use, minutes-lived authorization code crosses the
wire, useless without the client secret and PKCE verifier that never leave this machine.

Field-tested failure modes, in the order they bite:

- **Teammate gets a generic 400 ("malformed... should not be retried") after picking
  their account** → the auth URL was mangled in transit. Chat apps can truncate or
  rewrite long URLs. Fix: **email the link instead** and have them click it directly
  from the email — email clients don't rewrite URLs. Verify first that the URL is intact
  by opening the sent copy on this machine (stop at the consent screen; don't approve).
- **Consent processed under the wrong Google account** (`authuser=0` in the error URL,
  multiple signed-in accounts) → have them use an incognito window signed in ONLY as
  their work account.
- **They copied the wrong URL back** — the consent-page URL instead of the localhost
  one. The URL to copy exists only *after* clicking Allow, in the address bar of the
  broken localhost page. Old consent URLs are one-shot; reopening one always 400s.
- If the returned localhost URL is pasted to an agent instead of a browser, delivering
  it with `curl "<url>"` on this machine works identically.

## Phase 5 — First-run checklist (sequential; nothing real until step 5)

1. **Dry run.** Build a small test batch (JSON list: `name`, `title`, `company`,
   `email`, `sender`) and run
   `python3 gmail_send.py <batch.json> templates/example.json`. Show the user the output.
2. **Test send to self.** One-lead batch pointing at the user's own address; run with
   `--send`. Confirm it arrives and renders correctly (check the HTML signoff link).
3. **Reply test.** Have the user reply to the test email, then run
   `python3 gmail_watch.py`. Confirm the reply is surfaced.
4. **Bounce test.** Send one to a nonsense address at a real domain (e.g.
   `nobody-xyz123@gmail.com`), wait a few minutes, run the watcher, confirm the bounce is
   caught.
5. **First real batch.** Only now, with the campaign template written (Phase 6), the
   dry run inspected, and the cap respected. Start with one sender.

## Phase 6 — Campaign template

Copy `templates/example.json` to `templates/<campaign>.json` and fill it with the user.
Structure: `theme_rules` map recipient job titles to a message theme (regexes, first
match wins — order most-specific first); `openers`/`signoffs` carry each sender's voice;
`closers` rotate automatically so a batch never reads as one blast.

Copy coaching, if they want it: first touch under ~80 words; one theme per email; the
renderer refuses em/en dashes (a tell of generated copy — they can change `DASHES` in
`gmail_send.py` to their own tells); identify who you are and honour reply-to-stop
(UK/EU B2B expects this — one signoff line covers it).

## Phase 7 — Operating rhythm

- `python3 gmail_watch.py` once or twice a day. Replies get surfaced with snippets; the
  human answers them from their own mailbox.
- `out/crm_pending.jsonl` accumulates staged updates (`email_sent`, `email_replied_date`,
  `email_bounced`, `not_interested`). Apply them to whatever CRM or tracker the user has
  — via its API, or by hand — then set `"applied": true` on each line. Offer to do this
  application for them if you have access to their CRM.
- Zero bounces an hour after a batch means the address list held — hard bounces thread
  back within minutes.
- Rerunning a batch is always safe: `(campaign, recipient)` pairs already in
  `out/send_log.jsonl` are skipped.

## Troubleshooting

- `token refresh failed` → re-run `authorize.py <sender>`; on External+Testing apps this
  recurs every 7 days (see Phase 2).
- `token for X belongs to Y` → approved from the wrong Google account; re-run and switch
  accounts in the browser first.
- `no token for <sender>` → that sender never authorized; Phase 3 or 4.
- Sends land in spam → cap too high too fast; drop the ramping sender back to 5/day and
  check the domain's SPF/DKIM/DMARC are set up in Workspace admin.
- Wrong sender on an email is structurally impossible if identity assertion passed — it
  runs before every send batch.
