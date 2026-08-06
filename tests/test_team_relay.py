"""Tentative "team entered, leg unknown" relays.

Psych sheets carry team-level relay entries (a team name + seed time, no roster). When the searched
swimmer's own team is entered in a relay and no leg-naming source (uploaded relay PDF / private
roster add-on) confirms them on it, that event is surfaced as a distinct TENTATIVE calendar entry --
"your team is entered, confirm with your coach" -- with no roster and no asserted leg. A
leg-confirmed source always wins: an event with a confirmed entry never also shows a tentative one.

This is general (any team, any LSC), matched off the swimmer's own parsed team code:
  * club meet  -- code is "CLUB-LSC" (MAC-AZ); relay rows print the same code; exact match.
  * zone meet  -- code is a bare LSC (AZ); relay rows print the LSC display name (Arizona).
"""

from pathlib import Path
import re
import tempfile
import unittest

from swimtimeline.extract import (
    analyze_uploads,
    extract_team_relay_entries,
    relay_age_eligible,
    relay_gender_eligible,
    relay_team_matches_swimmer,
)

ROOT = Path(__file__).resolve().parents[1]
WZAG = ROOT / "meets/2026-wzag-championships-boise/input"
STATE = ROOT / "meets/2026-az-lc-age-group-state/input"
MAC_RELAY_ADDON = ROOT / "data/internal_relay_sources/mac-2026-age-group-state-relays.json"


def relay_events(result):
    return {item["event_number"]: item for item in result["items"] if item.get("type") == "relay"}


def analyze_wzag(**kwargs):
    return analyze_uploads(
        flyer_pdf=WZAG / "Sanctioned_2026 WZAG Championships - Boise (v5.pdf",
        psych_pdf=WZAG / "wzag psych sheet v3.pdf",
        timeline_pdf=WZAG / "wzag timelines v4.pdf",
        output_dir=Path(tempfile.mkdtemp()),
        meet_timezone="America/Boise",
        meet_venue="Idaho Central Aquatic Center, Boise, ID",
        modes=["detailed"], include_relays=True,
        **kwargs,
    )


def analyze_state(**kwargs):
    return analyze_uploads(
        flyer_pdf=STATE / "age-group-state-meet-flyer.pdf",
        psych_pdf=STATE / "age-group-state-psych-sheet.pdf",
        timeline_pdf=STATE / "age-group-state-timeline.pdf",
        output_dir=Path(tempfile.mkdtemp()),
        modes=["detailed"], include_relays=True,
        **kwargs,
    )


class TeamMatchingTest(unittest.TestCase):
    def test_zone_meet_matches_the_lsc_display_name(self):
        # Bare-LSC swimmer (AZ) at a zone meet whose relay rows print "Arizona".
        self.assertTrue(relay_team_matches_swimmer("Arizona", "AZ"))
        self.assertTrue(relay_team_matches_swimmer("AZ", "AZ"))  # code form also accepted
        # A column-truncated long name still prefix-matches its LSC.
        self.assertTrue(relay_team_matches_swimmer("Sierra Nevada Sw", "SN"))

    def test_zone_meet_rejects_other_lscs(self):
        for other in ("Pacific", "Snake River", "Colorado", "Utah"):
            self.assertFalse(relay_team_matches_swimmer(other, "AZ"), other)

    def test_club_meet_requires_exact_club_lsc(self):
        # Club-LSC swimmer (MAC-AZ): only their own club's rows match, never another AZ club.
        self.assertTrue(relay_team_matches_swimmer("MAC-AZ", "MAC-AZ"))
        self.assertFalse(relay_team_matches_swimmer("GM-AZ", "MAC-AZ"))
        # A club swimmer must not match a bare LSC display name (wrong granularity).
        self.assertFalse(relay_team_matches_swimmer("Arizona", "MAC-AZ"))

    def test_unmapped_or_empty_never_matches(self):
        self.assertFalse(relay_team_matches_swimmer("Arizona", "ZZ"))  # ZZ has no display name
        self.assertFalse(relay_team_matches_swimmer("", "AZ"))
        self.assertFalse(relay_team_matches_swimmer("Arizona", ""))


class EligibilityTest(unittest.TestCase):
    def test_age_and_under_groups(self):
        self.assertTrue(relay_age_eligible("Mixed 12 & Under 200 Free Relay", 12))
        self.assertFalse(relay_age_eligible("Mixed 10 & Under 200 Free Relay", 12))
        self.assertTrue(relay_age_eligible("Mixed 14 & Under 200 Free Relay", 12))

    def test_age_ranges_and_over_groups(self):
        self.assertTrue(relay_age_eligible("Girls 13-14 200 Medley Relay", 13))
        self.assertFalse(relay_age_eligible("Girls 13-14 200 Medley Relay", 12))
        self.assertTrue(relay_age_eligible("Women 15 & Over 200 Free Relay", 16))
        self.assertFalse(relay_age_eligible("Women 15 & Over 200 Free Relay", 12))
        self.assertTrue(relay_age_eligible("Mixed Open 200 Free Relay", 12))  # open -> no exclusion
        self.assertTrue(relay_age_eligible("Girls 12 & Under 200 Free Relay", None))  # unknown age

    def test_gender(self):
        self.assertTrue(relay_gender_eligible("Girls 12 & Under 200 Free Relay", "girls"))
        self.assertFalse(relay_gender_eligible("Boys 12 & Under 200 Free Relay", "girls"))
        self.assertTrue(relay_gender_eligible("Mixed 12 & Under 200 Free Relay", "girls"))  # mixed
        self.assertTrue(relay_gender_eligible("Boys 12 & Under 200 Free Relay", None))  # unknown


class WzagTentativeTest(unittest.TestCase):
    """Team entered, no leg source -> tentative. Cova (team AZ, age 12, girls) at WZAG."""

    def setUp(self):
        # Blank state -> team auto-detected as AZ; no relay PDF, no add-on.
        self.result = analyze_wzag(swimmer_name="Cova, Mila L", state="")

    def test_exactly_the_eligible_arizona_relays_surface_as_tentative(self):
        relays = relay_events(self.result)
        self.assertEqual(set(relays), {24, 25, 50, 52, 75, 76, 99, 101})
        for item in relays.values():
            self.assertEqual(item["relay_status"], "tentative")
            self.assertIsNone(item["leg"])
            self.assertTrue(item["is_team_entry"])
        self.assertEqual(self.result["verified_relay_count"], 0)
        self.assertEqual(self.result["tentative_relay_count"], 8)

    def test_no_false_positives_from_age_gender_or_absent_team(self):
        relays = relay_events(self.result)
        # Arizona IS entered but Cova is age-ineligible (10 & Under):
        self.assertNotIn(23, relays)
        self.assertNotIn(74, relays)
        # Arizona IS entered but Cova is gender-ineligible (Boys):
        self.assertNotIn(51, relays)
        self.assertNotIn(100, relays)
        # Arizona is NOT entered at all in these relay events:
        self.assertNotIn(48, relays)
        self.assertNotIn(53, relays)

    def test_tentative_relays_are_status_tentative_in_ics_even_on_a_final_timeline(self):
        # STATUS:TENTATIVE is unconditional for team entries -- here the WZAG timeline is treated as
        # final (timeline_projected defaults to False), yet every team-entry VEVENT is TENTATIVE.
        out = Path(tempfile.mkdtemp())
        analyze_uploads(
            flyer_pdf=WZAG / "Sanctioned_2026 WZAG Championships - Boise (v5.pdf",
            psych_pdf=WZAG / "wzag psych sheet v3.pdf",
            timeline_pdf=WZAG / "wzag timelines v4.pdf",
            swimmer_name="Cova, Mila L", output_dir=out, state="",
            meet_timezone="America/Boise", meet_venue="X", modes=["detailed"],
            timeline_projected=False, include_relays=True,
        )
        ics = (out / "detailed.ics").read_text(encoding="utf-8").replace("\r\n ", "")
        team_blocks = [b for b in ics.split("BEGIN:VEVENT") if "team entered" in b.lower()]
        self.assertEqual(len(team_blocks), 8)
        self.assertTrue(all("STATUS:TENTATIVE" in b for b in team_blocks))


class PrecedenceTest(unittest.TestCase):
    """Leg-confirmed source always wins; no event shows both a confirmed and a tentative entry."""

    def test_without_leg_source_team_entries_are_tentative(self):
        result = analyze_state(swimmer_name="Cova, Mila L", state="", internal_relay_sources=None)
        relays = relay_events(result)
        self.assertEqual(set(relays), {31, 41, 75, 107})
        self.assertTrue(all(item["relay_status"] == "tentative" for item in relays.values()))
        self.assertEqual(result["tentative_relay_count"], 4)
        self.assertEqual(result["verified_relay_count"], 0)

    def test_with_leg_source_those_events_are_confirmed_not_duplicated(self):
        result = analyze_state(
            swimmer_name="Cova, Mila L", state="", internal_relay_sources=[MAC_RELAY_ADDON]
        )
        relays = relay_events(result)
        # Same four events -- now confirmed, each exactly once, none tentative.
        self.assertEqual(set(relays), {31, 41, 75, 107})
        for item in relays.values():
            self.assertEqual(item["relay_status"], "confirmed")
            self.assertEqual(item["leg"], 4)
            self.assertFalse(item["is_team_entry"])
        self.assertEqual(result["verified_relay_count"], 4)
        self.assertEqual(result["tentative_relay_count"], 0)

        # And no event number appears more than once across all relay items.
        numbers = [item["event_number"] for item in result["items"] if item.get("type") == "relay"]
        self.assertEqual(len(numbers), len(set(numbers)))


class NoRelaysWhenTeamAbsentTest(unittest.TestCase):
    def test_meet_without_team_relay_rows_yields_nothing(self):
        # Narwhal's psych sheet has no relay events at all -> no tentative relays, no relay items.
        result = analyze_uploads(
            flyer_pdf=ROOT / "meets/2026-narwhal-invite/input/Narwhal Invite.pdf",
            psych_pdf=ROOT / "meets/2026-narwhal-invite/input/narwhal final psych again.pdf",
            timeline_pdf=ROOT / "meets/2026-narwhal-invite/input/narwhal final timeline.pdf",
            swimmer_name="Cova, Mila L", output_dir=Path(tempfile.mkdtemp()), state="AZ",
            modes=["detailed"],
        )
        self.assertEqual(relay_events(result), {})
        self.assertEqual(result["tentative_relay_count"], 0)

    def test_team_not_entered_yields_nothing(self):
        # A swimmer whose (mapped) LSC is simply not present in a meet's relay rows gets nothing.
        entries = extract_team_relay_entries(WZAG / "wzag psych sheet v3.pdf", "ZZ", 12, "girls")
        self.assertEqual(entries, [])


if __name__ == "__main__":
    unittest.main()
