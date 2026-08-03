"""Relay-count summary contract for the results page.

The page shows two summaries above the event table -- the header sentence and the stat cards -- both
built from the payload's relay counts. They previously showed only verified_relay_count, so a meet
with tentative-only relays read "0 relays" up top while the table below listed them. The frontend now
renders both counts; these tests lock the payload contract those surfaces consume:

  * verified_relay_count counts ONLY leg-confirmed relays,
  * tentative_relay_count counts the "team entered, leg unknown" relays,
  * both are present and each equals the number of matching rows in `items`,
  * and the family payload AGGREGATES tentative_relay_count (it previously dropped it).

The rendering itself is browser JS (no JS harness in this repo); it is verified live against Cova's
WZAG case, and these tests guard the data it reads.
"""

from pathlib import Path
import tempfile
import unittest

from swimtimeline.extract import analyze_uploads

ROOT = Path(__file__).resolve().parents[1]
WZAG = ROOT / "meets/2026-wzag-championships-boise/input"


def relay_items(payload):
    return [item for item in payload["items"] if item.get("type") == "relay"]


def analyze_cova_wzag():
    # Deliberately no internal_relay_sources: this is the "no private roster selected" path (a
    # family that hasn't checked the Arizona relay add-on box). WZAG now also has a real roster
    # (data/internal_relay_sources/az-2026-wzag-relays.json, see test_wzag_relay_roster.py), which
    # drops Cova to 4 confirmed/0 tentative -- but that only applies when the add-on is selected.
    # Without it, the psych sheet's team-entered heuristic still yields the same 0/8 split as before,
    # so this remains a genuine, reachable production scenario, not a stale one.
    return analyze_uploads(
        flyer_pdf=WZAG / "Sanctioned_2026 WZAG Championships - Boise (v5.pdf",
        psych_pdf=WZAG / "wzag psych sheet v3.pdf",
        timeline_pdf=WZAG / "wzag timelines v4.pdf",
        swimmer_name="Cova, Mila L", output_dir=Path(tempfile.mkdtemp()), state="",
        meet_timezone="America/Boise", meet_venue="Idaho Central Aquatic Center, Boise, ID",
        modes=["detailed"],
    )


class SingleSwimmerCountsTest(unittest.TestCase):
    """Cova at WZAG WITHOUT the Arizona relay add-on selected: 6 individual events, 0 confirmed
    relays, 8 tentative relays -- both counts must be present and consistent with the table rows, so
    the header/cards can show all of them. (With the add-on selected, this drops to 4/0 -- see
    test_wzag_relay_roster.py -- but that is a separate, opt-in scenario.)"""

    def setUp(self):
        self.payload = analyze_cova_wzag()

    def test_both_counts_present_and_match_the_table(self):
        confirmed = self.payload["verified_relay_count"]
        tentative = self.payload["tentative_relay_count"]
        self.assertEqual(confirmed, 0)
        self.assertEqual(tentative, 8)
        # The counts must equal what the table below actually lists (never a "0 relays" summary over
        # a non-empty table).
        rows = relay_items(self.payload)
        self.assertEqual(sum(1 for r in rows if r["relay_status"] == "confirmed"), confirmed)
        self.assertEqual(sum(1 for r in rows if r["relay_status"] == "tentative"), tentative)

    def test_tentative_relays_are_actually_present_so_summary_cannot_read_zero(self):
        # The exact failure the fix targets: tentative rows exist, so a summary keyed only on the
        # confirmed count (0) would be misleading.
        self.assertGreater(self.payload["tentative_relay_count"], 0)
        self.assertTrue(relay_items(self.payload))


class FamilyAggregationTest(unittest.TestCase):
    """The family (multi-swimmer) payload must SUM tentative_relay_count across swimmers -- it was
    previously omitted from the aggregate, so a combined calendar's top summary lost the tentatives."""

    def test_family_payload_sums_tentative_relay_count(self):
        try:
            from webapp.server import analyze_swimmer_set
        except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
            raise unittest.SkipTest(
                "webapp.server needs Python 3.12: the stdlib cgi module was removed in 3.13"
            ) from exc

        result = analyze_swimmer_set(
            flyer_path=WZAG / "Sanctioned_2026 WZAG Championships - Boise (v5.pdf",
            psych_path=WZAG / "wzag psych sheet v3.pdf",
            timeline_path=WZAG / "wzag timelines v4.pdf",
            relay_path=None, internal_relay_sources=None,
            swimmer_names=["Cova, Mila L", "Steinbis, River"],
            output_dir=Path(tempfile.mkdtemp()), state="", modes=["daily"], combine_family=True,
            estimate_heat_lanes=False, meet_timezone="America/Boise",
            meet_venue="Idaho Central Aquatic Center, Boise, ID",
        )
        per_swimmer = result["swimmers"]
        # Each swimmer row carries its own tentative count...
        self.assertTrue(all("tentative_relay_count" in s for s in per_swimmer))
        expected_total = sum(int(s.get("tentative_relay_count", 0)) for s in per_swimmer)
        # ...and the top-level aggregate equals their sum and is non-zero (Cova alone contributes 8).
        self.assertEqual(result["tentative_relay_count"], expected_total)
        self.assertGreaterEqual(result["tentative_relay_count"], 8)


if __name__ == "__main__":
    unittest.main()
