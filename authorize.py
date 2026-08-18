#!/usr/bin/env python3
"""One-time OAuth for a sender's mailbox.

Usage:  python3 authorize.py <sender_key>

Opens a browser; the sender approves gmail.send + gmail.readonly for their own
mailbox. The token lands in secrets/token_<sender>.json (chmod 600, gitignored)
and is individually revocable at myaccount.google.com -> Security.

Remote teammate? See SKILL.md Phase 4: they open the printed URL, approve, land
on a dead localhost page, and send you the full address-bar URL; you paste it
into a browser on THIS machine while this script is still waiting.

The final step asserts the authorized mailbox matches senders.json, so an
approval done from the wrong Google account fails HERE, not at send time.
"""
import os
import sys

from gmail_common import (
    CLIENT_SECRET, SCOPES, assert_identity, build_service, die,
    load_senders, save_token,
)


def main():
    if len(sys.argv) != 2:
        die(f"usage: python3 authorize.py <{'|'.join(load_senders())}>")
    sender_key = sys.argv[1].lower()
    senders = load_senders()
    if sender_key not in senders:
        die(f"unknown sender '{sender_key}' - senders.json has: {', '.join(senders)}")
    if not os.path.exists(CLIENT_SECRET):
        die(
            "secrets/client_secret.json missing. Create a Desktop-app OAuth client "
            "in the GCP console (consent screen: Internal), download its JSON there."
        )

    expected = senders[sender_key]["email"]
    print(f"Authorizing '{sender_key}' - approve as {expected} in the browser that opens.")

    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
    creds = flow.run_local_server(port=0)
    save_token(sender_key, creds)

    actual = assert_identity(build_service(creds), sender_key, expected)
    print(f"OK: token for '{sender_key}' verified as {actual}, saved chmod 600.")


if __name__ == "__main__":
    main()
