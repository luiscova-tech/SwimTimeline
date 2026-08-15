#!/usr/bin/env python3
"""Email subscribers when a meet they follow gets hosted documents. Local-only, manual trigger.

Run from a local checkout, never from the deployed web service, for two reasons:

  * data/subscribers.local.json holds real parent emails and children's names. An email address
    cannot be hashed and stay useful for sending, so -- unlike data/internal_relay_sources/, which
    stores salted hashes -- the whole file has to stay off this public repo. It is gitignored.
  * Render's web-service disk is ephemeral, so a server-side subscriber list or "already notified"
    log would be silently erased by the next deploy, and everyone would be re-emailed.

Matching reuses extract_psych_entries() -- the SAME entry point the website's swimmer search calls,
including its ambiguous-name guard. That guard is the reason "Stein" no longer silently merges the
Steinbis children (see tests/test_ambiguous_partial_name.py); a notifier with its own simpler
matcher would reintroduce exactly that class of bug, so nothing here re-implements name matching.
An ambiguous name is NEVER emailed about -- it is reported to you to fix in the subscriber file.

Usage:
    RESEND_API_KEY=re_... ./.venv312/bin/python scripts/notify_subscribers.py <meet_id>
    ./.venv312/bin/python scripts/notify_subscribers.py <meet_id> --dry-run   # no key needed
    RESEND_API_KEY=re_... ./.venv312/bin/python scripts/notify_subscribers.py <meet_id> --force

See docs/subscriber-notifications.md.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from swimtimeline.extract import (  # noqa: E402  (path setup must precede the import)
    AMBIGUOUS_NAME_MARKER,
    extract_psych_entries,
    lsc_from_team_code,
    relay_team_matches_swimmer,
)
from webapp.server import (  # noqa: E402
    public_current_meet,
    resolve_current_meet,
    resolve_current_meet_documents,
    write_json,
)

SUBSCRIBERS_PATH = ROOT / "data" / "subscribers.local.json"
NOTIFY_LOG_PATH = ROOT / "data" / "notify_log.local.json"
RESEND_ENDPOINT = "https://api.resend.com/emails"
SITE_URL = "https://swimtimeline.onrender.com"
# Resend's shared sandbox sender works with no domain verification, but it can only deliver to the
# Resend account owner's own address. Set NOTIFY_FROM_EMAIL to a verified-domain sender before
# emailing anyone else -- see docs/subscriber-notifications.md.
DEFAULT_FROM = "SwimTimeline <onboarding@resend.dev>"
API_KEY_ENV = "RESEND_API_KEY"
FROM_ENV = "NOTIFY_FROM_EMAIL"


class NotifyError(Exception):
    """Anything that should stop the run with a clear message rather than a traceback."""


@dataclass
class SwimmerMatch:
    """One configured swimmer resolved against one meet's psych sheet."""

    name: str
    status: str  # "matched" | "ambiguous" | "team_mismatch" | "no_match"
    matched_name: str = ""
    event_count: int = 0
    team: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def is_match(self) -> bool:
        return self.status == "matched"


def display_path(path: Path) -> str:
    """A short repo-relative path for messages, falling back to the full path.

    relative_to() RAISES for anything outside the repo, so calling it bare inside an error message
    replaced the real error with an unrelated ValueError -- the failure handler itself failing.
    """
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def declared_team_matches(sheet_team: str, declared_team: str) -> bool:
    """Whether a HUMAN-TYPED team from the subscriber file matches a psych sheet's team column.

    relay_team_matches_swimmer() does the hard part, but its `swimmer_team` argument is documented
    as the swimmer's OWN code parsed from the same sheet -- so for anything that isn't a bare LSC it
    demands an exact club match. Feeding it hand-typed input made a perfectly reasonable entry fail:
    a parent who writes their LSC ("AZ", which the file explicitly invites via the `state` alias)
    was rejected at every CLUB meet, where the sheet prints "MAC-AZ" -- 3 of the 5 hosted meets. The
    result was silence: a real, matched swimmer simply never got an email.

    So a BARE LSC additionally matches any team in that LSC, which is what a parent means by it and
    what the site's own LSC auto-detection already concludes from "MAC-AZ". A full club code stays
    strict: declaring "MAC-AZ" must never match "GM-AZ", a different Arizona club.
    """
    sheet_team = (sheet_team or "").strip()
    declared_team = (declared_team or "").strip()
    if not sheet_team or not declared_team:
        return False
    if relay_team_matches_swimmer(sheet_team, declared_team):
        return True
    declared_lsc = lsc_from_team_code(declared_team)
    if not declared_lsc or declared_team.upper() != declared_lsc:
        return False  # a club code, not a bare LSC -- no widening
    return lsc_from_team_code(sheet_team) == declared_lsc


def normalize_email(value: str) -> str:
    return str(value or "").strip().lower()


def load_subscribers(path: Path) -> list[dict]:
    """Read and validate the local subscriber file.

    Validation is strict and fails the whole run: a typo'd entry that silently parsed as "no
    swimmers" would look identical to "nobody matched" in the summary, and the operator would
    never learn their subscriber was skipped.
    """
    if not path.is_file():
        raise NotifyError(
            f"No subscriber file at {display_path(path)}.\n"
            f"Copy data/subscribers.local.example.json to that path and add real subscribers "
            f"(the real file is gitignored)."
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise NotifyError(f"{display_path(path)} is not valid JSON: {exc}") from exc

    raw = data.get("subscribers") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        raise NotifyError(
            f"{display_path(path)} must hold a list under a 'subscribers' key "
            f"(see data/subscribers.local.example.json)."
        )

    subscribers: list[dict] = []
    seen_emails: set[str] = set()
    for index, entry in enumerate(raw, start=1):
        where = f"subscriber #{index}"
        if not isinstance(entry, dict):
            raise NotifyError(f"{where}: expected an object, got {type(entry).__name__}.")
        email = normalize_email(entry.get("email"))
        if not email or "@" not in email:
            raise NotifyError(f"{where}: missing or malformed 'email' ({entry.get('email')!r}).")
        if email in seen_emails:
            # Two entries for one address would send that person two emails for the same meet on
            # the first run (the log only stops the SECOND run), so refuse instead of merging.
            raise NotifyError(f"{where}: duplicate email {email!r} -- merge its swimmers into one entry.")
        seen_emails.add(email)

        swimmers_raw = entry.get("swimmers")
        if not isinstance(swimmers_raw, list) or not swimmers_raw:
            raise NotifyError(f"{where} ({email}): 'swimmers' must be a non-empty list.")
        swimmers: list[dict] = []
        for swimmer in swimmers_raw:
            if isinstance(swimmer, str):  # tolerate the shorthand ["Cova, Mila L"]
                swimmer = {"name": swimmer}
            if not isinstance(swimmer, dict):
                raise NotifyError(f"{where} ({email}): each swimmer must be an object or a name string.")
            name = str(swimmer.get("name") or "").strip()
            if not name:
                raise NotifyError(f"{where} ({email}): a swimmer is missing 'name'.")
            # "state" is accepted as an alias for "team": a parent thinks in LSC ("AZ"), which is
            # exactly what a zone psych sheet prints as the team column.
            team = str(swimmer.get("team") or swimmer.get("state") or "").strip()
            swimmers.append({"name": name, "team": team})
        subscribers.append({"email": email, "swimmers": swimmers})
    return subscribers


def load_notify_log(path: Path) -> dict:
    if not path.is_file():
        return {"notified": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        # Never silently reset a corrupt log -- that would re-email everyone.
        raise NotifyError(
            f"{display_path(path)} is not valid JSON: {exc}\n"
            f"Fix or move it by hand; refusing to continue, because starting from an empty log "
            f"would re-notify every subscriber."
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("notified"), list):
        raise NotifyError(f"{display_path(path)} must be an object with a 'notified' list.")
    return data


def already_notified(log: dict, email: str, meet_id: str) -> bool:
    key = (normalize_email(email), meet_id)
    return any(
        (normalize_email(row.get("email")), str(row.get("meet_id"))) == key
        for row in log.get("notified", [])
        if isinstance(row, dict)
    )


def record_notification(log: dict, path: Path, *, email: str, meet_id: str, swimmers: list[str], resend_id: str) -> None:
    """Append one send to the log and flush it to disk IMMEDIATELY.

    Written per-send rather than once at the end so that a crash, a network hang, or a Ctrl-C
    halfway through a run can only ever under-record, never re-send: every address already emailed
    is durably on disk before the next one is attempted.
    """
    log.setdefault("notified", []).append(
        {
            "email": normalize_email(email),
            "meet_id": meet_id,
            "swimmers": swimmers,
            "resend_id": resend_id,
            "sent_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    )
    write_json(path, log)  # atomic temp-then-replace, shared with the web app


def match_swimmer(psych_path: Path, swimmer: dict) -> SwimmerMatch:
    """Resolve one configured swimmer against the meet's psych sheet.

    Delegates entirely to extract_psych_entries -- the website's own search path -- and only
    classifies what it returns:
      * entries          -> matched (a "high-confidence match" warning means it resolved fuzzily)
      * ambiguity warning -> ambiguous; never emailed, reported for you to make the name specific
      * neither          -> no match
    An optional declared team then only ever NARROWS a match, using the same team matcher the relay
    code uses; it can never turn a non-match into a match.
    """
    name = swimmer["name"]
    entries, _page_counts, warnings = extract_psych_entries(psych_path, name)

    if any(AMBIGUOUS_NAME_MARKER in warning for warning in warnings):
        return SwimmerMatch(name=name, status="ambiguous", notes=list(warnings))
    if not entries:
        return SwimmerMatch(name=name, status="no_match", notes=list(warnings))

    declared_team = swimmer.get("team") or ""
    if declared_team:
        kept = [entry for entry in entries if declared_team_matches(entry.team, declared_team)]
        if not kept:
            sheet_teams = sorted({entry.team for entry in entries if entry.team})
            return SwimmerMatch(
                name=name,
                status="team_mismatch",
                matched_name=next((e.matched_name for e in entries if e.matched_name), name),
                team=declared_team,
                notes=[
                    f"name matched, but declared team {declared_team!r} does not match the sheet's "
                    f"team ({', '.join(sheet_teams) or 'unknown'}) -- not emailed"
                ],
            )
        entries = kept

    return SwimmerMatch(
        name=name,
        status="matched",
        matched_name=next((e.matched_name for e in entries if e.matched_name), name),
        event_count=len(entries),
        team=next((e.team for e in entries if e.team), ""),
        notes=list(warnings),
    )


def meet_label(meet: dict) -> str:
    return str(meet.get("name") or meet.get("short_name") or meet.get("id") or "a meet")


def meet_dates(meet: dict) -> str:
    return str(meet.get("dates") or "").strip()


def build_email(meet: dict, matches: list[SwimmerMatch]) -> tuple[str, str]:
    """The subject and plain-text body for one subscriber. Plain text only -- it renders everywhere,
    can't leak a tracking pixel, and keeps this deliberately small feature small."""
    # The CONFIGURED name, not the sheet's matched_name. A psych sheet's name column can carry
    # parsing artifacts a parent would find odd in their own inbox -- the real WZAG sheet renders
    # Mila Cova as "Cova, Mila B", where the trailing "B" is the time-standard marker sitting next
    # to the name (see the marker handling in extract.py). The operator still sees the resolved
    # matched_name in the terminal, which is where verifying the match actually matters.
    names = [match.name for match in matches]
    name_list = names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"
    label = meet_label(meet)
    dates = meet_dates(meet)
    subject = f"New meet on SwimTimeline for {name_list}: {label}"
    when = f" ({dates})" if dates else ""
    body = (
        f"A new meet has been added for {name_list}: {label}{when}.\n"
        f"\n"
        f"Visit SwimTimeline and search their name to download a calendar:\n"
        f"{SITE_URL}\n"
        f"\n"
        f"Heat sheets and timelines are sometimes updated during a meet. Searching again, or "
        f"subscribing to the auto-updating calendar on the results page, picks up the latest "
        f"version.\n"
        f"\n"
        f"--\n"
        f"You're getting this because you asked to hear when a new meet is posted. "
        f"To stop receiving these, reply to this email and I'll remove you.\n"
    )
    return subject, body


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
            "User-Agent": "SwimTimeline-Notifier/1.0 (+https://swimtimeline.onrender.com)",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        # Deliberately reports status + Resend's own message and never the key or the header.
        raise NotifyError(f"Resend rejected the send ({exc.code} {exc.reason}): {detail}") from exc
    except urllib.error.URLError as exc:
        raise NotifyError(f"Could not reach Resend: {exc.reason}") from exc
    except OSError as exc:
        # urllib only wraps CONNECT-time errors in URLError. A timeout or a dropped connection
        # while reading the response surfaces as a bare TimeoutError/OSError from getresponse(),
        # which is NOT a URLError -- so without this it escaped the whole run, abandoning every
        # remaining subscriber. Caught as NotifyError so the caller treats it as one failed send.
        raise NotifyError(f"Lost the connection to Resend while reading its reply: {exc!r}") from exc

    # A 2xx whose body isn't the JSON object we expect must fail this ONE send, not the batch.
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise NotifyError(f"Resend returned a non-JSON success body: {raw[:200]!r} ({exc})") from exc
    if not isinstance(parsed, dict):
        raise NotifyError(f"Resend returned an unexpected success body: {raw[:200]!r}")
    message_id = str(parsed.get("id") or "")
    if not message_id:
        raise NotifyError(f"Resend accepted the request but returned no message id: {parsed!r}")
    return message_id


def resolve_meet_and_psych(meet_id: str) -> tuple[dict, Path]:
    """The meet record and its psych sheet, resolved exactly as handle_analyze_current does."""
    try:
        meet = resolve_current_meet(meet_id)
    except ValueError as exc:
        raise NotifyError(str(exc)) from exc
    if not public_current_meet(meet).get("is_ready_for_lookup"):
        # Emailing "your meet is ready" for a meet the site cannot yet search would send families
        # to a dead end, so this is a hard stop rather than a warning.
        raise NotifyError(
            f"Meet {meet_id!r} is not ready for lookup yet (status "
            f"{meet.get('status') or 'unknown'!r}); its documents are incomplete, so searching it "
            f"on the site would fail. Not notifying anyone."
        )
    try:
        psych_path = resolve_current_meet_documents(meet)["psych_path"]
    except ValueError as exc:
        raise NotifyError(f"Could not resolve this meet's documents: {exc}") from exc
    if psych_path is None:  # defensive: readiness above already requires a psych sheet
        raise NotifyError(f"Meet {meet_id!r} has no psych/heat sheet on file.")
    return meet, psych_path


def run(meet_id: str, *, force: bool, dry_run: bool, out=sys.stdout) -> int:
    meet, psych_path = resolve_meet_and_psych(meet_id)
    subscribers = load_subscribers(SUBSCRIBERS_PATH)
    log = load_notify_log(NOTIFY_LOG_PATH)

    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not dry_run and not api_key:
        raise NotifyError(
            f"{API_KEY_ENV} is not set. Export your Resend API key (never commit it), or re-run "
            f"with --dry-run to preview without sending."
        )
    from_address = os.environ.get(FROM_ENV, "").strip() or DEFAULT_FROM

    print(f"Meet:    {meet_label(meet)} ({meet_id})", file=out)
    print(f"Dates:   {meet_dates(meet) or 'unknown'}", file=out)
    print(f"Psych:   {display_path(psych_path)}", file=out)
    print(f"Mode:    {'DRY RUN (no email will be sent)' if dry_run else 'live send'}"
          f"{' [--force: ignoring the already-notified log]' if force else ''}", file=out)
    print(f"Checking {len(subscribers)} subscriber(s)...\n", file=out)

    checked = matched_subscribers = sent = skipped_already = failed = 0
    ambiguous_rows: list[tuple[str, SwimmerMatch]] = []
    team_mismatch_rows: list[tuple[str, SwimmerMatch]] = []

    for subscriber in subscribers:
        checked += 1
        email = subscriber["email"]
        matches = [match_swimmer(psych_path, swimmer) for swimmer in subscriber["swimmers"]]
        for match in matches:
            if match.status == "ambiguous":
                ambiguous_rows.append((email, match))
            elif match.status == "team_mismatch":
                team_mismatch_rows.append((email, match))

        # Dedupe by the swimmer the sheet actually RESOLVED to, not by the configured string: a
        # parent who lists both "Cova" and "Cova, Mila L" means one child, and without this the
        # email greeted them as two ("...added for Cova and Cova, Mila L").
        hits: list[SwimmerMatch] = []
        seen_swimmers: set[str] = set()
        for match in matches:
            if not match.is_match:
                continue
            key = (match.matched_name or match.name).strip().casefold()
            if key in seen_swimmers:
                continue
            seen_swimmers.add(key)
            hits.append(match)

        if not hits:
            print(f"  - {email}: no match", file=out)
            continue
        matched_subscribers += 1

        detail = ", ".join(
            f"{m.matched_name or m.name} ({m.event_count} event{'s' if m.event_count != 1 else ''})"
            for m in hits
        )
        # A fuzzy resolution still sends, but the operator should see WHICH name it landed on --
        # extract_psych_entries returns that as a "high-confidence match" warning, and it was
        # being collected and then silently dropped.
        for match in hits:
            for note in match.notes:
                print(f"      note: {match.name!r} -> {note}", file=out)
        if not force and already_notified(log, email, meet_id):
            skipped_already += 1
            print(f"  - {email}: MATCH [{detail}] -- already notified for this meet, skipping", file=out)
            continue

        subject, body = build_email(meet, hits)
        if dry_run:
            print(f"  - {email}: MATCH [{detail}] -- would send:", file=out)
            print(f"      subject: {subject}", file=out)
            for line in body.rstrip("\n").splitlines():
                print(f"      | {line}", file=out)
            continue

        try:
            message_id = send_via_resend(api_key, from_address, email, subject, body)
        except NotifyError as exc:
            # One bad address must not abandon the rest of the list.
            failed += 1
            print(f"  - {email}: MATCH [{detail}] -- SEND FAILED: {exc}", file=out)
            continue
        record_notification(
            log, NOTIFY_LOG_PATH,
            email=email, meet_id=meet_id,
            swimmers=[m.matched_name or m.name for m in hits], resend_id=message_id,
        )
        sent += 1
        print(f"  - {email}: MATCH [{detail}] -- sent (resend id {message_id})", file=out)

    print("", file=out)
    print("Summary", file=out)
    print(f"  subscribers checked:      {checked}", file=out)
    print(f"  subscribers with a match: {matched_subscribers}", file=out)
    print(f"  emails sent:              {sent}{' (dry run: 0 by design)' if dry_run else ''}", file=out)
    print(f"  skipped (already sent):   {skipped_already}", file=out)
    if failed:
        print(f"  failed sends:             {failed}", file=out)

    if ambiguous_rows:
        print(f"\n  Ambiguous names -- NOT emailed, fix these in data/subscribers.local.json:", file=out)
        for email, match in ambiguous_rows:
            print(f"    * {email}: {match.name!r} -> {' '.join(match.notes)}", file=out)
    if team_mismatch_rows:
        print(f"\n  Team mismatches -- NOT emailed:", file=out)
        for email, match in team_mismatch_rows:
            print(f"    * {email}: {match.name!r} -> {' '.join(match.notes)}", file=out)

    # Non-zero only for genuine send failures; ambiguity is a data-quality report, not a run failure.
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Email subscribers about a newly hosted meet (local-only, manual trigger).",
    )
    parser.add_argument("meet_id", help="A meet id from data/current_meets.json.")
    parser.add_argument(
        "--force", action="store_true",
        help="Send even to subscribers already notified about this meet (default: skip them).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Match and print the exact emails without sending. No API key required.",
    )
    args = parser.parse_args(argv)
    try:
        return run(args.meet_id, force=args.force, dry_run=args.dry_run)
    except NotifyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
