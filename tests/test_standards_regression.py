"""Spot-checks pinning the loaded motivational-standards catalog to official values.

Source of truth: USA Swimming 2024-2028 Motivational Standards, two-year age group
(docs/Sources/2028-motivational-standards-age-group.pdf), extracted by
scripts/extract_motivational_standards.py into data/motivational_standards.json.

These hardcoded values guard against a bad regeneration or hand-edit of the data file:
a broken extraction (column flip, wrong course/band, transcription drift) trips a test
immediately instead of surfacing months later when a parent notices. Coverage spans all
three courses, both genders, and multiple age bands -- not just the original Girls LCM.
"""

import unittest

from swimtimeline.standards import MOTIVATIONAL_STANDARDS, TIER_ORDER, parse_time


class MotivationalStandardsValueTest(unittest.TestCase):
    def value(self, course, gender, band, event, tier):
        return MOTIVATIONAL_STANDARDS[course][gender][band][event][tier]

    # --- Girls LCM (the original coverage, plus the reported bug) ---
    def test_girls_lcm_11_12_200_free(self):
        # The value a parent reported wrong: AAAA was 2:20.09, official band value is 2:19.79.
        self.assertEqual(self.value("LCM", "girls", "11-12", "200 free", "AAAA"), "2:19.79")
        self.assertEqual(self.value("LCM", "girls", "11-12", "200 free", "B"), "3:06.39")

    def test_girls_lcm_13_14_100_free(self):
        self.assertEqual(self.value("LCM", "girls", "13-14", "100 free", "AAAA"), "1:00.89")

    def test_girls_lcm_15_16_200_im(self):
        self.assertEqual(self.value("LCM", "girls", "15-16", "200 im", "AAAA"), "2:23.39")

    # --- Boys (new gender dimension; also checks the AAAA<->B column flip) ---
    def test_boys_lcm_11_12_50_free_column_order(self):
        self.assertEqual(self.value("LCM", "boys", "11-12", "50 free", "AAAA"), "28.09")
        self.assertEqual(self.value("LCM", "boys", "11-12", "50 free", "B"), "37.39")

    def test_boys_scy_15_16_100_free(self):
        self.assertEqual(self.value("SCY", "boys", "15-16", "100 free", "AAAA"), "46.49")

    # --- Short course yards (new course dimension) ---
    def test_girls_scy_10_and_under_50_free(self):
        self.assertEqual(self.value("SCY", "girls", "10 & under", "50 free", "B"), "39.79")
        self.assertEqual(self.value("SCY", "girls", "10 & under", "50 free", "AAAA"), "28.29")

    # --- Short course meters (new course dimension) ---
    def test_girls_scm_11_12_200_free(self):
        self.assertEqual(self.value("SCM", "girls", "11-12", "200 free", "AAAA"), "2:14.69")


class MotivationalStandardsStructureTest(unittest.TestCase):
    def test_all_courses_genders_and_bands_present(self):
        expected_bands = {"10 & under", "11-12", "13-14", "15-16", "17-18"}
        for course in ("SCY", "SCM", "LCM"):
            self.assertIn(course, MOTIVATIONAL_STANDARDS)
            for gender in ("girls", "boys"):
                self.assertIn(gender, MOTIVATIONAL_STANDARDS[course])
                self.assertEqual(set(MOTIVATIONAL_STANDARDS[course][gender]), expected_bands)

    def test_every_event_has_six_monotonic_tiers(self):
        for course, genders in MOTIVATIONAL_STANDARDS.items():
            for gender, bands in genders.items():
                for band, events in bands.items():
                    for event, tiers in events.items():
                        where = f"{course} {gender} {band} {event}"
                        self.assertEqual(set(tiers), set(TIER_ORDER), where)
                        seconds = [parse_time(tiers[t]) for t in TIER_ORDER]
                        self.assertTrue(all(s is not None for s in seconds), where)
                        # B (slowest) -> AAAA (fastest): strictly decreasing.
                        self.assertTrue(
                            all(seconds[i] > seconds[i + 1] for i in range(5)),
                            f"non-monotonic tiers at {where}: {tiers}",
                        )

    def test_catalog_totals_match_the_pdf(self):
        event_entries = sum(
            len(events)
            for genders in MOTIVATIONAL_STANDARDS.values()
            for bands in genders.values()
            for events in bands.values()
        )
        # 304 (course, age band, event) rows in the PDF, each split into a girls and a
        # boys entry -> 608 leaf entries -> 608 x 6 tiers = 3,648 values.
        self.assertEqual(event_entries, 608)
        values = sum(
            len(tiers)
            for genders in MOTIVATIONAL_STANDARDS.values()
            for bands in genders.values()
            for events in bands.values()
            for tiers in events.values()
        )
        self.assertEqual(values, 3648)


if __name__ == "__main__":
    unittest.main()
