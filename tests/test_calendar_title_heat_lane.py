"""Real heat/lane gets appended to the per-event ("detailed") calendar title; an ESTIMATE never
does, and a title with no heat/lane data at all stays exactly as it was.

entry_position_line() already draws this real-vs-estimated line for the event DESCRIPTION, right
next to an explicit "Estimated heat/lane" label. A calendar TITLE carries no such caveat -- it's
glanced at on its own, often days later -- so promoting a guess into it would present it as settled
fact. entry_title_heat_lane_suffix() (swimtimeline/extract.py) draws the same line for the title.

All three cases below run the real pipeline against WZAG's real fixtures (no synthetic PDFs, per
this project's standing practice) and read the actual written detailed.json -- the same file the
web app serves -- rather than calling internal helpers directly, so the wiring is covered too.
"""

from pathlib import Path
import json
import tempfile
import unittest

from swimtimeline.extract import analyze_uploads

ROOT = Path(__file__).resolve().parents[1]
WZAG = ROOT / "meets/2026-wzag-championships-boise/input"
PROGRAM = WZAG / "wzag wednesday prelim program.pdf"
DISTANCE = WZAG / "wzag wednesday distance timeline.pdf"
PSYCH = WZAG / "wzag psych sheet v3.pdf"
FLYER = WZAG / "Sanctioned_2026 WZAG Championships - Boise (v5.pdf"
TIMELINE = WZAG / "wzag timelines v4.pdf"

SWIMMER = "Cova, Mila"


def detailed_titles(**kwargs) -> dict[int, str]:
    """event_number -> calendar title, read back from the actual detailed.json this run writes."""
    output_dir = Path(tempfile.mkdtemp())
    analyze_uploads(
        flyer_pdf=FLYER,
        psych_pdf=PSYCH,
        timeline_pdf=TIMELINE,
        swimmer_name=SWIMMER,
        output_dir=output_dir,
        state="",
        meet_timezone="America/Boise",
        meet_venue="Idaho Central Aquatic Center, Boise, ID",
        modes=["detailed"],
        **kwargs,
    )
    payload = json.loads((output_dir / "detailed.json").read_text(encoding="utf-8"))
    return {
        int(event["title"].split("Event ")[1].split(":")[0]): event["title"] for event in payload["events"]
    }


class RealHeatLaneAppearsInTitleTest(unittest.TestCase):
    """WZAG's real Wednesday heat sheet gives events 5 and 11 genuine, non-estimated heat/lane."""

    @classmethod
    def setUpClass(cls):
        cls.titles = detailed_titles(heat_sheet_pdfs=[PROGRAM], distance_timeline_pdf=DISTANCE)

    def test_a_real_heat_lane_event_gets_it_appended_to_the_title(self):
        self.assertEqual(
            self.titles[5],
            "Cova, Mila - Event 5: 11-12 50 Breast (Heat 4, Lane 2)",
        )
        self.assertEqual(
            self.titles[11],
            "Cova, Mila - Event 11: 11-12 100 Free (Heat 3, Lane 6)",
        )

    def test_the_swimmer_name_and_event_name_number_are_still_intact(self):
        # A shared family calendar tracks more than one swimmer -- the name can never be dropped,
        # even once heat/lane is appended.
        self.assertTrue(self.titles[5].startswith("Cova, Mila - Event 5: 11-12 50 Breast"))

    def test_an_event_the_heat_sheet_overlay_does_not_cover_stays_unchanged(self):
        # The real heat sheet is Wednesday-only; events on other days have no real heat/lane in
        # this run and must not get anything appended.
        self.assertEqual(self.titles[28], "Cova, Mila - Event 28: 11-12 200 Free")
        self.assertNotIn("Heat", self.titles[28])


class NoHeatLaneDataLeavesTitleUnchangedTest(unittest.TestCase):
    """A plain psych sheet -- no heat sheet, no estimation opted in -- carries no heat/lane at all
    for these entries, so every title must render exactly as it did before this change."""

    @classmethod
    def setUpClass(cls):
        cls.titles = detailed_titles()

    def test_titles_have_no_heat_lane_suffix(self):
        self.assertEqual(
            self.titles,
            {
                5: "Cova, Mila - Event 5: 11-12 50 Breast",
                11: "Cova, Mila - Event 11: 11-12 100 Free",
                28: "Cova, Mila - Event 28: 11-12 200 Free",
                60: "Cova, Mila - Event 60: 11-12 100 Breast",
                70: "Cova, Mila - Event 70: 11-12 400 Free",
                81: "Cova, Mila - Event 81: 11-12 50 Free",
            },
        )


class EstimatedHeatLaneStaysOutOfTheTitleTest(unittest.TestCase):
    """The estimate_heat_lanes opt-in produces a real (non-None) heat/lane guess -- confirmed
    against the same entries below -- but it must NEVER be promoted into the title, since an
    estimate is explicitly not to be trusted as fact and the title carries no caveat."""

    @classmethod
    def setUpClass(cls):
        cls.titles = detailed_titles(estimate_heat_lanes=True)

    def test_estimation_actually_produced_a_heat_lane_guess_for_this_swimmer(self):
        # A non-vacuous check: if estimation silently didn't apply to any of Mila's events, the
        # negative assertion below would pass for the wrong reason.
        from swimtimeline.extract import (
            assign_days,
            estimate_heat_lanes_for_entries,
            extract_psych_entries,
            extract_text_pages,
            parse_timeline,
        )

        flyer_text = "\n".join(extract_text_pages(FLYER))
        _meet_name, _sessions, timeline_events = parse_timeline(TIMELINE, flyer_text=flyer_text, meet_venue="x")
        entries, _page_counts, _name_warnings = extract_psych_entries(PSYCH, SWIMMER)
        assign_days(entries, timeline_events)
        estimate_heat_lanes_for_entries(entries, timeline_events, flyer_text)
        estimated = [entry for entry in entries if entry.event_number in self.titles]
        self.assertTrue(estimated, "no matching entries found to check")
        for entry in estimated:
            self.assertIsNotNone(entry.heat)
            self.assertIsNotNone(entry.lane)
            self.assertTrue(entry.heat_is_estimated)

    def test_no_estimated_heat_lane_appears_in_any_title(self):
        for event_number, title in self.titles.items():
            self.assertNotIn("Heat", title, f"event {event_number} title should not carry an estimate: {title!r}")
        self.assertEqual(
            self.titles,
            {
                5: "Cova, Mila - Event 5: 11-12 50 Breast",
                11: "Cova, Mila - Event 11: 11-12 100 Free",
                28: "Cova, Mila - Event 28: 11-12 200 Free",
                60: "Cova, Mila - Event 60: 11-12 100 Breast",
                70: "Cova, Mila - Event 70: 11-12 400 Free",
                81: "Cova, Mila - Event 81: 11-12 50 Free",
            },
        )


if __name__ == "__main__":
    unittest.main()
