"""A "no time" seed can carry the same trailing conversion-flag letter a real time does.

Rediscovered from real data, not assumed: HY-TEK prints "NTY"/"NTL" for a no-time entry at a meet
run in a different course than the swimmer's other times -- the same Y/L suffix a real time like
"2:23.23Y" carries. The seed-time regexes in extract.py only ever allowed that trailing letter on
the numeric branch, never on the literal "NT" token, so a row printing "NTY"/"NTL" failed to parse
at all and the swim was silently dropped -- not a marker lost, the whole event gone with no
warning, the same failure mode the trailing-standard-marker bug had (see
test_wzag_wednesday_real_docs.py).

Confirmed against real fixtures already in this repo, not synthetic rows:
  - meets/2026-shark-open/input/2026-shark-open-heat-sheet.pdf, page 7:
    "Whalers-WI NTY 13Perelshteyn, Andrew6"
  - meets/2026-wzag-championships-boise/input/wzag psych sheet v3.pdf, page 17:
    "SNS NTL 14Galizio, Carmella B62"
  - meets/2026-speedo-invite/input/speedo psych.pdf, pages 16-17: five more "NTY" rows (Crane,
    Webb, Termain, Darisan, Van Male).
None of these seven swimmers were referenced by any existing test -- the bug had no tracked
regression coverage before this. The other two real fixtures searched (age-group-state and
narwhal psych sheets, plus the WZAG heat-sheet-style prelim programs) contain no "NT" rows at all,
flagged or bare.

Fixing it safely required one more real-data check: parse_para_psych_line (used only for the Para
Nationals psych sheet) has NO digit field between seed and name the way the main regex does, and
real Para Nationals rows glue a seed straight onto a name with zero separator ("NTThomas...",
"4:49.17Winnett..."). A naive optional trailing letter there swallowed the name's own first
letter. Fixed with a lookahead so the flag is only consumed when followed by whitespace -- true of
every confirmed real NTY/NTL row above, never true when the next character is actually a name.
"""

from pathlib import Path
import tempfile
import unittest

from swimtimeline.extract import analyze_uploads, parse_entry_fields, parse_para_psych_line

ROOT = Path(__file__).resolve().parents[1]
WZAG = ROOT / "meets/2026-wzag-championships-boise/input"


class NoTimeWithConversionFlagTest(unittest.TestCase):
    """Real rows, verbatim from the documents named in the module docstring."""

    def test_shark_open_heat_sheet_row(self):
        # Event 28, Boys 11 & Over 100 LC Meter Breaststroke, Heat 1 of 2, Finals.
        row = parse_entry_fields("Whalers-WI NTY 13Perelshteyn, Andrew6", 1, "Finals")
        self.assertIsNotNone(row)
        self.assertEqual((row.team, row.seed, row.age), ("Whalers-WI", "NTY", "13"))
        self.assertEqual(row.swimmer_name, "Perelshteyn, Andrew")
        self.assertEqual(row.lane, 6)

    def test_wzag_psych_sheet_row(self):
        # Event 36, Girls 13-14 100 LC Meter Butterfly.
        row = parse_entry_fields("SNS NTL 14Galizio, Carmella B62", None, None)
        self.assertIsNotNone(row)
        self.assertEqual((row.team, row.seed, row.age), ("SNS", "NTL", "14"))
        # "B" here is the psych sheet's marker-before-place form (see
        # test_psych_sheet_marker_before_place_still_parses in test_wzag_wednesday_real_docs.py) --
        # unrelated to this fix, called out so a future reader isn't surprised it's still attached.
        self.assertEqual(row.swimmer_name, "Galizio, Carmella B")
        self.assertEqual(row.seed_place, 62)

    def test_speedo_invite_psych_rows(self):
        # Event #501 Girls 10 & Under 25 Yard Butterfly and neighboring events.
        rows = [
            ("LIFE-AZ NTY 10Crane, Samantha7", "LIFE-AZ", "Crane, Samantha", 7),
            ("HEAT-AZ NTY  7Webb, Duke7", "HEAT-AZ", "Webb, Duke", 7),
            ("MAC-AZ NTY  7Termain, Aaron N8", "MAC-AZ", "Termain, Aaron N", 8),
            ("NEP-AZ NTY  8Darisan, Rafael10", "NEP-AZ", "Darisan, Rafael", 10),
            ("MAC-AZ NTY  7Van Male, Camden G12", "MAC-AZ", "Van Male, Camden G", 12),
        ]
        for raw, team, name, place in rows:
            row = parse_entry_fields(raw, None, None)
            self.assertIsNotNone(row, raw)
            self.assertEqual(row.team, team, raw)
            self.assertEqual(row.seed, "NTY", raw)
            self.assertEqual(row.swimmer_name, name, raw)
            self.assertEqual(row.seed_place, place, raw)

    def test_bare_nt_next_to_the_same_speedo_rows_is_unaffected(self):
        # The very next row on the real page after Crane's: bare "NT", no flag -- already worked
        # and must keep parsing byte-identically.
        row = parse_entry_fields("MAC-AZ NT 10Post, Reagan M8", None, None)
        self.assertEqual((row.seed, row.swimmer_name), ("NT", "Post, Reagan M"))

    def test_end_to_end_wzag_event_is_recovered(self):
        # Before the fix, event 36 was silently missing from Carmella Galizio's calendar with no
        # warning -- her other five events (real times) were unaffected, which is exactly why the
        # gap had no tracked regression coverage until now.
        payload = analyze_uploads(
            flyer_pdf=WZAG / "Sanctioned_2026 WZAG Championships - Boise (v5.pdf",
            psych_pdf=WZAG / "wzag psych sheet v3.pdf",
            timeline_pdf=WZAG / "wzag timelines v4.pdf",
            swimmer_name="Galizio, Carmella",
            output_dir=Path(tempfile.mkdtemp()),
            state="",
            meet_timezone="America/Boise",
            meet_venue="Idaho Central Aquatic Center, Boise, ID",
            modes=["daily"],
        )
        self.assertEqual(payload["verified_event_count"], 6)
        event_36 = next(i for i in payload["items"] if i["event_number"] == 36)
        self.assertEqual(event_36["seed_time"], "NTL")
        self.assertEqual(event_36["day"], "Thursday")


class ParaPsychLineRegressionGuardTest(unittest.TestCase):
    """parse_para_psych_line (Para Nationals only) has no digit field between seed and name, so
    real rows glue the seed straight onto the name with zero separator. A naive copy of the same
    fix used above would have corrupted them -- these are the exact real rows that caught it
    during development, kept here so the guard can never silently regress."""

    def test_real_numeric_seed_glued_to_name_is_unaffected(self):
        row = parse_para_psych_line(
            "S10-Sun City Masters 4:49.17Winnett, Taylor S10  27   A, 12+1", None, None
        )
        self.assertIsNotNone(row)
        self.assertEqual(row.seed, "4:49.17")
        self.assertEqual(row.swimmer_name, "Winnett, Taylor")  # not "innett, Taylor"

    def test_real_bare_nt_glued_to_name_is_unaffected(self):
        row = parse_para_psych_line("S6-PSRT-NI NTThomas, Noah S6  22   12+19", None, None)
        self.assertIsNotNone(row)
        self.assertEqual(row.seed, "NT")
        self.assertEqual(row.swimmer_name, "Thomas, Noah")  # not "homas, Noah"


if __name__ == "__main__":
    unittest.main()
