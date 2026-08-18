#!/usr/bin/env python3
"""Shared plumbing: paths, sender registry, OAuth tokens, Gmail service,
identity assertion, and the send log.

The send log (out/send_log.jsonl) is the join key of the whole system:
gmail_thread_id <-> crm_record_id. gmail_send.py appends to it,
gmail_watch.py reads it, and nothing else may write it.

Thread lifecycle lives NEXT TO the log, not in it (the log is append-only
audit; state mutates). out/thread_status.json maps gmail_thread_id ->
{state, last_touch, followup_stage}. States: "open" (watch it), "replied",
"not_interested", "bounced", "closed_quiet" (no answer for CLOSE_AFTER_DAYS).
Anything not "open" is skipped by the watcher and the follow-up sender, so
their cost tracks ACTIVE conversations, not all history.
"""
import json
import os
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
SECRETS = os.path.join(HERE, "secrets")
OUT = os.path.join(HERE, "out")
CLIENT_SECRET = os.path.join(SECRETS, "client_secret.json")
SEND_LOG = os.path.join(OUT, "send_log.jsonl")
THREAD_STATUS = os.path.join(OUT, "thread_status.json")

# Quiet threads close after this many days without an inbound message.
CLOSE_AFTER_DAYS = int(os.environ.get("GTM_MAIL_CLOSE_DAYS", "30"))

SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
]


def die(msg):
    """All failures are loud. Never return a half-truth."""
    raise SystemExit(f"gtm-mail FATAL: {msg}")


def load_senders():
    path = os.path.join(HERE, "senders.json")
    if not os.path.exists(path):
        die("senders.json missing - copy senders.example.json and fill in your team")
    with open(path) as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def token_path(sender_key):
    return os.path.join(SECRETS, f"token_{sender_key}.json")


def load_credentials(sender_key):
    """Load + refresh a stored OAuth token. Fails loud if missing or dead."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = token_path(sender_key)
    if not os.path.exists(path):
        die(f"no token for '{sender_key}' - run: python3 authorize.py {sender_key}")
    creds = Credentials.from_authorized_user_file(path, SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                die(f"token refresh failed for '{sender_key}' ({e}) - re-run authorize.py {sender_key}")
            save_token(sender_key, creds)
        else:
            die(f"token for '{sender_key}' is invalid - re-run authorize.py {sender_key}")
    return creds


def save_token(sender_key, creds):
    os.makedirs(SECRETS, exist_ok=True)
    path = token_path(sender_key)
    with open(path, "w") as f:
        f.write(creds.to_json())
    os.chmod(path, 0o600)


def build_service(creds):
    from googleapiclient.discovery import build
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def assert_identity(service, sender_key, expected_email):
    """The token must belong to the mailbox it claims to be. Hard exit if not,
    BEFORE anything is sent - a mixed-up token would send as the wrong person."""
    profile = service.users().getProfile(userId="me").execute()
    actual = (profile.get("emailAddress") or "").lower()
    if actual != expected_email.lower():
        die(
            f"token for '{sender_key}' belongs to {actual}, but senders.json says "
            f"{expected_email}. Fix senders.json or re-run authorize.py {sender_key}."
        )
    return actual


def read_send_log():
    if not os.path.exists(SEND_LOG):
        return []
    with open(SEND_LOG) as f:
        return [json.loads(line) for line in f if line.strip()]


def append_send_log(record):
    os.makedirs(OUT, exist_ok=True)
    with open(SEND_LOG, "a") as f:
        f.write(json.dumps(record) + "\n")


def load_thread_status():
    if not os.path.exists(THREAD_STATUS):
        return {}
    with open(THREAD_STATUS) as f:
        return json.load(f)


def save_thread_status(status):
    os.makedirs(OUT, exist_ok=True)
    with open(THREAD_STATUS, "w") as f:
        json.dump(status, f, indent=1)


def ensure_thread_status(status, record):
    """Backfill: a logged send with no status entry becomes an open thread
    (covers logs from before lifecycles existed, and crash-between-writes)."""
    return status.setdefault(record["gmail_thread_id"], {
        "state": "open",
        "last_touch": record["sent_at"],
        "followup_stage": 0,
    })


def days_since(iso_ts):
    then = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).days
