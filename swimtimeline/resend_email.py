"""One place that knows how to hand an email to Resend.

Extracted from scripts/notify_subscribers.py so the live web service can reuse it for failure
alerts without importing that script -- which is not just a style preference: notify_subscribers
imports FROM webapp.server, so webapp.server importing back from it would be a circular import.
The hard-won details below (the User-Agent header, the OSError branch) came out of real sends
failing, so there must be exactly one copy of them.

The API key is read from the environment by callers and passed in; it is never stored here, never
written to any file in this repo, and never echoed into an error message.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from .site import SITE_URL

RESEND_ENDPOINT = "https://api.resend.com/emails"
API_KEY_ENV = "RESEND_API_KEY"
FROM_ENV = "NOTIFY_FROM_EMAIL"
# Resend's shared sandbox sender works with no domain verification, but it can only deliver to the
# Resend account owner's own address. Set NOTIFY_FROM_EMAIL to a verified-domain sender before
# emailing anyone else -- see docs/subscriber-notifications.md.
DEFAULT_FROM = "SwimTimeline <onboarding@resend.dev>"


class ResendError(Exception):
    """One send failed. Callers decide whether that stops a run or is merely logged."""


def send_via_resend(api_key: str, from_address: str, to_address: str, subject: str, body: str) -> str:
    """POST one email to Resend with the stdlib. Returns Resend's message id.

    urllib rather than requests on purpose: this repo ships exactly one pip dependency
    (requirements.txt) and a notifier is not a good reason to add a second.
    """
    payload = json.dumps({"from": from_address, "to": [to_address], "subject": subject, "text": body}).encode("utf-8")
    request = urllib.request.Request(
        RESEND_ENDPOINT,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # Required in practice, not politeness: Resend sits behind Cloudflare, which rejects
            # urllib's default "Python-urllib/3.12" agent outright with 403 error code 1010
            # ("banned browser signature") before the request ever reaches the API. That looks
            # exactly like an auth failure in the logs. Found by an actual send -- no mocked test
            # could have caught it.
            "User-Agent": f"SwimTimeline-Notifier/1.0 (+{SITE_URL})",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        # Deliberately reports status + Resend's own message and never the key or the header.
        raise ResendError(f"Resend rejected the send ({exc.code} {exc.reason}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise ResendError(f"Could not reach Resend: {exc.reason}") from exc
    except OSError as exc:
        # urllib only wraps CONNECT-time errors in URLError. A timeout or a dropped connection
        # while reading the response surfaces as a bare TimeoutError/OSError from getresponse(),
        # which is NOT a URLError -- so without this it escaped the whole run, abandoning every
        # remaining subscriber. Caught as ResendError so the caller treats it as one failed send.
        raise ResendError(f"Lost the connection to Resend while reading its reply: {exc!r}") from exc

    # A 2xx whose body isn't the JSON object we expect must fail this ONE send, not the batch.
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise ResendError(f"Resend returned a non-JSON success body: {raw[:200]!r} ({exc})") from exc
    if not isinstance(parsed, dict):
        raise ResendError(f"Resend returned an unexpected success body: {raw[:200]!r}")
    message_id = str(parsed.get("id") or "")
    if not message_id:
        raise ResendError(f"Resend accepted the request but returned no message id: {parsed!r}")
    return message_id
