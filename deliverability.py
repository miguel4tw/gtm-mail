#!/usr/bin/env python3
"""Deliverability check without a SaaS: DNS auth, blocklists, seed placement.

Usage:
  python3 deliverability.py dns <domain>            # SPF / DKIM / DMARC / Spamhaus
  python3 deliverability.py placement               # where did seed sends land (Gmail
                                                    # org mailboxes with a token only)

What each piece replaces:
  dns        -> the "Authenticator Check" + blocklist panel of any deliverability tool.
  placement  -> the inbox/spam/promotions panel, for seeds we can read via OAuth.
                For external seeds (personal Outlook/Yahoo), placement is a human
                looking at their inbox - no way around that without a seed network.

Spam-scoring of the actual copy is done by sending the rendered template to a
mail-tester.com seed address (free SpamAssassin + auth report) - see README.
"""
import json
import os
import re
import subprocess
import sys

from gmail_common import (
    build_service, die, load_credentials, load_senders, read_send_log, token_path,
)

DKIM_SELECTORS = ["google", "default", "selector1", "selector2"]


def dig(name, rtype="TXT"):
    try:
        out = subprocess.run(["dig", "+short", name, rtype],
                             capture_output=True, text=True, timeout=10).stdout
        return [l.strip().strip('"') for l in out.splitlines() if l.strip()]
    except Exception as e:
        return [f"(dig failed: {e})"]


def check_dns(domain):
    ok = True
    spf = [r for r in dig(domain) if r.lower().startswith("v=spf1")]
    print(f"SPF    {'PASS' if spf else 'FAIL'}  {spf[0] if spf else 'no v=spf1 TXT record'}")
    ok &= bool(spf)
    if spf and "~all" not in spf[0] and "-all" not in spf[0]:
        print("       WARN: SPF has no ~all/-all terminator")

    dkim = None
    for sel in DKIM_SELECTORS:
        recs = [r for r in dig(f"{sel}._domainkey.{domain}") if "v=dkim1" in r.lower() or "k=rsa" in r.lower()]
        if recs:
            dkim = (sel, recs[0][:60])
            break
    print(f"DKIM   {'PASS' if dkim else 'FAIL'}  " +
          (f"selector '{dkim[0]}': {dkim[1]}..." if dkim else f"no key at selectors {DKIM_SELECTORS}"))
    ok &= bool(dkim)

    dmarc = [r for r in dig(f"_dmarc.{domain}") if r.lower().startswith("v=dmarc1")]
    print(f"DMARC  {'PASS' if dmarc else 'FAIL'}  {dmarc[0] if dmarc else 'no _dmarc TXT record'}")
    ok &= bool(dmarc)
    if dmarc and "p=none" in dmarc[0].replace(" ", ""):
        print("       NOTE: p=none means DMARC monitors but does not enforce (fine to start)")

    # Spamhaus DBL: NXDOMAIN = clean. Answers in 127.0.1.x = listed.
    # 127.255.255.x = query refused (public resolver) - inconclusive, not a fail.
    dbl = dig(f"{domain}.dbl.spamhaus.org", "A")
    if not dbl:
        print("DBL    PASS  domain not on Spamhaus DBL")
    elif any(r.startswith("127.255.255.") for r in dbl):
        print("DBL    SKIP  resolver refused (public DNS) - check manually at check.spamhaus.org")
    else:
        print(f"DBL    FAIL  LISTED on Spamhaus DBL: {dbl} - fix before sending anything")
        ok = False
    return ok


def check_placement():
    """For every send-log recipient that is one of OUR org mailboxes with a token,
    ask Gmail where the message actually landed. Labels tell the truth:
    SPAM / INBOX / CATEGORY_PROMOTIONS."""
    senders = load_senders()
    by_email = {v["email"].lower(): k for k, v in senders.items()}
    log = read_send_log()
    seeds = [r for r in log if r["recipient"].lower() in by_email]
    if not seeds:
        print("No send-log recipients are org mailboxes with tokens - nothing to check.")
        print("Send the template to your own mailboxes first (they are your Gmail seeds).")
        return
    for r in seeds:
        key = by_email[r["recipient"].lower()]
        if not os.path.exists(token_path(key)):
            print(f"  {r['recipient']}: no token, cannot check")
            continue
        svc = build_service(load_credentials(key))
        try:
            msg = svc.users().messages().get(userId="me", id=r["gmail_message_id"],
                                             format="minimal").execute()
        except Exception:
            # recipient copy has a different message id in their mailbox - search by rfc822 id
            q = svc.users().messages().list(userId="me",
                    q=f"subject:\"{r['subject']}\" newer_than:7d",
                    includeSpamTrash=True, maxResults=5).execute()
            ids = [m["id"] for m in q.get("messages", [])]
            msg = svc.users().messages().get(userId="me", id=ids[0], format="minimal").execute() if ids else None
        if not msg:
            print(f"  {r['recipient']}: message not found (unreceived?)")
            continue
        labels = set(msg.get("labelIds", []))
        verdict = ("SPAM" if "SPAM" in labels else
                   "PROMOTIONS" if "CATEGORY_PROMOTIONS" in labels else
                   "INBOX" if "INBOX" in labels else ",".join(sorted(labels)))
        print(f"  {r['recipient']:40} -> {verdict}")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("dns", "placement"):
        die("usage: deliverability.py dns <domain> | placement")
    if sys.argv[1] == "dns":
        if len(sys.argv) != 3:
            die("usage: deliverability.py dns <domain>")
        sys.exit(0 if check_dns(sys.argv[2]) else 1)
    check_placement()


if __name__ == "__main__":
    main()
