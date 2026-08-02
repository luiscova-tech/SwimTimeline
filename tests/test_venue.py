"""Regression fixtures for the venue/facility bug.

Root cause: the timeline parser used to *guess* a facility from the session name -- any session
containing "final"/"sr" mapped to "Skyline Aquatic Center" and any "ag" session to "Kino Aquatic
Complex", both of which location_for_session() then expanded to fixed Mesa, Arizona street
addresses. That put a Mesa, AZ address on every non-Arizona meet's finals sessions (confirmed on
the Boise, ID WZAG meet and the Sarasota, FL Shark Open).

The fix removes the guess entirely. Facility now comes only from the meet's own data: parsed from
its flyer/timeline, or the meet record's explicit ``venue`` field (threaded through as
``meet_venue`` exactly like ``meet_timezone``). When nothing is known the location is the neutral
"Meet facility" -- never a specific wrong address.

These tests pin both sides: non-AZ meets no longer leak Mesa, AZ, and the real Arizona meets
(whose venues are parsed from their own documents) still show their correct Mesa, AZ addresses.
"""

from pathlib import Path
import unittest

from swimtimeline.extract import analyze_uploads

ROOT = Path(__file__).resolve().parents[1]


def daily_ics_for(output_name, *, meet_venue=None, **kwargs):
    out_dir = Path("/private/tmp") / output_name
    result = analyze_uploads(output_dir=out_dir, modes=["daily"], meet_venue=meet_venue, **kwargs)
    ics_text = (out_dir / "daily.ics").read_text(encoding="utf-8")
    return result, ics_text


class NonArizonaMeetVenueTest(unittest.TestCase):
    """Non-AZ meets must never inherit the hardcoded Mesa, AZ venue."""

    def test_wzag_boise_shows_idaho_venue_not_mesa_az(self):
        result, ics = daily_ics_for(
            "swimtimeline-venue-wzag",
            flyer_pdf=ROOT / "meets/2026-wzag-championships-boise/input/Sanctioned_2026 WZAG Championships - Boise (v5.pdf",
            psych_pdf=ROOT / "meets/2026-wzag-championships-boise/input/wzag psych sheet v3.pdf",
            timeline_pdf=ROOT / "meets/2026-wzag-championships-boise/input/wzag timelines v4.pdf",
            swimmer_name="Stein, Layla",
            state="ID",
            meet_venue="Idaho Central Aquatic Center, 3575 S. Findley Ave., Boise, ID 83705",
        )
        self.assertEqual(result["verified_event_count"], 6)
        # Stein's Wednesday event is the 800 Free finals swim -- the exact session that used to
        # inherit the Skyline, Mesa AZ address. It must now show the real Boise venue.
        self.assertIn("Idaho Central Aquatic Center", ics)
        self.assertNotIn("Mesa", ics)
        self.assertNotIn("Skyline", ics)
        self.assertNotIn("Kino", ics)

    def test_shark_open_sarasota_shows_florida_venue_not_mesa_az(self):
        result, ics = daily_ics_for(
            "swimtimeline-venue-shark",
            flyer_pdf=ROOT / "meets/2026-shark-open/input/2026-shark-open-flyer.pdf",
            psych_pdf=ROOT / "meets/2026-shark-open/input/2026-shark-open-heat-sheet.pdf",
            timeline_pdf=ROOT / "meets/2026-shark-open/input/2026-shark-open-timeline.pdf",
            swimmer_name="Sydney Hardy",
            state="FL",
            meet_venue="Selby Aquatic Center, Sarasota, FL",
        )
        self.assertGreater(result["verified_event_count"], 0)
        self.assertIn("Selby Aquatic Center", ics)
        self.assertNotIn("Mesa", ics)
        self.assertNotIn("Skyline", ics)

    def test_non_az_meet_without_a_venue_field_falls_back_to_neutral_not_mesa(self):
        # Root-cause proof: even with NO venue supplied (as an uploaded meet would have), a non-AZ
        # meet must not inherit the AZ address. It falls back to the neutral "Meet facility".
        result, ics = daily_ics_for(
            "swimtimeline-venue-wzag-novenue",
            flyer_pdf=ROOT / "meets/2026-wzag-championships-boise/input/Sanctioned_2026 WZAG Championships - Boise (v5.pdf",
            psych_pdf=ROOT / "meets/2026-wzag-championships-boise/input/wzag psych sheet v3.pdf",
            timeline_pdf=ROOT / "meets/2026-wzag-championships-boise/input/wzag timelines v4.pdf",
            swimmer_name="Stein, Layla",
            state="ID",
            meet_venue=None,
        )
        self.assertNotIn("Mesa", ics)
        self.assertNotIn("Skyline", ics)
        self.assertIn("Meet facility", ics)


class ArizonaMeetVenueTest(unittest.TestCase):
    """The real Arizona meets parse their venue from their own documents and must be unchanged --
    Mesa, AZ is correct for them and must not regress."""

    def test_narwhal_still_shows_its_real_mesa_az_venues(self):
        # Narwhal genuinely runs at Skyline (Senior) and Kino (Age Group) in Mesa, AZ -- both
        # parsed from its own flyer/timeline, not guessed. This must survive the fix.
        result, ics = daily_ics_for(
            "swimtimeline-venue-narwhal",
            flyer_pdf=ROOT / "meets/2026-narwhal-invite/input/Narwhal Invite.pdf",
            psych_pdf=ROOT / "meets/2026-narwhal-invite/input/narwhal final psych again.pdf",
            timeline_pdf=ROOT / "meets/2026-narwhal-invite/input/narwhal final timeline.pdf",
            swimmer_name="Cova, Mila L",
            state="AZ",
        )
        self.assertEqual(result["verified_event_count"], 7)
        self.assertIn("Mesa", ics)
        self.assertTrue(("Skyline" in ics) or ("Kino" in ics))

    def test_az_age_group_state_still_shows_its_parsed_oasis_venue(self):
        result, ics = daily_ics_for(
            "swimtimeline-venue-azstate",
            flyer_pdf=ROOT / "meets/2026-az-lc-age-group-state/input/age-group-state-meet-flyer.pdf",
            psych_pdf=ROOT / "meets/2026-az-lc-age-group-state/input/age-group-state-psych-sheet.pdf",
            timeline_pdf=ROOT / "meets/2026-az-lc-age-group-state/input/age-group-state-timeline.pdf",
            swimmer_name="Mila Cova",
            state="AZ",
        )
        self.assertGreater(result["verified_event_count"], 0)
        self.assertIn("Oasis Swim Center", ics)
        # This AZ meet is NOT at Kino/Skyline, so those must not appear either.
        self.assertNotIn("Skyline", ics)
        self.assertNotIn("Kino", ics)


if __name__ == "__main__":
    unittest.main()
