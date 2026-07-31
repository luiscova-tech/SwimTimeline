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

from swimtimeline.standards import AZSI_11_12_GIRLS_LCM, MOTIVATIONAL_STANDARDS, TIER_ORDER, parse_time


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


class AzsiStandardsValueTest(unittest.TestCase):
    """Spot-checks for the Arizona LSC table against the official 2025-2026 AZSI Age Group
    State and Regional Qualifying Time Standards (docs/Sources/azsi-*-2025-2026.pdf),
    WOMEN 11-12 Long Course Meters.

    The table had regressed to a gender-mixed state: seven events (50/100/200/800 free,
    50 back, 50 breast, 50 fly) carried the MEN 11-12 cuts, plus a few stray typos. These
    checks pin the corrected WOMEN values and guard the specific wrong ones that were live.
    """

    def test_100_free_is_women_not_men(self):
        # Was 1:11.99 (the MEN 11-12 state cut); the WOMEN cut is 1:09.89.
        self.assertEqual(AZSI_11_12_GIRLS_LCM["100 free"]["state"], "1:09.89")
        self.assertEqual(AZSI_11_12_GIRLS_LCM["100 free"]["regional"], "1:25.49")

    def test_50_free_is_women_not_men(self):
        self.assertEqual(AZSI_11_12_GIRLS_LCM["50 free"]["state"], "31.99")
        self.assertEqual(AZSI_11_12_GIRLS_LCM["50 free"]["regional"], "38.19")

    def test_800_free_state_and_regional(self):
        self.assertEqual(AZSI_11_12_GIRLS_LCM["800 free"]["state"], "11:34.19")
        self.assertEqual(AZSI_11_12_GIRLS_LCM["800 free"]["regional"], "13:29.59")

    def test_400_im_state_typo_fixed(self):
        # State was 6:04.49 (matched no published row); correct value is 6:27.69.
        self.assertEqual(AZSI_11_12_GIRLS_LCM["400 im"]["state"], "6:27.69")

    def test_200_im_regional_typo_fixed(self):
        # State (2:56.29) was already correct; regional was 3:31.59, should be 3:33.19.
        self.assertEqual(AZSI_11_12_GIRLS_LCM["200 im"]["state"], "2:56.29")
        self.assertEqual(AZSI_11_12_GIRLS_LCM["200 im"]["regional"], "3:33.19")

    def test_events_that_were_already_correct_stay_correct(self):
        self.assertEqual(AZSI_11_12_GIRLS_LCM["100 back"]["state"], "1:21.09")
        self.assertEqual(AZSI_11_12_GIRLS_LCM["100 breast"]["regional"], "1:54.59")
        self.assertEqual(AZSI_11_12_GIRLS_LCM["400 free"]["state"], "5:25.79")

    def test_state_cut_is_faster_than_regional_for_every_event(self):
        # Arizona rule: meeting the State cut removes Regional eligibility (swim up to State),
        # which only holds if State is the faster time everywhere.
        for event, cuts in AZSI_11_12_GIRLS_LCM.items():
            state, regional = parse_time(cuts["state"]), parse_time(cuts["regional"])
            self.assertIsNotNone(state, event)
            self.assertIsNotNone(regional, event)
            self.assertLess(state, regional, f"{event}: state {cuts['state']} not faster than regional {cuts['regional']}")


if __name__ == "__main__":
    unittest.main()
