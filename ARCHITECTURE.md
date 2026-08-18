# Architecture

Why gtm-mail is shaped the way it is. Read this before changing it — most of the obvious
"improvements" are things it deliberately avoids.

## Why not the alternatives

| Option | Why not |
|---|---|
| Sequencer SaaS | Cost, and opacity. When a sequencer misfires — wrong step, wrong subject, an action you didn't intend — you find out from the prospect. At tens of sends a day the risk outweighs the automation. |
| Google Workspace domain-wide delegation | One service-account key that can silently send as **anyone in the domain, forever**. Your admin should hesitate; the hesitation is correct. |
| Gmail "send as" delegation | Renders as "alice@ on behalf of bob@" in several clients. Fatal on a cold email. |
| SMTP + app passwords | Storing colleagues' credentials; Google blocks basic auth for Workspace by default anyway. |

**Per-user OAuth** wins: each teammate approves their own mailbox once, from anywhere
(see the remote handshake in SKILL.md), each token is individually revocable from that
person's own Google account page, and no admin action is required at all.

## The shape

```
                     campaign batch (JSON: lead, sender, email, title)
                                 │
                                 ▼
                        gmail_send.py  ──────────►  Gmail API (users.messages.send)
                                 │                     as alice@ / bob@ / carol@
                                 │                                │
                                 ▼                                ▼
                        out/send_log.jsonl                   recipient
                        (thread_id ↔ crm record)                  │
                                 ▲                                │ replies
                                 │                                ▼
                        gmail_watch.py  ◄──────────  sender's own mailbox
                                 │                   (users.threads.get, readonly)
                                 ▼
                        out/crm_pending.jsonl  ──►  your CRM / tracker
                                 │
                                 ▼
                        a human answers the replies
```

Three stores, one job each:

- **Your CRM/tracker** owns lead state (contacted, replied, bounced, not interested).
- **The batch JSON** defines one campaign run: who, from which sender, with which theme.
- **`out/send_log.jsonl`** is append-only and is the join key of the whole system: every
  send records `gmail_thread_id ↔ crm_record_id`. Without it the watcher cannot know
  which lead a reply belongs to. Only `gmail_send.py` writes it.

The watcher does **not** write your CRM directly. It stages updates in
`out/crm_pending.jsonl`; something you already trust — a script against your CRM's API,
or an AI-assistant session — applies them. The watcher therefore needs no CRM
credentials, and the staged file doubles as a reviewable changelog.

## The five guarantees

Everything in the sender exists to uphold one of these, in priority order:

1. **Identity** — every token is asserted against the sender registry
   (`users.getProfile`) *before anything sends*. A mixed-up token is a hard exit, not an
   email from the wrong person.
2. **Idempotency** — `(campaign, recipient)` is checked against the send log. Rerunning
   a batch after a partial failure cannot double-send. This is the failure that costs you
   a prospect; it gets the strongest guard.
3. **Caps** — a per-sender daily cap, counted from the log, never from memory.
4. **Copy discipline** — the renderer refuses bodies containing an em or en dash (swap in
   your own tells); copy rules are enforced by code, not memory.
5. **Ordering** — send → append log → stage CRM. A send that fails to record still exists
   in Gmail, so the log write happens immediately; anything downstream is repairable from
   the log.

## Watcher scope discipline

`gmail.readonly` is broader than the job — Google offers no narrower Gmail read scope,
and teammates see "read your email" on the consent screen. Be straight with them about
that, and hold the compensating line in code: **the watcher only ever fetches thread IDs
that appear in the send log. It never lists, searches, or enumerates a mailbox.** The
token is broad; the code must not be.

## Operating principles

- **Ramp cold mailboxes.** No cold-send history → start 5/day, +5 per week. A cap
  shortfall is reported loudly and never fixed by raising the cap mid-ramp.
- **Bounce silence is signal.** Hard bounces thread back within minutes; zero bounces an
  hour after a batch means the address list held.
- **Verify effects, not statuses.** After anything automated touches email, check the
  mailbox or the API result — never trust a tool's own success report. (This system
  exists partly because a sequencer once reported `completed` while doing nothing, and
  once did things that were never asked.)
- **Replies are answered by humans.** The watcher surfaces; it never sends.

## What's deliberately absent

- **No auto-follow-ups.** Follow-ups are a copy decision per lead. If you must, add a
  `followups` template section and a `--followup` mode — but keep the human deciding who
  gets one.
- **No web UI, no server, no database.** State is three flat files and your CRM. When a
  team outgrows that, it migrates a clean audit log instead of a mystery.
- **No shared credentials.** One mailbox, one token, revocable by its owner. When someone
  leaves, their token dies with their access and the system keeps working for everyone
  else.
