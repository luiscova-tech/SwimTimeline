"""Auto-detect a swimmer's LSC from their parsed team code when the State/LSC field is blank.

Every psych-sheet entry's team code carries the swimmer's LSC as a 2-letter token -- a "CLUB-LSC"
suffix on local meets (MAC-AZ) or standing alone on zone/all-star sheets (AZ, SR). Families who did
not know to type "AZ" into the optional State/LSC field used to see none of the AZSI/Sectional
benchmarks; a blank field now falls back to that code. Precedence is strict: an explicitly typed
value always wins, and detection is per entry, so a combined family calendar (or even one lookup
that fuzzy-matches swimmers from different LSCs) resolves each swimmer against their own code.
"""

from pathlib import Path
import sys
import tempfile
import unittest

from swimtimeline.extract import (
    analyze_uploads,
    auto_detect_state_warnings,
    lsc_from_team_code,
)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

NARWHAL = ROOT / "meets/2026-narwhal-invite/input"
WZAG = ROOT / "meets/2026-wzag-championships-boise/input"
SHARK = ROOT / "meets/2026-shark-open/input"


def psych_entry(team):
    from swimtimeline.extract import PsychEntry

    return PsychEntry(
        day="Wednesday", event_number=5, event_name="Girls 11-12 50 LC Meter Freestyle",
        seed_time="28.62", seed_place=1, age="12", team=team, page=1, column="Left", source_line="",
    )


def lsc_lines(result):
    return [item["benchmarks"]["lsc"] for item in result["items"] if item.get("type") != "relay"]


def has_auto_note(result):
    return any("auto-detected" in warning for warning in result.get("warnings", []))


class TeamCodeLscTest(unittest.TestCase):
    """The 2-letter LSC is the trailing token; a bare club code with no LSC yields None."""

    def test_club_lsc_suffix_yields_the_lsc(self):
        self.assertEqual(lsc_from_team_code("MAC-AZ"), "AZ")
        self.assertEqual(lsc_from_team_code("AASC-AZ"), "AZ")
        self.assertEqual(lsc_from_team_code("GM-AZ"), "AZ")
        self.assertEqual(lsc_from_team_code("HSC-FL"), "FL")

    def test_standalone_lsc_yields_itself(self):
        # Zone/all-star psych sheets (WZAG) list the team column as just the LSC.
        self.assertEqual(lsc_from_team_code("AZ"), "AZ")
        self.assertEqual(lsc_from_team_code("SR"), "SR")

    def test_bare_club_code_without_an_lsc_yields_none(self):
        # No 2-letter token to read -> nothing detected -> the swimmer falls through exactly as a
        # blank State/LSC field does today.
        self.assertIsNone(lsc_from_team_code("MAC"))
        self.assertIsNone(lsc_from_team_code("HEAT"))
        self.assertIsNone(lsc_from_team_code(""))
        self.assertIsNone(lsc_from_team_code(None))


class AutoDetectWarningTest(unittest.TestCase):
    def test_blank_field_with_supported_lsc_announces_detection(self):
        warnings = auto_detect_state_warnings("", [psych_entry("MAC-AZ")])
        self.assertEqual(len(warnings), 1)
        self.assertIn("auto-detected as AZ", warnings[0])

    def test_explicit_value_is_never_announced(self):
        # A typed value wins, so there is nothing auto-detected to explain.
        self.assertEqual(auto_detect_state_warnings("AZ", [psych_entry("MAC-AZ")]), [])
        self.assertEqual(auto_detect_state_warnings("CA", [psych_entry("MAC-AZ")]), [])

    def test_unsupported_or_absent_lsc_is_never_announced(self):
        # Non-AZ (FL) and no-LSC produce the same output as before, so nothing is announced.
        self.assertEqual(auto_detect_state_warnings("", [psych_entry("HSC-FL")]), [])
        self.assertEqual(auto_detect_state_warnings("", [psych_entry("MAC")]), [])


class BlankFieldAutoDetectsSupportedLscTest(unittest.TestCase):
    """Blank field + AZ team code -> AZSI lines appear without the family typing anything."""

    def test_cova_narwhal_blank_field_surfaces_azsi(self):
        out = Path(tempfile.mkdtemp())
        result = analyze_uploads(
            flyer_pdf=NARWHAL / "Narwhal Invite.pdf",
            psych_pdf=NARWHAL / "narwhal final psych again.pdf",
            timeline_pdf=NARWHAL / "narwhal final timeline.pdf",
            swimmer_name="Cova, Mila L", output_dir=out, state="",
        )
        lines = lsc_lines(result)
        self.assertTrue(lines)
        self.assertTrue(all(line.startswith("AZSI") for line in lines), lines)
        self.assertTrue(has_auto_note(result))

    def test_cova_wzag_blank_field_surfaces_azsi(self):
        # WZAG lists the team column as a standalone "AZ" (no club suffix) -- also detected.
        out = Path(tempfile.mkdtemp())
        result = analyze_uploads(
            flyer_pdf=WZAG / "Sanctioned_2026 WZAG Championships - Boise (v5.pdf",
            psych_pdf=WZAG / "wzag psych sheet v3.pdf",
            timeline_pdf=WZAG / "wzag timelines v4.pdf",
            swimmer_name="Cova, Mila L", output_dir=out, state="",
            meet_timezone="America/Boise", meet_venue="Idaho Central Aquatic Center, Boise, ID",
        )
        lines = lsc_lines(result)
        self.assertTrue(lines)
        self.assertTrue(all(line.startswith("AZSI") for line in lines), lines)
        self.assertTrue(has_auto_note(result))


class BlankFieldUnsupportedLscStillNotConfiguredTest(unittest.TestCase):
    """Blank field + non-AZ team code -> exactly the "not configured" result it produces today."""

    def test_shark_open_florida_swimmer_blank_field_stays_not_configured(self):
        out = Path(tempfile.mkdtemp())
        result = analyze_uploads(
            flyer_pdf=SHARK / "2026-shark-open-flyer.pdf",
            psych_pdf=SHARK / "2026-shark-open-heat-sheet.pdf",
            timeline_pdf=SHARK / "2026-shark-open-timeline.pdf",
            swimmer_name="Alegi, Grace", output_dir=out, state="",
        )
        lines = lsc_lines(result)
        self.assertTrue(lines)
        self.assertTrue(all("not configured" in line for line in lines), lines)
        self.assertFalse(has_auto_note(result))


class ManualEntryOverridesAutoDetectionTest(unittest.TestCase):
    """An explicitly entered State/LSC always wins, even against a swimmer's own AZ team code."""

    def _run(self, state):
        out = Path(tempfile.mkdtemp())
        return analyze_uploads(
            flyer_pdf=NARWHAL / "Narwhal Invite.pdf",
            psych_pdf=NARWHAL / "narwhal final psych again.pdf",
            timeline_pdf=NARWHAL / "narwhal final timeline.pdf",
            swimmer_name="Cova, Mila L", output_dir=out, state=state,
        )

    def test_manual_ca_suppresses_azsi_despite_az_team_code(self):
        result = self._run("CA")
        lines = lsc_lines(result)
        self.assertTrue(all("not configured" in line for line in lines), lines)
        self.assertFalse(has_auto_note(result))

    def test_manual_az_still_shows_azsi_without_an_auto_note(self):
        # Baseline: a typed "AZ" behaves exactly as before -- AZSI shown, but not "auto-detected".
        result = self._run("AZ")
        lines = lsc_lines(result)
        self.assertTrue(all(line.startswith("AZSI") for line in lines), lines)
        self.assertFalse(has_auto_note(result))


class PerSwimmerIndependenceTest(unittest.TestCase):
    """A combined family calendar detects each swimmer from their own team code, not one shared
    value: on the WZAG sheet, Cova (AZ) surfaces AZSI while Steinbis (SR) stays not-configured."""

    def test_family_calendar_resolves_each_swimmer_independently(self):
        try:
            from webapp.server import analyze_swimmer_set
        except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
            raise unittest.SkipTest(
                "webapp.server needs Python 3.12: the stdlib cgi module was removed in 3.13"
            ) from exc

        out = Path(tempfile.mkdtemp())
        result = analyze_swimmer_set(
            flyer_path=WZAG / "Sanctioned_2026 WZAG Championships - Boise (v5.pdf",
            psych_path=WZAG / "wzag psych sheet v3.pdf",
            timeline_path=WZAG / "wzag timelines v4.pdf",
            relay_path=None, internal_relay_sources=None,
            swimmer_names=["Cova, Mila L", "Steinbis, River"],
            output_dir=out, state="", modes=["daily"], combine_family=True,
            estimate_heat_lanes=False, meet_timezone="America/Boise",
            meet_venue="Idaho Central Aquatic Center, Boise, ID",
        )
        by_swimmer = {}
        for item in result["events"]:
            if item.get("type") == "relay":
                continue
            by_swimmer.setdefault(item["swimmer"], []).append(item["benchmarks"]["lsc"])

        cova = by_swimmer["Cova, Mila L"]
        steinbis = by_swimmer["Steinbis, River"]
        self.assertTrue(cova and all(line.startswith("AZSI") for line in cova), cova)
        self.assertTrue(steinbis and all("not configured" in line for line in steinbis), steinbis)

        # The auto-detect note is per swimmer: it names Cova (AZ), never Steinbis (SR).
        notes = [w for w in result.get("warnings", []) if "auto-detected" in w]
        self.assertTrue(any("Cova" in note for note in notes), notes)
        self.assertFalse(any("Steinbis" in note for note in notes), notes)


if __name__ == "__main__":
    unittest.main()
