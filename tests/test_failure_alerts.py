"""Which Officials failures email the maintainer, and which stay quiet.

The split is the whole point of the feature, so it is tested on its own here: should_notify_failure()
is pure, so every case below runs with no network, no RESEND_API_KEY and no Resend involved.

Two kinds of case are used deliberately:
  * REAL errors raised by the real code paths -- each one produced by actually calling the officials
    endpoints/library the way tests/test_officials_badge_downloads.py does, rather than by
    hand-constructing an exception that merely looks like what the server raises.
  * The exception TYPES a malformed document could realistically throw (pypdf errors, KeyError,
    TypeError), to pin the fail-safe direction: anything not explicitly classified as input error
    notifies.

Sending itself is covered separately (and still without touching the network) by injecting a fake
sender, since the requirement is that a Resend outage or a missing key can never affect the
response already going back to whoever hit the failure.
"""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from swimtimeline.badges import (  # noqa: E402
    MAX_HANDOUT_COPIES,
    cards_for_timeline,
    render_cards_pdf,
    render_handout_sheet_pdf,
)
from swimtimeline.failure_alerts import (  # noqa: E402
    ALERT_TO,
    FailureReport,
    InputError,
    format_failure_email,
    notify_failure,
    should_notify_failure,
)
from swimtimeline.resend_email import DEFAULT_FROM, ResendError  # noqa: E402

try:
    from webapp.server import officials_cards_for_meet, officials_upload_paths, resolve_current_meet
except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
    raise unittest.SkipTest("webapp.server needs Python 3.12: the stdlib cgi module was removed in 3.13") from exc

HERC_DIR = ROOT / "meets/2026-herculean-invitational/input"
HERC_TIMELINE = HERC_DIR / "2026-herculean-invitational-timeline.pdf"
HERC_PSYCH = HERC_DIR / "2026-herculean-invitational-psych-sheet.pdf"


def raised(callable_obj, *args, **kwargs) -> BaseException:
    """The exception a real call actually raises, so these tests classify the genuine article
    rather than a look-alike built by hand."""
    try:
        callable_obj(*args, **kwargs)
    except BaseException as exc:  # noqa: BLE001 - capturing it IS the point
        return exc
    raise AssertionError("expected that call to raise")


class RoutineValidationStaysQuietTest(unittest.TestCase):
    """Working-as-designed input validation. The endpoint already answers these with a clear 400
    and the person corrects their request -- emailing them would be pure noise."""

    def test_an_unknown_meet_id_is_not_worth_an_email(self):
        exc = raised(resolve_current_meet, "no-such-meet")
        self.assertIsInstance(exc, InputError)
        self.assertFalse(should_notify_failure(exc))

    def test_an_invalid_upload_token_is_not_worth_an_email(self):
        exc = raised(officials_upload_paths, "not-a-real-token")
        self.assertIsInstance(exc, InputError)
        self.assertFalse(should_notify_failure(exc))

    def test_an_expired_upload_token_is_not_worth_an_email(self):
        # Correctly SHAPED (so it passes the format check) but no such run directory exists.
        exc = raised(officials_upload_paths, "1700000000-abcdef12")
        self.assertIsInstance(exc, InputError)
        self.assertFalse(should_notify_failure(exc))
        self.assertIn("expired", str(exc))

    def test_copies_out_of_range_is_not_worth_an_email(self):
        """Raised from render_handout_sheet_pdf -- a "rendering" function -- which is exactly why
        the split is by exception type and not by which layer threw."""
        _name, cards, _highlights = cards_for_timeline(HERC_TIMELINE)
        for bad in (0, -5, MAX_HANDOUT_COPIES + 1):
            exc = raised(render_handout_sheet_pdf, cards[0], bad)
            self.assertIsInstance(exc, InputError, bad)
            self.assertFalse(should_notify_failure(exc), bad)

    def test_every_send_badges_pdf_validation_message_is_quiet(self):
        """The literal validation errors that endpoint raises, as InputError instances."""
        for message in (
            "Unknown layout 'nope'. Use 'cards', 'sheet', or 'handout'.",
            "copies is only used with layout=handout.",
            "layout=handout needs a copies=N param saying how many to print.",
            "copies must be a positive whole number.",
            "A hosted meet id or an upload token is required.",
            "Session 99 is not in this meet's timeline.",
            "Session 1X is not in this meet's timeline.",
            "highlighted_only needs at least one swimmer name to filter by.",
            "Choose a hosted meet or upload a Session Report PDF.",
            "Timeline must be a PDF.",
        ):
            self.assertFalse(should_notify_failure(InputError(message)), message)

    def test_an_input_error_is_still_a_value_error_so_handlers_are_unchanged(self):
        """The reason converting these raise sites changed no HTTP behaviour: every existing
        `except ValueError` / `except Exception` still catches them identically."""
        exc = InputError("nope")
        self.assertIsInstance(exc, ValueError)
        self.assertIsInstance(exc, Exception)


class RealDocumentFailuresDoNotifyTest(unittest.TestCase):
    """A real meet document that will not process. These are the bugs worth hearing about, even
    when the exception is a plain ValueError with a tidy message."""

    def test_a_psych_sheet_fed_in_as_a_timeline_is_worth_an_email(self):
        """The exact mistake a real official made: a genuine PDF, just not a Session Report."""
        exc = raised(cards_for_timeline, HERC_PSYCH)
        self.assertIsInstance(exc, ValueError)
        self.assertNotIsInstance(exc, InputError)
        self.assertTrue(should_notify_failure(exc))
        self.assertIn("No sessions or events were found", str(exc))

    def test_a_pdf_with_no_parseable_date_range_is_worth_an_email(self):
        """parse_timeline's own "Could not find meet date range" path, reached with a real PDF
        that has no date anywhere -- a WZAG per-day prelims timeline, which genuinely has none."""
        no_date = ROOT / "meets/2026-wzag-championships-boise/input/wzag wednesday prelims timeline.pdf"
        exc = raised(cards_for_timeline, no_date)
        self.assertNotIsInstance(exc, InputError)
        self.assertTrue(should_notify_failure(exc))

    def test_a_corrupt_file_is_worth_an_email_whatever_type_it_raises(self):
        """Not a PDF at all: whatever pypdf throws for this, the fail-safe direction means it
        notifies without anyone having to anticipate the type."""
        junk = Path(__file__)  # a real file, definitively not a PDF
        exc = raised(cards_for_timeline, junk)
        self.assertNotIsInstance(exc, InputError)
        self.assertTrue(should_notify_failure(exc))

    def test_rendering_with_no_cards_is_worth_an_email(self):
        """A defensive guard that the endpoint's own validation should make unreachable -- so if
        it ever fires, something really is wrong."""
        exc = raised(render_cards_pdf, [])
        self.assertNotIsInstance(exc, InputError)
        self.assertTrue(should_notify_failure(exc))

    def test_unexpected_exception_types_notify_by_default(self):
        """The fail-safe direction: to make something quiet you must classify it, not the reverse."""
        for exc in (
            KeyError("event_number"),
            TypeError("unsupported operand"),
            AttributeError("'NoneType' object has no attribute 'start'"),
            ZeroDivisionError("division by zero"),
            RuntimeError("something nobody predicted"),
            ValueError("a plain value error from deep inside a parser"),
        ):
            self.assertTrue(should_notify_failure(exc), repr(exc))

    def test_a_real_psych_sheet_failure_is_reported_rather_than_only_warned(self):
        """swimmer_event_numbers() swallows per-name psych failures on purpose, so they never
        reach an except block. They are still surfaced on processing_errors so the server can
        alert -- without that, a psych sheet that will not parse is invisible."""
        _name, _cards, highlights = cards_for_timeline(
            HERC_TIMELINE,
            psych_pdf=Path(__file__),  # not a PDF: every name fails to match
            swimmer_names=["Cova, Mila"],
        )
        self.assertEqual(highlights.event_numbers, set())
        self.assertTrue(highlights.warnings)
        self.assertEqual(len(highlights.processing_errors), 1)
        name, exc = highlights.processing_errors[0]
        self.assertEqual(name, "Cova, Mila")
        self.assertTrue(should_notify_failure(exc))

    def test_a_healthy_psych_sheet_reports_no_processing_errors(self):
        """The control: the real psych sheet must not generate spurious alerts."""
        _name, _cards, highlights = cards_for_timeline(
            HERC_TIMELINE, psych_pdf=HERC_PSYCH, swimmer_names=["Cova, Mila"]
        )
        self.assertEqual(highlights.processing_errors, [])
        self.assertTrue(highlights.event_numbers)

    def test_an_ambiguous_name_is_not_a_processing_error(self):
        """"Vickers" matches two real swimmers -- a warning for the official to resolve, not a
        document failure, so it must not email anyone."""
        _name, _cards, highlights = cards_for_timeline(
            HERC_TIMELINE, psych_pdf=HERC_PSYCH, swimmer_names=["Vickers"]
        )
        self.assertTrue(highlights.warnings)
        self.assertEqual(highlights.processing_errors, [])


class EmailContentTest(unittest.TestCase):
    def test_body_carries_when_where_what_was_asked_and_what_broke(self):
        exc = raised(cards_for_timeline, HERC_PSYCH)
        subject, body = format_failure_email(
            FailureReport(
                endpoint="/api/officials/badges",
                exc=exc,
                params={
                    "meet_id": "2026-herculean-invitational",
                    "session": "1",
                    "layout": "handout",
                    "copies": "10",
                    "swimmer_names": ["Cova, Mila", "Vickers, Natalie"],
                },
                stage="building cards",
            )
        )
        self.assertIn("/api/officials/badges", subject)
        self.assertIn("ValueError", subject)
        self.assertIn("UTC", body)
        self.assertIn("/api/officials/badges", body)
        self.assertIn("building cards", body)
        self.assertIn("No sessions or events were found", body)
        self.assertIn("2026-herculean-invitational", body)
        self.assertIn("layout = handout", body)
        self.assertIn("copies = 10", body)
        self.assertIn("Cova, Mila", body)
        self.assertIn("Vickers, Natalie", body)
        self.assertIn("Traceback", body)

    def test_unlisted_params_and_file_contents_are_never_included(self):
        """Only the handful of params someone would retype -- explicitly not uploaded bytes."""
        _subject, body = format_failure_email(
            FailureReport(
                endpoint="/api/officials/sessions",
                exc=ValueError("boom"),
                params={
                    "meet_id": "m1",
                    "timeline_pdf": b"%PDF-1.4 secret contents",
                    "psych_pdf_bytes": "a" * 5000,
                    "api_key": "re_supersecret",
                },
            )
        )
        self.assertIn("meet_id = m1", body)
        self.assertNotIn("%PDF", body)
        self.assertNotIn("re_supersecret", body)
        self.assertNotIn("aaaaaaaaaa", body)

    def test_long_values_are_truncated_rather_than_mailed_whole(self):
        _subject, body = format_failure_email(
            FailureReport(
                endpoint="/api/officials/badges",
                exc=ValueError("x" * 5000),
                params={"meet_id": "m" * 5000},
            )
        )
        self.assertIn("[truncated]", body)
        self.assertLess(len(body), 6000)

    def test_a_report_with_no_reportable_params_still_reads_sensibly(self):
        _subject, body = format_failure_email(
            FailureReport(endpoint="/api/officials/meets", exc=RuntimeError("disk gone"))
        )
        self.assertIn("no reportable parameters", body)
        self.assertIn("disk gone", body)


class SendingIsBestEffortTest(unittest.TestCase):
    """notify_failure() runs while a response is already going back to whoever hit the failure, so
    nothing it does -- including failing -- may propagate."""

    def setUp(self):
        self.logged: list[str] = []

    def log(self, message):
        self.logged.append(str(message))

    def test_a_validation_error_sends_nothing_even_with_a_key_present(self):
        sent = []
        result = notify_failure(
            FailureReport(endpoint="/api/officials/badges", exc=InputError("Session 99 is not in this meet's timeline.")),
            sender=lambda *args: sent.append(args) or "id_1",
            logger=self.log,
        )
        self.assertIsNone(result)
        self.assertEqual(sent, [])

    def test_a_real_failure_sends_one_email_to_the_alert_address(self):
        import os

        sent = []

        def fake_sender(api_key, from_address, to_address, subject, body):
            sent.append((api_key, from_address, to_address, subject, body))
            return "msg_123"

        os.environ["RESEND_API_KEY"] = "re_test_key"
        try:
            result = notify_failure(
                FailureReport(endpoint="/api/officials/badges", exc=ValueError("real parse failure")),
                sender=fake_sender,
                logger=self.log,
            )
        finally:
            os.environ.pop("RESEND_API_KEY", None)
        self.assertEqual(result, "msg_123")
        self.assertEqual(len(sent), 1)  # one email per failure, no batching
        api_key, from_address, to_address, subject, body = sent[0]
        self.assertEqual(api_key, "re_test_key")
        self.assertEqual(to_address, ALERT_TO)
        self.assertEqual(to_address, "swimtimelineapp@gmail.com")
        # The sandbox sender is what makes this work with no domain verification, because
        # ALERT_TO is the Resend account's own address (docs/subscriber-notifications.md).
        self.assertEqual(from_address, DEFAULT_FROM)
        self.assertIn("onboarding@resend.dev", from_address)
        self.assertIn("real parse failure", body)

    def test_a_missing_api_key_degrades_to_a_log_line(self):
        import os

        saved = os.environ.pop("RESEND_API_KEY", None)
        try:
            result = notify_failure(
                FailureReport(endpoint="/api/officials/badges", exc=ValueError("boom")),
                sender=lambda *args: (_ for _ in ()).throw(AssertionError("must not send")),
                logger=self.log,
            )
        finally:
            if saved is not None:
                os.environ["RESEND_API_KEY"] = saved
        self.assertIsNone(result)
        self.assertTrue(any("alert-skipped" in line for line in self.logged))
        self.assertTrue(any("RESEND_API_KEY" in line for line in self.logged))

    def test_a_resend_outage_cannot_propagate(self):
        import os

        os.environ["RESEND_API_KEY"] = "re_test_key"
        try:
            result = notify_failure(
                FailureReport(endpoint="/api/officials/badges", exc=ValueError("boom")),
                sender=lambda *args: (_ for _ in ()).throw(ResendError("Could not reach Resend")),
                logger=self.log,
            )
        finally:
            os.environ.pop("RESEND_API_KEY", None)
        self.assertIsNone(result)
        self.assertTrue(any("alert-failed" in line for line in self.logged))

    def test_even_an_unexpected_error_inside_the_sender_cannot_propagate(self):
        """Not just ResendError: any Exception the sender raises is swallowed too.

        Deliberately an Exception and not KeyboardInterrupt/SystemExit -- notify_failure catches
        Exception, not BaseException, so a Ctrl-C or a process shutdown still propagates as it
        should rather than being eaten by an error reporter.
        """
        import os

        os.environ["RESEND_API_KEY"] = "re_test_key"
        try:
            for blow_up in (
                RuntimeError("json module vanished"),
                MemoryError(),
                AttributeError("sender had no attribute"),
            ):
                result = notify_failure(
                    FailureReport(endpoint="/api/officials/badges", exc=ValueError("boom")),
                    sender=lambda *args, _e=blow_up: (_ for _ in ()).throw(_e),
                    logger=self.log,
                )
                self.assertIsNone(result, repr(blow_up))
        finally:
            os.environ.pop("RESEND_API_KEY", None)

    def test_a_keyboard_interrupt_is_deliberately_not_swallowed(self):
        """Pins the BaseException boundary: an error reporter must not make a server un-stoppable."""
        import os

        os.environ["RESEND_API_KEY"] = "re_test_key"
        try:
            with self.assertRaises(KeyboardInterrupt):
                notify_failure(
                    FailureReport(endpoint="/api/officials/badges", exc=ValueError("boom")),
                    sender=lambda *args: (_ for _ in ()).throw(KeyboardInterrupt()),
                    logger=self.log,
                )
        finally:
            os.environ.pop("RESEND_API_KEY", None)

    def test_a_broken_report_cannot_propagate_either(self):
        """Even if formatting the email itself fails, the response must be unaffected."""

        class ExplodingStr(ValueError):
            def __str__(self):
                raise RuntimeError("cannot stringify")

        import os

        os.environ["RESEND_API_KEY"] = "re_test_key"
        try:
            result = notify_failure(
                FailureReport(endpoint="/api/officials/badges", exc=ExplodingStr()),
                sender=lambda *args: "never",
                logger=self.log,
            )
        finally:
            os.environ.pop("RESEND_API_KEY", None)
        self.assertIsNone(result)
        self.assertTrue(any("alert-failed" in line for line in self.logged))


class EndpointSplitTest(unittest.TestCase):
    """The same split, exercised through the REAL HTTP endpoints rather than by classifying
    exceptions in isolation -- so the wiring (which handler reports, with which params) is covered
    too, not just the decision function.

    Every request is fired first and the alerts are asserted as a SET at the end, on purpose: the
    alert is deliberately sent AFTER the response is written, on the handler's own thread, so
    sampling a counter right after each response is inherently racy. Asserting the final set is
    race-free and also proves the ordering requirement -- the response never waits on Resend.
    """

    @classmethod
    def setUpClass(cls):
        import io
        import os
        import threading
        import urllib.error
        import urllib.request
        from http.server import ThreadingHTTPServer

        import webapp.server as srv
        import swimtimeline.failure_alerts as alerts

        cls.io, cls.urllib_request, cls.urllib_error = io, urllib.request, urllib.error
        cls.srv = srv
        cls.sent: list[tuple] = []
        cls.lock = threading.Lock()
        real_notify = alerts.notify_failure

        def recording_notify(report, sender=None, logger=print):
            # Delegates to the REAL notify_failure so the real classification runs; only the
            # network send is replaced.
            def fake_sender(*args):
                with cls.lock:
                    cls.sent.append(args)
                return "msg_fake"

            return real_notify(report, sender=fake_sender, logger=lambda message: None)

        cls._real_server_notify = srv.notify_failure
        srv.notify_failure = recording_notify
        cls._saved_key = os.environ.get("RESEND_API_KEY")
        os.environ["RESEND_API_KEY"] = "re_fake_for_test"

        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.SwimTimelineHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        import os

        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.srv.notify_failure = cls._real_server_notify
        if cls._saved_key is None:
            os.environ.pop("RESEND_API_KEY", None)
        else:
            os.environ["RESEND_API_KEY"] = cls._saved_key

    def setUp(self):
        # The recorder lives on the class (the patched seam is module-level), so each test starts
        # from a clean slate. Safe because every test settle()s before it finishes, so no alert
        # from a previous test can still be in flight.
        with self.lock:
            self.sent.clear()

    def get_badges(self, query: str) -> int:
        try:
            with self.urllib_request.urlopen(
                f"http://127.0.0.1:{self.port}/api/officials/badges?{query}", timeout=60
            ) as response:
                return response.status
        except self.urllib_error.HTTPError as error:
            return error.code

    def post_upload(self, filename: str, content: bytes) -> int:
        boundary = "----alertsplitboundary"
        body = self.io.BytesIO()
        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="timeline_pdf"; filename="{filename}"\r\n'.encode())
        body.write(b"Content-Type: application/octet-stream\r\n\r\n" + content + b"\r\n")
        body.write(f"--{boundary}--\r\n".encode())
        request = self.urllib_request.Request(
            f"http://127.0.0.1:{self.port}/api/officials/sessions",
            data=body.getvalue(), method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with self.urllib_request.urlopen(request, timeout=60) as response:
                return response.status
        except self.urllib_error.HTTPError as error:
            return error.code

    def post_meet(self, meet_id: str) -> int:
        import json as json_module

        request = self.urllib_request.Request(
            f"http://127.0.0.1:{self.port}/api/officials/sessions",
            data=json_module.dumps({"meet_id": meet_id} if meet_id else {}).encode("utf-8"),
            method="POST", headers={"Content-Type": "application/json"},
        )
        try:
            with self.urllib_request.urlopen(request, timeout=60) as response:
                return response.status
        except self.urllib_error.HTTPError as error:
            return error.code

    def settle(self):
        """Let the handler threads finish their post-response alerting."""
        import time

        time.sleep(1.5)

    def test_only_real_failures_email_across_every_officials_path(self):
        meet = "2026-herculean-invitational"

        # --- routine validation, working as designed: every one of these must stay silent -------
        routine = [
            (f"meet_id={meet}&layout=bogus", "unknown layout"),
            (f"meet_id={meet}&layout=handout&copies=5", "copies without a session"),
            (f"meet_id={meet}&layout=handout&session=1&copies=9999", "copies over the cap"),
            (f"meet_id={meet}&layout=handout&session=1&copies=0", "copies below 1"),
            (f"meet_id={meet}&layout=handout&session=1", "handout without copies"),
            (f"meet_id={meet}&session=99", "session not in the meet"),
            (f"meet_id={meet}&session=abc", "session id not in the meet"),
            ("layout=sheet", "no meet id at all"),
            ("meet_id=nope-not-real", "unknown meet id"),
            ("token=1700000000-abcdef12", "well-formed but expired token"),
            ("token=not-a-token", "malformed token"),
            (f"meet_id={meet}&highlighted_only=1", "highlighted_only with no names"),
            (f"meet_id={meet}&swimmer_names=Nobody%2C+Atall&highlighted_only=1", "no matching sessions"),
        ]
        for query, label in routine:
            self.assertEqual(self.get_badges(query), 400, label)
        self.assertEqual(self.post_meet(""), 400)            # no meet chosen
        self.assertEqual(self.post_meet("nope-not-real"), 400)  # unknown meet
        self.assertEqual(self.post_upload("x.txt", b"not a pdf"), 400)  # non-PDF upload

        # --- successful requests: obviously silent too -------------------------------------------
        for query in (
            f"meet_id={meet}",
            f"meet_id={meet}&layout=sheet",
            f"meet_id={meet}&layout=handout&session=1&copies=5",
            f"meet_id={meet}&swimmer_names=Cova%2C+Mila&highlighted_only=1",
        ):
            self.assertEqual(self.get_badges(query), 200, query)

        self.settle()
        with self.lock:
            after_quiet_cases = list(self.sent)
        self.assertEqual(
            after_quiet_cases, [], f"routine validation emailed: {[a[3] for a in after_quiet_cases]}"
        )

        # --- real document failures: each must email exactly once --------------------------------
        psych_bytes = HERC_PSYCH.read_bytes()
        self.assertEqual(self.post_upload("t.pdf", psych_bytes), 400)  # real PDF, wrong kind
        self.assertEqual(self.post_upload("t.pdf", b"%PDF-1.4 garbage"), 400)  # corrupt PDF
        self.settle()

        with self.lock:
            real_failures = list(self.sent)
        subjects = [args[3] for args in real_failures]
        self.assertEqual(len(real_failures), 2, subjects)
        # One email per failure, no batching, both to the alert address via the sandbox sender.
        for api_key, from_address, to_address, subject, body in real_failures:
            self.assertEqual(api_key, "re_fake_for_test")
            self.assertEqual(to_address, ALERT_TO)
            self.assertEqual(from_address, DEFAULT_FROM)
            self.assertIn("/api/officials/sessions", subject)
            self.assertIn("/api/officials/sessions", body)
            self.assertIn("UTC", body)
            self.assertIn("Traceback", body)
        self.assertTrue(any("ValueError" in s for s in subjects), subjects)          # failed to parse
        self.assertTrue(any("PdfStreamError" in s for s in subjects), subjects)      # corrupt file

    def test_a_real_failure_on_the_badges_get_path_also_emails(self):
        """The GET download path, not just the upload POST: a token whose saved timeline.pdf is a
        real-but-wrong document, which is what a stale run directory would look like."""
        import shutil
        import time

        run_id = f"{int(time.time())}-beefcafe"
        upload_dir = self.srv.RUNS_DIR / run_id / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(HERC_PSYCH, upload_dir / "timeline.pdf")  # a PDF, just not a Session Report
        try:
            self.assertEqual(self.get_badges(f"token={run_id}"), 400)
            self.settle()
        finally:
            shutil.rmtree(self.srv.RUNS_DIR / run_id, ignore_errors=True)
        with self.lock:
            alerts = list(self.sent)
        self.assertEqual(len(alerts), 1, [args[3] for args in alerts])
        self.assertIn("/api/officials/badges", alerts[0][3])
        self.assertIn("No sessions or events were found", alerts[0][4])


if __name__ == "__main__":
    unittest.main()
