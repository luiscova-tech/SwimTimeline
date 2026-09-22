"""Next Up card: the timezone-aware start_at field, and the boundary logic it exists to support.

summarize_swim()/summarize_relay() (swimtimeline/extract.py) already emit "sort_start" -- a naive
wall-clock ISO string used only for sorting (see analyze_uploads) -- and now also emit "start_at",
the same instant with a real UTC offset attached, so a browser's `new Date(...)` can parse it
unambiguously regardless of the viewer's own timezone. This is what the frontend's "Next up" card
and live countdown (webapp/static/shared-render.js) are built on; sort_start's own value and
meaning are untouched.

These tests deliberately use real fixtures at two DIFFERENT real timezones -- Phoenix, which never
observes DST, and Shark Open's Eastern-time venue, which does -- because a bug that only shows up
away from the venue's own zone, or only outside DST, would sail through any test that only ever
checks Phoenix in winter.
"""

from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from swimtimeline.extract import analyze_uploads

ROOT = Path(__file__).resolve().parents[1]

HIGLEY_DIR = ROOT / "meets/2026-higley-knights-spooktacular/input"
HIGLEY_TIMELINE = HIGLEY_DIR / "2026-higley-knights-spooktacular-timeline.pdf"
HIGLEY_HEAT_SHEET = HIGLEY_DIR / "2026-higley-knights-spooktacular-heat-sheet.pdf"

SHARK_DIR = ROOT / "meets/2026-shark-open/input"
SHARK_FLYER = SHARK_DIR / "2026-shark-open-flyer.pdf"
SHARK_HEAT_SHEET = SHARK_DIR / "2026-shark-open-heat-sheet.pdf"
SHARK_TIMELINE = SHARK_DIR / "2026-shark-open-timeline.pdf"

WZAG_DIR = ROOT / "meets/2026-wzag-championships-boise/input"
WZAG_FLYER = WZAG_DIR / "Sanctioned_2026 WZAG Championships - Boise (v5.pdf"
WZAG_PSYCH = WZAG_DIR / "wzag psych sheet v3.pdf"
WZAG_TIMELINE = WZAG_DIR / "wzag timelines v4.pdf"


def analyze_higley():
    return analyze_uploads(
        flyer_pdf=None,
        psych_pdf=HIGLEY_HEAT_SHEET,
        timeline_pdf=HIGLEY_TIMELINE,
        swimmer_name="Beltran, Christian",
        output_dir=Path(tempfile.mkdtemp()),
        state="AZ",
        meet_timezone="America/Phoenix",
        meet_venue="Williams Field High School, Gilbert, AZ",
        modes=["daily"],
        estimate_heat_lanes=False,
        timeline_projected=False,
    )


def analyze_shark_open():
    return analyze_uploads(
        flyer_pdf=SHARK_FLYER,
        psych_pdf=SHARK_HEAT_SHEET,
        timeline_pdf=SHARK_TIMELINE,
        swimmer_name="Sydney Hardy",
        output_dir=Path(tempfile.mkdtemp()),
        state="FL",
        modes=["daily"],
    )


class StartAtCarriesRealOffsetTest(unittest.TestCase):
    """start_at must carry the VENUE's real UTC offset -- never this app's own America/Phoenix
    default when the venue is elsewhere -- and must never disturb sort_start's existing naive
    value or meaning."""

    def test_higley_phoenix_offset_matches_sort_start_wall_clock(self):
        payload = analyze_higley()
        items = payload["items"]
        self.assertTrue(items)
        for item in items:
            # Phoenix never observes DST -- always -07:00, year-round.
            self.assertTrue(item["start_at"].endswith("-07:00"), item)
            # Same wall-clock digits as sort_start; start_at just adds a real offset on top.
            self.assertEqual(item["start_at"][:19], item["sort_start"], item)
            self.assertEqual(len(item["sort_start"]), 19, "sort_start must stay naive/offset-free")

    def test_shark_open_eastern_offset_not_phoenix(self):
        payload = analyze_shark_open()
        items = payload["items"]
        self.assertTrue(items)
        for item in items:
            # Meet dates are in June -- Eastern DAYLIGHT time (-04:00), not -05:00 and not this
            # app's America/Phoenix default (-07:00).
            self.assertTrue(item["start_at"].endswith("-04:00"), item)
            self.assertFalse(item["start_at"].endswith("-07:00"), item)
            self.assertEqual(item["start_at"][:19], item["sort_start"], item)
            self.assertEqual(len(item["sort_start"]), 19, "sort_start must stay naive/offset-free")


class NextUpBoundaryRealFixtureTest(unittest.TestCase):
    """The frontend's "next up" pick is "the first item whose start_at is still in the future"
    (see nextUpwardItem in shared-render.js). This pins that exact comparison at the real
    boundary of a real swim's own start instant, using Beltran, Christian's two real, verified
    Higley events -- never synthetic times."""

    def test_selection_flips_exactly_at_the_first_events_own_start_instant(self):
        payload = analyze_higley()
        self.assertEqual(payload["verified_event_count"], 2)
        items = sorted(payload["items"], key=lambda item: item["start_at"])
        self.assertEqual(len(items), 2)
        first, second = items
        self.assertNotEqual(first["event_number"], second["event_number"])

        first_start = datetime.fromisoformat(first["start_at"])
        second_start = datetime.fromisoformat(second["start_at"])
        self.assertLess(first_start, second_start)

        def next_up(now):
            return next((item for item in items if datetime.fromisoformat(item["start_at"]) > now), None)

        # One second before the first event's own start: it is still upcoming -- it's next up.
        still_upcoming = next_up(first_start - timedelta(seconds=1))
        self.assertIsNotNone(still_upcoming)
        self.assertEqual(still_upcoming["event_number"], first["event_number"])

        # One second after that SAME instant: the first event is no longer upcoming -- the
        # second one is now next up.
        just_started = next_up(first_start + timedelta(seconds=1))
        self.assertIsNotNone(just_started)
        self.assertEqual(just_started["event_number"], second["event_number"])

        # And once even the last event has started, nothing is left to swim next.
        self.assertIsNone(next_up(second_start + timedelta(seconds=1)))


class RelayStartAtTest(unittest.TestCase):
    """summarize_relay() got the identical one-line change as summarize_swim() -- confirmed here
    against WZAG's real tentative team-relay fixture (see tests/test_team_relay.py), not a
    constructed RelayEvent."""

    def test_tentative_relay_items_carry_a_well_formed_start_at(self):
        payload = analyze_uploads(
            flyer_pdf=WZAG_FLYER,
            psych_pdf=WZAG_PSYCH,
            timeline_pdf=WZAG_TIMELINE,
            swimmer_name="Cova, Mila L",
            output_dir=Path(tempfile.mkdtemp()),
            meet_timezone="America/Boise",
            meet_venue="Idaho Central Aquatic Center, Boise, ID",
            modes=["detailed"],
            include_relays=True,
            state="",
        )
        relays = [item for item in payload["items"] if item["type"] == "relay"]
        self.assertEqual({item["event_number"] for item in relays}, {24, 25, 50, 52, 75, 76, 99, 101})
        for item in relays:
            parsed = datetime.fromisoformat(item["start_at"])
            self.assertIsNotNone(parsed.tzinfo, item)
            self.assertEqual(item["start_at"][:19], item["sort_start"], item)


if __name__ == "__main__":
    unittest.main()
