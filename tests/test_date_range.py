"""parse_date_range()'s single-date fallback, and a permanent regression guard for every real
timeline/flyer/psych fixture already in meets/*/input/.

This function is shared by every meet in the system (cached_timeline() raises "Could not find meet
date range" and refuses to parse ANY session/event data at all if it returns None), so a new
pattern here is one of the highest-blast-radius changes this codebase makes. This file exists as
its own test module -- rather than folding into tests/test_badges.py or a meet-specific file --
because parse_date_range() is a core swimtimeline.extract concern that outlives any one meet or
feature: it has to keep protecting every fixture added after this one too.

Two range-shaped patterns ("M/D/YYYY to M/D/YYYY", "Month D-D, YYYY") are tried first; a single
numeric M/D/YYYY date is a fallback that only fires when both of those fail. The real trap: every
HY-TEK export in this repo stamps its OWN print date on a "MEET MANAGER" banner line, ahead of the
meet's title line, and that print date is never the meet's actual date -- so the fallback is
line-anchored to the title's own "<name> - <date>" shape and explicitly skips the banner line,
rather than searching the whole document for the first M/D/YYYY it can find.

Known, deliberately unaddressed gap: a lone spelled-out date ("September 12, 2026"). No real
fixture in this repo states one, so there is nothing to build or test that shape against.
"""

from datetime import date
from pathlib import Path
import glob
import unittest

from swimtimeline.extract import extract_text_pages, parse_date_range

ROOT = Path(__file__).resolve().parents[1]
CROSWHITE_TIMELINE = ROOT / "meets/2026-croswhite-invite/input/2026-croswhite-invite-timeline.pdf"

# Every real PDF under meets/*/input/, and parse_date_range()'s CORRECT, already-verified result
# for each -- most via the two existing range patterns, a few None (no date range with the word
# "to" or a "Month D-D, YYYY" span at all -- e.g. a relay roster with no date info of its own), and
# exactly one (Croswhite's timeline) via the new single-date fallback this file adds coverage for.
# This table is the regression guard: every one of these must still resolve exactly this way after
# ANY future change to parse_date_range(), not just today's.
EXPECTED_RANGES: list[tuple[str, tuple[date, date] | None]] = [
    ("meets/2026-az-lc-age-group-state/input/age-group-state-meet-flyer.pdf", (date(2026, 7, 23), date(2026, 7, 26))),
    ("meets/2026-az-lc-age-group-state/input/age-group-state-psych-sheet.pdf", (date(2026, 7, 23), date(2026, 7, 26))),
    ("meets/2026-az-lc-age-group-state/input/age-group-state-timeline.pdf", (date(2026, 7, 23), date(2026, 7, 26))),
    # Lettered-session fixture (sessions 1B/1G/2B/2G...), added for the two-pool session-id fix;
    # a plain "to"-range date line, unaffected by that change.
    ("meets/2026-az-sc-age-group-state/input/timeline.pdf", (date(2026, 3, 5), date(2026, 3, 8))),
    ("meets/2026-croswhite-invite/input/2026-croswhite-invite-timeline.pdf", (date(2026, 9, 12), date(2026, 9, 12))),
    ("meets/2026-herculean-invitational/input/2026-herculean-invitational-flyer.pdf", (date(2026, 9, 11), date(2026, 9, 13))),
    ("meets/2026-herculean-invitational/input/2026-herculean-invitational-psych-sheet.pdf", (date(2026, 9, 11), date(2026, 9, 13))),
    ("meets/2026-herculean-invitational/input/2026-herculean-invitational-timeline.pdf", (date(2026, 9, 11), date(2026, 9, 13))),
    ("meets/2026-narwhal-invite/input/Narwhal Invite.pdf", (date(2026, 6, 12), date(2026, 6, 14))),
    ("meets/2026-narwhal-invite/input/narwhal final psych again.pdf", (date(2026, 6, 12), date(2026, 6, 14))),
    ("meets/2026-narwhal-invite/input/narwhal final timeline.pdf", (date(2026, 6, 12), date(2026, 6, 14))),
    ("meets/2026-para-nationals/input/2026_NC_Meet_Packet.eqdx.pdf", (date(2026, 6, 12), date(2026, 6, 14))),
    ("meets/2026-para-nationals/input/2026_para_swim_nationals_psych_sheet.pdf", (date(2026, 6, 12), date(2026, 6, 14))),
    ("meets/2026-shark-open/input/2026-shark-open-flyer.pdf", None),
    ("meets/2026-shark-open/input/2026-shark-open-heat-sheet.pdf", (date(2026, 6, 11), date(2026, 6, 14))),
    ("meets/2026-shark-open/input/2026-shark-open-timeline.pdf", (date(2026, 6, 11), date(2026, 6, 14))),
    ("meets/2026-speedo-invite/input/Mesa Aquatics Club _ Relay Teams.pdf", None),
    ("meets/2026-speedo-invite/input/SpeedoInvite.pdf", (date(2026, 5, 22), date(2026, 5, 25))),
    ("meets/2026-speedo-invite/input/speedo final timeline 2.pdf", (date(2026, 5, 22), date(2026, 5, 25))),
    ("meets/2026-speedo-invite/input/speedo psych.pdf", (date(2026, 5, 22), date(2026, 5, 25))),
    ("meets/2026-wzag-championships-boise/input/2026 WZAG Championships - Event Order.pdf", (date(2026, 8, 5), date(2026, 8, 8))),
    ("meets/2026-wzag-championships-boise/input/Sanctioned_2026 WZAG Championships - Boise (v5.pdf", (date(2026, 8, 5), date(2026, 8, 8))),
    ("meets/2026-wzag-championships-boise/input/wzag friday prelim program.pdf", (date(2026, 8, 5), date(2026, 8, 8))),
    ("meets/2026-wzag-championships-boise/input/wzag friday prelims timeline.pdf", None),
    ("meets/2026-wzag-championships-boise/input/wzag psych sheet v3.pdf", (date(2026, 8, 5), date(2026, 8, 8))),
    ("meets/2026-wzag-championships-boise/input/wzag thursday prelim program v2.pdf", (date(2026, 8, 5), date(2026, 8, 8))),
    ("meets/2026-wzag-championships-boise/input/wzag thursday prelims timeline.pdf", None),
    ("meets/2026-wzag-championships-boise/input/wzag timelines v4.pdf", (date(2026, 8, 5), date(2026, 8, 8))),
    ("meets/2026-wzag-championships-boise/input/wzag warm-up assignments.pdf", (date(2026, 8, 5), date(2026, 8, 8))),
    ("meets/2026-wzag-championships-boise/input/wzag wednesday distance timeline.pdf", None),
    ("meets/2026-wzag-championships-boise/input/wzag wednesday prelim program.pdf", (date(2026, 8, 5), date(2026, 8, 8))),
    ("meets/2026-wzag-championships-boise/input/wzag wednesday prelims timeline.pdf", None),
]


class EveryRealFixtureUnchangedTest(unittest.TestCase):
    """The regression guard the task asked for: every existing real fixture's date_range is
    pinned to its exact known-correct value, so the single-date fallback (or any future change to
    this function) can never silently collapse a real multi-day range, or invent a date where none
    exists, without a test failing here.
    """

    def test_every_real_fixture_matches_its_pinned_expected_range(self):
        for relative_path, expected in EXPECTED_RANGES:
            path = ROOT / relative_path
            self.assertTrue(path.is_file(), f"fixture missing: {relative_path}")
            text = "\n".join(extract_text_pages(path))
            self.assertEqual(parse_date_range(text), expected, relative_path)

    def test_the_expected_table_covers_every_pdf_actually_on_disk(self):
        """A fixture added to meets/*/input/ after this file was written must be added to
        EXPECTED_RANGES too -- this fails loudly instead of the new file just being silently
        unchecked."""
        on_disk = {
            str(Path(p).relative_to(ROOT)) for p in glob.glob(str(ROOT / "meets/*/input/*.pdf"))
        }
        pinned = {relative_path for relative_path, _expected in EXPECTED_RANGES}
        self.assertEqual(on_disk, pinned)

    def test_every_multi_day_real_meet_still_spans_more_than_one_day(self):
        """A tighter check than plain equality: confirm the multi-day fixtures really are
        MULTI-day (start != end), which is exactly the failure mode a fallback bug would produce
        -- collapsing a real range down to a single day rather than merely getting a date wrong."""
        multi_day_meets = {
            "2026-az-lc-age-group-state": True,
            "2026-az-sc-age-group-state": True,
            "2026-herculean-invitational": True,
            "2026-narwhal-invite": True,
            "2026-para-nationals": True,
            "2026-shark-open": True,
            "2026-speedo-invite": True,
            "2026-wzag-championships-boise": True,
        }
        for relative_path, expected in EXPECTED_RANGES:
            if expected is None:
                continue
            meet_id = Path(relative_path).parts[1]
            if multi_day_meets.get(meet_id):
                self.assertNotEqual(expected[0], expected[1], relative_path)


class SingleDateFallbackTest(unittest.TestCase):
    """The new fallback, against the real Croswhite fixture: a lone numeric date in the title
    line, no flyer, no "to"/"-" range anywhere in the document."""

    def setUp(self):
        self.text = "\n".join(extract_text_pages(CROSWHITE_TIMELINE))

    def test_real_croswhite_document_has_no_range_shaped_text_at_all(self):
        """Confirms this genuinely exercises the FALLBACK, not a range pattern that happens to
        also match: neither existing pattern's keywords appear in the real document."""
        self.assertNotIn(" to ", self.text)
        import re

        self.assertIsNone(re.search(r"[A-Za-z]+\s+\d{1,2}\s*(-|–|—)\s*\d{1,2},\s*\d{4}", self.text))

    def test_resolves_to_the_meet_date_as_a_single_day_range(self):
        self.assertEqual(parse_date_range(self.text), (date(2026, 9, 12), date(2026, 9, 12)))

    def test_the_document_contains_a_second_date_that_must_not_be_picked(self):
        """The real trap: HY-TEK's own print-date banner ("9/6/2026") precedes the meet's title
        date ("9/12/2026") in the document, and is NOT the meet's date. The banner line is real
        text from the actual fixture (not fabricated), reused here in isolation to prove the
        banner-skip logic on its own, independent of where it sits relative to the title line."""
        first_line = self.text.splitlines()[0]
        self.assertIn("MEET MANAGER", first_line.upper())
        self.assertIn("9/6/2026", first_line)
        self.assertNotIn("9/12/2026", first_line)
        # The banner line alone, with no title line present, must resolve to nothing -- not the
        # print date pressed into service as a fake single-day meet.
        self.assertIsNone(parse_date_range(first_line))

    def test_range_patterns_are_tried_before_the_fallback_can_fire(self):
        """A range-shaped date pasted alongside Croswhite's real single date must win -- proving
        the fallback truly only fires when both range patterns fail, not merely that it happens to
        agree with them on this fixture."""
        with_range = self.text + "\nSanctioned 9/1/2026 to 9/3/2026\n"
        self.assertEqual(parse_date_range(with_range), (date(2026, 9, 1), date(2026, 9, 3)))


if __name__ == "__main__":
    unittest.main()
