"""/api/officials/badges -- the three download shapes and the highlighted-only filter.

These drive the REAL HTTP endpoint (the same path the Officials page's download links hit), not
the badges module directly, because the parameter parsing, the layout selection and the
highlighted-only filtering all live in send_badges_pdf(). The per-card rendering itself is covered
in tests/test_badges.py.

Three shapes, all from the same per-session draw_card() call:
  * default          -- one 144x216pt page per session; a page IS a card, so it can be cut out and
                        worn in a badge holder. Unchanged by the sheet layout.
  * ?session=N       -- the same, for one session.
  * ?layout=sheet    -- those native-size cards tiled on shared 612x792pt letter sheets.

`?highlighted_only=1` drops sessions none of the named swimmers swim in, and composes with either
multi-session layout.

NOTE on coverage: the equivalent CLIENT-SIDE table filtering in webapp/static/officials.js has no
automated coverage -- this repo has no JS test runner or harness of any kind, so that half is
verified by hand in a browser only. What IS covered automatically is the server-side filter below,
which is the rule the PDFs actually obey.
"""

from io import BytesIO
from pathlib import Path
import json
import sys
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from http.server import ThreadingHTTPServer
    from webapp.server import SwimTimelineHandler
except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
    raise unittest.SkipTest("webapp.server needs Python 3.12: the stdlib cgi module was removed in 3.13") from exc

HERCULEAN = "2026-herculean-invitational"
# Cova, Mila's real Herculean entries are events 1/5/7 (session 1) and 27/29 (session 5) -- so
# exactly two of the meet's six sessions have any highlight. Established in test_badges.py's
# SwimmerHighlightCardTest against the real psych sheet.
COVA = "Cova, Mila"
COVA_SESSIONS = (1, 5)


class OfficialsBadgeDownloadTest(unittest.TestCase):
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

    def badges(self, **params) -> tuple[int, dict, bytes]:
        """(status, headers, body) for both success and error, since an HTTPError still carries
        all three. Sequence values are repeated as separate params (swimmer_names=a&swimmer_names=b),
        matching how the page posts them."""
        pairs: list[tuple[str, str]] = []
        for key, value in params.items():
            if isinstance(value, (list, tuple)):
                pairs.extend((key, str(item)) for item in value)
            else:
                pairs.append((key, str(value)))
        url = f"http://127.0.0.1:{self.port}/api/officials/badges?{urllib.parse.urlencode(pairs)}"
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), error.read()

    def assert_pdf(self, status: int, headers: dict, body: bytes, pages: int, size: tuple):
        self.assertEqual(status, 200, body[:300])
        self.assertEqual(headers["Content-Type"], "application/pdf")
        self.assertIn("attachment;", headers["Content-Disposition"])
        reader = PdfReader(BytesIO(body))
        self.assertEqual(len(reader.pages), pages)
        for page in reader.pages:
            self.assertAlmostEqual(float(page.mediabox.width), size[0], places=2)
            self.assertAlmostEqual(float(page.mediabox.height), size[1], places=2)
        return reader

    @staticmethod
    def page_text(reader: PdfReader, index: int) -> str:
        return " ".join((reader.pages[index].extract_text() or "").split())

    # ---- the three layouts -------------------------------------------------

    def test_default_layout_is_one_card_sized_page_per_session(self):
        status, headers, body = self.badges(meet_id=HERCULEAN)
        self.assert_pdf(status, headers, body, pages=6, size=(144.0, 216.0))
        self.assertIn("badge-cards.pdf", headers["Content-Disposition"])

    def test_explicit_single_session_is_one_card_sized_page(self):
        status, headers, body = self.badges(meet_id=HERCULEAN, session=1)
        reader = self.assert_pdf(status, headers, body, pages=1, size=(144.0, 216.0))
        self.assertIn("SESSION 1", self.page_text(reader, 0))
        self.assertIn("session-1-badge-card.pdf", headers["Content-Disposition"])

    def test_sheet_layout_tiles_every_session_on_one_letter_sheet(self):
        status, headers, body = self.badges(meet_id=HERCULEAN, layout="sheet")
        reader = self.assert_pdf(status, headers, body, pages=1, size=(612.0, 792.0))
        tiled = self.page_text(reader, 0)
        for session_number in range(1, 7):
            self.assertIn(f"SESSION {session_number}", tiled)
        self.assertIn("badge-card-sheets.pdf", headers["Content-Disposition"])

    def test_layout_cards_is_the_same_as_omitting_layout(self):
        _s1, h1, body1 = self.badges(meet_id=HERCULEAN)
        _s2, h2, body2 = self.badges(meet_id=HERCULEAN, layout="cards")
        self.assertEqual(h1["Content-Disposition"], h2["Content-Disposition"])
        self.assertEqual(len(PdfReader(BytesIO(body1)).pages), len(PdfReader(BytesIO(body2)).pages))

    def test_an_unknown_layout_is_refused_rather_than_silently_defaulting(self):
        status, _headers, body = self.badges(meet_id=HERCULEAN, layout="12up")
        self.assertEqual(status, 400)
        self.assertIn("Unknown layout", json.loads(body)["error"])

    def test_the_sheet_layout_did_not_change_the_cut_out_layout(self):
        """The one-card-per-page format exists so a printed page fits a 2x3 badge holder; the sheet
        layout is an addition, not a replacement."""
        status, headers, body = self.badges(meet_id=HERCULEAN)
        self.assert_pdf(status, headers, body, pages=6, size=(144.0, 216.0))

    # ---- highlighted-only filter -------------------------------------------

    def test_filter_keeps_only_the_sessions_cova_actually_swims(self):
        status, headers, body = self.badges(
            meet_id=HERCULEAN, swimmer_names=COVA, highlighted_only=1
        )
        reader = self.assert_pdf(status, headers, body, pages=2, size=(144.0, 216.0))
        texts = [self.page_text(reader, index) for index in range(2)]
        for expected_session, text in zip(COVA_SESSIONS, texts):
            self.assertIn(f"SESSION {expected_session}", text)
        # The four sessions she is not in are gone.
        joined = " ".join(texts)
        for absent in (2, 3, 4, 6):
            self.assertNotIn(f"SESSION {absent} ", joined)
        self.assertIn("highlighted", headers["Content-Disposition"])

    def test_unfiltered_still_returns_all_six_sessions_with_the_same_names_given(self):
        """Proves the 2-page result above is the FILTER's doing, not something about the names."""
        status, headers, body = self.badges(meet_id=HERCULEAN, swimmer_names=COVA)
        self.assert_pdf(status, headers, body, pages=6, size=(144.0, 216.0))
        self.assertNotIn("highlighted", headers["Content-Disposition"])

    def test_filter_composes_with_the_sheet_layout(self):
        status, headers, body = self.badges(
            meet_id=HERCULEAN, swimmer_names=COVA, highlighted_only=1, layout="sheet"
        )
        reader = self.assert_pdf(status, headers, body, pages=1, size=(612.0, 792.0))
        tiled = self.page_text(reader, 0)
        for expected_session in COVA_SESSIONS:
            self.assertIn(f"SESSION {expected_session}", tiled)
        for absent in (2, 3, 4, 6):
            self.assertNotIn(f"SESSION {absent} ", tiled)
        # Exactly two cards placed; the other seven slots of the 3x3 grid stay empty.
        self.assertEqual(tiled.count("Est. Finish"), 2)
        self.assertIn("badge-card-sheets-highlighted.pdf", headers["Content-Disposition"])

    def test_filter_with_several_names_keeps_the_union_of_their_sessions(self):
        """Vickers, Natalie swims sessions Cova does not, so adding her widens the result rather
        than intersecting it."""
        status, headers, body = self.badges(
            meet_id=HERCULEAN,
            swimmer_names=[COVA, "Vickers, Natalie"],
            highlighted_only=1,
        )
        self.assertEqual(status, 200, body[:300])
        reader = PdfReader(BytesIO(body))
        self.assertGreater(len(reader.pages), len(COVA_SESSIONS))
        self.assertLessEqual(len(reader.pages), 6)

    def test_zero_matching_sessions_is_a_clear_error_not_a_broken_file(self):
        status, headers, body = self.badges(
            meet_id=HERCULEAN, swimmer_names="Nobody, Atall", highlighted_only=1
        )
        self.assertEqual(status, 400)
        self.assertEqual(headers["Content-Type"], "application/json")
        message = json.loads(body)["error"]
        self.assertIn("Nobody, Atall", message)
        self.assertIn("nothing", message.lower())
        # Definitely not a zero-page or truncated PDF.
        self.assertNotIn(b"%PDF", body)

    def test_an_ambiguous_name_alone_also_produces_the_clear_zero_match_error(self):
        """"Vickers" matches two real swimmers, so it resolves to no events at all -- the filter
        then has nothing to keep, and must say so rather than emit an empty document."""
        status, _headers, body = self.badges(
            meet_id=HERCULEAN, swimmer_names="Vickers", highlighted_only=1
        )
        self.assertEqual(status, 400)
        self.assertIn("Vickers", json.loads(body)["error"])

    def test_the_filter_needs_names_to_filter_by(self):
        status, _headers, body = self.badges(meet_id=HERCULEAN, highlighted_only=1)
        self.assertEqual(status, 400)
        self.assertIn("swimmer name", json.loads(body)["error"])

    def test_filter_and_an_explicit_session_that_excludes_it_errors_clearly(self):
        """Contradictory but legal params: session 2 has none of Cova's events. Both filters are
        applied literally and the zero-match error explains it, rather than one silently winning."""
        status, _headers, body = self.badges(
            meet_id=HERCULEAN, swimmer_names=COVA, highlighted_only=1, session=2
        )
        self.assertEqual(status, 400)
        self.assertIn("Cova, Mila", json.loads(body)["error"])

    def test_filter_and_an_explicit_session_that_matches_still_works(self):
        status, headers, body = self.badges(
            meet_id=HERCULEAN, swimmer_names=COVA, highlighted_only=1, session=1
        )
        reader = self.assert_pdf(status, headers, body, pages=1, size=(144.0, 216.0))
        self.assertIn("SESSION 1", self.page_text(reader, 0))


if __name__ == "__main__":
    unittest.main()
