"""Stroke & Turn DQ code quick-reference: the officials-page addition covering
swimtimeline.badges.DQ_CODE_SIDES / dq_code_reference_payload() / render_dq_reference_pdf(), and
the two server routes that expose them (GET /api/officials/dq-codes JSON, GET
/api/officials/dq-codes.pdf).

Static reference content (not derived from any meet), so "real fixture" here means the actual PDF
bytes reportlab produces -- checked with pdfplumber for text position/bounds, not synthetic data.
"""

from http.server import ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import unittest
import urllib.request

import pdfplumber

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from swimtimeline.badges import (
    DQ_CARD_H,
    DQ_CARD_W,
    DQ_CODE_SIDES,
    dq_code_reference_payload,
    format_dq_group_lines,
    render_dq_reference_pdf,
)

try:
    from webapp.server import SwimTimelineHandler
except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
    raise unittest.SkipTest("webapp.server needs Python 3.12: the stdlib cgi module was removed in 3.13") from exc


class DqCodeSidesDataTest(unittest.TestCase):
    """The transcribed content itself: both sides present, every stroke present, spot-checked
    codes match the source card exactly."""

    def test_both_sides_and_every_stroke_present(self):
        self.assertEqual([side["side"] for side in DQ_CODE_SIDES], ["A", "B"])
        side_a, side_b = DQ_CODE_SIDES
        self.assertEqual([s["name"] for s in side_a["strokes"]], ["Butterfly", "Backstroke", "Freestyle"])
        self.assertEqual(
            [s["name"] for s in side_b["strokes"]],
            ["Breaststroke", "Individual Medley", "Relays", "Miscellaneous"],
        )

    def test_spot_check_real_codes_from_the_source_card(self):
        def find(side_letter, stroke_name):
            side = next(s for s in DQ_CODE_SIDES if s["side"] == side_letter)
            return next(s for s in side["strokes"] if s["name"] == stroke_name)

        butterfly = find("A", "Butterfly")
        kick_group = butterfly["groups"][0]
        self.assertEqual(kick_group, ("Kick", [("Alternating", "1A"), ("Breast", "1B"), ("Scissors", "1C")]))

        breaststroke = find("B", "Breaststroke")
        arms_group = breaststroke["groups"][1]
        self.assertEqual(arms_group[0], "Arms")
        self.assertEqual(len(arms_group[1]), 5)  # the real 5-item group that forces line-wrapping

        relays = find("B", "Relays")
        # Ranges, not single codes -- transcribed exactly as the source card prints them.
        self.assertIn(("Stroke Infraction", "61-64"), relays["groups"][0][1])
        self.assertIn(("Other", "7S-7Z"), find("B", "Miscellaneous")["groups"][2][1])

        im = find("B", "Individual Medley")
        # No code at all on these two lines on the source card -- preserved as None, not guessed.
        self.assertIn(("Stroke Infraction(s)", None), im["groups"][0][1])
        self.assertIn(("4th Distance Swum in Style of Previous Stroke", None), im["groups"][2][1])


class DqCodeReferencePayloadTest(unittest.TestCase):
    """dq_code_reference_payload() is the exact data render_dq_reference_pdf() draws from --
    confirming the JSON shape the on-screen officials page consumes matches it means the two can
    never drift apart."""

    def test_json_shape_matches_the_underlying_data(self):
        payload = dq_code_reference_payload()
        self.assertEqual(len(payload["sides"]), 2)
        side_a = payload["sides"][0]
        self.assertEqual(side_a["side"], "A")
        butterfly = side_a["strokes"][0]
        self.assertEqual(butterfly["name"], "Butterfly")
        kick_group = butterfly["groups"][0]
        self.assertEqual(kick_group["label"], "Kick")
        self.assertEqual(kick_group["items"][0], {"text": "Alternating", "code": "1A"})

    def test_payload_is_json_serializable(self):
        import json

        json.dumps(dq_code_reference_payload())  # raises if anything isn't JSON-safe


class FormatDqGroupLinesTest(unittest.TestCase):
    """The line-wrapping helper: whole "text (code)" units wrap, never split mid-unit, and a
    label-less group omits the "label:" prefix entirely."""

    def test_short_group_stays_on_one_line(self):
        lines = format_dq_group_lines("Kick", [("Alternating", "1A"), ("Breast", "1B")], "Helvetica", 10.0, 500.0)
        self.assertEqual(lines, ["Kick:  Alternating (1A)   •   Breast (1B)"])

    def test_long_group_wraps_without_splitting_a_unit(self):
        # The real Breaststroke Arms group -- 5 items -- forced to wrap at a narrow width.
        items = [
            ("Past Hipline", "3E"),
            ("Non-Simultaneous", "3F"),
            ("Two Strokes Under", "3G"),
            ("Not Same Horizontal Plane", "3H"),
            ("Elbows Recovered Over Water", "3I"),
        ]
        lines = format_dq_group_lines("Arms", items, "Helvetica", 10.0, 250.0)
        self.assertGreater(len(lines), 1)
        # Every "text (code)" unit appears intact in exactly one line -- never broken across two.
        for text, code in items:
            unit = f"{text} ({code})"
            self.assertEqual(sum(unit in line for line in lines), 1, unit)

    def test_no_label_omits_the_prefix(self):
        lines = format_dq_group_lines(None, [("Other", "1T")], "Helvetica", 10.0, 500.0)
        self.assertEqual(lines, ["Other (1T)"])


class RenderDqReferencePdfTest(unittest.TestCase):
    """The actual rendered PDF: two letter pages, everything within page bounds -- checked with
    pdfplumber against the real reportlab output, not assumed from the drawing code alone."""

    @classmethod
    def setUpClass(cls):
        cls.pdf_bytes = render_dq_reference_pdf()

    def test_starts_with_the_pdf_magic_bytes(self):
        self.assertTrue(self.pdf_bytes.startswith(b"%PDF-"))

    def test_two_badge_size_cards_side_a_then_side_b(self):
        import io

        with pdfplumber.open(io.BytesIO(self.pdf_bytes)) as pdf:
            self.assertEqual(len(pdf.pages), 2)
            for page in pdf.pages:
                self.assertEqual(page.width, DQ_CARD_W)
                self.assertEqual(page.height, DQ_CARD_H)
            self.assertIn("Side A", pdf.pages[0].extract_text())
            self.assertIn("Side B", pdf.pages[1].extract_text())

    def test_nothing_overflows_the_page_bounds(self):
        import io

        with pdfplumber.open(io.BytesIO(self.pdf_bytes)) as pdf:
            for page in pdf.pages:
                chars = page.chars
                self.assertTrue(chars)
                self.assertLessEqual(max(c["x1"] for c in chars), page.width)
                self.assertGreaterEqual(min(c["top"] for c in chars), 0)
                self.assertLessEqual(max(c["bottom"] for c in chars), page.height)

    def test_every_stroke_name_appears_on_its_own_page(self):
        import io

        with pdfplumber.open(io.BytesIO(self.pdf_bytes)) as pdf:
            page_a_text = pdf.pages[0].extract_text()
            page_b_text = pdf.pages[1].extract_text()
        for stroke in ("BUTTERFLY", "BACKSTROKE", "FREESTYLE"):
            self.assertIn(stroke, page_a_text)
        for stroke in ("BREASTSTROKE", "INDIVIDUAL MEDLEY", "RELAYS", "MISCELLANEOUS"):
            self.assertIn(stroke, page_b_text)


class DqCodeReferenceApiTest(unittest.TestCase):
    """The two server routes, driven over real HTTP the same way test_subscribe_ics.py and
    test_timeline_api.py drive theirs."""

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

    def test_json_endpoint_matches_the_payload_function(self):
        import json

        url = f"http://127.0.0.1:{self.port}/api/officials/dq-codes"
        with urllib.request.urlopen(url, timeout=30) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get_content_type(), "application/json")
            body = json.loads(response.read())
        self.assertEqual(body, dq_code_reference_payload())

    def test_pdf_endpoint_returns_a_real_pdf_with_a_download_filename(self):
        import io

        url = f"http://127.0.0.1:{self.port}/api/officials/dq-codes.pdf"
        with urllib.request.urlopen(url, timeout=30) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get_content_type(), "application/pdf")
            disposition = response.headers.get("Content-Disposition", "")
            self.assertIn("attachment", disposition)
            self.assertIn("stroke-turn-dq-code-reference.pdf", disposition)
            body = response.read()
        self.assertTrue(body.startswith(b"%PDF-"))
        # Not a byte-for-byte comparison against a second render_dq_reference_pdf() call:
        # reportlab stamps a fresh random document /ID into every PDF it produces, so two
        # separate renders of identical content are never byte-identical. Compare the actual
        # content instead.
        with pdfplumber.open(io.BytesIO(body)) as pdf:
            self.assertEqual(len(pdf.pages), 2)
            self.assertIn("Side A", pdf.pages[0].extract_text())
            self.assertIn("Side B", pdf.pages[1].extract_text())


if __name__ == "__main__":
    unittest.main()
