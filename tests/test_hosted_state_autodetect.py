"""Hosted-meet flow must not block LSC auto-detection with the meet's venue state.

The State/LSC field is the SWIMMER's LSC. The hosted endpoint used to substitute the meet record's
"state" (the VENUE's state -- "ID" for the Boise WZAG meet) whenever the field was blank, which
masqueraded as an explicitly-entered LSC and blocked per-swimmer team-code auto-detection: searching
"Cova" at hosted WZAG with a blank field showed "LSC: standards not configured" instead of her real
AZSI line. (The frontend had the same substitution -- app.js no longer sends meet.state either.)

This drives the REAL HTTP endpoint (/api/analyze-current) against the hosted WZAG meet record, the
same path the website uses, not analyze_uploads directly.
"""

import json
from pathlib import Path
import sys
import threading
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from http.server import ThreadingHTTPServer
    from webapp.server import SwimTimelineHandler
except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
    raise unittest.SkipTest("webapp.server needs Python 3.12: the stdlib cgi module was removed in 3.13") from exc


def post_analyze_current(port: int, payload: dict) -> dict:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/analyze-current",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


class HostedFlowAutoDetectTest(unittest.TestCase):
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

    def test_blank_state_at_hosted_wzag_auto_detects_az_and_shows_azsi(self):
        result = post_analyze_current(self.port, {
            "meet_id": "2026-wzag-championships-boise",
            "swimmer_names": ["Cova, Mila L"],
            "state": "",  # genuinely blank field
            "modes": ["daily"],
        })
        individual = [item for item in result["items"] if item.get("type") != "relay"]
        self.assertTrue(individual)
        # Her real AZSI lines appear -- the venue's "ID" must not masquerade as her LSC.
        lsc_lines = [item["benchmarks"]["lsc"] for item in individual]
        self.assertTrue(all(line.startswith("AZSI") for line in lsc_lines), lsc_lines)
        self.assertTrue(any("auto-detected as AZ" in w for w in result.get("warnings", [])))

    def test_explicitly_typed_state_still_wins_over_auto_detection(self):
        result = post_analyze_current(self.port, {
            "meet_id": "2026-wzag-championships-boise",
            "swimmer_names": ["Cova, Mila L"],
            "state": "FL",  # parent explicitly typed a different LSC
            "modes": ["daily"],
        })
        individual = [item for item in result["items"] if item.get("type") != "relay"]
        self.assertTrue(all("not configured" in item["benchmarks"]["lsc"] for item in individual))


if __name__ == "__main__":
    unittest.main()
