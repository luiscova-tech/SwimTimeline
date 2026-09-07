"""2026 Herculean Invitational (Swim Neptune / Mesa Aquatics Club) -- the first REAL short course
yards (SCY) meet fixture in this repo (every other real meet here is LCM), and the first real meet
whose two age divisions (12&Over, 11&Under) run in fully parallel, simultaneous pools each day on
separate event-number blocks sharing one warm-up/start time.

Two things this fixture exercises for the first time against real data, both covered elsewhere with
their own dedicated tests and just spot-checked again here end-to-end:
  * The day-varying, swimmer-universal warm-up time -- see tests/test_warmup.py's
    DayVaryingPerSessionWarmupTest for the detailed keyed-by-date-not-session-number proof.
  * SCY-aware standards lookups (USA-S/AZSI/Sectional all correctly label "SCY", not silently
    defaulting to no course or the wrong one) -- covered by CovaRealEventsTest below.

This file additionally confirms: no relay events exist anywhere in the meet's documents; the two
parallel pools parse as independent sessions rather than bleeding into each other's time windows;
the "entry sheet lists event(s) as ..." calendar line correctly says "SC Yard" (it used to hardcode
"LC Meter" unconditionally -- untested until this SCY fixture existed to catch it); the positive
check-in note (a per-meet hardcoded lookup table, not a generic flyer parser) correctly covers this
meet's 400-yard-and-longer events; and three real same-surname sibling/teammate groups (Vickers,
Post, Beltran) are flagged ambiguous by last name alone and resolve individually by first name.
"""

from pathlib import Path
import tempfile
import unittest

from swimtimeline.extract import analyze_uploads, extract_text_pages, parse_timeline

ROOT = Path(__file__).resolve().parents[1]
HERC = Path(ROOT / "meets/2026-herculean-invitational/input")
FLYER = HERC / "2026-herculean-invitational-flyer.pdf"
PSYCH = HERC / "2026-herculean-invitational-psych-sheet.pdf"
TIMELINE = HERC / "2026-herculean-invitational-timeline.pdf"
VENUE = "Kino Junior High, 848 N. Horne, Mesa, AZ 85203"


def analyze(name, **kwargs):
    out = Path(tempfile.mkdtemp())
    return analyze_uploads(
        flyer_pdf=FLYER, psych_pdf=PSYCH, timeline_pdf=TIMELINE,
        swimmer_name=name, output_dir=out, state="AZ",
        meet_timezone="America/Phoenix", meet_venue=VENUE,
        modes=["daily"], timeline_projected=True, **kwargs,
    )


def swims_only(result):
    return [item for item in result["items"] if item.get("type") != "relay"]


class NoRelayEventsConfirmedTest(unittest.TestCase):
    """Confirmed by reading the actual documents, not assumed: zero relay events anywhere."""

    def test_no_document_mentions_relay(self):
        for label, path in [("flyer", FLYER), ("psych", PSYCH), ("timeline", TIMELINE)]:
            text = "\n".join(extract_text_pages(path))
            self.assertNotIn("relay", text.lower(), label)

    def test_no_relay_items_in_a_real_swimmers_calendar(self):
        result = analyze("Cova, Mila")
        self.assertEqual(result["items"], swims_only(result))


class ParallelDualPoolSessionTest(unittest.TestCase):
    """The real HY-TEK timeline reports 6 separate sessions (not 3) -- a 12&Over and an 11&Under
    pool for each of the 3 days, sharing that day's warm-up/start time but running fully
    independently. Verified directly against the parsed timeline, then end-to-end for two real
    swimmers (one per pool) on the same Friday.
    """

    def test_timeline_reports_six_sessions_paired_by_day_and_shared_start(self):
        flyer_text = "\n".join(extract_text_pages(FLYER))
        _name, sessions, _events = parse_timeline(TIMELINE, flyer_text=flyer_text, meet_venue=VENUE)
        self.assertEqual(len(sessions), 6)
        pairs = [(1, 2), (3, 4), (5, 6)]
        for a, b in pairs:
            self.assertEqual(sessions[a].date, sessions[b].date, (a, b))
            self.assertEqual(sessions[a].start_time, sessions[b].start_time, (a, b))
            self.assertEqual(sessions[a].warmup_time, sessions[b].warmup_time, (a, b))
        self.assertNotEqual(sessions[1].date, sessions[3].date)
        self.assertNotEqual(sessions[3].date, sessions[5].date)

    def test_two_swimmers_in_different_pools_get_independent_non_bleeding_windows(self):
        # Zaffos, Selah (12&Over, event 21) and Post, Zoey (11&Under) both swim Friday, at the
        # same warm-up/start -- but each swimmer's own windows must come from their OWN pool's
        # event chain, not drift into the other pool's times.
        twelve_over = swims_only(analyze("Zaffos, Selah"))
        eleven_under = swims_only(analyze("Post, Zoey"))
        friday_12over = [s for s in twelve_over if s["day"] == "Friday"]
        friday_11under = [s for s in eleven_under if s["day"] == "Friday"]
        self.assertTrue(friday_12over and friday_11under)
        for s in friday_12over:
            self.assertLess(int(s["event_number"]), 100, s)
        for s in friday_11under:
            self.assertGreaterEqual(int(s["event_number"]), 100, s)
        # Both pools' Friday sessions start at 5:30 PM -- the very first heat of whichever event
        # each swimmer's OWN pool begins with.
        self.assertEqual(min(s["window"].split("-")[0] for s in friday_12over), "5:30 PM")
        self.assertEqual(min(s["window"].split("-")[0] for s in friday_11under), "5:30 PM")


class CovaRealEventsTest(unittest.TestCase):
    """Mila Cova (MAC-AZ, age 12), events #1, #5, #7, #27, #29 -- exactly as entered on the real
    psych sheet. Seed, window (the full event-wide timeline block, since no heat sheet exists yet
    for this projected-timeline meet), and SCY-labeled benchmarks all verified against the actual
    documents.
    """

    @classmethod
    def setUpClass(cls):
        cls.swims = {int(s["event_number"]): s for s in swims_only(analyze("Cova, Mila"))}

    def test_exactly_the_five_real_events_are_present(self):
        self.assertEqual(set(self.swims), {1, 5, 7, 27, 29})

    def test_seeds_and_windows_match_the_real_documents(self):
        cases = {
            1: ("55.49", "Friday", "5:30 PM-5:48 PM"),
            5: ("34.84", "Friday", "6:22 PM-6:31 PM"),
            7: ("1:08.12", "Friday", "6:40 PM-6:54 PM"),
            27: ("1:17.66", "Sunday", "9:46 AM-10:00 AM"),
            29: ("2:04.38", "Sunday", "10:12 AM-10:33 AM"),
        }
        for event_number, (seed, day, window) in cases.items():
            swim = self.swims[event_number]
            self.assertEqual(swim["seed_time"], seed, event_number)
            self.assertEqual(swim["day"], day, event_number)
            self.assertEqual(swim["window"], window, event_number)

    def test_benchmarks_are_labeled_scy_not_silently_wrong_or_absent(self):
        for event_number, swim in self.swims.items():
            usa = swim["benchmarks"]["usa"]
            lsc = swim["benchmarks"]["lsc"]
            self.assertIn("SCY", usa, event_number)
            self.assertIn("SCY", lsc, event_number)


class ScyEntrySheetCourseLabelTest(unittest.TestCase):
    """The "Pool/course: ...; entry sheet lists event(s) as X" calendar line used to hardcode "LC
    Meter" unconditionally -- nothing had ever caught it because no real fixture before this one
    was anything but LCM. It must say "SC Yard" for this meet, never "LC Meter".
    """

    def test_daily_calendar_says_sc_yard(self):
        out = Path(tempfile.mkdtemp())
        analyze_uploads(
            flyer_pdf=FLYER, psych_pdf=PSYCH, timeline_pdf=TIMELINE,
            swimmer_name="Cova, Mila", output_dir=out, state="AZ",
            meet_timezone="America/Phoenix", meet_venue=VENUE,
            modes=["daily", "detailed"], timeline_projected=True,
        )
        for filename in ("daily.ics", "detailed.ics"):
            ics = (out / filename).read_text(encoding="utf-8").replace("\r\n ", "").replace("\n ", "")
            self.assertIn("entry sheet lists event", ics, filename)
            self.assertIn("SC Yard", ics, filename)
            self.assertNotIn("LC Meter", ics, filename)


class PositiveCheckinTest(unittest.TestCase):
    """Events 400 yards and longer (500 Free, 400 IM) are positive check-in per the flyer's own
    rule 6 -- exactly events 21, 22, 31, 32, 125, 126, 139, 140. checkin_note() is a per-meet
    hardcoded lookup table, not a generic flyer parser, so this meet needed its own branch; the
    flyer's title itself wraps "Herculean" and "Invitational" onto separate lines, which a plain
    substring check (the style every prior branch used) would silently miss.
    """

    def test_every_400_plus_event_shows_the_checkin_note_and_nothing_else_does(self):
        # Exhaustive, direct check against the real flyer text for every event number in the meet.
        from swimtimeline.extract import checkin_note

        checkin_events = {21, 22, 31, 32, 125, 126, 139, 140}
        flyer_text = "\n".join(extract_text_pages(FLYER))
        for event_number in range(1, 141):
            note = checkin_note(event_number, flyer_text)
            if event_number in checkin_events:
                self.assertIsNotNone(note, event_number)
                self.assertIn("Positive check-in", note)
                self.assertIn("400", note)
            else:
                self.assertIsNone(note, event_number)

    def test_end_to_end_on_a_real_swimmer_entered_in_a_checkin_event(self):
        # Zaffos, Selah is entered in #21 (500 Free) for real.
        swims = {int(s["event_number"]): s for s in swims_only(analyze("Zaffos, Selah"))}
        self.assertIn(21, swims)
        self.assertIsNotNone(swims[21]["checkin_note"])
        self.assertIn("Positive check-in", swims[21]["checkin_note"])
        for number, swim in swims.items():
            if number != 21:
                self.assertIsNone(swim["checkin_note"], number)


class AmbiguousNameFamiliesTest(unittest.TestCase):
    """Three real same-surname groups at this meet: Vickers (Natalie, Grace), Post (Reagan,
    Harper, Zoey), Beltran (Christian, Adrian). Last name alone must be flagged ambiguous and
    generate nothing; "Last, First" must resolve each swimmer to their own events only.
    """

    def test_vickers_alone_is_ambiguous_between_natalie_and_grace(self):
        result = analyze("Vickers")
        self.assertTrue(result["ambiguous_swimmer_match"])
        self.assertEqual(swims_only(result), [])
        warning = next(w for w in result["warnings"] if "Vickers" in w)
        self.assertIn("Grace Vickers", warning)
        self.assertIn("Natalie Vickers", warning)

    def test_vickers_natalie_and_vickers_grace_each_resolve_individually(self):
        for query in ("Vickers, Natalie", "Vickers, Grace"):
            result = analyze(query)
            self.assertFalse(result["ambiguous_swimmer_match"], query)
            self.assertTrue(swims_only(result), query)

    def test_post_alone_is_ambiguous_between_all_three(self):
        result = analyze("Post")
        self.assertTrue(result["ambiguous_swimmer_match"])
        self.assertEqual(swims_only(result), [])
        warning = next(w for w in result["warnings"] if "Post" in w)
        for first in ("Reagan Post", "Harper Post", "Zoey Post"):
            self.assertIn(first, warning)

    def test_post_reagan_harper_and_zoey_each_resolve_individually(self):
        for query in ("Post, Reagan", "Post, Harper", "Post, Zoey"):
            result = analyze(query)
            self.assertFalse(result["ambiguous_swimmer_match"], query)
            self.assertTrue(swims_only(result), query)

    def test_beltran_alone_is_ambiguous_between_christian_and_adrian(self):
        result = analyze("Beltran")
        self.assertTrue(result["ambiguous_swimmer_match"])
        self.assertEqual(swims_only(result), [])
        warning = next(w for w in result["warnings"] if "Beltran" in w)
        self.assertIn("Christian Beltran", warning)
        self.assertIn("Adrian Beltran", warning)

    def test_beltran_christian_and_beltran_adrian_each_resolve_individually(self):
        for query in ("Beltran, Christian", "Beltran, Adrian"):
            result = analyze(query)
            self.assertFalse(result["ambiguous_swimmer_match"], query)
            self.assertTrue(swims_only(result), query)


if __name__ == "__main__":
    unittest.main()
