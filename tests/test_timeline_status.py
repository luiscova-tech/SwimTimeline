"""Regression fixtures for final-vs-projected timeline certainty in generated calendars.

A meet's timeline is either a settled *final* schedule (Narwhal, Shark Open, AZ Age Group State)
or a pre-meet *projected* schedule whose times may still shift (WZAG). Which one is a real,
machine-readable field on the meet record -- ``timeline_type`` in data/current_meets.json --
threaded through as ``timeline_projected`` exactly like meet_timezone/meet_venue, NOT a string
match on display text.

Projected meets get STATUS:TENTATIVE on every calendar event plus an explicit per-event caveat
line; final meets are unchanged (STATUS:CONFIRMED, no caveat). These tests pin both directions.
"""

from pathlib import Path
import unittest

from swimtimeline.extract import analyze_uploads, PROJECTED_TIMELINE_NOTE

ROOT = Path(__file__).resolve().parents[1]

# A distinctive, punctuation-free slice of PROJECTED_TIMELINE_NOTE. Checking a fragment avoids
# iCal escaping (the note's ";" and "," are written as "\;"/"\," in the .ics), while still
# guarding both that the note fires and roughly what it says.
NOTE_FRAGMENT = "pre-meet projected timeline and may still shift"
assert NOTE_FRAGMENT in PROJECTED_TIMELINE_NOTE

WZAG = dict(
    flyer_pdf=ROOT / "meets/2026-wzag-championships-boise/input/Sanctioned_2026 WZAG Championships - Boise (v5.pdf",
    psych_pdf=ROOT / "meets/2026-wzag-championships-boise/input/wzag psych sheet v3.pdf",
    timeline_pdf=ROOT / "meets/2026-wzag-championships-boise/input/wzag timelines v4.pdf",
    swimmer_name="Stein, Layla",
    state="ID",
    meet_timezone="America/Boise",
    meet_venue="Idaho Central Aquatic Center, Boise, ID",
)

NARWHAL = dict(
    flyer_pdf=ROOT / "meets/2026-narwhal-invite/input/Narwhal Invite.pdf",
    psych_pdf=ROOT / "meets/2026-narwhal-invite/input/narwhal final psych again.pdf",
    timeline_pdf=ROOT / "meets/2026-narwhal-invite/input/narwhal final timeline.pdf",
    swimmer_name="Cova, Mila L",
    state="AZ",
)


def unfolded_ics(out_dir, name):
    # Undo iCal 75-octet line folding so substring checks aren't split mid-word.
    raw = (out_dir / f"{name}.ics").read_text(encoding="utf-8")
    return raw.replace("\r\n ", "").replace("\n ", "")


class ProjectedTimelineTest(unittest.TestCase):
    """WZAG's timeline is a pre-meet projection -> tentative events with a caveat on each."""

    def setUp(self):
        self.out = Path("/private/tmp/swimtimeline-timeline-projected")
        analyze_uploads(output_dir=self.out, modes=["daily", "detailed", "weekend"],
                        timeline_projected=True, **WZAG)

    def test_every_calendar_type_marks_events_tentative_not_confirmed(self):
        for name in ("daily", "detailed", "weekend"):
            ics = unfolded_ics(self.out, name)
            self.assertIn("STATUS:TENTATIVE", ics, name)
            self.assertNotIn("STATUS:CONFIRMED", ics, name)

    def test_every_calendar_type_carries_the_projection_caveat(self):
        for name in ("daily", "detailed", "weekend"):
            ics = unfolded_ics(self.out, name)
            self.assertIn(NOTE_FRAGMENT, ics, name)

    def test_caveat_count_matches_event_count_in_detailed(self):
        # Stein swims 6 individual events; each detailed VEVENT gets its own caveat + TENTATIVE.
        ics = unfolded_ics(self.out, "detailed")
        self.assertEqual(ics.count("STATUS:TENTATIVE"), 6)
        self.assertEqual(ics.count(NOTE_FRAGMENT), 6)


class FinalTimelineTest(unittest.TestCase):
    """Narwhal's timeline is final -> confirmed events, no caveat, no behavior change."""

    def setUp(self):
        self.out = Path("/private/tmp/swimtimeline-timeline-final")
        # timeline_projected defaults to False -- the same as an unmarked/uploaded meet.
        analyze_uploads(output_dir=self.out, modes=["daily", "detailed", "weekend"], **NARWHAL)

    def test_every_calendar_type_stays_confirmed(self):
        for name in ("daily", "detailed", "weekend"):
            ics = unfolded_ics(self.out, name)
            self.assertIn("STATUS:CONFIRMED", ics, name)
            self.assertNotIn("STATUS:TENTATIVE", ics, name)

    def test_no_projection_caveat_anywhere(self):
        for name in ("daily", "detailed", "weekend"):
            ics = unfolded_ics(self.out, name)
            self.assertNotIn(NOTE_FRAGMENT, ics, name)
            self.assertNotIn("projected timeline", ics, name)


if __name__ == "__main__":
    unittest.main()
