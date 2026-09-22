"""2026 MAC Red v Black Intrasquad -- Mesa Aquatics Club's own intrasquad scrimmage, added as a
hosted Current Meet in data/current_meets.json.

Real facts, verified directly from the uploaded documents: single day, single session ("1 Friday
PM", 9/25/2026, starts 5:30 PM), all Finals (no prelims round anywhere in the Session Report), 26
individual events + 2 relay events (1-2), swimmers split into two informal intrasquad teams --
"MAC-AZ" and "Black Team-AZ" -- which are NOT two different real USA-S clubs, just one club's own
squad split. Standard USA-S motivational benchmarks apply; no standards override is configured.

Testing this meet's real heat sheet ("preliminary heat sheet.pdf") surfaced three real parsing
bugs, all fixed in swimtimeline/extract.py and covered here against the real fixture that exposed
them (not synthetic data):

  1. This heat sheet is a "fill in the result by hand" meet program: every single row ends with a
     blank underscore rule ("...Nesbitt, Quinn J2 _____") for a timer to write in the actual time
     at the meet. parse_entry_fields()'s and TEAM_RELAY_ROW's anchored end-of-row patterns did not
     expect this trailing text and silently dropped EVERY row in the entire document -- not a
     handful of edge-case rows, all 26 individual events and both relays.
  2. "Black Team-AZ" contains an internal space. parse_entry_fields()'s team group's character
     class had no space in it and (with no `^` anchor on the pattern) simply skipped "Black ",
     silently resolving the team as "Team-AZ" -- losing exactly the squad distinction this meet's
     relay-team matching depends on.
  3. A real false positive: this heat sheet's own relay blocks print full leg-by-leg names right
     next to each team/seed row (several rows per event, one per relay letter -- e.g. Black
     Team-AZ alone has five separate lettered entries in Event 1), but the relay pipeline had no
     way to read them, so EVERY age/gender-eligible swimmer on an entered team got a generic
     "your team is entered, confirm with your coach" tentative line -- including swimmers who
     were not personally named on ANY of their team's entries for that event at all (Cova, Mila L
     is the real, confirmed example: zero appearances in either relay document, yet the old
     fallback still gave her a tentative Event 1 line). Fixed by adding
     extract_confirmed_relay_legs_from_psych(), which parses the named legs directly out of this
     SAME document (a different real shape than extract_relay_entries()'s separate-document
     format) and checks EVERY one of the swimmer's own team's rows for an event, not just the
     first one found. When a swimmer is actually named on one, it now surfaces a CONFIRMED entry
     with the real relay letter and leg number instead of a generic tentative guess; when they are
     not named on any of their team's rows for that event -- even though the team as a whole is
     entered -- it now shows nothing for that event, rather than guessing. A heat sheet with only
     bare team-level rows (no names at all, e.g. WZAG's) is untouched: this only ever changes
     behavior for an event where the swimmer's own team's rows in THIS document actually carry
     real names.

Known, deliberately unfixed here (flagged separately, out of scope):
  * parse_meet_name()'s keyword heuristic (invite/invitational/open/championship/nationals) does
    not recognize "Intrasquad", so this meet's displayed calendar name falls back to generic
    "Swim Meet" rather than "2026 MAC Red v Black Intrasquad".
"""

from pathlib import Path
import json
import tempfile
import unittest

from swimtimeline.extract import (
    analyze_uploads,
    extract_confirmed_relay_legs_from_psych,
    parse_entry_fields,
    relay_team_matches_swimmer,
    swimmer_relay_identity,
    extract_psych_entries,
    RELAY_LEG_PAIR_RE,
    TEAM_RELAY_ROW,
)

ROOT = Path(__file__).resolve().parents[1]
MEET_DIR = ROOT / "meets/2026-mac-red-v-black-intrasquad/input"
HEAT_SHEET = MEET_DIR / "2026-mac-red-v-black-intrasquad-heat-sheet.pdf"
TIMELINE = MEET_DIR / "2026-mac-red-v-black-intrasquad-timeline.pdf"
VENUE = "Skyline High School, 845 S. Crismon, Mesa, AZ 85208"


def analyze(name, **kwargs):
    return analyze_uploads(
        flyer_pdf=None,
        psych_pdf=HEAT_SHEET,
        timeline_pdf=TIMELINE,
        swimmer_name=name,
        output_dir=Path(tempfile.mkdtemp()),
        state="AZ",
        meet_timezone="America/Phoenix",
        meet_venue=VENUE,
        modes=["daily"],
        **kwargs,
    )


def individual(payload):
    return {item["event_number"]: item for item in payload["items"] if item["type"] != "relay"}


class RegistrationTest(unittest.TestCase):
    """The hosted meet entry in data/current_meets.json matches the real facts and its files
    actually exist on disk."""

    @classmethod
    def setUpClass(cls):
        data = json.loads((ROOT / "data/current_meets.json").read_text(encoding="utf-8"))
        cls.meet = next(m for m in data["current_meets"] if m["id"] == "2026-mac-red-v-black-intrasquad")

    def test_dates_and_venue(self):
        self.assertEqual(self.meet["dates"], "2026-09-25")
        self.assertEqual(self.meet["start_date"], "2026-09-25")
        self.assertEqual(self.meet["end_date"], "2026-09-25")
        self.assertEqual(self.meet["state"], "AZ")
        self.assertEqual(self.meet["timezone"], "America/Phoenix")
        self.assertEqual(self.meet["venue"], VENUE)

    def test_final_not_projected_since_heats_and_lanes_are_already_assigned(self):
        self.assertEqual(self.meet["timeline_type"], "final")

    def test_ready_status_with_no_standards_override(self):
        self.assertEqual(self.meet["status"], "ready")
        # A normal club meet: standard USA-S benchmarks apply, no AIA/other body override.
        self.assertNotIn("standards", self.meet)

    def test_files_point_at_the_heat_sheet_and_timeline_with_no_separate_relay_doc(self):
        self.assertEqual(
            self.meet["files"]["psych"],
            "meets/2026-mac-red-v-black-intrasquad/input/2026-mac-red-v-black-intrasquad-heat-sheet.pdf",
        )
        self.assertEqual(
            self.meet["files"]["timeline"],
            "meets/2026-mac-red-v-black-intrasquad/input/2026-mac-red-v-black-intrasquad-timeline.pdf",
        )
        self.assertIsNone(self.meet["files"]["relay"])
        self.assertTrue((ROOT / self.meet["files"]["psych"]).is_file())
        self.assertTrue((ROOT / self.meet["files"]["timeline"]).is_file())


class SessionReportTest(unittest.TestCase):
    """Single day, single session, all Finals -- no prelims round anywhere."""

    def test_single_friday_pm_session_all_finals(self):
        payload = analyze("Fry, Jacob M")
        self.assertEqual(len(payload["sessions"]), 1)
        session = payload["sessions"][0]
        self.assertEqual(session["name"], "Friday PM")
        self.assertEqual(session["date"], "2026-09-25")
        self.assertEqual(session["start_time"], "17:30")
        for item in payload["items"]:
            self.assertNotIn("prelim", item["event_format"].lower())


class MacAzSwimmerTest(unittest.TestCase):
    """Fry, Jacob M -- real MAC-AZ swimmer, in 4 individual events and named-leg-confirmed relay
    Event 2 (he's named on the leg 1 spot of MAC-AZ's "Relay A" entry)."""

    @classmethod
    def setUpClass(cls):
        cls.payload = analyze("Fry, Jacob M", include_relays=True)

    def test_four_real_individual_events_with_correct_seed_heat_lane(self):
        items = individual(self.payload)
        self.assertEqual(sorted(items), [8, 14, 20, 26])
        expected = {
            8: ("28.52", 4, 1),
            14: ("30.51", 4, 1),
            20: ("31.96", 2, 5),
            26: ("25.87", 3, 7),
        }
        for number, (seed, heat, lane) in expected.items():
            item = items[number]
            self.assertEqual(item["seed_time"], seed, number)
            self.assertEqual(item["heat"], heat, number)
            self.assertEqual(item["lane"], lane, number)
            self.assertFalse(item["heat_is_estimated"], number)

    def test_standard_usas_benchmarks_apply_not_aia_or_any_special_body(self):
        items = individual(self.payload)
        for item in items.values():
            self.assertIn("USA-S", item["benchmarks"]["usa"])
            self.assertNotIn("AIA", item["benchmarks"]["usa"])

    def test_his_named_relay_leg_surfaces_confirmed_not_tentative(self):
        self.assertEqual(self.payload["verified_relay_count"], 1)
        self.assertEqual(self.payload["tentative_relay_count"], 0)
        relays = [item for item in self.payload["items"] if item["type"] == "relay"]
        self.assertEqual(len(relays), 1)
        relay = relays[0]
        self.assertEqual(relay["event_number"], 2)
        self.assertEqual(relay["event_name"], "Boys 14 & Under 200 Yard Freestyle Relay")
        self.assertEqual(relay["relay_label"], "Relay A")
        self.assertEqual(relay["leg"], 1)
        self.assertEqual(relay["seed_time"], "NT")
        self.assertFalse(relay["is_team_entry"])
        self.assertEqual(relay["relay_status"], "confirmed")


class BlackTeamAzSwimmerTest(unittest.TestCase):
    """Allison, Mikaela B -- real Black Team-AZ swimmer, in 4 individual events and a named-leg-
    confirmed relay Event 1 (she's leg 1 of Black Team-AZ's "Relay D" entry). Specifically chosen
    because her own team name has the internal space that used to get silently truncated to
    'Team-AZ'."""

    @classmethod
    def setUpClass(cls):
        cls.payload = analyze("Allison, Mikaela B", include_relays=True)

    def test_her_team_resolves_to_the_full_name_not_truncated(self):
        entries, _page_counts, _warnings = extract_psych_entries(HEAT_SHEET, "Allison, Mikaela B")
        self.assertTrue(entries)
        for entry in entries:
            self.assertEqual(entry.team, "Black Team-AZ")
        team, age, gender = swimmer_relay_identity(entries)
        self.assertEqual(team, "Black Team-AZ")
        self.assertEqual(age, 12)
        self.assertEqual(gender, "girls")

    def test_four_real_individual_events_with_correct_seed_heat_lane(self):
        items = individual(self.payload)
        self.assertEqual(sorted(items), [5, 11, 17, 23])
        expected = {
            5: ("42.73", 3, 8),
            11: ("35.88", 5, 2),
            17: ("57.37", 2, 8),
            23: ("31.61", 5, 2),
        }
        for number, (seed, heat, lane) in expected.items():
            item = items[number]
            self.assertEqual(item["seed_time"], seed, number)
            self.assertEqual(item["heat"], heat, number)
            self.assertEqual(item["lane"], lane, number)

    def test_her_named_relay_leg_surfaces_confirmed_not_mac_azs(self):
        self.assertEqual(self.payload["verified_relay_count"], 1)
        self.assertEqual(self.payload["tentative_relay_count"], 0)
        relay = next(item for item in self.payload["items"] if item["type"] == "relay")
        self.assertEqual(relay["event_number"], 1)
        self.assertEqual(relay["event_name"], "Girls 14 & Under 200 Yard Freestyle Relay")
        self.assertEqual(relay["relay_label"], "Relay D")
        self.assertEqual(relay["leg"], 1)
        self.assertEqual(relay["seed_time"], "2:30.00")
        self.assertEqual(relay["relay_status"], "confirmed")


class DifferentLetteredEntryTest(unittest.TestCase):
    """Claypool, Ivy D -- real MAC-AZ swimmer named on leg 2 of MAC-AZ's Event 1 "Relay D" entry,
    MAC-AZ's LAST (5th) lettered row in that event, not its first ("Relay E", team1). Proves the
    matcher checks every one of the team's rows for an event rather than stopping at the first
    one it finds -- a real risk given a team can have several lettered entries per event."""

    def test_finds_her_on_the_later_lettered_entry_not_the_first(self):
        payload = analyze("Claypool, Ivy D", include_relays=True)
        self.assertEqual(payload["verified_relay_count"], 1)
        self.assertEqual(payload["tentative_relay_count"], 0)
        relay = next(item for item in payload["items"] if item["type"] == "relay")
        self.assertEqual(relay["event_number"], 1)
        self.assertEqual(relay["relay_label"], "Relay D")
        self.assertEqual(relay["leg"], 2)
        self.assertEqual(relay["relay_status"], "confirmed")


class CovaSwimmerTest(unittest.TestCase):
    """Cova, Mila L -- real Black Team-AZ swimmer, age 13, confirmed (checked both relay
    documents herself) to appear on NONE of Black Team-AZ's five real lettered entries in Event 1.
    She IS age/gender-eligible (13 <= "14 & Under") and her team IS entered -- the exact situation
    that used to produce a false-positive tentative "team entered, confirm with coach" line before
    named legs were parseable. Now that they are, she correctly gets no relay line at all."""

    def test_four_real_individual_events(self):
        payload = analyze("Cova, Mila L", include_relays=True)
        items = individual(payload)
        self.assertEqual(sorted(items), [7, 13, 19, 25])
        expected = {
            7: ("30.00", 3, 8),
            13: ("31.90", 3, 2),
            19: ("34.82", 3, 8),
            25: ("25.53", 3, 6),
        }
        for number, (seed, heat, lane) in expected.items():
            item = items[number]
            self.assertEqual(item["seed_time"], seed, number)
            self.assertEqual(item["heat"], heat, number)
            self.assertEqual(item["lane"], lane, number)

    def test_gets_no_relay_line_at_all_not_a_tentative_guess(self):
        payload = analyze("Cova, Mila L", include_relays=True)
        self.assertEqual(payload["verified_relay_count"], 0)
        self.assertEqual(payload["tentative_relay_count"], 0)
        self.assertEqual([item for item in payload["items"] if item["type"] == "relay"], [])
        self.assertEqual(
            [w for w in payload["warnings"] if "relay" in w.lower()],
            [],
            "no relay-related warning either -- not even the tentative-fallback notice",
        )


class IntrasquadTeamCodeWrinkleTest(unittest.TestCase):
    """The specific risk flagged when this meet was added: "MAC-AZ" and "Black Team-AZ" are NOT
    two different real USA-S clubs, just one club's own intrasquad split. Confirms
    relay_team_matches_swimmer() -- an exact club-LSC match, no LSC-display-name lookup involved
    for either code -- keeps the two squads distinct rather than merging or confusing them."""

    def test_each_squad_matches_only_itself(self):
        self.assertTrue(relay_team_matches_swimmer("MAC-AZ", "MAC-AZ"))
        self.assertTrue(relay_team_matches_swimmer("Black Team-AZ", "Black Team-AZ"))
        self.assertFalse(relay_team_matches_swimmer("Black Team-AZ", "MAC-AZ"))
        self.assertFalse(relay_team_matches_swimmer("MAC-AZ", "Black Team-AZ"))


class TrailingBlankRulePlaceholderRegressionTest(unittest.TestCase):
    """Direct regression coverage for the "fill in the result by hand" trailing-underscore fix,
    isolated from the full-meet fixture above so a future change to either regex fails here first."""

    def test_parse_entry_fields_tolerates_the_trailing_blank_rule(self):
        with_blank = parse_entry_fields("MAC-AZ NT 8Nesbitt, Quinn J2 _____", 1, "Finals")
        without_blank = parse_entry_fields("MAC-AZ NT 8Nesbitt, Quinn J2", 1, "Finals")
        self.assertIsNotNone(with_blank)
        self.assertEqual(with_blank, without_blank)

    def test_team_relay_row_tolerates_no_time_and_the_trailing_blank_rule(self):
        self.assertIsNotNone(TEAM_RELAY_ROW.match("A NTMAC-AZ5 _____"))
        self.assertIsNotNone(TEAM_RELAY_ROW.match("E 2:40.00MAC-AZ1 _____"))
        # Existing real formats (numeric seed, no trailing blank) must still match unchanged.
        self.assertIsNotNone(TEAM_RELAY_ROW.match("A 2:18.00Arizona16"))


class NamedRelayLegParsingRegressionTest(unittest.TestCase):
    """Direct regression coverage for RELAY_LEG_PAIR_RE and extract_confirmed_relay_legs_from_psych(),
    isolated from the full-meet fixture above so a future change to either fails here first."""

    def test_relay_leg_pair_regex_parses_two_legs_per_line(self):
        matches = list(RELAY_LEG_PAIR_RE.finditer("1) Estiller, Erza A 10 2) Golden, Layla A 11"))
        self.assertEqual([m.group("leg") for m in matches], ["1", "2"])
        self.assertEqual(matches[0].group("name"), "Estiller, Erza A")
        self.assertEqual(matches[0].group("age"), "10")
        self.assertEqual(matches[1].group("name"), "Golden, Layla A")

    def test_relay_leg_pair_regex_handles_a_slash_inside_a_first_name(self):
        # Real MAC Red v Black row: "2) Zapata, Cat/Cataleya G 11" -- confirms the lazy, unrestricted
        # name group (bounded by the trailing age digits, not an explicit character class) doesn't
        # stop early at the "/".
        matches = list(RELAY_LEG_PAIR_RE.finditer("2) Zapata, Cat/Cataleya G 11"))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].group("name"), "Zapata, Cat/Cataleya G")

    def test_a_real_bare_team_only_fixture_is_completely_unaffected(self):
        # WZAG's real psych sheet (see tests/test_team_relay.py) prints team-level relay rows with
        # NO names at all -- the exact case this function must leave alone. Cova, Mila L there is
        # a real tentative-relay swimmer today; confirming named_leg_events comes back empty is
        # the proof this new capability is a true no-op on a document that never names legs.
        wzag_psych = ROOT / "meets/2026-wzag-championships-boise/input/wzag psych sheet v3.pdf"
        entries, _page_counts, _warnings = extract_psych_entries(wzag_psych, "Cova, Mila L")
        team, age, gender = swimmer_relay_identity(entries)
        self.assertEqual(team, "AZ")
        matches, named_leg_events = extract_confirmed_relay_legs_from_psych(wzag_psych, "Cova, Mila L", team, age, gender)
        self.assertEqual(matches, [])
        self.assertEqual(named_leg_events, set())


if __name__ == "__main__":
    unittest.main()
