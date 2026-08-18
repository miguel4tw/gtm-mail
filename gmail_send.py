#!/usr/bin/env python3
"""Send a campaign batch, each lead from its assigned sender's own mailbox.

Usage:
  python3 gmail_send.py <batch.json> <template.json>            # DRY RUN (default)
  python3 gmail_send.py <batch.json> <template.json> --send     # real sends
  python3 gmail_send.py ... --sender alice                      # one sender only

Batch: a JSON list of leads, each {name, title, company, email, sender,
crm_record_id?}. Leads with no email are skipped and reported.

Guarantees, in order of what they protect:
  1. Identity: every token asserted against senders.json BEFORE anything sends.
  2. Idempotency: (campaign, recipient) already in send_log => skipped.
  3. Caps: per-sender daily cap from senders.json, counted from send_log.
  4. Copy: any body containing an em/en dash refuses to send.
  5. Ordering: send -> append send_log -> stage CRM update.
"""
import argparse
import base64
import json
import os
import random
import re
import time
from datetime import date, datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from gmail_common import (
    OUT, append_send_log, assert_identity, build_service, die,
    load_credentials, load_senders, load_thread_status, read_send_log,
    save_thread_status,
)

DASHES = re.compile(r"[–—]")  # en dash, em dash - swap in your own copy tells
PENDING = os.path.join(OUT, "crm_pending.jsonl")


def load_batch(path):
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, list):
        die(f"batch must be a JSON list of leads: {path}")
    for p in data:
        if "sender" not in p:
            die(f"lead missing 'sender': {p.get('name')}")
    return [dict(p, sender=p["sender"].lower()) for p in data]


def pick_theme(template, title):
    for rule in template["theme_rules"]:
        if re.search(rule["pattern"], title or "", re.I):
            return rule["theme"]
    return template["theme_rules"][-1]["theme"]


def render(template, lead, closer_ix):
    """One lead -> (subject, text_body, html_body, theme). Closers rotate."""
    sender = lead["sender"]
    first = lead["name"].split()[0]
    theme = pick_theme(template, lead.get("title", ""))
    closer = template["closers"][closer_ix % len(template["closers"])]
    paragraphs = [
        f"Hi {first},",
        f"{template['openers'][sender]} {template['theme_lines'][theme]}",
        template["middle"],
        closer,
    ]
    text = "\n\n".join(paragraphs + [template["signoffs"][sender]["text"]])
    html = "".join(f"<p>{p}</p>" for p in paragraphs) + \
        f"<p>{template['signoffs'][sender]['html']}</p>"
    if DASHES.search(text):
        die(f"em/en dash in rendered body for {lead['name']} - fix the template")
    return template["subject"], text, html, theme


def build_mime(sender_email, sender_name, to_addr, subject, text, html):
    msg = MIMEMultipart("alternative")
    msg["To"] = to_addr
    msg["From"] = f"{sender_name} <{sender_email}>"
    msg["Subject"] = subject
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))
    return {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}


def sent_today(log, sender_key):
    today = date.today().isoformat()
    return sum(1 for r in log if r["sender"] == sender_key and r["sent_at"][:10] == today)


def stage_crm(lead, campaign):
    """Stage the tracker update for whatever applies changes to your CRM."""
    entry = {
        "staged_at": datetime.now(timezone.utc).isoformat(),
        "campaign": campaign,
        "applied": False,
        "crm_record_id": lead.get("crm_record_id"),
        "match": {"name": lead["name"], "email": lead["email"]},
        "update": {"email_sent": True, "email_sent_date": date.today().isoformat()},
    }
    os.makedirs(OUT, exist_ok=True)
    with open(PENDING, "a") as f:
        f.write(json.dumps(entry) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("batch")
    ap.add_argument("template")
    ap.add_argument("--send", action="store_true", help="real sends (default: dry run)")
    ap.add_argument("--sender", help="only this sender key")
    args = ap.parse_args()

    senders = load_senders()
    with open(args.template) as f:
        template = json.load(f)
    campaign = template["campaign"]
    leads = load_batch(args.batch)
    if args.sender:
        leads = [l for l in leads if l["sender"] == args.sender.lower()]

    log = read_send_log()
    already = {(r["campaign"], r["recipient"].lower()) for r in log}
    services = {}

    # Guarantee 1: assert every identity before ANY send. (Dry run renders
    # without touching Gmail, so it works before any token exists.)
    needed = sorted({l["sender"] for l in leads if l.get("email")})
    for key in needed:
        if key not in senders:
            die(f"batch references unknown sender '{key}'")
    if args.send:
        for key in needed:
            svc = build_service(load_credentials(key))
            assert_identity(svc, key, senders[key]["email"])
            services[key] = svc
            print(f"identity OK: {key} = {senders[key]['email']}")

    sent = skipped_dup = skipped_cap = 0
    no_email = []
    for i, lead in enumerate(leads):
        if not lead.get("email"):
            no_email.append(lead["name"])
            continue
        key = lead["sender"]
        if (campaign, lead["email"].lower()) in already:
            print(f"  skip (already sent): {lead['name']} <{lead['email']}>")
            skipped_dup += 1
            continue
        if sent_today(log, key) >= senders[key]["daily_cap"]:
            print(f"  skip (daily cap hit for {key}): {lead['name']}")
            skipped_cap += 1
            continue

        subject, text, html, theme = render(template, lead, i)
        if not args.send:
            print(f"  DRY RUN {key} -> {lead['name']} <{lead['email']}> [{theme}]")
            continue

        body = build_mime(senders[key]["email"], senders[key]["name"],
                          lead["email"], subject, text, html)
        resp = services[key].users().messages().send(userId="me", body=body).execute()
        record = {
            "sent_at": datetime.now(timezone.utc).isoformat(),
            "campaign": campaign,
            "sender": key,
            "recipient": lead["email"],
            "name": lead["name"],
            "gmail_message_id": resp["id"],
            "gmail_thread_id": resp["threadId"],
            "crm_record_id": lead.get("crm_record_id"),
            "subject": subject,
            "theme": theme,
        }
        append_send_log(record)   # Guarantee 5: log immediately after send.
        status = load_thread_status()
        status[resp["threadId"]] = {"state": "open", "last_touch": record["sent_at"],
                                    "followup_stage": 0}
        save_thread_status(status)
        stage_crm(lead, campaign)
        log.append(record)
        already.add((campaign, lead["email"].lower()))
        sent += 1
        print(f"  SENT {key} -> {lead['name']} <{lead['email']}> thread={resp['threadId']}")
        time.sleep(random.uniform(30, 90))  # pacing jitter between real sends

    mode = "SENT" if args.send else "DRY RUN"
    print(f"\n=== {mode}: {sent if args.send else len(leads) - skipped_dup - skipped_cap - len(no_email)} "
          f"| dup-skipped {skipped_dup} | cap-skipped {skipped_cap} | no-email {len(no_email)} ===")
    if no_email:
        print("  skipped, no address: " + ", ".join(no_email))
    if skipped_cap:
        print("  CAP SHORTFALL: report it, do not raise the cap mid-ramp.")


if __name__ == "__main__":
    main()
