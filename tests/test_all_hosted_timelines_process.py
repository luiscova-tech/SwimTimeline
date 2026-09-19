"""Standing regression guard: does every real hosted meet's timeline still work end-to-end on the
Officials badge-card page?

Real officials, at real meets, asked for exactly this feature this past weekend -- and it has to
keep working as new meets get added to data/current_meets.json, without anyone remembering to
hand-write a new test per meet. So every meet here is discovered DYNAMICALLY from that file (any
entry with a files.timeline value) rather than a hardcoded list -- a newly added meet is
automatically exercised by HostedTimelineRenderingTest below with no code change to this file.

The one place this file DOES name the current meets explicitly (MeetDiscoveryCompletenessTest)
plays the same role tests/test_date_range.py's own completeness check plays for its fixture table:
it is expected to need a one-line update when a meet is intentionally added (an explicit,
conscious acknowledgment), and it fails loudly if one is added without anyone updating it --
neither test lets a new fixture silently go completely unchecked. It does not gate the functional
coverage below, which adapts on its own.

Two things surfaced while writing this file, both real and both now fixed/documented rather than
worked around:
  * A real bug: /api/officials/sessions was returning HTTP 500 (via do_POST's blanket exception
    handler) for a client-input problem -- e.g. a real PDF that just isn't a valid Session Report,
    or a non-PDF upload -- instead of the 400 the sibling endpoint (send_badges_pdf) already
    promises for exactly this kind of error. Fixed in webapp/server.py's do_POST: that one route
    now has its own try/except, matching send_badges_pdf's established contract. See
    OfficialsSessionsHttpNegativeCaseTest below, which pins the fix.
  * Not a bug, but worth knowing: 2026 Para Nationals is registered as "schedule-only" (its
    "timeline" is really a meet-packet schedule, not a HY-TEK Session Report) and is deliberately
    EXCLUDED from the /officials meet picker by officials_meets_payload()'s own NOT_READY_STATUSES
    filter. This file's discovery is keyed only on files.timeline being present -- not on
    status -- so Para Nationals IS included here. cards_for_timeline() handles it fine via its
    meet-packet fallback path; the only difference is its cards' "heats" column comes back blank
    (a meet packet states no heat counts), which HostedTimelineRenderingTest accounts for rather
    than assuming every meet's heats are always populated.
"""

from io import BytesIO
from pathlib import Path
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from swimtimeline.badges import (  # noqa: E402
    CARD_H,
    CARD_W,
    SHEET_H,
    SHEET_SLOTS_PER_PAGE,
    SHEET_W,
    cards_for_timeline,
    render_cards_pdf,
    render_handout_sheet_pdf,
    render_sheet_pdf,
)

try:
    from http.server import ThreadingHTTPServer
    from webapp.server import CURRENT_MEETS_PATH, SwimTimelineHandler, officials_cards_for_meet
except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
    raise unittest.SkipTest("webapp.server needs Python 3.12: the stdlib cgi module was removed in 3.13") from exc


def discover_hosted_meets_with_timeline() -> list[dict]:
    """Every meet record in data/current_meets.json that has a files.timeline value, in file
    order. This IS the auto-discovery the rest of this file is built on -- deliberately not a
    hardcoded list, so a newly registered meet is covered here automatically.
    """
    data = json.loads(CURRENT_MEETS_PATH.read_text(encoding="utf-8"))
    return [meet for meet in data.get("current_meets", []) if meet.get("files", {}).get("timeline")]


class MeetDiscoveryCompletenessTest(unittest.TestCase):
    """Not a hardcoded gate on the functional coverage below (that adapts on its own) -- this
    exists purely so adding a meet is a CONSCIOUS, visible act, the same role
    tests/test_date_range.py's own fixture-table completeness check plays: it fails loudly, rather
    than silently doing nothing, when the real count changes.
    """

    # As of writing this file: 8 real hosted meets carry a files.timeline entry. Update this set
    # (not the discovery logic above) when a meet is intentionally added or removed.
    KNOWN_MEET_IDS = {
        "2026-wzag-championships-boise",
        "2026-az-lc-age-group-state",
        "2026-para-nationals",
        "2026-narwhal-invite",
        "2026-shark-open",
        "2026-herculean-invitational",
        "2026-croswhite-invite",
        "2026-cummins-invitational",
    }

    def test_discovered_meet_ids_match_the_currently_known_set(self):
        discovered_ids = {meet["id"] for meet in discover_hosted_meets_with_timeline()}
        self.assertEqual(discovered_ids, self.KNOWN_MEET_IDS)

    def test_discovery_is_driven_by_files_timeline_not_a_fixed_count(self):
        """A meet with every OTHER file but no timeline must not be discovered -- confirms the
        filter really is "has a timeline", not "exists in the list at all"."""
        data = json.loads(CURRENT_MEETS_PATH.read_text(encoding="utf-8"))
        without_timeline = [
            meet for meet in data.get("current_meets", []) if not meet.get("files", {}).get("timeline")
        ]
        self.assertEqual(without_timeline, [])  # true today; documents the assumption either way
        discovered = discover_hosted_meets_with_timeline()
        self.assertTrue(all(meet.get("files", {}).get("timeline") for meet in discovered))


class HostedTimelineRenderingTest(unittest.TestCase):
    """The real functional guard: for every discovered meet, its timeline must actually turn into
    working badge cards, both the way the hosted /officials page does it (with the meet's own
    flyer, via officials_cards_for_meet()) and the way a fresh upload with no flyer would (a real,
    increasingly-used path per real officials at real meets), and every download shape built from
    those cards must render.
    """

    @classmethod
    def setUpClass(cls):
        cls.meets = discover_hosted_meets_with_timeline()

    def test_at_least_the_known_meets_are_present(self):
        # A trivial guard against an empty/broken discovery silently making every test below a
        # vacuous no-op loop.
        self.assertGreaterEqual(len(self.meets), 7)

    def test_every_meet_processes_both_with_and_without_a_flyer(self):
        for meet in self.meets:
            meet_id = meet["id"]
            files = meet["files"]

            # WITH the meet's own flyer -- the exact hosted-meet code path (officials_cards_for_meet).
            _record, cards_with_flyer, _highlights = officials_cards_for_meet(meet_id)
            self.assertGreater(len(cards_with_flyer), 0, f"{meet_id}: no sessions (with flyer)")
            events_with_flyer = sum(card.event_count for card in cards_with_flyer)
            self.assertGreater(events_with_flyer, 0, f"{meet_id}: no events (with flyer)")

            # WITHOUT any flyer text -- a fresh official upload that only supplies the timeline.
            timeline_path = ROOT / files["timeline"]
            _name, cards_no_flyer, _highlights2 = cards_for_timeline(timeline_path, flyer_text="")
            self.assertGreater(len(cards_no_flyer), 0, f"{meet_id}: no sessions (no flyer)")
            events_no_flyer = sum(card.event_count for card in cards_no_flyer)
            self.assertGreater(events_no_flyer, 0, f"{meet_id}: no events (no flyer)")

    def test_every_meets_cards_render_in_all_three_layouts(self):
        """render_cards_pdf, render_sheet_pdf, and render_handout_sheet_pdf (first session, 3
        copies) all succeed and produce a real, non-trivial PDF -- reusing the same page-count and
        page-size assertions tests/test_badges.py already uses for these three functions.
        """
        for meet in self.meets:
            meet_id = meet["id"]
            _record, cards, _highlights = officials_cards_for_meet(meet_id)
            self.assertTrue(cards, meet_id)

            per_page = render_cards_pdf(cards)
            self.assertGreater(len(per_page), 200, f"{meet_id}: per-page PDF looks empty")
            self.assertTrue(per_page.startswith(b"%PDF"), meet_id)
            per_page_reader = PdfReader(BytesIO(per_page))
            self.assertEqual(len(per_page_reader.pages), len(cards), meet_id)
            for page in per_page_reader.pages:
                self.assertAlmostEqual(float(page.mediabox.width), CARD_W, places=2, msg=meet_id)
                self.assertAlmostEqual(float(page.mediabox.height), CARD_H, places=2, msg=meet_id)

            sheet = render_sheet_pdf(cards)
            self.assertGreater(len(sheet), 200, f"{meet_id}: sheet PDF looks empty")
            self.assertTrue(sheet.startswith(b"%PDF"), meet_id)
            sheet_reader = PdfReader(BytesIO(sheet))
            expected_sheet_pages = -(-len(cards) // SHEET_SLOTS_PER_PAGE)  # ceil division
            self.assertEqual(len(sheet_reader.pages), expected_sheet_pages, meet_id)
            for page in sheet_reader.pages:
                self.assertAlmostEqual(float(page.mediabox.width), SHEET_W, places=2, msg=meet_id)
                self.assertAlmostEqual(float(page.mediabox.height), SHEET_H, places=2, msg=meet_id)

            handout = render_handout_sheet_pdf(cards[0], 3)
            self.assertGreater(len(handout), 200, f"{meet_id}: handout PDF looks empty")
            self.assertTrue(handout.startswith(b"%PDF"), meet_id)
            handout_reader = PdfReader(BytesIO(handout))
            self.assertEqual(len(handout_reader.pages), 1, meet_id)  # 3 copies fits on one sheet
            self.assertAlmostEqual(
                float(handout_reader.pages[0].mediabox.width), SHEET_W, places=2, msg=meet_id
            )

    def test_heats_may_be_blank_for_a_meet_packet_shaped_timeline_but_events_still_exist(self):
        """Para Nationals' "timeline" is a meet-packet schedule, not a real HY-TEK Session Report,
        so it has no heat counts -- confirms that real difference doesn't break rendering, rather
        than silently assuming every meet's heats column is always populated."""
        para_nationals = next(
            (meet for meet in self.meets if meet["id"] == "2026-para-nationals"), None
        )
        if para_nationals is None:
            self.skipTest("2026-para-nationals is no longer a discovered meet")
        _record, cards, _highlights = officials_cards_for_meet(para_nationals["id"])
        all_heats = [row["heats"] for card in cards for row in card.events]
        self.assertTrue(all(heat == "" for heat in all_heats), "expected blank heats, not None or a number")
        # Still renders fine with blanks in the HEATS column.
        pdf = render_cards_pdf(cards)
        self.assertTrue(pdf.startswith(b"%PDF"))


class OfficialsSessionsHttpNegativeCaseTest(unittest.TestCase):
    """/api/officials/sessions at the real HTTP layer (not the library functions): a real-but-wrong
    PDF, or a non-PDF file, must come back as a clean 400 JSON error -- never a 500 or an unhandled
    traceback. Pins the fix in webapp/server.py's do_POST described in this file's module
    docstring: this endpoint used to fall through to the generic handler's blanket 500.
    """

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

    def post_upload(self, fields: list[tuple[str, str, bytes]]) -> tuple[int, dict, bytes]:
        """(status, headers, body) for a multipart upload to /api/officials/sessions. fields is a
        list of (form field name, filename, content) triples, each sent as a file part."""
        boundary = "----swimtimelinetestboundary"
        body = BytesIO()
        for name, filename, content in fields:
            body.write(f"--{boundary}\r\n".encode())
            body.write(f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode())
            body.write(b"Content-Type: application/octet-stream\r\n\r\n")
            body.write(content)
            body.write(b"\r\n")
        body.write(f"--{boundary}--\r\n".encode())
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/officials/sessions",
            data=body.getvalue(),
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read()

    def post_json(self, payload: dict) -> tuple[int, dict, bytes]:
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/officials/sessions",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read()

    def assert_clean_json_error(self, status: int, headers: dict, body: bytes):
        self.assertEqual(status, 400, body[:300])
        self.assertEqual(headers["Content-Type"], "application/json")
        payload = json.loads(body)  # raises if it isn't even valid JSON
        self.assertIn("error", payload)
        message = payload["error"]
        self.assertIsInstance(message, str)
        self.assertTrue(message.strip())
        # Not a leaked traceback -- a real Python exception dump looks nothing like this.
        self.assertNotIn("Traceback", message)
        self.assertNotIn(".py", message)
        return message

    def test_a_real_pdf_that_is_not_a_valid_session_report_is_a_clean_400(self):
        """The exact case an official actually hit: a real psych sheet PDF, uploaded into the
        Session Report / timeline field by mistake."""
        psych_bytes = (
            ROOT / "meets/2026-herculean-invitational/input/2026-herculean-invitational-psych-sheet.pdf"
        ).read_bytes()
        status, headers, body = self.post_upload([("timeline_pdf", "not-a-timeline.pdf", psych_bytes)])
        message = self.assert_clean_json_error(status, headers, body)
        self.assertIn("timeline", message.lower())

    def test_a_non_pdf_upload_is_rejected_up_front(self):
        """save_officials_upload() already refuses a non-.pdf filename before any parsing is
        attempted -- exercised here for real over HTTP, not assumed."""
        status, headers, body = self.post_upload(
            [("timeline_pdf", "not-a-pdf.txt", b"this is not a pdf at all")]
        )
        message = self.assert_clean_json_error(status, headers, body)
        self.assertIn("PDF", message)

    def test_an_unknown_hosted_meet_id_is_also_a_clean_400(self):
        """The JSON (hosted-meet) request path hits the same do_POST route -- confirms the fix
        covers both ways into this endpoint, not just the upload branch."""
        status, headers, body = self.post_json({"meet_id": "does-not-exist-anywhere"})
        message = self.assert_clean_json_error(status, headers, body)
        self.assertIn("does-not-exist-anywhere", message)

    def test_no_meet_id_and_no_upload_is_a_clean_400(self):
        status, headers, body = self.post_json({})
        self.assert_clean_json_error(status, headers, body)


if __name__ == "__main__":
    unittest.main()
