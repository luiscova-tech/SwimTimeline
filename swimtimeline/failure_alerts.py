"""Email the maintainer when the Officials badge-card feature hits a REAL failure.

The whole value of this module is the line it draws, so that line is drawn by TYPE, at the place
the error is raised, rather than by guessing afterwards from a message string:

  * InputError  -- somebody asked for something that isn't valid: no meet chosen, a session number
                   that isn't in the meet, copies out of range, an unknown layout, a non-PDF
                   upload, an expired upload token. The endpoint already answers these with a clear
                   400 and the person fixes their request. Nothing is broken, so nothing is sent.
  * anything else -- a real document failed to process, or something genuinely unexpected happened.
                   That is worth an email, including a plain ValueError out of the parsing layer
                   ("No sessions or events were found in that timeline PDF."), because a real meet
                   document that will not parse IS the bug worth knowing about.

Two consequences of classifying by type at the raise site rather than by inspecting messages:

  * The default direction is fail-safe. A new exception nobody anticipated -- a pypdf error on a
    corrupt file, a KeyError on a malformed one -- notifies, because it is not an InputError. To
    make something quiet, you have to say so explicitly.
  * The HTTP behaviour does not change at all. InputError subclasses ValueError, so every existing
    `except Exception`/`except ValueError` catches it exactly as before and the same 400s come back
    with the same messages. Only this alerting layer reads the type.

Sending is deliberately best-effort and utterly unable to make a bad situation worse: notify() can
never raise, and a missing RESEND_API_KEY (which is how this runs until the key is set on Render)
or a Resend outage degrades to a local log line. One email per failure, no batching.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
import traceback

from .resend_email import API_KEY_ENV, DEFAULT_FROM, FROM_ENV, ResendError, send_via_resend

# Where alerts go. Resend's sandbox sender (DEFAULT_FROM) delivers ONLY to the Resend account
# owner's own address, and for this project that is exactly this address -- see
# docs/subscriber-notifications.md -- so these alerts work with no domain verification at all. The
# separate subscriber-notification feature still needs a verified domain precisely because it has
# to reach OTHER people's inboxes; this one does not.
ALERT_TO = "swimtimelineapp@gmail.com"

# Params worth repeating back, and nothing else. No uploaded file contents, no file bytes, no
# psych-sheet rows -- just enough to retype the request that broke.
REPORTED_PARAMS = ("meet_id", "token", "session", "layout", "copies", "highlighted_only", "swimmer_names")
MAX_PARAM_CHARS = 200
MAX_MESSAGE_CHARS = 1000
MAX_TRACEBACK_CHARS = 3000


class InputError(ValueError):
    """A request that was never going to work: the caller has to change it, nothing is broken.

    Subclasses ValueError on purpose so that raising it in place of a plain ValueError changes no
    existing handler's behaviour -- only should_notify_failure() can tell the difference.
    """


def should_notify_failure(exc: BaseException) -> bool:
    """True when this exception is a real failure worth an email.

    Pure and dependency-free so the notify-vs-don't decision is testable on its own, with no
    network, no environment and no Resend involved -- see tests/test_failure_alerts.py, which feeds
    it the real error cases the officials test suite already produces.
    """
    return not isinstance(exc, InputError)


@dataclass
class FailureReport:
    """One failure, in the shape the email needs."""

    endpoint: str
    exc: BaseException
    params: dict = field(default_factory=dict)
    # Where in the pipeline it happened, when the caller knows ("rendering a card", "matching
    # swimmers against the psych sheet"). Free text, purely to speed up reading the email.
    stage: str = ""
    occurred_at: datetime | None = None

    def timestamp(self) -> str:
        moment = self.occurred_at or datetime.now(timezone.utc)
        return moment.strftime("%Y-%m-%d %H:%M:%S UTC")


def _clip(value: str, limit: int) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else f"{text[:limit]}... [truncated]"


def format_failure_email(report: FailureReport) -> tuple[str, str]:
    """(subject, body) for one failure: when, where, what was asked for, and what broke."""
    exc_type = type(report.exc).__name__
    subject = f"[SwimTimeline] {report.endpoint} failed: {exc_type}"

    lines = [
        f"When:     {report.timestamp()}",
        f"Endpoint: {report.endpoint}",
    ]
    if report.stage:
        lines.append(f"Stage:    {report.stage}")
    lines.append(f"Error:    {exc_type}: {_clip(str(report.exc), MAX_MESSAGE_CHARS)}")
    lines.append("")
    lines.append("Request:")
    reported_any = False
    for key in REPORTED_PARAMS:
        if key not in report.params:
            continue
        value = report.params[key]
        if value in (None, "", [], ()):
            continue
        if isinstance(value, (list, tuple)):
            value = ", ".join(str(item) for item in value)
        lines.append(f"  {key} = {_clip(value, MAX_PARAM_CHARS)}")
        reported_any = True
    if not reported_any:
        lines.append("  (no reportable parameters)")

    detail = "".join(
        traceback.format_exception(type(report.exc), report.exc, report.exc.__traceback__)
    )
    if detail.strip():
        lines.extend(["", "Traceback:", _clip_block(detail, MAX_TRACEBACK_CHARS)])
    return subject, "\n".join(lines)


def _clip_block(text: str, limit: int) -> str:
    """Like _clip but keeps line breaks -- a traceback is unreadable collapsed onto one line."""
    if len(text) <= limit:
        return text.rstrip()
    return f"{text[:limit].rstrip()}\n... [truncated]"


def notify_failure(report: FailureReport, sender=send_via_resend, logger=print) -> str | None:
    """Email one failure. Returns Resend's message id, or None if nothing was sent.

    NEVER raises. This runs while an HTTP response is already being produced for whoever hit the
    failure, and a Resend outage must not turn one real bug into two -- so every path out of here,
    including a missing API key and an unexpected error inside the sender itself, degrades to a log
    line and None.
    """
    try:
        if not should_notify_failure(report.exc):
            return None
        api_key = os.environ.get(API_KEY_ENV, "").strip()
        if not api_key:
            # Expected until the key is set on Render: log locally so the failure is still visible
            # in the service logs, and carry on.
            logger(
                f"[alert-skipped] {API_KEY_ENV} unset; not emailing "
                f"{type(report.exc).__name__} from {report.endpoint}: {_clip(str(report.exc), 200)}"
            )
            return None
        from_address = os.environ.get(FROM_ENV, "").strip() or DEFAULT_FROM
        subject, body = format_failure_email(report)
        message_id = sender(api_key, from_address, ALERT_TO, subject, body)
        logger(f"[alert-sent] {subject} -> {ALERT_TO} ({message_id})")
        return message_id
    except ResendError as exc:
        logger(f"[alert-failed] could not email the failure report: {exc}")
        return None
    except Exception as exc:  # noqa: BLE001 - a broken alerter must never break the response
        logger(f"[alert-failed] unexpected error while reporting a failure: {exc!r}")
        return None
