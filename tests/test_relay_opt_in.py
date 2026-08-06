"""Relay output is opt-in, and the gate must cover BOTH kinds of relay.

Reported live: tentative "team entered, confirm with coach" relays appeared for parents who left the
relay add-on unchecked. The confirmed sources (a relay PDF, a private roster) were only ever gated
IMPLICITLY -- supplying no relay document meant there was nothing to find -- so no explicit gate
existed. Tentative relays read the psych sheet, which is always present, so they slipped straight
into the results pipeline for everyone.

Unchecked now means no relay section at all, exactly as before tentative relays existed. Checked
means both kinds, unchanged from the roster fix.
"""

from pathlib import Path
import re
import tempfile
import unittest

from swimtimeline.extract import analyze_uploads

ROOT = Path(__file__).resolve().parents[1]
WZAG = ROOT / "meets/2026-wzag-championships-boise/input"
ROSTER = ROOT / "data/internal_relay_sources/az-2026-wzag-relays.json"


def analyze(*, include_relays=None, roster=False):
    return analyze_uploads(
        flyer_pdf=WZAG / "Sanctioned_2026 WZAG Championships - Boise (v5.pdf",
        psych_pdf=WZAG / "wzag psych sheet v3.pdf",
        timeline_pdf=WZAG / "wzag timelines v4.pdf",
        swimmer_name="Cova, Mila L", output_dir=Path(tempfile.mkdtemp()), state="",
        meet_timezone="America/Boise", meet_venue="Idaho Central Aquatic Center, Boise, ID",
        modes=["daily", "detailed"],
        internal_relay_sources=[ROSTER] if roster else None,
        include_relays=include_relays,
    )


def relay_rows(payload):
    return [i for i in payload["items"] if i.get("type") == "relay"]


class OptedOutTest(unittest.TestCase):
    """Unchecked: no relay rows, no relay counts, no relay warnings, no relay VEVENTs."""

    def setUp(self):
        self.payload = analyze(include_relays=False)

    def test_no_relay_rows_at_all(self):
        self.assertEqual(relay_rows(self.payload), [])
        self.assertEqual(self.payload["relays"], [])

    def test_both_relay_counts_are_zero(self):
        self.assertEqual(self.payload["verified_relay_count"], 0)
        self.assertEqual(self.payload["tentative_relay_count"], 0)

    def test_the_tentative_warning_is_not_shown(self):
        self.assertFalse(any("team entered" in w for w in self.payload["warnings"]))

    def test_individual_events_are_untouched(self):
        # Gating relays must not disturb the swims themselves.
        events = [i for i in self.payload["items"] if i.get("type") != "relay"]
        self.assertEqual(len(events), 6)

    def test_no_relay_vevents_in_the_generated_calendar(self):
        out = Path(tempfile.mkdtemp())
        analyze_uploads(
            flyer_pdf=WZAG / "Sanctioned_2026 WZAG Championships - Boise (v5.pdf",
            psych_pdf=WZAG / "wzag psych sheet v3.pdf",
            timeline_pdf=WZAG / "wzag timelines v4.pdf",
            swimmer_name="Cova, Mila L", output_dir=out, state="",
            meet_timezone="America/Boise", meet_venue="X", modes=["detailed"],
            include_relays=False,
        )
        ics = (out / "detailed.ics").read_text(encoding="utf-8").replace("\r\n ", "").replace("\n ", "")
        # Every individual event legitimately carries a "Relay source: n/a" verification line, so
        # assert on the VEVENTs themselves: none of them may be a relay.
        summaries = re.findall(r"SUMMARY:(.*)", ics)
        self.assertEqual(len(summaries), 6)
        self.assertFalse([s for s in summaries if "Relay" in s], summaries)


class OptedInTest(unittest.TestCase):
    """Checked: tentative relays return, and with the roster the confirmed ones do."""

    def test_tentative_relays_appear_when_opted_in(self):
        payload = analyze(include_relays=True)
        self.assertEqual(payload["tentative_relay_count"], 8)
        self.assertEqual(payload["verified_relay_count"], 0)
        self.assertTrue(any("team entered" in w for w in payload["warnings"]))

    def test_roster_relays_appear_when_opted_in(self):
        # Exactly what the roster fix produced: her four real relays, none tentative.
        payload = analyze(include_relays=True, roster=True)
        self.assertEqual(payload["verified_relay_count"], 4)
        self.assertEqual(payload["tentative_relay_count"], 0)
        self.assertEqual(sorted(i["event_number"] for i in relay_rows(payload)), [25, 50, 75, 99])


class DerivedDefaultTest(unittest.TestCase):
    """include_relays=None derives the opt-in from whether a relay source was supplied, so every
    pre-existing caller keeps behaving as it did."""

    def test_no_source_and_no_explicit_flag_means_no_relays(self):
        payload = analyze()
        self.assertEqual(relay_rows(payload), [])
        self.assertEqual(payload["tentative_relay_count"], 0)

    def test_supplying_a_roster_is_itself_the_opt_in(self):
        payload = analyze(roster=True)
        self.assertEqual(payload["verified_relay_count"], 4)


class ServerWiringTest(unittest.TestCase):
    """The checkbox is what drives the flag on the hosted path."""

    def test_relay_option_ids_drive_the_flag(self):
        try:
            from webapp.server import analyze_swimmer_set
        except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
            raise unittest.SkipTest("webapp.server needs Python 3.12") from exc

        common = dict(
            flyer_path=WZAG / "Sanctioned_2026 WZAG Championships - Boise (v5.pdf",
            psych_path=WZAG / "wzag psych sheet v3.pdf",
            timeline_path=WZAG / "wzag timelines v4.pdf",
            relay_path=None, swimmer_names=["Cova, Mila L"],
            state="", modes=["daily"], combine_family=True, estimate_heat_lanes=False,
            meet_timezone="America/Boise", meet_venue="X",
        )
        # Unchecked: no relay option ids resolved -> no sources, no opt-in.
        unchecked = analyze_swimmer_set(
            internal_relay_sources=[], output_dir=Path(tempfile.mkdtemp()),
            include_relays=False, **common,
        )
        self.assertEqual(unchecked["tentative_relay_count"], 0)
        self.assertEqual(unchecked["verified_relay_count"], 0)
        # Checked: the add-on resolves to the roster and the flag is set.
        checked = analyze_swimmer_set(
            internal_relay_sources=[ROSTER], output_dir=Path(tempfile.mkdtemp()),
            include_relays=True, **common,
        )
        self.assertEqual(checked["verified_relay_count"], 4)


if __name__ == "__main__":
    unittest.main()
