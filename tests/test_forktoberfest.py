"""2026 Mona Nyheim-Canales Forktoberfest -- hosted on the Officials side only (timeline-only,
same minimal pattern as the Croswhite and Cummins entries in data/current_meets.json): no psych
sheet, so no swimmer search/subscribe, just session event cards straight from the real Session
Report.

Real facts, verified directly from the uploaded timeline: single day, single session ("1
Saturday", 10/17/2026, starts 3:15 PM, finishes 9:34 PM), 22 individual/relay events combined into
11 Boys/Girls row pairs, one real "Break: 15 Minutes" after event 8.

This meet's title line ("2026 Mona Nyheim-Canales Forktoberfest - 10/17/2026") is also the real
document that drove fixing parse_meet_name()'s single-day-meet branch in swimtimeline/extract.py:
it contains none of the old keyword heuristic's words (invite/invitational/open/championship/
nationals), so before that fix it fell back to the generic "Swim Meet" rather than its real title.
See tests/test_mac_red_v_black_intrasquad.py for the regression check across every other real
fixture in this repo.
"""

from io import BytesIO
from pathlib import Path
import json
import unittest

from pypdf import PdfReader

from swimtimeline.badges import cards_for_timeline, render_cards_pdf
from swimtimeline.extract import parse_timeline

ROOT = Path(__file__).resolve().parents[1]
MEET_DIR = ROOT / "meets/2026-forktoberfest/input"
TIMELINE = MEET_DIR / "2026-forktoberfest-timeline.pdf"
MEET_NAME = "2026 Mona Nyheim-Canales Forktoberfest - 10/17/2026"


class RegistrationTest(unittest.TestCase):
    """The hosted meet entry in data/current_meets.json matches the real facts, is timeline-only
    (officials side), and its file actually exists on disk."""

    @classmethod
    def setUpClass(cls):
        data = json.loads((ROOT / "data/current_meets.json").read_text(encoding="utf-8"))
        cls.meet = next(m for m in data["current_meets"] if m["id"] == "2026-forktoberfest")

    def test_dates_and_venue(self):
        self.assertEqual(self.meet["name"], "2026 Mona Nyheim-Canales Forktoberfest")
        self.assertEqual(self.meet["dates"], "2026-10-17")
        self.assertEqual(self.meet["start_date"], "2026-10-17")
        self.assertEqual(self.meet["end_date"], "2026-10-17")
        self.assertEqual(self.meet["expires_at"], "2026-10-18")
        self.assertEqual(self.meet["state"], "AZ")
        self.assertEqual(self.meet["timezone"], "America/Phoenix")
        self.assertEqual(self.meet["venue"], "ASU Activity Center, Tempe, AZ")

    def test_projected_not_final_since_this_is_a_pre_meet_schedule(self):
        self.assertEqual(self.meet["timeline_type"], "projected")

    def test_ready_status_with_no_standards_override(self):
        self.assertEqual(self.meet["status"], "ready")
        self.assertNotIn("standards", self.meet)

    def test_timeline_only_no_flyer_psych_or_relay_doc(self):
        self.assertIsNone(self.meet["files"]["flyer"])
        self.assertIsNone(self.meet["files"]["psych"])
        self.assertIsNone(self.meet["files"]["relay"])
        self.assertEqual(
            self.meet["files"]["timeline"],
            "meets/2026-forktoberfest/input/2026-forktoberfest-timeline.pdf",
        )
        self.assertEqual(self.meet["documents"], ["Timeline"])
        self.assertTrue((ROOT / self.meet["files"]["timeline"]).is_file())


class SessionCardTest(unittest.TestCase):
    """cards_for_timeline() on the real document: correct meet name (not "Swim Meet"), the single
    session's real date/start/finish, 22 events, and the real 15-minute break after event 8."""

    @classmethod
    def setUpClass(cls):
        cls.meet_name, cls.sessions, cls.events = parse_timeline(TIMELINE)
        cls.card_meet_name, cls.cards, cls.highlights = cards_for_timeline(TIMELINE)

    def test_meet_name_is_the_real_title_not_swim_meet(self):
        self.assertEqual(self.meet_name, MEET_NAME)
        self.assertEqual(self.card_meet_name, MEET_NAME)

    def test_single_saturday_session_with_real_date_and_times(self):
        self.assertEqual(len(self.cards), 1)
        card = self.cards[0]
        self.assertEqual(card.session_label, "SESSION 1 — SATURDAY")
        self.assertEqual(card.date_label, "Sat, Oct 17")
        self.assertEqual(card.start_label, "3:15 PM")
        self.assertEqual(card.finish_label, "9:34 PM")

    def test_twenty_two_events(self):
        self.assertEqual(len(self.events), 22)
        self.assertEqual(self.cards[0].event_count, 22)

    def test_the_real_break_after_event_8_lands_right_after_its_row(self):
        card = self.cards[0]
        self.assertEqual(card.row_count, 12)  # 11 combined Boys/Girls rows + 1 break
        kinds_and_nums = [(row.get("kind", "event"), row.get("num")) for row in card.events]
        self.assertEqual(
            kinds_and_nums,
            [
                ("event", "1/2"), ("event", "3/4"), ("event", "5/6"), ("event", "7/8"),
                ("break", None),
                ("event", "9/10"), ("event", "11/12"), ("event", "13/14"), ("event", "15/16"),
                ("event", "17/18"), ("event", "19/20"), ("event", "21/22"),
            ],
        )
        break_row = card.events[4]
        self.assertEqual(break_row, {"kind": "break", "duration_minutes": 15, "label": None})

    def test_no_psych_sheet_still_produces_a_working_card(self):
        """Timeline-only, like Croswhite -- cards_for_timeline() must not require a psych sheet."""
        self.assertEqual(self.highlights.event_numbers, set())

    def test_renders_at_one_page(self):
        reader = PdfReader(BytesIO(render_cards_pdf(self.cards)))
        self.assertEqual(len(reader.pages), 1)


class OfficialsMeetPickerTest(unittest.TestCase):
    """Appears in officials_meets_payload()'s current-meets list -- the same discovery path
    /officials uses to populate its hosted-meet picker."""

    def test_listed_as_a_current_meet(self):
        try:
            from webapp.server import officials_meets_payload
        except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
            raise unittest.SkipTest(
                "webapp.server needs Python 3.12: the stdlib cgi module was removed in 3.13"
            ) from exc

        payload = officials_meets_payload()
        current_ids = [meet["id"] for meet in payload["current_meets"]]
        self.assertIn("2026-forktoberfest", current_ids)
        entry = next(meet for meet in payload["current_meets"] if meet["id"] == "2026-forktoberfest")
        self.assertEqual(entry["name"], "2026 Mona Nyheim-Canales Forktoberfest")
        self.assertEqual(entry["short_name"], "Forktoberfest")


if __name__ == "__main__":
    unittest.main()
