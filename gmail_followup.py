#!/usr/bin/env python3
"""Human-gated follow-ups: nudge leads who never answered, in the same thread.

Usage:
  python3 gmail_followup.py <template.json>            # DRY RUN (default)
  python3 gmail_followup.py <template.json> --send     # real sends
  python3 gmail_followup.py <template.json> --sender alice

Touches 2-5 typically capture ~40% of all replies; this exists so they stop
being forgotten, NOT so they become automatic. The human sees the dry run and
decides; the tool renders, threads, and remembers who is on which touch.

Template needs a "followups" section:
  "followups": [
    {"after_days": 3, "bodies": {"alice": "Hi {first},\\n\\n<nudge>\\n\\nAlice"}},
    {"after_days": 7, "bodies": {"alice": "Hi {first},\\n\\n<last nudge>\\n\\nAlice"}}
  ]
after_days counts from the LAST touch (initial send or previous follow-up).
Bodies are complete messages including greeting and sign-off - {first} is the
only substitution. PLAIN TEXT ONLY, deliberately: no HTML part and no links -
a second touch with no links inboxes better, and the thread carries context.

Safety, same order as gmail_send.py:
  - identity asserted before anything sends
  - a thread is eligible ONLY while its status is "open" (run gmail_watch.py
    first; anyone who replied, bounced, or opted out is out of scope)
  - just before sending, the thread is re-fetched: an inbound message that
    arrived since the last watch run cancels the follow-up for that lead
  - followup_stage in thread_status is the idempotency guard - a rerun never
    repeats a touch
  - daily caps count initial sends and follow-ups together
  - the em/en dash refusal applies to follow-up bodies too
"""
import argparse
import base64
import json
import random
import re
import time
from datetime import datetime, timezone
from email.mime.text import MIMEText

from gmail_common import (
    OUT, append_send_log, assert_identity, build_service, days_since, die,
    ensure_thread_status, load_credentials, load_senders, load_thread_status,
    read_send_log, save_thread_status,
)
from gmail_send import DASHES, PENDING, sent_today


def stage_followup_crm(record, stage_n):
    entry = {
        "staged_at": datetime.now(timezone.utc).isoformat(),
        "campaign": record["campaign"], "applied": False,
        "crm_record_id": record.get("crm_record_id"),
        "match": {"name": record["name"], "email": record["recipient"]},
        "update": {"followup_sent": stage_n,
                   "followup_date": datetime.now(timezone.utc).date().isoformat()},
    }
    with open(PENDING, "a") as f:
        f.write(json.dumps(entry) + "\n")

def render_followup(touch, record):
    body_tpl = touch["bodies"].get(record["sender"])
    if not body_tpl:
        die(f"followup touch has no body for sender '{record['sender']}'")
    first = record["name"].split()[0]
    body = body_tpl.format(first=first)
    if DASHES.search(body):
        die(f"em/en dash in follow-up body for {record['name']} - fix the template")
    return body


def thread_has_inbound(service, record, sender_email):
    """Final pre-send check: anything in the thread not from us cancels."""
    thread = service.users().threads().get(
        userId="me", id=record["gmail_thread_id"], format="metadata",
        metadataHeaders=["From"]).execute()
    for msg in thread.get("messages", []):
        hdrs = {h["name"].lower(): h["value"]
                for h in msg.get("payload", {}).get("headers", [])}
        frm = hdrs.get("from", "")
        if frm and sender_email.lower() not in frm.lower():
            return True
    return False


def original_headers(service, record):
    """Message-ID + Subject of the initial send, for proper threading."""
    msg = service.users().messages().get(
        userId="me", id=record["gmail_message_id"], format="metadata",
        metadataHeaders=["Message-ID", "Subject"]).execute()
    hdrs = {h["name"].lower(): h["value"]
            for h in msg.get("payload", {}).get("headers", [])}
    return hdrs.get("message-id", ""), hdrs.get("subject", record["subject"])


def build_reply_mime(sender_email, sender_name, record, subject, msg_id, body):
    msg = MIMEText(body, "plain")
    msg["To"] = record["recipient"]
    msg["From"] = f"{sender_name} <{sender_email}>"
    msg["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    if msg_id:
        msg["In-Reply-To"] = msg_id
        msg["References"] = msg_id
    return {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode(),
            "threadId": record["gmail_thread_id"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("template")
    ap.add_argument("--send", action="store_true", help="real sends (default: dry run)")
    ap.add_argument("--sender", help="only this sender key")
    args = ap.parse_args()

    senders = load_senders()
    with open(args.template) as f:
        template = json.load(f)
    touches = template.get("followups")
    if not touches:
        die("template has no \"followups\" section - nothing to send")
    campaign = template["campaign"]

    log = read_send_log()
    status = load_thread_status()
    initial = [r for r in log if r.get("kind") != "followup" and r["campaign"] == campaign]
    if args.sender:
        initial = [r for r in initial if r["sender"] == args.sender.lower()]

    due, waiting = [], []
    for r in initial:
        st = ensure_thread_status(status, r)
        if st["state"] != "open" or st["followup_stage"] >= len(touches):
            continue
        touch = touches[st["followup_stage"]]
        age = days_since(st["last_touch"])
        if age >= touch["after_days"]:
            due.append((r, st, touch))
        else:
            # human touch number: initial send is touch 1
            waiting.append((r["name"], st["followup_stage"] + 2,
                            touch["after_days"] - age))

    if not due:
        print("No follow-ups due.")
        for name, touch_no, days in waiting:
            print(f"  waiting: {name} (touch {touch_no} due in {days}d)")
        save_thread_status(status)
        return

    services = {}
    if args.send:
        for key in sorted({r["sender"] for r, _, _ in due}):
            svc = build_service(load_credentials(key))
            assert_identity(svc, key, senders[key]["email"])
            services[key] = svc
            print(f"identity OK: {key} = {senders[key]['email']}")

    sent = cancelled = capped = 0
    for r, st, touch in due:
        key = r["sender"]
        body = render_followup(touch, r)
        stage_n = st["followup_stage"] + 1      # 1-based follow-up number
        touch_no = stage_n + 1                  # human touch number incl. initial
        if not args.send:
            print(f"  DRY RUN touch {touch_no} {key} -> {r['name']} <{r['recipient']}>")
            print("      " + body.replace("\n", "\n      ")[:300])
            continue
        if sent_today(log, key) >= senders[key]["daily_cap"]:
            print(f"  skip (daily cap hit for {key}): {r['name']}")
            capped += 1
            continue
        svc = services[key]
        if thread_has_inbound(svc, r, senders[key]["email"]):
            print(f"  CANCELLED (inbound found, run gmail_watch.py): {r['name']}")
            cancelled += 1
            continue
        msg_id, subject = original_headers(svc, r)
        payload = build_reply_mime(senders[key]["email"], senders[key]["name"],
                                   r, subject, msg_id, body)
        resp = svc.users().messages().send(userId="me", body=payload).execute()
        record = {
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "campaign": campaign, "kind": "followup", "stage": stage_n,
            "sender": key, "recipient": r["recipient"], "name": r["name"],
            "gmail_message_id": resp["id"], "gmail_thread_id": resp["threadId"],
            "crm_record_id": r.get("crm_record_id"), "subject": subject,
        }
        append_send_log(record)
        st["followup_stage"] = stage_n
        st["last_touch"] = record["sent_at"]
        save_thread_status(status)
        stage_followup_crm(record, stage_n)
        log.append(record)
        sent += 1
        print(f"  SENT touch {touch_no} {key} -> {r['name']} <{r['recipient']}>")
        time.sleep(random.uniform(30, 90))

    if not args.send:
        print(f"\n=== DRY RUN: {len(due)} follow-up(s) would send | {len(waiting)} waiting ===")
        print("Re-run with --send after reviewing. Run gmail_watch.py first.")
    else:
        print(f"\n=== SENT {sent} | cancelled {cancelled} | cap-skipped {capped} ===")
        save_thread_status(status)


if __name__ == "__main__":
    main()
