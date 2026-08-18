#!/usr/bin/env python3
"""Watch sent threads for replies and bounces; stage CRM updates.

Usage:  python3 gmail_watch.py            # all senders with a token
        python3 gmail_watch.py --sender alice

Scope discipline: reads ONLY the threads named in out/send_log.jsonl. It never
lists or searches a mailbox, even though gmail.readonly would allow it - the
token is broader than the job, the code must not be.

  reply  = a message whose From is neither the sender nor a mailer daemon.
           Stages a reply date + surfaces a snippet. An explicit no also
           stages not_interested. Never auto-replies.
  bounce = a message from mailer-daemon/postmaster, or carrying a
           delivery-status part. Stages email_bounced so a dead address stops
           looking like an unanswered lead.

Dedup via out/replies_seen.json: a message id is only surfaced once.
"""
import argparse
import json
import os
import re
from datetime import datetime, timezone

from gmail_common import (
    OUT, assert_identity, build_service, load_credentials,
    load_senders, read_send_log, token_path,
)

SEEN_FILE = os.path.join(OUT, "replies_seen.json")
LATEST_FILE = os.path.join(OUT, "replies_latest.json")
PENDING = os.path.join(OUT, "crm_pending.jsonl")

BOUNCE_FROM = re.compile(r"mailer-daemon|postmaster", re.I)
EXPLICIT_NO = re.compile(r"\bno thanks\b|\bnot interested\b|\bunsubscribe\b", re.I)


def header(msg, name):
    for h in msg.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def is_bounce(msg):
    if BOUNCE_FROM.search(header(msg, "From")):
        return True
    def walk(part):
        if part.get("mimeType") == "message/delivery-status":
            return True
        return any(walk(p) for p in part.get("parts", []))
    return walk(msg.get("payload", {}))


def stage_crm(record, update):
    entry = {
        "staged_at": datetime.now(timezone.utc).isoformat(),
        "campaign": record["campaign"],
        "applied": False,
        "crm_record_id": record.get("crm_record_id"),
        "match": {"name": record["name"], "email": record["recipient"]},
        "update": update,
    }
    os.makedirs(OUT, exist_ok=True)
    with open(PENDING, "a") as f:
        f.write(json.dumps(entry) + "\n")


def check_thread(service, record, sender_email, seen):
    thread = service.users().threads().get(
        userId="me", id=record["gmail_thread_id"], format="full").execute()
    events = []
    for msg in thread.get("messages", []):
        if msg["id"] in seen or msg["id"] == record["gmail_message_id"]:
            continue
        from_hdr = header(msg, "From")
        if sender_email.lower() in from_hdr.lower():
            seen[msg["id"]] = "own-followup"
            continue
        snippet = msg.get("snippet", "")
        ts = datetime.fromtimestamp(
            int(msg["internalDate"]) / 1000, tz=timezone.utc).date().isoformat()
        if is_bounce(msg):
            seen[msg["id"]] = "bounce"
            stage_crm(record, {"email_bounced": True})
            events.append({"kind": "bounce", "date": ts})
        else:
            seen[msg["id"]] = "reply"
            update = {"email_replied_date": ts}
            if EXPLICIT_NO.search(snippet):
                update["not_interested"] = True
            stage_crm(record, update)
            events.append({"kind": "reply", "date": ts, "from": from_hdr,
                           "snippet": snippet[:300]})
    return events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sender", help="only this sender key")
    args = ap.parse_args()

    senders = load_senders()
    log = read_send_log()
    if not log:
        print("send_log is empty - nothing to watch.")
        return
    seen = json.load(open(SEEN_FILE)) if os.path.exists(SEEN_FILE) else {}

    findings = []
    for key, meta in senders.items():
        if args.sender and key != args.sender.lower():
            continue
        records = [r for r in log if r["sender"] == key]
        if not records:
            continue
        if not os.path.exists(token_path(key)):
            print(f"WARNING: {len(records)} logged sends for '{key}' but no token.")
            continue
        service = build_service(load_credentials(key))
        assert_identity(service, key, meta["email"])
        for record in records:
            for ev in check_thread(service, record, meta["email"], seen):
                findings.append(dict(ev, name=record["name"],
                                     recipient=record["recipient"], sender=key))

    os.makedirs(OUT, exist_ok=True)
    json.dump(seen, open(SEEN_FILE, "w"), indent=1)
    json.dump(findings, open(LATEST_FILE, "w"), indent=1)

    print(f"\n=== {len(findings)} new event(s) ===")
    for ev in findings:
        line = f"  [{ev['kind'].upper()}] {ev['name']} <{ev['recipient']}> via {ev['sender']} on {ev['date']}"
        if ev["kind"] == "reply":
            line += f"\n      {ev['snippet']}"
        print(line)
    if findings:
        print(f"\nCRM updates staged in {PENDING} - apply them, then answer replies personally.")


if __name__ == "__main__":
    main()
