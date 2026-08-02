"""AZSI "next age band" bonus check.

Once a swimmer has met their own age band's State cut for an event, the AZSI line also notes when
they already clear the NEXT band's (faster) State cut -- e.g. an 11-12 girl under the 13-14 cut. It
is appended to the existing "State met" line, never a restructuring, and only ever fires from the
State-met branch so it stays silent for a swimmer still chasing their own cut.

The three transitions are not a uniform "+1 band" step. Age Group (10 & under -> 11-12 -> 13-14)
walks its own three-band table, but 13-14 -> Senior crosses into the separately-shaped, band-less
Senior catalog, and Senior itself has no band above it. Each is exercised below against a real
swimmer/event; boundary times are pinned to the 2025-2026 AZSI cuts.
"""

import unittest

from swimtimeline.standards import azsi_next_band_standard, lookup


class NextBandStandardLookupTest(unittest.TestCase):
    """The dataset-shape handling: Age Group steps within its table; 13-14 reads the flat Senior
    catalog; Senior and any out-of-range label have no next band."""

    def test_age_group_bands_step_within_the_age_group_table(self):
        label, cell = azsi_next_band_standard("LCM", "girls", "10 & under", "50 free")
        self.assertEqual(label, "11-12")
        self.assertEqual(cell["state"], "31.99")

        label, cell = azsi_next_band_standard("LCM", "girls", "11-12", "50 free")
        self.assertEqual(label, "13-14")
        self.assertEqual(cell["state"], "30.19")

    def test_13_14_crosses_into_the_flat_senior_catalog(self):
        # The structurally distinct transition: the next cut is pulled from the band-less Senior
        # dataset (29.59), not any row of the Age Group table.
        label, cell = azsi_next_band_standard("LCM", "girls", "13-14", "50 free")
        self.assertEqual(label, "Senior")
        self.assertEqual(cell["state"], "29.59")

    def test_senior_and_unmapped_labels_have_no_next_band(self):
        self.assertEqual(azsi_next_band_standard("LCM", "girls", "Senior", "50 free"), (None, None))
        self.assertEqual(azsi_next_band_standard("LCM", "girls", "15-16", "50 free"), (None, None))
        self.assertEqual(azsi_next_band_standard("LCM", "girls", None, "50 free"), (None, None))


class NextBandBonusFiresPerTransitionTest(unittest.TestCase):
    """Current-age State met AND next-band State met -> a correctly labeled bonus, for all three
    transition types, each tied to a real AZ swimmer's real seed time."""

    def test_ten_and_under_to_11_12(self):
        # Nguyen, Felix S (age 10), 2026 AZ LC Age Group State: 50 fly 35.82 <= 10&U 44.29, and also
        # <= 11-12 36.39.
        result = lookup("Boys 10 & Under 50 LC Meter Butterfly", "35.82", state="AZ", age="10")
        self.assertEqual(
            result.lsc_summary,
            "AZSI 10 & under Boys LCM: State met; State 44.29; also meets 11-12 State standard (36.39)",
        )

    def test_11_12_to_13_14(self):
        # Cova, Mila (age 12): 50 LCM free 28.62 <= 11-12 31.99, and also <= 13-14 30.19.
        result = lookup("Girls 11-12 50 LC Meter Freestyle", "28.62", state="AZ", age="12")
        self.assertEqual(
            result.lsc_summary,
            "AZSI 11-12 Girls LCM: State met; State 31.99; also meets 13-14 State standard (30.19)",
        )

    def test_13_14_to_senior(self):
        # Stein, Layla (age 13): 50 LCM free 28.27 <= 13-14 30.19, and also <= Senior 29.59. This is
        # the cross-dataset case -- the Senior cut has no age band.
        result = lookup("Girls 13-14 50 LC Meter Freestyle", "28.27", state="AZ", age="13")
        self.assertEqual(
            result.lsc_summary,
            "AZSI 13-14 Girls LCM: State met; State 30.19; also meets Senior State standard (29.59)",
        )


class NextBandBonusGatingTest(unittest.TestCase):
    """The bonus stays silent unless it is both relevant and true."""

    def test_state_not_met_never_checks_the_next_band(self):
        # 33.00 beats only the 11-12 Regional cut (38.19), not State (31.99): the Regional branch
        # must not consult the next band at all.
        result = lookup("Girls 11-12 50 LC Meter Freestyle", "33.00", state="AZ", age="12")
        self.assertIn("Regional met", result.lsc_summary)
        self.assertNotIn("also meets", result.lsc_summary)

    def test_state_met_but_next_band_not_met_has_no_bonus_line(self):
        # 31.00 <= 11-12 State 31.99 (own met) but > 13-14 State 30.19 (next not met).
        result = lookup("Girls 11-12 50 LC Meter Freestyle", "31.00", state="AZ", age="12")
        self.assertEqual(result.lsc_summary, "AZSI 11-12 Girls LCM: State met; State 31.99")
        self.assertNotIn("also meets", result.lsc_summary)

    def test_senior_swimmer_has_no_next_band(self):
        # Senior (15-18) is the top of the in-app AZSI ladder: a fast Senior seed still gets no bonus.
        result = lookup("Girls 15-16 50 LC Meter Freestyle", "26.00", state="AZ", age="16")
        self.assertIn("AZSI Senior", result.lsc_summary)
        self.assertNotIn("also meets", result.lsc_summary)

    def test_non_az_state_never_gets_a_bonus(self):
        # The whole AZSI layer (and thus the bonus) is AZ-only.
        result = lookup("Girls 11-12 50 LC Meter Freestyle", "28.62", state="FL", age="12")
        self.assertNotIn("also meets", result.lsc_summary)


if __name__ == "__main__":
    unittest.main()
