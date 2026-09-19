"""A Session Report whose event rows print one cell per physical line, not one line per event.

Real fixture: meets/2026-cummins-invitational/input/2026-cummins-invitational-timeline.pdf -- a
real HY-TEK Session Report for the Cummins Invitational 26. Every other real Session Report
fixture in this repo (e.g. 2026 Croswhite Invite's timeline) has its PDF export join each event's
Round/Event/Entries/Heats/Starts-at cells onto one line, e.g.:
    Finals 2 Girls 200 Medley Relay 32 4 _______06:00 PM
This meet's export instead prints each cell on its own line:
    Finals
    1 Boys 200 Medley Relay
    29
    3
    09:00 AM
    ____
parse_timeline()'s event_line/finish_line regexes only match the joined shape, so before the fix
cards_for_timeline() raised "No sessions or events were found in that timeline PDF" for this real,
correctly-formed document -- not a corrupt or unsupported file, just a different (and equally
valid) column layout. merge_wrapped_session_report_rows() recognizes the exact split and rejoins
it before the existing regexes ever see it, rather than teaching every regex two shapes.
"""

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from swimtimeline.badges import cards_for_timeline  # noqa: E402
from swimtimeline.extract import merge_wrapped_session_report_rows, parse_timeline  # noqa: E402

TIMELINE = ROOT / "meets/2026-cummins-invitational/input/2026-cummins-invitational-timeline.pdf"


class WrappedSessionReportRowsUnitTest(unittest.TestCase):
    """merge_wrapped_session_report_rows() in isolation, on synthetic input."""

    def test_a_split_event_row_is_rejoined_into_the_single_line_shape(self):
        lines = ["Finals", "1 Boys 200 Medley Relay", "29", "3", "09:00 AM", "____"]
        self.assertEqual(
            merge_wrapped_session_report_rows(lines),
            ["Finals 1 Boys 200 Medley Relay 29 3 ____09:00 AM"],
        )

    def test_the_trailing_placeholder_line_is_optional(self):
        """The blank hand-written-result column isn't always followed by a lone "____" line
        (e.g. the very last row on a page) -- merging must not require it."""
        lines = ["Finals", "1 Boys 200 Medley Relay", "29", "3", "09:00 AM"]
        self.assertEqual(
            merge_wrapped_session_report_rows(lines),
            ["Finals 1 Boys 200 Medley Relay 29 3 ____09:00 AM"],
        )

    def test_a_split_finish_time_footer_is_rejoined_too(self):
        lines = ["Finish Time", "02:20 PM", "____"]
        self.assertEqual(merge_wrapped_session_report_rows(lines), ["Finish Time ____02:20 PM"])

    def test_an_already_joined_line_passes_through_unchanged(self):
        """Croswhite's shape (and every other existing fixture) must be completely unaffected."""
        lines = ["Finals 2 Girls 200 Medley Relay 32 4 _______06:00 PM"]
        self.assertEqual(merge_wrapped_session_report_rows(lines), lines)

    def test_a_bare_round_line_with_no_valid_followup_is_left_alone(self):
        """A "Finals" line not actually followed by the split-row shape (e.g. end of page, or an
        unrelated document) must not be swallowed or corrupted."""
        lines = ["Finals", "Some unrelated text", "not a number"]
        self.assertEqual(merge_wrapped_session_report_rows(lines), lines)


class WrappedSessionReportRowsRealFixtureTest(unittest.TestCase):
    """The real Cummins Invitational 26 timeline, end to end."""

    @classmethod
    def setUpClass(cls):
        cls.meet_name, cls.sessions, cls.events = parse_timeline(TIMELINE)

    def test_all_22_events_parse(self):
        # Before the fix: 0. The whole document silently produced no events at all.
        self.assertEqual(len(self.events), 22)

    def test_session_1_carries_the_real_start_and_finish_times(self):
        session = self.sessions["1"]
        self.assertEqual(session.start_time, "09:00")
        self.assertEqual(session.finish_time, "14:20")

    def test_the_first_and_last_events_match_the_real_document(self):
        first, last = self.events[0], self.events[-1]
        self.assertEqual((first.event_number, first.event_name), (1, "Boys 200 Medley Relay"))
        self.assertEqual(first.start.strftime("%I:%M %p"), "09:00 AM")
        self.assertEqual((last.event_number, last.event_name), (22, "Girls 400 Freestyle Relay"))
        self.assertEqual(last.start.strftime("%I:%M %p"), "02:05 PM")

    def test_entries_and_heats_are_read_from_their_own_split_lines(self):
        by_number = {e.event_number: e for e in self.events}
        self.assertEqual((by_number[7].entries, by_number[7].heats), (86, 9))  # Boys 50 Free


class WrappedSessionReportRowsCardTest(unittest.TestCase):
    """cards_for_timeline(), the function that raised "No sessions or events were found" before
    the fix."""

    def test_the_card_carries_all_22_events(self):
        _meet_name, cards, _highlights = cards_for_timeline(TIMELINE)
        self.assertEqual(len(cards), 1)
        self.assertEqual(len(cards[0].events), 22)


if __name__ == "__main__":
    unittest.main()
