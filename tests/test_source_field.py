"""Regression fixtures for the parent-facing Source field.

page_column_for_line() returns the internal sentinel "Unknown" when it cannot resolve a psych-sheet
entry's x-position to a Left/Middle/Right column. That sentinel used to leak straight into the
Source field a family sees ("... page 2, unknown column" in the .ics, "Unknown column" in the web
table). It's an internal parsing state, not information for a parent, so it is now suppressed: the
source line shows the column only when it was actually located, and omits it entirely otherwise.
"""

from pathlib import Path
import tempfile
import unittest

from swimtimeline.extract import (
    PsychEntry,
    analyze_uploads,
    entry_column_clause,
    entry_column_display,
    entry_source_line,
)

ROOT = Path(__file__).resolve().parents[1]


def psych_entry(column, page=2):
    return PsychEntry(
        day="Wednesday", event_number=5, event_name="Girls 11-12 50 LC Meter Freestyle",
        seed_time="28.62", seed_place=1, age="12", team="AZ", page=page, column=column, source_line="",
    )


class ColumnHelperTest(unittest.TestCase):
    def test_unknown_and_empty_are_never_surfaced(self):
        for value in ("Unknown", "unknown", "UNKNOWN", "", None):
            self.assertEqual(entry_column_display(value), "")
            self.assertEqual(entry_column_clause(value), "")

    def test_real_columns_pass_through(self):
        self.assertEqual(entry_column_display("Left"), "Left")
        self.assertEqual(entry_column_clause("Left"), ", left column")
        self.assertEqual(entry_column_clause("Right"), ", right column")

    def test_source_line_omits_unknown_column(self):
        self.assertEqual(entry_source_line(psych_entry("Unknown")), "Psych/entry sheet: page 2")

    def test_source_line_keeps_a_located_column(self):
        self.assertEqual(entry_source_line(psych_entry("Left")), "Psych/entry sheet: page 2, left column")


class UnknownColumnDoesNotLeakTest(unittest.TestCase):
    """Real WZAG psych sheet: every one of Cova's entries has an unresolved column, so the Source
    field must never say 'unknown column' -- in the .ics or the web payload."""

    def test_wzag_detailed_ics_and_payload_have_no_unknown_column(self):
        out = Path(tempfile.mkdtemp())
        base = ROOT / "meets/2026-wzag-championships-boise/input"
        result = analyze_uploads(
            flyer_pdf=base / "Sanctioned_2026 WZAG Championships - Boise (v5.pdf",
            psych_pdf=base / "wzag psych sheet v3.pdf",
            timeline_pdf=base / "wzag timelines v4.pdf",
            swimmer_name="Cova, Mila L", output_dir=out, state="AZ",
            meet_timezone="America/Boise", meet_venue="Idaho Central Aquatic Center, Boise, ID",
            modes=["detailed"],
        )
        ics = (out / "detailed.ics").read_text(encoding="utf-8").replace("\r\n ", "").lower()
        self.assertNotIn("unknown column", ics)
        # The web payload's column field is blank for an unresolved column, so app.js renders no
        # column line at all (rather than "Unknown column").
        columns = [item["column"] for item in result["items"] if item.get("type") != "relay"]
        self.assertTrue(columns)  # Cova has events
        self.assertTrue(all(c == "" for c in columns), columns)
        # The audit report is a family download too, so the raw "Unknown" sentinel must not appear
        # there either -- its Column cells are blank for unresolved entries.
        audit = (out / "audit.md").read_text(encoding="utf-8")
        self.assertNotIn("Unknown", audit)


class LocatedColumnStillShownTest(unittest.TestCase):
    """The suppression must not over-reach: a psych sheet whose columns ARE resolved (Narwhal)
    still shows them, so the fix hides only the meaningless sentinel."""

    def test_narwhal_detailed_ics_still_names_a_column(self):
        out = Path(tempfile.mkdtemp())
        base = ROOT / "meets/2026-narwhal-invite/input"
        analyze_uploads(
            flyer_pdf=base / "Narwhal Invite.pdf",
            psych_pdf=base / "narwhal final psych again.pdf",
            timeline_pdf=base / "narwhal final timeline.pdf",
            swimmer_name="Cova, Mila L", output_dir=out, state="AZ", modes=["detailed"],
        )
        ics = (out / "detailed.ics").read_text(encoding="utf-8").replace("\r\n ", "").lower()
        self.assertNotIn("unknown column", ics)
        self.assertTrue(
            any(f"{side} column" in ics for side in ("left", "middle", "right")),
            "expected a located Left/Middle/Right column in Narwhal's source lines",
        )


if __name__ == "__main__":
    unittest.main()
