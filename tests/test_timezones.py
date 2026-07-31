from pathlib import Path
import unittest

from swimtimeline.extract import analyze_uploads, resolve_meet_timezone
from swimtimeline.ics import build_ics, vtimezone_lines


ROOT = Path(__file__).resolve().parents[1]


class ResolveMeetTimezoneTest(unittest.TestCase):
    def test_state_table_covers_known_meet_states(self):
        self.assertEqual(resolve_meet_timezone("AZ"), "America/Phoenix")
        self.assertEqual(resolve_meet_timezone("ID"), "America/Boise")
        self.assertEqual(resolve_meet_timezone("FL"), "America/New_York")

    def test_explicit_timezone_overrides_state_table(self):
        self.assertEqual(resolve_meet_timezone("AZ", "America/Boise"), "America/Boise")

    def test_unknown_state_and_missing_explicit_falls_back_to_default(self):
        self.assertEqual(resolve_meet_timezone("ZZ"), "America/Phoenix")
        self.assertEqual(resolve_meet_timezone(None), "America/Phoenix")

    def test_invalid_explicit_timezone_falls_back_to_state_table(self):
        self.assertEqual(resolve_meet_timezone("FL", "Not/AZone"), "America/New_York")


class VtimezoneLinesTest(unittest.TestCase):
    def test_no_dst_zone_emits_single_fixed_offset_standard_block(self):
        lines = vtimezone_lines("America/Phoenix")
        joined = "\n".join(lines)
        self.assertIn("TZOFFSETFROM:-0700", joined)
        self.assertIn("TZOFFSETTO:-0700", joined)
        self.assertIn("TZNAME:MST", joined)
        self.assertNotIn("BEGIN:DAYLIGHT", joined)

    def test_dst_zone_emits_standard_and_daylight_blocks_with_correct_offsets(self):
        lines = vtimezone_lines("America/Boise")
        joined = "\n".join(lines)
        self.assertIn("BEGIN:DAYLIGHT", joined)
        self.assertIn("TZNAME:MST", joined)
        self.assertIn("TZNAME:MDT", joined)
        self.assertIn("TZOFFSETTO:-0700", joined)  # standard (winter)
        self.assertIn("TZOFFSETTO:-0600", joined)  # daylight (summer)

    def test_eastern_zone_emits_est_edt_offsets(self):
        lines = vtimezone_lines("America/New_York")
        joined = "\n".join(lines)
        self.assertIn("TZNAME:EST", joined)
        self.assertIn("TZNAME:EDT", joined)
        self.assertIn("TZOFFSETTO:-0500", joined)
        self.assertIn("TZOFFSETTO:-0400", joined)


class BuildIcsTimezoneTest(unittest.TestCase):
    def _payload(self, timezone):
        return {
            "calendar": {"name": "Test", "timezone": timezone},
            "events": [
                {
                    "uid": "test@swimtimeline",
                    "title": "Test Event",
                    "start": "2026-06-12T07:00:00",
                    "end": "2026-06-12T08:00:00",
                    "location": "Test Pool",
                    "description_lines": ["line one"],
                }
            ],
        }

    def test_ics_dtstart_uses_the_payload_timezone_id(self):
        ics_text = build_ics(self._payload("America/Boise"))
        self.assertIn("TZID:America/Boise", ics_text)
        self.assertIn("DTSTART;TZID=America/Boise:20260612T070000", ics_text)

    def test_ics_no_longer_hardcodes_mst_for_a_non_arizona_meet(self):
        ics_text = build_ics(self._payload("America/New_York"))
        self.assertNotIn("TZNAME:MST", ics_text)
        self.assertIn("TZNAME:EDT", ics_text)


class KnownGoodMeetRegressionTest(unittest.TestCase):
    """Fixes 2-4 must not change extraction results for a known-good meet/swimmer."""

    def test_narwhal_invite_cova_mila_l_still_matches_seven_events(self):
        result = analyze_uploads(
            flyer_pdf=ROOT / "meets/2026-narwhal-invite/input/Narwhal Invite.pdf",
            psych_pdf=ROOT / "meets/2026-narwhal-invite/input/narwhal final psych again.pdf",
            timeline_pdf=ROOT / "meets/2026-narwhal-invite/input/narwhal final timeline.pdf",
            swimmer_name="Cova, Mila L",
            output_dir=Path("/private/tmp/swimtimeline-narwhal-regression-test"),
            state="AZ",
            modes=["daily"],
        )

        self.assertEqual(result["verified_event_count"], 7)
        self.assertEqual(result["verified_relay_count"], 0)
        self.assertEqual(result["warnings"], [])
        self.assertEqual(result["files"]["daily_ics"], "daily.ics")

        ics_path = Path("/private/tmp/swimtimeline-narwhal-regression-test/daily.ics")
        ics_text = ics_path.read_text(encoding="utf-8")
        # Arizona does not observe DST: a single fixed -0700 offset all year.
        self.assertIn("TZID:America/Phoenix", ics_text)
        self.assertIn("TZOFFSETFROM:-0700", ics_text)
        self.assertIn("TZOFFSETTO:-0700", ics_text)
        self.assertNotIn("BEGIN:DAYLIGHT", ics_text)


class OutOfStateMeetTimezoneTest(unittest.TestCase):
    """Regression fixture for the timezone bug: a real out-of-state (FL) meet."""

    def test_shark_open_uses_eastern_time_not_phoenix(self):
        result = analyze_uploads(
            flyer_pdf=ROOT / "meets/2026-shark-open/input/2026-shark-open-flyer.pdf",
            psych_pdf=ROOT / "meets/2026-shark-open/input/2026-shark-open-heat-sheet.pdf",
            timeline_pdf=ROOT / "meets/2026-shark-open/input/2026-shark-open-timeline.pdf",
            swimmer_name="Sydney Hardy",
            output_dir=Path("/private/tmp/swimtimeline-shark-open-tz-test"),
            state="FL",
            modes=["daily"],
        )

        self.assertGreater(result["verified_event_count"], 0)

        ics_path = Path("/private/tmp/swimtimeline-shark-open-tz-test/daily.ics")
        ics_text = ics_path.read_text(encoding="utf-8")
        self.assertIn("TZID:America/New_York", ics_text)
        self.assertIn("X-WR-TIMEZONE:America/New_York", ics_text)
        self.assertNotIn("America/Phoenix", ics_text)
        # Real DST rule for Eastern time: both EST and EDT must be represented.
        self.assertIn("TZNAME:EST", ics_text)
        self.assertIn("TZNAME:EDT", ics_text)

        # The event's local wall-clock hour must be unaffected by the timezone
        # fix (only the TZID/VTIMEZONE label changes, not the parsed time).
        event_line = next(line for line in ics_text.splitlines() if line.startswith("DTSTART;TZID="))
        self.assertIn("America/New_York:", event_line)


if __name__ == "__main__":
    unittest.main()
