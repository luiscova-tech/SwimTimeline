"""WZAG private relay roster + the precedence fix it revealed.

The Arizona relay lineup (data/internal_relay_sources/az-2026-wzag-relays.json) mirrors the MAC
state-meet roster: salted-hash names, one entry per relay event, leg per swimmer. With a real roster
loaded, a swimmer sees exactly the relays they are actually on -- and, crucially, the team-entered
tentative heuristic is suppressed for EVERY event the roster covers, not just the events the swimmer
matched. So a swimmer whose team is entered but who is NOT on the published lineup for a
roster-covered event now sees nothing there, rather than a false "team entered" tentative.

Relays that include a withdrawn swimmer (Adrian Beltran, #24/#51/#100) are flagged lineup_pending:
still confirmed, but marked subject to change so they are not presented as settled as the rest.
"""

from pathlib import Path
import tempfile
import unittest

from swimtimeline.extract import analyze_uploads, relay_roster_event_numbers

ROOT = Path(__file__).resolve().parents[1]
WZAG = ROOT / "meets/2026-wzag-championships-boise/input"
ROSTER = ROOT / "data/internal_relay_sources/az-2026-wzag-relays.json"


def analyze(name, *, with_roster):
    return analyze_uploads(
        flyer_pdf=WZAG / "Sanctioned_2026 WZAG Championships - Boise (v5.pdf",
        psych_pdf=WZAG / "wzag psych sheet v3.pdf",
        timeline_pdf=WZAG / "wzag timelines v4.pdf",
        swimmer_name=name, output_dir=Path(tempfile.mkdtemp()), state="",
        meet_timezone="America/Boise", meet_venue="Idaho Central Aquatic Center, Boise, ID",
        modes=["detailed"], internal_relay_sources=[ROSTER] if with_roster else None,
        include_relays=True,
    )


def relay_map(payload):
    return {i["event_number"]: i for i in payload["items"] if i.get("type") == "relay"}


class RosterCoverageTest(unittest.TestCase):
    def test_roster_covers_all_twelve_relay_events(self):
        covered = relay_roster_event_numbers(None, [ROSTER])
        self.assertEqual(covered, {23, 24, 25, 50, 51, 52, 74, 75, 76, 99, 100, 101})


class SwimmerSeesOnlyTheirRelaysTest(unittest.TestCase):
    def test_cova_has_exactly_her_four_confirmed_relays(self):
        payload = analyze("Cova, Mila L", with_roster=True)
        relays = relay_map(payload)
        self.assertEqual(set(relays), {25, 50, 75, 99})
        self.assertTrue(all(r["relay_status"] == "confirmed" for r in relays.values()))
        self.assertEqual(payload["verified_relay_count"], 4)
        self.assertEqual(payload["tentative_relay_count"], 0)

    def test_stein_has_exactly_her_four_confirmed_relays(self):
        payload = analyze("Stein, Layla", with_roster=True)
        relays = relay_map(payload)
        self.assertEqual(set(relays), {25, 52, 76, 101})
        self.assertEqual(payload["verified_relay_count"], 4)
        self.assertEqual(payload["tentative_relay_count"], 0)


class PrecedenceFixTest(unittest.TestCase):
    """A roster-covered event suppresses tentatives for it EVEN FOR a swimmer not on that relay."""

    def test_roster_removes_false_tentatives_that_appear_without_it(self):
        without = relay_map(analyze("Cova, Mila L", with_roster=False))
        with_roster = relay_map(analyze("Cova, Mila L", with_roster=True))

        # Without a roster, Cova's team-entered heuristic yields 8 tentatives (4 real + 4 false).
        self.assertEqual(set(without), {24, 25, 50, 52, 75, 76, 99, 101})
        self.assertTrue(all(r["relay_status"] == "tentative" for r in without.values()))

        # With the roster, the 4 events Cova is NOT on (#24/#52/#76/#101) disappear ENTIRELY -- not
        # relabeled, not tentative -- because the roster settles who is on them.
        self.assertEqual(set(with_roster), {25, 50, 75, 99})
        for event in (24, 52, 76, 101):
            self.assertNotIn(event, with_roster)

    def test_event_covered_by_roster_but_swimmer_absent_yields_nothing(self):
        # #52 (Girls 14&U Medley) is in the roster; Cova is not on it. She must see nothing for it.
        with_roster = relay_map(analyze("Cova, Mila L", with_roster=True))
        self.assertNotIn(52, with_roster)


class BeltranPendingFlagTest(unittest.TestCase):
    """Relays including the withdrawn swimmer are confirmed but flagged pending, distinct from
    both settled-confirmed and tentative."""

    def test_carter_pending_relay_is_distinct_from_settled_relays(self):
        payload = analyze("Carter, Izzy", with_roster=True)
        relays = relay_map(payload)
        self.assertEqual(set(relays), {24, 50, 75, 99})
        # #24 includes Beltran -> flagged pending; her other relays are settled.
        self.assertEqual(relays[24]["relay_status"], "confirmed_pending")
        self.assertTrue(relays[24]["lineup_pending"])
        self.assertIn("pending", relays[24]["event_format"].lower())
        for event in (50, 75, 99):
            self.assertEqual(relays[event]["relay_status"], "confirmed")
            self.assertFalse(relays[event]["lineup_pending"])

    def test_pending_relay_still_counts_as_confirmed_not_tentative(self):
        payload = analyze("Carter, Izzy", with_roster=True)
        self.assertEqual(payload["verified_relay_count"], 4)
        self.assertEqual(payload["tentative_relay_count"], 0)

    def test_a_pending_warning_is_surfaced(self):
        payload = analyze("Carter, Izzy", with_roster=True)
        self.assertTrue(any("pending a lineup change" in w for w in payload.get("warnings", [])))

    def test_swimmer_with_no_pending_relays_has_no_pending_flag(self):
        payload = analyze("Cova, Mila L", with_roster=True)
        relays = relay_map(payload)
        self.assertTrue(all(not r["lineup_pending"] for r in relays.values()))


if __name__ == "__main__":
    unittest.main()
