#!/usr/bin/env python3
"""Shared plumbing: paths, sender registry, OAuth tokens, Gmail service,
identity assertion, and the send log.

The send log (out/send_log.jsonl) is the join key of the whole system:
gmail_thread_id <-> crm_record_id. gmail_send.py appends to it,
gmail_watch.py reads it, and nothing else may write it.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SECRETS = os.path.join(HERE, "secrets")
OUT = os.path.join(HERE, "out")
CLIENT_SECRET = os.path.join(SECRETS, "client_secret.json")
SEND_LOG = os.path.join(OUT, "send_log.jsonl")

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
