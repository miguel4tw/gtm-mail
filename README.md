# gtm-mail

Outbound campaign email from your team's **own Gmail mailboxes** — no sequencer SaaS, no
shared credentials, no server. ~400 lines of Python: a sender with hard safety guarantees
(dry-run default, identity assertion, idempotency, daily caps) and a watcher that follows
only the threads it sent, surfacing replies and bounces for a human to act on.

Built for small teams doing manual-quality outbound at small volume — tens of sends a day,
not thousands. If you need thousands, buy a sequencer. If you need trust, build this: real
mailboxes, real threads, an append-only audit log, and zero chance the tool does something
you didn't ask.

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
- `gmail_watch.py` checks only the threads in the send log, classifies replies and
  bounces, and stages CRM updates in a reviewable JSONL. It never lists a mailbox and
  never auto-replies.
- `out/send_log.jsonl`: an append-only audit trail joining every send to its Gmail thread
  and your CRM record.

## What's deliberately absent

No auto-follow-ups, no web UI, no database, no shared credentials. The moment a tool
auto-replies as a person, you've rebuilt the sequencer you were avoiding.

## Requirements

Python 3.9+ · `pip3 install google-auth google-auth-oauthlib google-api-python-client` ·
a Google Workspace domain (consumer Gmail works with caveats — see SKILL.md Phase 2).

## License

MIT — see [LICENSE](LICENSE).
