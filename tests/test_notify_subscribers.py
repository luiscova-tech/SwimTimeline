"""Subscriber notification matching, against the real WZAG psych sheet already in this repo.

The point of this feature is that it must NOT invent its own name matching: it calls
extract_psych_entries -- the website's own search entry point -- so it inherits the ambiguous-name
guard that stops "Stein" from silently merging the Steinbis children
(tests/test_ambiguous_partial_name.py). These tests pin that inheritance to real rows: a real
matching swimmer, a really-ambiguous surname, a really-absent swimmer, and the team filter against
the real team codes the sheet prints.

Nothing here touches the network. The one real end-to-end Resend send is a manual step documented
in docs/subscriber-notifications.md -- an automated test that emails a real person on every
`pytest` run would be its own bug.
"""

from pathlib import Path
import json
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from scripts.notify_subscribers import (
        NOTIFY_LOG_PATH,
        SUBSCRIBERS_PATH,
        NotifyError,
        SwimmerMatch,
        already_notified,
        build_email,
        declared_team_matches,
        load_notify_log,
        load_subscribers,
        match_swimmer,
        record_notification,
        send_via_resend,
    )
except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
    raise unittest.SkipTest(
        "notify_subscribers imports webapp.server, which needs Python 3.12 (stdlib cgi)"
    ) from exc

WZAG = ROOT / "meets/2026-wzag-championships-boise/input"
PSYCH = WZAG / "wzag psych sheet v3.pdf"


def write_json_file(payload) -> Path:
    path = Path(tempfile.mkdtemp()) / "subscribers.local.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class MatchingInheritsTheWebSearchTest(unittest.TestCase):
    """Real swimmers on the real WZAG psych sheet."""

    def test_real_swimmer_matches_with_her_real_event_count(self):
        match = match_swimmer(PSYCH, {"name": "Cova, Mila L", "team": ""})
        self.assertEqual(match.status, "matched")
        self.assertTrue(match.is_match)
        # Her six real individual events, the same count the web search returns for this sheet.
        self.assertEqual(match.event_count, 6)

    def test_ambiguous_surname_is_refused_never_emailed(self):
        # "Yang" is three real swimmers at WZAG (Richelle, Roddy, Yi) -- the exact class of bug the
        # guard exists for. A notifier must inherit the refusal, not pick one or merge them.
        match = match_swimmer(PSYCH, {"name": "Yang", "team": ""})
        self.assertEqual(match.status, "ambiguous")
        self.assertFalse(match.is_match)
        self.assertTrue(any("matches more than one swimmer" in note for note in match.notes))

    def test_unambiguous_partial_surname_still_resolves(self):
        # The other half of the guard: a partial name that IS unique must still match, or the
        # notifier would quietly stop working for anyone who configured a surname.
        match = match_swimmer(PSYCH, {"name": "Cova", "team": ""})
        self.assertEqual(match.status, "matched")
        self.assertEqual(match.event_count, 6)

    def test_absent_swimmer_is_a_clean_no_match(self):
        match = match_swimmer(PSYCH, {"name": "Notarealswimmer, Nobody", "team": ""})
        self.assertEqual(match.status, "no_match")
        self.assertFalse(match.is_match)


class DeclaredTeamOnlyNarrowsTest(unittest.TestCase):
    """The optional team is a precision filter -- it can never create a match."""

    def test_correct_bare_lsc_keeps_the_match(self):
        # WZAG is a zone meet: the sheet prints "AZ" for Arizona swimmers.
        match = match_swimmer(PSYCH, {"name": "Cova, Mila L", "team": "AZ"})
        self.assertEqual(match.status, "matched")
        self.assertEqual(match.event_count, 6)

    def test_wrong_team_suppresses_an_otherwise_real_match(self):
        match = match_swimmer(PSYCH, {"name": "Cova, Mila L", "team": "MAC-AZ"})
        self.assertEqual(match.status, "team_mismatch")
        self.assertFalse(match.is_match)

    def test_team_cannot_rescue_an_ambiguous_name(self):
        # Ambiguity is resolved before the team filter runs, deliberately: using a declared team to
        # disambiguate would be re-implementing the guard's job with weaker evidence.
        match = match_swimmer(PSYCH, {"name": "Yang", "team": "AZ"})
        self.assertEqual(match.status, "ambiguous")

    def test_team_cannot_create_a_match_for_an_absent_swimmer(self):
        match = match_swimmer(PSYCH, {"name": "Notarealswimmer, Nobody", "team": "AZ"})
        self.assertEqual(match.status, "no_match")


class SubscriberFileValidationTest(unittest.TestCase):
    """A malformed entry must fail loudly: silently skipping one would be indistinguishable from
    'nobody matched' in the summary, and that subscriber would never be told about their meet."""

    def test_valid_file_loads_and_normalizes_email_case(self):
        path = write_json_file({"subscribers": [{"email": "  Parent@Example.COM ", "swimmers": ["Cova, Mila L"]}]})
        subscribers = load_subscribers(path)
        self.assertEqual(subscribers[0]["email"], "parent@example.com")
        self.assertEqual(subscribers[0]["swimmers"], [{"name": "Cova, Mila L", "team": ""}])

    def test_state_is_accepted_as_an_alias_for_team(self):
        path = write_json_file({"subscribers": [{"email": "a@b.com", "swimmers": [{"name": "X", "state": "AZ"}]}]})
        self.assertEqual(load_subscribers(path)[0]["swimmers"][0]["team"], "AZ")

    def test_missing_file_is_a_clear_error(self):
        with self.assertRaises(NotifyError):
            load_subscribers(Path(tempfile.mkdtemp()) / "does-not-exist.json")

    def test_malformed_email_is_rejected(self):
        path = write_json_file({"subscribers": [{"email": "not-an-email", "swimmers": ["X"]}]})
        with self.assertRaises(NotifyError):
            load_subscribers(path)

    def test_duplicate_email_is_rejected(self):
        # Two entries for one address would double-email that person on the first run; the
        # already-notified log only stops the SECOND run.
        path = write_json_file({"subscribers": [
            {"email": "a@b.com", "swimmers": ["X"]},
            {"email": "A@B.com", "swimmers": ["Y"]},
        ]})
        with self.assertRaises(NotifyError):
            load_subscribers(path)

    def test_swimmer_without_a_name_is_rejected(self):
        path = write_json_file({"subscribers": [{"email": "a@b.com", "swimmers": [{"team": "AZ"}]}]})
        with self.assertRaises(NotifyError):
            load_subscribers(path)

    def test_empty_swimmer_list_is_rejected(self):
        path = write_json_file({"subscribers": [{"email": "a@b.com", "swimmers": []}]})
        with self.assertRaises(NotifyError):
            load_subscribers(path)


class NotifyLogTest(unittest.TestCase):
    def test_records_and_recognizes_a_sent_notification(self):
        path = Path(tempfile.mkdtemp()) / "notify_log.local.json"
        log = load_notify_log(path)  # missing file starts empty
        self.assertFalse(already_notified(log, "a@b.com", "meet-1"))

        record_notification(log, path, email="a@b.com", meet_id="meet-1", swimmers=["X"], resend_id="re_1")
        self.assertTrue(already_notified(log, "a@b.com", "meet-1"))
        # Written through to disk immediately, so a crash mid-run cannot cause a re-send.
        self.assertTrue(already_notified(load_notify_log(path), "a@b.com", "meet-1"))

    def test_is_scoped_per_meet_and_case_insensitive_on_email(self):
        path = Path(tempfile.mkdtemp()) / "notify_log.local.json"
        log = load_notify_log(path)
        record_notification(log, path, email="a@b.com", meet_id="meet-1", swimmers=["X"], resend_id="re_1")
        self.assertFalse(already_notified(log, "a@b.com", "meet-2"))  # a different meet still notifies
        self.assertTrue(already_notified(log, "A@B.COM", "meet-1"))   # same person, different casing

    def test_corrupt_log_refuses_rather_than_resetting(self):
        # Silently starting from an empty log would re-email everyone already notified.
        path = Path(tempfile.mkdtemp()) / "notify_log.local.json"
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(NotifyError):
            load_notify_log(path)


class EmailContentTest(unittest.TestCase):
    MEET = {
        "id": "2026-wzag-championships-boise",
        "name": "2026 Western Zone Age Group Championships",
        "dates": "2026-08-05 through 2026-08-08",
    }

    def test_body_names_the_swimmer_the_meet_and_the_dates(self):
        subject, body = build_email(self.MEET, [SwimmerMatch(name="Cova, Mila L", status="matched")])
        self.assertIn("Cova, Mila L", subject)
        self.assertIn("2026 Western Zone Age Group Championships", subject)
        self.assertIn("A new meet has been added for Cova, Mila L", body)
        self.assertIn("2026-08-05 through 2026-08-08", body)
        self.assertIn("https://swimtimeline.onrender.com", body)

    def test_body_always_carries_an_opt_out_line(self):
        _subject, body = build_email(self.MEET, [SwimmerMatch(name="Cova, Mila L", status="matched")])
        self.assertIn("reply to this email", body.lower())

    def test_uses_the_configured_name_not_the_sheets_marker_artifact(self):
        # The real WZAG sheet renders her as "Cova, Mila B" -- the trailing "B" is the
        # time-standard marker, not her name. A parent must not see that in their inbox.
        match = SwimmerMatch(name="Cova, Mila L", status="matched", matched_name="Cova, Mila B")
        _subject, body = build_email(self.MEET, [match])
        self.assertIn("Cova, Mila L", body)
        self.assertNotIn("Cova, Mila B", body)

    def test_several_swimmers_are_listed_in_one_email(self):
        matches = [
            SwimmerMatch(name="Cova, Mila L", status="matched"),
            SwimmerMatch(name="Stein, Layla", status="matched"),
        ]
        subject, body = build_email(self.MEET, matches)
        self.assertIn("Cova, Mila L and Stein, Layla", subject)
        self.assertIn("Cova, Mila L and Stein, Layla", body)


class AdversarialReviewRegressionTest(unittest.TestCase):
    """Each of these pins a defect an adversarial review found in the first working version.
    They are grouped here because none of them were caught by the tests written alongside the
    feature -- that is exactly why they are worth keeping."""

    def test_local_data_files_and_their_atomic_temp_siblings_are_gitignored(self):
        # write_json() writes "<path>.tmp" and then renames it. Ignoring only the exact .json names
        # left that temp file TRACKED, so a crash inside the write window -- the case the per-send
        # flush exists to survive -- could stage every real subscriber address on a `git add -A`.
        import subprocess

        for path in (SUBSCRIBERS_PATH, NOTIFY_LOG_PATH):
            for candidate in (path, path.with_suffix(path.suffix + ".tmp")):
                result = subprocess.run(
                    ["git", "check-ignore", "-q", str(candidate)], cwd=ROOT, capture_output=True
                )
                self.assertEqual(result.returncode, 0, f"{candidate.name} is NOT gitignored")

    def test_the_committed_example_template_stays_tracked(self):
        # The widened ignore globs must not also swallow the template that documents the format.
        import subprocess

        result = subprocess.run(
            ["git", "check-ignore", "-q", str(ROOT / "data/subscribers.local.example.json")],
            cwd=ROOT, capture_output=True,
        )
        self.assertNotEqual(result.returncode, 0, "the example template must remain tracked")

    def test_bare_lsc_matches_a_club_sheets_code(self):
        # The reported failure: a parent writing their LSC ("AZ") was rejected at every club meet,
        # where the sheet prints "MAC-AZ" -- so a real matched swimmer silently got no email.
        self.assertTrue(declared_team_matches("MAC-AZ", "AZ"))
        self.assertTrue(declared_team_matches("AZ", "AZ"))
        self.assertTrue(declared_team_matches("Arizona", "AZ"))  # zone sheet's display name

    def test_a_club_code_stays_strict_against_a_different_club(self):
        # The widening must apply ONLY to a bare LSC: two different Arizona clubs are not the same.
        self.assertFalse(declared_team_matches("GM-AZ", "MAC-AZ"))
        self.assertTrue(declared_team_matches("MAC-AZ", "MAC-AZ"))

    def test_a_different_lsc_never_matches(self):
        self.assertFalse(declared_team_matches("SNS", "AZ"))

    def test_a_read_timeout_is_a_single_failed_send_not_an_aborted_run(self):
        # urllib only wraps CONNECT-time errors in URLError; a timeout while reading the reply
        # arrives as a bare TimeoutError, which escaped both except clauses and killed the whole
        # batch mid-run -- abandoning every remaining subscriber with the send unrecorded.
        self.assertFalse(issubclass(TimeoutError, __import__("urllib.error", fromlist=["error"]).URLError))

        import urllib.request

        original = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(TimeoutError("timed out"))
        try:
            with self.assertRaises(NotifyError):  # NotifyError == "this one send failed", run continues
                send_via_resend("key-not-used", "a@b.com", "c@d.com", "s", "b")
        finally:
            urllib.request.urlopen = original

    def test_a_non_json_success_body_fails_only_that_send(self):
        import io
        import urllib.request

        class FakeResponse(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        original = urllib.request.urlopen
        urllib.request.urlopen = lambda *a, **k: FakeResponse(b"<html>gateway</html>")
        try:
            with self.assertRaises(NotifyError):
                send_via_resend("key-not-used", "a@b.com", "c@d.com", "s", "b")
        finally:
            urllib.request.urlopen = original

    def test_one_child_listed_twice_is_named_once_in_the_email(self):
        # "Cova" and "Cova, Mila L" are one child; the email greeted them as two.
        matches = [
            SwimmerMatch(name="Cova", status="matched", matched_name="Cova, Mila B"),
            SwimmerMatch(name="Cova, Mila L", status="matched", matched_name="Cova, Mila B"),
        ]
        deduped: list[SwimmerMatch] = []
        seen: set[str] = set()
        for match in matches:  # mirrors run()'s dedupe
            key = (match.matched_name or match.name).strip().casefold()
            if key not in seen:
                seen.add(key)
                deduped.append(match)
        self.assertEqual(len(deduped), 1)
        _subject, body = build_email({"name": "M", "dates": "d"}, deduped)
        self.assertNotIn(" and ", body.split("\n")[0])


if __name__ == "__main__":
    unittest.main()
