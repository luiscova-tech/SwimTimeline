"""/subscribe.ics -- a live-updating calendar feed for the Current Meets flow.

Unlike every other endpoint, this one is meant to be polled forever by a calendar app, so it
re-reads the meet's files from disk on every single request (no caching stale results across
requests -- only a short in-memory TTL keyed by the exact resolved params, to absorb bursts) and
must never leave its throwaway run directory behind. This drives the REAL HTTP endpoint against
the hosted WZAG meet record, the same path a subscribed calendar app hits.
"""

from pathlib import Path
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from http.server import ThreadingHTTPServer
    from webapp.server import RUNS_DIR, SwimTimelineHandler
except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
    raise unittest.SkipTest("webapp.server needs Python 3.12: the stdlib cgi module was removed in 3.13") from exc


def get_subscribe(port: int, query: str) -> tuple[int, dict, bytes]:
    """Returns (status, headers, body) for both success and error responses -- HTTPError still
    carries all three, it just also raises, so callers that want to assert on errors catch it."""
    url = f"http://127.0.0.1:{port}/subscribe.ics?{query}"
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            return response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()


class SubscribeIcsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), SwimTimelineHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def assert_well_formed_ics(self, body: bytes):
        text = body.decode("utf-8")
        self.assertTrue(text.startswith("BEGIN:VCALENDAR\r\n") or text.startswith("BEGIN:VCALENDAR\n"), text[:40])
        self.assertIn("VERSION:2.0", text)
        self.assertIn("PRODID:", text)
        self.assertTrue(text.rstrip().endswith("END:VCALENDAR"), text[-40:])
        self.assertEqual(text.count("BEGIN:VEVENT"), text.count("END:VEVENT"))
        self.assertGreater(text.count("BEGIN:VEVENT"), 0, "feed produced zero events")
        return text

    def test_valid_request_returns_a_well_formed_calendar(self):
        status, headers, body = get_subscribe(
            self.port, "meet_id=2026-wzag-championships-boise&swimmer=Cova%2C+Mila+L"
        )
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/calendar; charset=utf-8")
        self.assertIn("inline", headers["Content-Disposition"])
        self.assertIn("public, max-age=300", headers["Cache-Control"])
        text = self.assert_well_formed_ics(body)
        # Her real 4-day daily calendar, same as the hosted download.
        self.assertEqual(text.count("BEGIN:VEVENT"), 4)

    def test_modes_defaults_to_daily_when_omitted(self):
        status, headers, _ = get_subscribe(self.port, "meet_id=2026-wzag-championships-boise&swimmer=Cova")
        self.assertEqual(status, 200)
        self.assertIn("-daily.ics", headers["Content-Disposition"])

    def test_explicit_weekend_mode_is_honored(self):
        status, headers, body = get_subscribe(
            self.port, "meet_id=2026-wzag-championships-boise&swimmer=Cova&modes=weekend"
        )
        self.assertEqual(status, 200)
        self.assertIn("-weekend.ics", headers["Content-Disposition"])
        text = self.assert_well_formed_ics(body)
        self.assertEqual(text.count("BEGIN:VEVENT"), 1)  # one event for the whole meet

    def test_missing_meet_id_is_a_clean_400_not_a_500(self):
        status, _, _ = get_subscribe(self.port, "swimmer=Cova")
        self.assertEqual(status, 400)

    def test_missing_swimmer_is_a_clean_400_not_a_500(self):
        status, _, _ = get_subscribe(self.port, "meet_id=2026-wzag-championships-boise")
        self.assertEqual(status, 400)

    def test_unknown_meet_id_is_a_404_not_a_500(self):
        status, _, _ = get_subscribe(self.port, "meet_id=does-not-exist&swimmer=Cova")
        self.assertEqual(status, 404)

    def test_no_matching_swimmer_is_a_404_not_an_empty_calendar(self):
        status, _, body = get_subscribe(
            self.port, "meet_id=2026-wzag-championships-boise&swimmer=Zzzznotarealswimmer"
        )
        self.assertEqual(status, 404)
        self.assertNotIn(b"BEGIN:VCALENDAR", body)

    def test_ambiguous_swimmer_name_is_a_400_not_a_merged_calendar(self):
        # "Yang" resolves to two real AZ namesakes at WZAG plus Yi Yang -- see
        # test_ambiguous_partial_name.py. A silently merged/first-match feed would be worse than
        # an error: it would keep "working" while quietly showing someone else's swims forever.
        status, _, body = get_subscribe(self.port, "meet_id=2026-wzag-championships-boise&swimmer=Yang")
        self.assertEqual(status, 400)
        self.assertNotIn(b"BEGIN:VCALENDAR", body)

    def test_meet_not_ready_for_lookup_is_rejected_cleanly(self):
        # Para Nationals is loaded from a meet packet (schedule-only), so the UI never offers
        # calendar generation for it even though a psych sheet is on file.
        status, _, body = get_subscribe(self.port, "meet_id=2026-para-nationals&swimmer=Anyone")
        self.assertEqual(status, 409)
        self.assertNotIn(b"BEGIN:VCALENDAR", body)

    def test_invalid_relay_option_id_is_a_400_not_a_500(self):
        status, _, _ = get_subscribe(
            self.port, "meet_id=2026-wzag-championships-boise&swimmer=Cova&relay_options=not-a-real-option"
        )
        self.assertEqual(status, 400)

    def test_show_team_relays_toggle_adds_tentative_relays_to_the_feed(self):
        _, _, without = get_subscribe(self.port, "meet_id=2026-wzag-championships-boise&swimmer=Cova%2C+Mila+L")
        _, _, with_toggle = get_subscribe(
            self.port, "meet_id=2026-wzag-championships-boise&swimmer=Cova%2C+Mila+L&show_team_relays=1"
        )
        self.assertNotIn(b"team entered", without)
        self.assertIn(b"team entered", with_toggle)

    def test_repeated_polling_leaves_no_run_directories_behind(self):
        # This route is polled forever by calendar apps and there is no sweep that cleans up
        # RUNS_DIR, so every request -- success or error -- must remove its own throwaway dir.
        before = {p.name for p in RUNS_DIR.glob("subscribe-*")} if RUNS_DIR.exists() else set()
        get_subscribe(self.port, "meet_id=2026-wzag-championships-boise&swimmer=Cova&modes=detailed")
        get_subscribe(self.port, "meet_id=2026-wzag-championships-boise&swimmer=Yang")  # error path too
        after = {p.name for p in RUNS_DIR.glob("subscribe-*")} if RUNS_DIR.exists() else set()
        self.assertEqual(after, before)

    def test_repeated_identical_requests_are_served_from_the_short_lived_cache(self):
        # Not a timing assertion (too flaky) -- just that a second identical request returns byte-
        # identical content and does not leave a second run directory behind either.
        _, _, first = get_subscribe(self.port, "meet_id=2026-wzag-championships-boise&swimmer=Cova")
        before = {p.name for p in RUNS_DIR.glob("subscribe-*")} if RUNS_DIR.exists() else set()
        _, _, second = get_subscribe(self.port, "meet_id=2026-wzag-championships-boise&swimmer=Cova")
        after = {p.name for p in RUNS_DIR.glob("subscribe-*")} if RUNS_DIR.exists() else set()
        self.assertEqual(first, second)
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
