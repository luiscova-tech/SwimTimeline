"""Benchmark source links + the reduced confidence line.

Every displayed standard label is a checkable link to the document that number came from, sourced
from each dataset's own JSON `source`/meet metadata: Motivational and AZSI carry one source URL
each; Sectional/national carry a full URL per meet. The redundant "Standards confidence: ...
verified" line is gone (the tier/cut on each summary line already shows what was checked); only
genuine gaps ("not configured") remain on that line.
"""

from pathlib import Path
import tempfile
import unittest

from swimtimeline.standards import (
    AZSI_SOURCE_URL,
    MOTIVATIONAL_SOURCE_URL,
    lookup,
)
from swimtimeline.extract import analyze_uploads, benchmark_line_with_sources

ROOT = Path(__file__).resolve().parents[1]
WZAG = ROOT / "meets/2026-wzag-championships-boise/input"


class SourceLinkPresenceTest(unittest.TestCase):
    """One live example per dataset type, from a real Cova WZAG event."""

    def setUp(self):
        # 100 LCM Free @ 1:03.41 is AAAA -> carries usa, lsc, sectional, national, and the advanced
        # "Beyond AAAA" line that names the sectional meets.
        self.result = lookup("Girls 11-12 100 LC Meter Freestyle", "1:03.41", state="AZ", age="12")

    def test_motivational_label_links_to_its_recorded_source(self):
        usa = self.result.sources["usa"]
        self.assertEqual(len(usa), 1)
        self.assertEqual(usa[0]["label"], "USA-S 11-12 Girls LCM")
        self.assertEqual(usa[0]["url"], MOTIVATIONAL_SOURCE_URL)
        self.assertTrue(usa[0]["url"].startswith("https://www.usaswimming.org/"))
        # The label is an exact substring of the displayed line so the UI can linkify it in place.
        self.assertIn(usa[0]["label"], self.result.usa_summary)

    def test_azsi_label_links_to_its_recorded_source(self):
        lsc = self.result.sources["lsc"]
        self.assertEqual(lsc[0]["label"], "AZSI 11-12 Girls LCM")
        self.assertEqual(lsc[0]["url"], AZSI_SOURCE_URL)
        self.assertTrue(lsc[0]["url"].startswith("https://www.azswimming.org/"))
        self.assertIn(lsc[0]["label"], self.result.lsc_summary)

    def test_sectional_meets_link_to_their_per_meet_sources(self):
        # Sectional meets are named on the advanced "Beyond AAAA" line for an AAAA swimmer.
        advanced = {s["label"]: s["url"] for s in self.result.sources["advanced"]}
        self.assertIn("Four Corners Spring Speedo Sectional", advanced)
        self.assertEqual(
            advanced["Four Corners Spring Speedo Sectional"],
            "https://www.gomotionapp.com/azseals/__doc__/211074_4_2026-speedo-sectionals-four-corners-final.pdf",
        )
        for label, url in advanced.items():
            self.assertIn(label, self.result.advanced_summary)
            self.assertTrue(url.startswith("https://"))

    def test_national_meets_link_to_their_per_meet_sources(self):
        national = {s["label"]: s["url"] for s in self.result.sources["national"]}
        self.assertIn("TYR Futures Championships", national)
        self.assertTrue(national["TYR Futures Championships"].startswith("https://www.usaswimming.org/"))

    def test_every_source_url_is_present_and_absolute(self):
        for line_sources in self.result.sources.values():
            for source in line_sources:
                self.assertTrue(source["url"] and source["url"].startswith("http"), source)


class ReducedConfidenceLineTest(unittest.TestCase):
    def test_all_resolved_omits_the_confidence_line_entirely(self):
        # No redundant "USA-S verified, AZSI verified" restatement.
        result = lookup("Girls 11-12 50 LC Meter Breaststroke", "39.50", state="AZ", age="12")
        self.assertEqual(result.confidence_summary, "")
        self.assertNotIn("verified", result.confidence_summary)

    def test_gaps_are_still_flagged(self):
        # A non-AZ swimmer has no LSC standards configured -- that IS informative and must remain.
        result = lookup("Girls 11-12 50 LC Meter Freestyle", "28.62", state="CA", age="12")
        self.assertIn("not configured", result.confidence_summary)
        self.assertIn("LSC not configured", result.confidence_summary)
        # ...and the gap is also on the line itself, unchanged.
        self.assertIn("not configured", result.lsc_summary)

    def test_every_resolution_combination_is_reduced_consistently(self):
        # The reduction must hold across every reachable combination, not just one showcase case:
        # (USA-S ok, LSC ok) / (ok, gap) / (gap, gap) / unparseable seed. In no combination may a
        # redundant "verified" (or the old advanced buckets) reappear.
        cases = {
            "fully resolved": (
                lookup("Girls 11-12 50 LC Meter Freestyle", "28.62", state="AZ", age="12"),
                "",
            ),
            "partial gap (USA-S ok, LSC not configured)": (
                lookup("Girls 11-12 50 LC Meter Freestyle", "28.62", state="CA", age="12"),
                "Standards confidence: LSC not configured",
            ),
            "fully gap (age 19 falls off both catalogs)": (
                lookup("Women 100 LC Meter Freestyle", "1:00.00", state="AZ", age="19"),
                "Standards confidence: USA-S not configured, LSC not configured",
            ),
            "seed not parseable": (
                lookup("Girls 11-12 50 LC Meter Freestyle", "NT", state="AZ", age="12"),
                "Standards confidence: not calculated",
            ),
        }
        for label, (result, expected) in cases.items():
            self.assertEqual(result.confidence_summary, expected, label)
            self.assertNotIn("verified", result.confidence_summary, label)
            self.assertNotIn("advanced", result.confidence_summary, label)


class PlainTextSurfaceTest(unittest.TestCase):
    def test_ics_helper_appends_url_as_plain_text(self):
        line = benchmark_line_with_sources(
            "USA-S 11-12 Girls LCM: AAAA",
            [{"label": "USA-S 11-12 Girls LCM", "url": "https://example.org/motivational"}],
        )
        self.assertEqual(line, "USA-S 11-12 Girls LCM: AAAA (source: https://example.org/motivational)")

    def test_ics_helper_joins_multiple_meet_urls(self):
        line = benchmark_line_with_sources(
            "Beyond AAAA: next A / B 1:01.26",
            [{"label": "A", "url": "https://ex/a"}, {"label": "B", "url": "https://ex/b"}],
        )
        self.assertIn("(source: https://ex/a; https://ex/b)", line)

    def test_no_sources_leaves_the_line_unchanged(self):
        self.assertEqual(benchmark_line_with_sources("LSC: n/a", None), "LSC: n/a")
        self.assertEqual(benchmark_line_with_sources("LSC: n/a", []), "LSC: n/a")


class WebAndIcsIntegrationTest(unittest.TestCase):
    """End-to-end against real Cova WZAG data: web payload carries structured sources, the .ics
    carries plain-text URLs, and no redundant confidence line survives."""

    def setUp(self):
        self.out = Path(tempfile.mkdtemp())
        self.result = analyze_uploads(
            flyer_pdf=WZAG / "Sanctioned_2026 WZAG Championships - Boise (v5.pdf",
            psych_pdf=WZAG / "wzag psych sheet v3.pdf",
            timeline_pdf=WZAG / "wzag timelines v4.pdf",
            swimmer_name="Cova, Mila L", output_dir=self.out, state="",
            meet_timezone="America/Boise", meet_venue="X", modes=["detailed"],
        )

    def test_web_payload_items_carry_benchmark_sources(self):
        item = next(
            i for i in self.result["items"]
            if i.get("type") != "relay" and i["event_name"] == "Girls 11-12 100 LC Meter Freestyle"
        )
        sources = item["benchmarks"]["sources"]
        self.assertEqual(sources["usa"][0]["url"], MOTIVATIONAL_SOURCE_URL)
        self.assertEqual(sources["lsc"][0]["url"], AZSI_SOURCE_URL)
        self.assertTrue(any("gomotionapp" in s["url"] for s in sources["advanced"]))

    def test_ics_carries_plain_text_source_urls_and_no_confidence_line(self):
        # Undo iCal 75-octet line folding (read_text normalizes CRLF -> LF, so the fold is "\n ").
        ics = (self.out / "detailed.ics").read_text(encoding="utf-8").replace("\r\n ", "").replace("\n ", "")
        self.assertIn("(source: " + MOTIVATIONAL_SOURCE_URL, ics)
        self.assertIn("(source: " + AZSI_SOURCE_URL, ics)
        self.assertIn("gomotionapp", ics)  # sectional URL on the advanced line
        self.assertNotIn("Standards confidence", ics)  # redundant line gone (Cova is all-resolved)


if __name__ == "__main__":
    unittest.main()
