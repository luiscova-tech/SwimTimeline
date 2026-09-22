"""GET /api/timeline -- the JSON endpoint behind the bookmarkable /timeline "Next up" live view.

Same shape as /subscribe.ics (see test_subscribe_ics.py's module docstring): meet_id + swimmer_b64
(falling back to the legacy plaintext "swimmer" param) straight from the query string, re-resolved
from disk on every request, nothing persisted. This drives the REAL HTTP endpoint against the
hosted WZAG meet record, the same one /subscribe.ics's own tests use.
"""

from pathlib import Path
import sys
import threading
import unittest
import urllib.error
import urllib.request
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from http.server import ThreadingHTTPServer
    from webapp.server import SwimTimelineHandler
except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
    raise unittest.SkipTest("webapp.server needs Python 3.12: the stdlib cgi module was removed in 3.13") from exc

from tests.test_subscribe_ics import encode_swimmer_param

MEET_ID = "2026-wzag-championships-boise"


def get_timeline(port: int, query: str) -> tuple[int, dict]:
    """Returns (status, parsed JSON body) for both success and error responses -- send_json
    always writes a JSON body, including on the 400/404/409 error paths."""
    url = f"http://127.0.0.1:{port}/api/timeline?{query}"
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


class TimelineApiTest(unittest.TestCase):
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

    def test_valid_request_returns_items_with_a_tz_aware_start_at(self):
        swimmer_b64 = encode_swimmer_param("Cova, Mila L")
        status, payload = get_timeline(self.port, f"meet_id={MEET_ID}&swimmer_b64={swimmer_b64}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["current_meet_id"], MEET_ID)
        self.assertEqual(payload["swimmer"], "Cova, Mila L")
        self.assertTrue(payload["items"])
        for item in payload["items"]:
            self.assertIn("start_at", item)
            self.assertIn("sort_start", item)
            self.assertEqual(item["start_at"][:19], item["sort_start"])

    def test_legacy_plaintext_swimmer_param_still_works(self):
        # No swimmer_b64 at all -- the original pre-privacy-fix param, same backward-compatibility
        # contract /subscribe.ics already guarantees (see decode_swimmer_param's docstring).
        status, payload = get_timeline(self.port, f"meet_id={MEET_ID}&swimmer=Cova%2C+Mila+L")
        self.assertEqual(status, 200)
        self.assertEqual(payload["swimmer"], "Cova, Mila L")

    def test_swimmer_b64_takes_precedence_over_plaintext(self):
        swimmer_b64 = encode_swimmer_param("Cova, Mila L")
        status, payload = get_timeline(
            self.port, f"meet_id={MEET_ID}&swimmer_b64={swimmer_b64}&swimmer=Somebody+Else"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["swimmer"], "Cova, Mila L")

    def test_missing_meet_id_is_a_client_error_not_a_500(self):
        status, payload = get_timeline(self.port, "swimmer_b64=" + encode_swimmer_param("Cova, Mila L"))
        self.assertEqual(status, 400)
        self.assertIn("meet_id", payload["error"])

    def test_unknown_meet_id_is_not_found(self):
        status, payload = get_timeline(
            self.port, f"meet_id=not-a-real-meet&swimmer_b64={encode_swimmer_param('Cova, Mila L')}"
        )
        self.assertEqual(status, 404)
        self.assertIn("error", payload)

    def test_swimmer_with_no_matches_is_not_found(self):
        status, payload = get_timeline(
            self.port, f"meet_id={MEET_ID}&swimmer_b64={encode_swimmer_param('Nobody Realatall')}"
        )
        self.assertEqual(status, 404)
        self.assertIn("error", payload)


if __name__ == "__main__":
    unittest.main()
