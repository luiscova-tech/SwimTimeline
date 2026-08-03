"""Name-matching: tightened fuzzy rules, the merge-and-mislabel guard, and partial-name resolution.

Three distinct contexts, per this session's investigation:
  * live search fuzzy matching -- must catch dropped/doubled-letter typos WITHOUT merging distinct
    real swimmers (Marco/Mario Gonzalez, Prima/Priya Sanders, Amy/Andy Wang were real collisions);
  * a partial (last-name-only) live search -- individual events match by substring but relays hash a
    full (first, last) pair, so a partial query must resolve to the swimmer's full name to get relays;
  * roster-building -- a coach's typed email matched against the authoritative psych sheet, scoped to
    the (Arizona, same-age) slice and accepted only when unambiguous (Rosetti -> Rossetti).
"""

from pathlib import Path
import re
import tempfile
import unittest

from swimtimeline.extract import (
    analyze_uploads,
    close_name_pair,
    distinct_swimmer_pairs,
    extract_internal_relay_entries,
    extract_text_pages,
    name_pairs,
    resolve_fuzzy_match,
    resolved_relay_query,
    single_indel,
    PsychEntry,
)

ROOT = Path(__file__).resolve().parents[1]
WZAG = ROOT / "meets/2026-wzag-championships-boise/input"
ROSTER = ROOT / "data/internal_relay_sources/az-2026-wzag-relays.json"


def psych_entry(matched_name):
    return PsychEntry(
        day="", event_number=5, event_name="Girls 11-12 50 LC Meter Freestyle", seed_time="30.0",
        seed_place=1, age="12", team="AZ", page=1, column="", source_line="", matched_name=matched_name,
    )


class TightenedFuzzyTest(unittest.TestCase):
    """Single indels (dropped/doubled letter) match; same-length substitutions do not."""

    def test_single_indel_helper(self):
        self.assertTrue(single_indel("mil", "mila"))       # dropped trailing letter
        self.assertTrue(single_indel("rosetti", "rossetti"))  # doubled letter
        self.assertFalse(single_indel("marco", "mario"))   # same-length substitution
        self.assertFalse(single_indel("amy", "andy"))      # two edits

    def test_legitimate_single_letter_typos_still_match(self):
        self.assertTrue(close_name_pair(("mil", "cova"), ("mila", "cova")))       # first-name indel
        self.assertTrue(close_name_pair(("eva", "rosetti"), ("eva", "rossetti"))) # last-name indel

    def test_known_bad_collisions_no_longer_match(self):
        # The three real different-swimmer pairs found in WZAG data must NOT be treated as one person.
        self.assertFalse(close_name_pair(("marco", "gonzalez"), ("mario", "gonzalez")))
        self.assertFalse(close_name_pair(("prima", "sanders"), ("priya", "sanders")))
        self.assertFalse(close_name_pair(("amy", "wang"), ("andy", "wang")))

    def test_no_different_swimmer_collisions_across_the_full_wzag_sheet(self):
        # The exact verification from the investigation: zero collisions among all 653 real swimmers.
        txt = "\n".join(extract_text_pages(WZAG / "wzag psych sheet v3.pdf"))
        swimmers = set()
        for line in txt.splitlines():
            m = re.match(
                r"^([A-Za-z]{2,6}(?:-[A-Za-z]{2})?)\s+[\d:]+\.?\d*L?\s+(\d{1,2})"
                r"([A-Z][a-zA-Z'\-]+,\s*[A-Z][a-zA-Z'\-]+)",
                line.strip(),
            )
            if m and name_pairs(m.group(3).strip()):
                swimmers.add(name_pairs(m.group(3).strip())[0])
        names = sorted(swimmers)
        self.assertGreater(len(names), 600)  # sanity: we really parsed the whole sheet
        collisions = [
            (a, b) for i, a in enumerate(names) for b in names[i + 1:]
            if a != b and close_name_pair(a, b)
        ]
        self.assertEqual(collisions, [])


class MergeMislabelGuardTest(unittest.TestCase):
    """A fuzzy pass that matches multiple distinct swimmers must surface ambiguity, not merge them."""

    def test_distinct_swimmer_pairs_collapses_rows_of_one_swimmer(self):
        one = [psych_entry("Cova, Mila B"), psych_entry("Cova, Mila WZAG")]
        self.assertEqual(len(distinct_swimmer_pairs(one)), 1)
        two = [psych_entry("Gonzalez, Marco"), psych_entry("Gonzalez, Mario")]
        self.assertEqual(len(distinct_swimmer_pairs(two)), 2)

    def test_multiple_distinct_fuzzy_matches_are_refused(self):
        entries = [psych_entry("Gonzalez, Marco"), psych_entry("Gonzalez, Mario")]
        result_entries, page_counts, warnings = resolve_fuzzy_match("Gonzale", entries, [{"page": 1, "count": 2}])
        self.assertEqual(result_entries, [])   # not merged
        self.assertEqual(page_counts, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("more than one swimmer", warnings[0])
        self.assertIn("Marco Gonzalez", warnings[0])
        self.assertIn("Mario Gonzalez", warnings[0])

    def test_single_resolved_swimmer_passes_through_with_notice(self):
        entries = [psych_entry("Cova, Mila B"), psych_entry("Cova, Mila WZAG")]
        result_entries, _, warnings = resolve_fuzzy_match("Mila Cov", entries, [{"page": 2, "count": 2}])
        self.assertEqual(len(result_entries), 2)  # kept -- one swimmer
        self.assertTrue(any("high-confidence match" in w for w in warnings))


class ResolvedRelayQueryTest(unittest.TestCase):
    def test_unambiguous_partial_resolves_to_full_name(self):
        entries = [psych_entry("Cova, Mila B"), psych_entry("Cova, Mila WZAG")]
        self.assertEqual(resolved_relay_query("Cova", entries), "Cova, Mila B")

    def test_ambiguous_query_is_not_resolved_to_one_swimmer(self):
        entries = [psych_entry("Gonzalez, Marco"), psych_entry("Gonzalez, Mario")]
        self.assertEqual(resolved_relay_query("Gonzalez", entries), "Gonzalez")  # raw query, no guess

    def test_no_entries_falls_back_to_raw_query(self):
        self.assertEqual(resolved_relay_query("Nobody", []), "Nobody")


class PartialNameLiveSearchTest(unittest.TestCase):
    """Cova last-name-only must now get the same complete result as her full name."""

    def _run(self, name):
        return analyze_uploads(
            flyer_pdf=WZAG / "Sanctioned_2026 WZAG Championships - Boise (v5.pdf",
            psych_pdf=WZAG / "wzag psych sheet v3.pdf",
            timeline_pdf=WZAG / "wzag timelines v4.pdf",
            swimmer_name=name, output_dir=Path(tempfile.mkdtemp()), state="",
            meet_timezone="America/Boise", meet_venue="Idaho Central Aquatic Center, Boise, ID",
            modes=["detailed"], internal_relay_sources=[ROSTER],
        )

    def test_last_name_only_matches_full_name_result(self):
        partial = self._run("Cova")
        full = self._run("Mila Cova")
        relays_partial = sorted(i["event_number"] for i in partial["items"] if i.get("type") == "relay")
        relays_full = sorted(i["event_number"] for i in full["items"] if i.get("type") == "relay")
        self.assertEqual(relays_partial, [25, 50, 75, 99])
        self.assertEqual(relays_partial, relays_full)
        self.assertEqual(partial["verified_relay_count"], 4)
        # State/LSC still auto-detected -> AZSI benchmarks present (unaffected by the partial query).
        azsi = [i for i in partial["items"] if i.get("type") != "relay" and i["benchmarks"]["lsc"].startswith("AZSI")]
        self.assertTrue(azsi)


class RosterFuzzyResolutionTest(unittest.TestCase):
    """The generated roster resolves the coach's 'Rosetti' to the psych sheet's 'Rossetti'."""

    def test_rossetti_is_matchable_in_the_roster(self):
        for name in ("Eva Rossetti", "Rossetti, Eva"):
            relays, _ = extract_internal_relay_entries([ROSTER], name)
            self.assertEqual(sorted(r.event_number for r in relays), [24, 50, 74, 99], name)

    def test_only_the_withdrawn_swimmer_is_left_unmatchable(self):
        import json
        doc = json.loads(ROSTER.read_text(encoding="utf-8"))
        empty = [(e["event_number"], s["leg"]) for e in doc["entries"] for s in e["swimmers"] if not s["hashes"]]
        # Exactly Beltran's three legs (he withdrew, absent from the psych sheet); Rosetti now resolves.
        self.assertEqual(sorted(empty), [(24, 1), (51, 2), (100, 3)])


if __name__ == "__main__":
    unittest.main()
