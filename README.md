# gtm-mail

**The alternative to Instantly or HeyReach for small teams that don't need the volume.**
Sequencers are built for thousands of sends a month; if you're doing tens a day, you're
paying for scale you don't use and giving up control you do need. gtm-mail sends outbound
from your team's **own Gmail mailboxes** — no SaaS subscription, no shared credentials, no
server. ~400 lines of Python you can read in full.

What you get: a sender with hard safety guarantees (dry-run default, identity assertion,
idempotency, daily caps) and a watcher that follows only the threads it sent, surfacing
replies and bounces for a human to act on. Real mailboxes, real threads, an append-only
audit log, and zero chance the tool does something you didn't ask.

## Three ways to use this repo

**1. With Claude Code (recommended) — it guides you through everything.**

```bash
git clone https://github.com/miguel4tw/gtm-mail ~/.claude/skills/gtm-mail
```

Then in Claude Code: `/gtm-mail` (or just say "set up gtm-mail"). The agent walks you
through the Google Cloud setup, authorizes each teammate's mailbox (including teammates on
the other side of the world), and refuses to let you send anything real until the test
checklist passes.

**2. With any other agentic tool (Cursor, Codex, etc.).**

Clone the repo and tell your agent: *"Follow SKILL.md in this directory and guide me
through it."* The instructions are plain markdown with verification gates at every step —
any competent agent can execute them.

**3. By hand.**

Read [ARCHITECTURE.md](ARCHITECTURE.md) for the design, then [SKILL.md](SKILL.md) as a
setup manual — every step works as human instructions too.

## What you end up with

- Each teammate approves their own mailbox **once** (per-user OAuth, revocable by them,
  no admin action, no shared keys). You create your own Google Cloud OAuth app — ~20
  minutes, free, and nothing here connects to anyone else's infrastructure.
- `gmail_send.py` sends a campaign batch, each lead from its assigned sender, with five
  ordered guarantees: identity asserted before anything sends; `(campaign, recipient)`
  idempotency so reruns can't double-send; per-sender daily caps counted from the log;
  copy rules enforced by code; send → log → stage, in that order.
- `gmail_watch.py` checks only the open threads in the send log, classifies replies and
  bounces, and stages CRM updates in a reviewable JSONL. It never lists a mailbox and
  never auto-replies. Threads that resolve — or go 30 days quiet — close, so watching
  cost tracks active conversations, not history.
- `gmail_followup.py` proposes human-gated follow-ups (touches 2+ typically capture
  ~40% of replies): same thread, plain text, no links, dry-run gate, and a pre-send
  re-check so anyone who just replied never gets nudged.
- `out/send_log.jsonl`: an append-only audit trail joining every send to its Gmail thread
  and your CRM record.
- `deliverability.py`: the preflight a deliverability SaaS would sell you — SPF/DKIM/
  DMARC/Spamhaus checks for your domain, plus a placement check that asks Gmail directly
  whether a logged send landed in INBOX, PROMOTIONS, or SPAM for seeds you own.

## What's deliberately absent

No auto-*sent* follow-ups (a human approves every batch), no web UI, no database, no
shared credentials. The moment a tool auto-replies as a person, you've rebuilt the
sequencer you were avoiding.

## Requirements

Python 3.9+ · `pip3 install google-auth google-auth-oauthlib google-api-python-client` ·
a Google Workspace domain (consumer Gmail works with caveats — see SKILL.md Phase 2).

## License

MIT — see [LICENSE](LICENSE).
