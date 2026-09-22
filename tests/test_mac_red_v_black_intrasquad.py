"""2026 MAC Red v Black Intrasquad -- Mesa Aquatics Club's own intrasquad scrimmage, added as a
hosted Current Meet in data/current_meets.json.

Real facts, verified directly from the uploaded documents: single day, single session ("1 Friday
PM", 9/25/2026, starts 5:30 PM), all Finals (no prelims round anywhere in the Session Report), 26
individual events + 2 relay events (1-2), swimmers split into two informal intrasquad teams --
"MAC-AZ" and "Black Team-AZ" -- which are NOT two different real USA-S clubs, just one club's own
squad split. Standard USA-S motivational benchmarks apply; no standards override is configured.

Testing this meet's real heat sheet ("preliminary heat sheet.pdf") surfaced two real parsing bugs,
both fixed in swimtimeline/extract.py and covered here against the real fixture that exposed them
(not synthetic data):

  1. This heat sheet is a "fill in the result by hand" meet program: every single row ends with a
     blank underscore rule ("...Nesbitt, Quinn J2 _____") for a timer to write in the actual time
     at the meet. parse_entry_fields()'s and TEAM_RELAY_ROW's anchored end-of-row patterns did not
     expect this trailing text and silently dropped EVERY row in the entire document -- not a
     handful of edge-case rows, all 26 individual events and both relays.
  2. "Black Team-AZ" contains an internal space. parse_entry_fields()'s team group's character
     class had no space in it and (with no `^` anchor on the pattern) simply skipped "Black ",
     silently resolving the team as "Team-AZ" -- losing exactly the squad distinction this meet's
     relay-team matching depends on.

Known, deliberately unfixed here (flagged separately, out of scope for adding a meet):
  * This heat sheet's own relay event blocks print real, leg-confirmed swimmer names directly
    ("1) Fry, Jacob M 13 2) Harker, Bronco C 14 ..."), richer than what this app's relay pipeline
    currently extracts without a separate relay document or private roster -- relays here
    correctly surface as TENTATIVE ("your team is entered, confirm with your coach"), not with
    per-leg detail, which is honest given the inputs but leaves real data on the table.
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
    parse_entry_fields,
    relay_team_matches_swimmer,
    swimmer_relay_identity,
    extract_psych_entries,
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
    """Fry, Jacob M -- real MAC-AZ swimmer, in 4 individual events and confirmed relay Event 2."""

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

    def test_his_own_team_relay_surfaces_as_confirmed_team_tentative(self):
        self.assertEqual(self.payload["tentative_relay_count"], 1)
        relays = [item for item in self.payload["items"] if item["type"] == "relay"]
        self.assertEqual(len(relays), 1)
        relay = relays[0]
        self.assertEqual(relay["event_number"], 2)
        self.assertEqual(relay["event_name"], "Boys 14 & Under 200 Yard Freestyle Relay")
        self.assertTrue(relay["is_team_entry"])
        self.assertEqual(relay["relay_status"], "tentative")


class BlackTeamAzSwimmerTest(unittest.TestCase):
    """Allison, Mikaela B -- real Black Team-AZ swimmer, in 4 individual events and confirmed
    relay Event 1. Specifically chosen because her own team name has the internal space that
    used to get silently truncated to 'Team-AZ'."""

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

    def test_her_own_teams_relay_surfaces_tentative_not_mac_azs(self):
        self.assertEqual(self.payload["tentative_relay_count"], 1)
        relay = next(item for item in self.payload["items"] if item["type"] == "relay")
        self.assertEqual(relay["event_number"], 1)
        self.assertEqual(relay["event_name"], "Girls 14 & Under 200 Yard Freestyle Relay")


class CovaSwimmerTest(unittest.TestCase):
    """Cova, Mila L -- real Black Team-AZ swimmer, age 13. She is not personally named in Event
    1's real leg list, but the tentative-team-entry feature doesn't know that (it isn't sourcing
    legs at all here -- see the module docstring's known gap) and correctly still flags Event 1
    tentative for her: her team IS entered, and she's age/gender-eligible (13 <= "14 & Under")."""

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

    def test_eligible_for_her_teams_relay_even_though_not_personally_named_in_it(self):
        payload = analyze("Cova, Mila L", include_relays=True)
        self.assertEqual(payload["tentative_relay_count"], 1)
        relay = next(item for item in payload["items"] if item["type"] == "relay")
        self.assertEqual(relay["event_number"], 1)
        self.assertEqual(relay["relay_status"], "tentative")


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


if __name__ == "__main__":
    unittest.main()
