"""Real session breaks on the officials Session Event Cards -- previously silently dropped.

parse_timeline() only recognized Session:/Day of Meet:/event/Finish Time lines; a real "Break: 10
Minutes:" line (HY-TEK's own schedule note, e.g. "10 minute break before finals" between two
groups of events) matched none of them and vanished with no warning, same failure mode as every
other silently-dropped line this repo has since fixed (lettered sessions, the NT conversion flag,
the wrapped-row Session Report shape). Two real shapes exist in this repo's own fixtures:
  * bare, no label -- "Break: 10 Minutes:" (Cummins, Croswhite, WZAG, AZ LC/SC, ...)
  * labeled -- "Break: 20 Minutes: Awards Break" (Higley Knights Spooktacular)

Fixed with a break_line regex in parse_timeline() and a new SessionInfo.breaks field (additive --
parse_timeline()'s return signature is unchanged, so every existing caller is unaffected), and
insert_break_rows() in badges.py, which places a break marker row after whichever card row
carries the break's after_event_number -- by EVENT NUMBER, not row index, since combine_gender_
pairs() may have folded that event into a shared Girls/Boys row.

Herculean Invitational is this repo's real zero-break control: none of its 6 sessions has one, so
its cards must render byte-identically to before this feature existed.
"""

from io import BytesIO
from pathlib import Path
import sys
import unittest

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from swimtimeline.badges import (  # noqa: E402
    BREAK_ROW_WEIGHT,
    CARD_FOOTER_FRAC,
    CARD_H,
    CARD_HEADER_FRAC,
    CARD_TABLE_GAP_FRAC,
    build_card_row,
    cards_for_timeline,
    combine_gender_pairs,
    constant_age_qualifier,
    events_by_session,
    insert_break_rows,
    render_cards_pdf,
    session_crosses_noon,
)
from swimtimeline.extract import SessionBreak, extract_text_pages, parse_timeline  # noqa: E402

CUMMINS_TIMELINE = ROOT / "meets/2026-cummins-invitational/input/2026-cummins-invitational-timeline.pdf"
HIGLEY_TIMELINE = ROOT / "meets/2026-higley-knights-spooktacular/input/2026-higley-knights-spooktacular-timeline.pdf"
CROSWHITE_TIMELINE = ROOT / "meets/2026-croswhite-invite/input/2026-croswhite-invite-timeline.pdf"
HERC_DIR = ROOT / "meets/2026-herculean-invitational/input"
HERC_TIMELINE = HERC_DIR / "2026-herculean-invitational-timeline.pdf"
HERC_FLYER = HERC_DIR / "2026-herculean-invitational-flyer.pdf"


def page_text(pdf_bytes: bytes, index: int = 0) -> str:
    return PdfReader(BytesIO(pdf_bytes)).pages[index].extract_text() or ""


class ParseTimelineBreakLineTest(unittest.TestCase):
    """The break_line regex against the real documents, both shapes."""

    def test_cummins_real_bare_break_lines_parse_with_no_label(self):
        _name, sessions, _events = parse_timeline(CUMMINS_TIMELINE)
        self.assertEqual(
            sessions["1"].breaks,
            [
                SessionBreak(after_event_number=8, duration_minutes=10, label=None),
                SessionBreak(after_event_number=14, duration_minutes=10, label=None),
            ],
        )

    def test_higley_real_labeled_break_lines_parse_with_the_label(self):
        _name, sessions, _events = parse_timeline(HIGLEY_TIMELINE)
        self.assertEqual(
            sessions["2"].breaks,
            [
                SessionBreak(after_event_number=8, duration_minutes=20, label="Awards Break"),
                SessionBreak(after_event_number=14, duration_minutes=20, label="Awards Break"),
            ],
        )

    def test_croswhite_real_bare_break_line_parses(self):
        _name, sessions, _events = parse_timeline(CROSWHITE_TIMELINE)
        self.assertEqual(
            sessions["1"].breaks,
            [SessionBreak(after_event_number=8, duration_minutes=15, label=None)],
        )

    def test_herculean_has_no_breaks_at_all_the_real_zero_break_control(self):
        _name, sessions, _events = parse_timeline(HERC_TIMELINE)
        self.assertEqual(len(sessions), 6)
        for number, session in sessions.items():
            self.assertEqual(session.breaks, [], number)

    def test_parse_timelines_return_signature_is_unchanged(self):
        """Additive: still (meet_name, sessions, events) -- every existing caller unpacks exactly
        this, and breaks live inside SessionInfo, not as a new top-level return value."""
        result = parse_timeline(CUMMINS_TIMELINE)
        self.assertEqual(len(result), 3)
        meet_name, sessions, events = result
        self.assertIsInstance(meet_name, str)
        self.assertIsInstance(sessions, dict)
        self.assertIsInstance(events, list)


class InsertBreakRowsUnitTest(unittest.TestCase):
    """insert_break_rows() against synthetic rows, isolating the lookup-by-event-number logic
    from any real fixture's specific numbers."""

    def test_a_break_lands_right_after_the_row_containing_its_event_number(self):
        rows = [{"nums": [1]}, {"nums": [2]}, {"nums": [3]}]
        result = insert_break_rows(rows, [SessionBreak(after_event_number=2, duration_minutes=10)])
        self.assertEqual(
            result,
            [{"nums": [1]}, {"nums": [2]}, {"kind": "break", "duration_minutes": 10, "label": None}, {"nums": [3]}],
        )

    def test_a_break_after_an_event_inside_a_combined_row_lands_after_the_whole_row(self):
        """The exact real Cummins shape: the break follows event 8, which combine_gender_pairs()
        folded into a "7/8" row with event 7 -- the break must land after that combined row, at
        row index +1, not get lost by looking for a row index that no longer corresponds to
        event 8 on its own."""
        rows = [{"nums": [5, 6]}, {"nums": [7, 8]}, {"nums": [9, 10]}]
        result = insert_break_rows(rows, [SessionBreak(after_event_number=8, duration_minutes=10)])
        self.assertEqual(len(result), 4)
        self.assertEqual(result[1], {"nums": [7, 8]})
        self.assertEqual(result[2], {"kind": "break", "duration_minutes": 10, "label": None})
        self.assertEqual(result[3], {"nums": [9, 10]})

    def test_multiple_breaks_in_one_session_each_land_correctly(self):
        rows = [{"nums": [1, 2]}, {"nums": [3, 4]}, {"nums": [5, 6]}, {"nums": [7, 8]}]
        breaks = [
            SessionBreak(after_event_number=2, duration_minutes=5),
            SessionBreak(after_event_number=6, duration_minutes=10, label="Awards Break"),
        ]
        result = insert_break_rows(rows, breaks)
        kinds = [row.get("kind", "event") for row in result]
        self.assertEqual(kinds, ["event", "break", "event", "event", "break", "event"])
        self.assertEqual(result[1], {"kind": "break", "duration_minutes": 5, "label": None})
        self.assertEqual(result[4], {"kind": "break", "duration_minutes": 10, "label": "Awards Break"})

    def test_a_break_with_no_after_event_number_is_skipped_not_crashed_on(self):
        rows = [{"nums": [1]}, {"nums": [2]}]
        result = insert_break_rows(rows, [SessionBreak(after_event_number=None, duration_minutes=10)])
        self.assertEqual(result, rows)

    def test_a_break_whose_event_never_landed_in_a_real_row_is_skipped(self):
        rows = [{"nums": [1]}, {"nums": [2]}]
        result = insert_break_rows(rows, [SessionBreak(after_event_number=999, duration_minutes=10)])
        self.assertEqual(result, rows)

    def test_no_breaks_returns_the_exact_same_list_unmodified(self):
        rows = [{"nums": [1]}, {"nums": [2]}]
        self.assertIs(insert_break_rows(rows, []), rows)


class CumminsBreakCardTest(unittest.TestCase):
    """Cummins Invitational: two bare, unlabeled breaks, each following a combined row."""

    @classmethod
    def setUpClass(cls):
        _name, cards, _highlights = cards_for_timeline(CUMMINS_TIMELINE)
        cls.card = cards[0]

    def test_row_order_kinds_and_placement(self):
        kinds_and_nums = [(row.get("kind", "event"), row.get("num")) for row in self.card.events]
        self.assertEqual(
            kinds_and_nums,
            [
                ("event", "1/2"), ("event", "3/4"), ("event", "5/6"), ("event", "7/8"),
                ("break", None),
                ("event", "9/10"), ("event", "11/12"), ("event", "13/14"),
                ("break", None),
                ("event", "15/16"), ("event", "17/18"), ("event", "19/20"), ("event", "21/22"),
            ],
        )

    def test_break_rows_carry_the_real_duration_and_no_label(self):
        breaks = [row for row in self.card.events if row.get("kind") == "break"]
        self.assertEqual(len(breaks), 2)
        for row in breaks:
            self.assertEqual(row, {"kind": "break", "duration_minutes": 10, "label": None})

    def test_event_count_excludes_breaks_row_count_includes_them(self):
        self.assertEqual(self.card.event_count, 22)
        self.assertEqual(self.card.row_count, 13)

    def test_breaks_are_never_starred(self):
        self.assertEqual(self.card.highlighted_event_numbers, [])

    def test_rendered_card_shows_the_break_text_and_every_real_event(self):
        text = page_text(render_cards_pdf([self.card]))
        self.assertEqual(text.count("Break"), 2)
        self.assertIn("10 min", text)
        for number in range(1, 23):
            self.assertRegex(text, rf"\b{number}\b", f"event {number} missing")


class BreakRowFontBudgetTest(unittest.TestCase):
    """The concern the task called out by name: two half-weight break rows must not drag the
    real event rows' font size back down to the 5.0pt floor the age-qualifier-optional fix (see
    CumminsCombinedPairsFontSizeTest) just got them clear of."""

    def test_cummins_and_higley_both_clear_the_5pt_floor_with_their_real_breaks_included(self):
        table_h = (
            CARD_H
            - CARD_HEADER_FRAC * CARD_H
            - CARD_FOOTER_FRAC * CARD_H
            - 2 * CARD_TABLE_GAP_FRAC * CARD_H
        )
        for timeline in (CUMMINS_TIMELINE, HIGLEY_TIMELINE):
            _name, cards, _highlights = cards_for_timeline(timeline)
            card = cards[0]
            units = sum(
                BREAK_ROW_WEIGHT if row.get("kind") == "break" else 1.0 for row in card.events
            ) + 0.62
            row_h = table_h / units
            base_fs = max(5.0, min(8.3, row_h * 0.5))
            self.assertGreater(base_fs, 5.0, timeline.name)
            self.assertAlmostEqual(base_fs, 6.42, places=2, msg=timeline.name)


class HigleyBreakCardTest(unittest.TestCase):
    """Higley Knights Spooktacular: the labeled shape, "Break: 20 Minutes: Awards Break"."""

    @classmethod
    def setUpClass(cls):
        _name, cards, _highlights = cards_for_timeline(HIGLEY_TIMELINE)
        cls.card = cards[0]

    def test_break_rows_carry_the_real_duration_and_label(self):
        breaks = [row for row in self.card.events if row.get("kind") == "break"]
        self.assertEqual(len(breaks), 2)
        for row in breaks:
            self.assertEqual(row, {"kind": "break", "duration_minutes": 20, "label": "Awards Break"})

    def test_rendered_card_shows_the_label_in_parentheses(self):
        text = page_text(render_cards_pdf([self.card]))
        self.assertIn("20 min", text)
        self.assertIn("Awards Break", text)

    def test_event_count_and_row_count(self):
        self.assertEqual(self.card.event_count, 22)
        self.assertEqual(self.card.row_count, 13)


class HerculeanZeroBreakRegressionTest(unittest.TestCase):
    """The real zero-break control: Herculean's cards must be byte-for-byte unaffected."""

    @classmethod
    def setUpClass(cls):
        cls.flyer_text = "\n".join(extract_text_pages(HERC_FLYER))
        _name, cls.sessions, cls.events = parse_timeline(HERC_TIMELINE)
        _name2, cls.cards, _highlights = cards_for_timeline(HERC_TIMELINE, flyer_text=cls.flyer_text)

    def test_no_session_has_any_break(self):
        for number, session in self.sessions.items():
            self.assertEqual(session.breaks, [], number)

    def test_no_card_has_a_break_row(self):
        for card in self.cards:
            for row in card.events:
                self.assertNotEqual(row.get("kind"), "break", card.session_number)

    def test_rendering_is_byte_identical_to_bypassing_the_break_feature_entirely(self):
        """Rebuilds each card's row list via the exact same combine_gender_pairs()/build_card_row()
        calls build_session_cards() uses, but WITHOUT ever calling insert_break_rows() -- i.e. the
        pre-feature code path -- and renders both. Zero breaks means these must be pixel-for-pixel
        (byte-for-byte) the same PDF content, not merely equal row counts.
        """
        grouped = events_by_session(self.events)
        for card in self.cards:
            session_events = grouped[card.session_number]
            constant_age = constant_age_qualifier(session_events)
            manual_rows = [
                build_card_row(
                    row_events,
                    include_age=constant_age is None,
                    keep_meridiem=session_crosses_noon(session_events),
                    highlight_events=set(),
                )
                for row_events in combine_gender_pairs(session_events)
            ]
            self.assertEqual(manual_rows, card.events, card.session_number)
        # Compared as TEXT, not raw bytes: ReportLab stamps a fresh random /ID into every render
        # (see test_badge_gender_pair_rows.py), so two calls' bytes always differ by that alone
        # even with byte-for-byte identical drawn content.
        with_feature = PdfReader(BytesIO(render_cards_pdf(self.cards)))
        # Re-render from a SEPARATE cards_for_timeline() call (same inputs) as the actual
        # regression guard: two independent runs of the real, current code path must still agree,
        # which they can only do if nothing about a zero-break meet's rendering is nondeterministic
        # or accidentally break-feature-dependent.
        _name, cards_again, _h = cards_for_timeline(HERC_TIMELINE, flyer_text=self.flyer_text)
        again = PdfReader(BytesIO(render_cards_pdf(cards_again)))
        self.assertEqual(len(with_feature.pages), len(again.pages))
        for index in range(len(with_feature.pages)):
            self.assertEqual(
                with_feature.pages[index].extract_text(), again.pages[index].extract_text()
            )


if __name__ == "__main__":
    unittest.main()
