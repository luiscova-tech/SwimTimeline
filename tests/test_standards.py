import unittest

from swimtimeline.standards import lookup


class StandardsLookupTest(unittest.TestCase):
    def test_resolves_girls_lcm_11_12_band(self):
        result = lookup("Girls 11-12 50 LC Meter Breaststroke", "39.50", state="AZ", age="12")

        self.assertEqual(result.event_key, "50 breast")
        self.assertEqual(result.usa_summary, "USA-S 11-12 Girls LCM: AA; next AAA 39.09")
        self.assertIn("AZSI 11-12 Girls LCM", result.lsc_summary)
        self.assertEqual(result.confidence_summary, "Standards confidence: USA-S verified, AZSI verified")

    def test_age_11_and_12_share_the_11_12_band(self):
        # Single-age tables are gone: 11 and 12 now both resolve to the 11-12 age group.
        eleven = lookup("Girls 11-12 50 LC Meter Breaststroke", "39.50", state="AZ", age="11")
        twelve = lookup("Girls 11-12 50 LC Meter Breaststroke", "39.50", state="AZ", age="12")

        self.assertIn("USA-S 11-12 Girls LCM", eleven.usa_summary)
        self.assertEqual(eleven.usa_summary, twelve.usa_summary)

    def test_resolves_a_boys_event(self):
        result = lookup("Boys 13-14 100 LC Meter Freestyle", "1:02.00", state="AZ", age="14")

        self.assertEqual(result.usa_summary, "USA-S 13-14 Boys LCM: A; next AA 1:01.69")

    def test_resolves_a_short_course_yards_event(self):
        result = lookup("Boys 15-16 100 Yard Freestyle", "47.00", state="AZ", age="16")

        self.assertEqual(result.usa_summary, "USA-S 15-16 Boys SCY: AAA; next AAAA 46.49")

    def test_configures_age_13_that_the_old_narrow_table_could_not(self):
        result = lookup("Girls 13-14 100 LC Meter Freestyle", "1:02.00", state="AZ", age="13")

        self.assertIn("USA-S 13-14 Girls LCM", result.usa_summary)

    def test_refuses_senior_ages_outside_the_motivational_bands(self):
        result = lookup("Women 100 LC Meter Freestyle", "1:00.00", state="AZ", age="19")

        self.assertEqual(result.usa_summary, "USA-S: not configured for this swimmer age")
        self.assertEqual(result.confidence_summary, "Standards confidence: USA-S not configured, LSC not configured")


class AzsiSeniorBoundaryTest(unittest.TestCase):
    """The 14/15 boundary between AZSI Age Group and AZSI Senior, and the 18/19 upper edge.

    Age Group bands cover 14 and under; Senior covers 15-18. Neither overlaps the other, and 19+
    falls off both (matching the motivational bands, which also stop at 17-18).
    """

    def test_age_14_still_resolves_to_age_group_unchanged(self):
        result = lookup("Girls 13-14 100 LC Meter Freestyle", "1:02.00", state="AZ", age="14")
        self.assertIn("AZSI 13-14 Girls LCM", result.lsc_summary)
        # A 14-year-old is classified into the Age Group 13-14 band, never the Senior catalog. (The
        # summary may still mention Senior in the trailing "also meets the next band" bonus clause --
        # that is the 13-14 -> Senior lookahead, not this swimmer's own classification.)
        self.assertNotIn("AZSI Senior", result.lsc_summary)
        self.assertIn("AZSI verified", result.confidence_summary)

    def test_age_15_resolves_to_senior(self):
        result = lookup("Girls 15-16 100 LC Meter Freestyle", "1:02.00", state="AZ", age="15")
        # State cut for Girls Senior LCM 100 free is 1:03.69; a 1:02.00 seed has met it. Regional
        # is suppressed once State is met (see AzsiRegionalSuppressionTest below).
        self.assertEqual(
            result.lsc_summary,
            "AZSI Senior Girls LCM: State met; State 1:03.69",
        )
        self.assertIn("AZSI verified", result.confidence_summary)

    def test_age_18_still_resolves_to_senior(self):
        result = lookup("Boys 17-18 50 Yard Freestyle", "24.00", state="AZ", age="18")
        self.assertIn("AZSI Senior Boys SCY", result.lsc_summary)


class AzsiRegionalSuppressionTest(unittest.TestCase):
    """Arizona's own eligibility rule: meeting the State cut in an event removes Regional
    eligibility for that event entirely (the swimmer swims up to State). Showing a Regional value
    once State is met would misstate an eligibility the swimmer no longer has, so lookup() drops
    the Regional value/target from lsc_summary in that case -- not just a display preference.
    When State is NOT met, Regional continues to show exactly as before (met or target).
    Covers both the Age Group and Senior AZSI catalogs, which share this summary logic.
    """

    def test_state_met_suppresses_regional_entirely_age_group(self):
        # Girls 11-12 LCM 50 Breast: State cut 42.79, Regional 51.99. A 39.50 seed meets State.
        result = lookup("Girls 11-12 50 LC Meter Breaststroke", "39.50", state="AZ", age="12")
        self.assertEqual(result.lsc_summary, "AZSI 11-12 Girls LCM: State met; State 42.79")
        self.assertNotIn("Regional", result.lsc_summary)

    def test_state_met_suppresses_regional_entirely_senior(self):
        # Girls Senior LCM 100 Free: State cut 1:03.69, Regional 1:19.19. A 1:02.00 seed meets State.
        result = lookup("Girls 15-16 100 LC Meter Freestyle", "1:02.00", state="AZ", age="15")
        self.assertEqual(result.lsc_summary, "AZSI Senior Girls LCM: State met; State 1:03.69")
        self.assertNotIn("Regional", result.lsc_summary)

    def test_regional_met_but_not_state_still_shows_regional(self):
        # Same event/cuts as above: a 45.00 seed misses State (42.79) but meets Regional (51.99).
        result = lookup("Girls 11-12 50 LC Meter Breaststroke", "45.00", state="AZ", age="12")
        self.assertEqual(
            result.lsc_summary,
            "AZSI 11-12 Girls LCM: Regional met; State target 42.79, Regional 51.99",
        )

    def test_neither_met_still_shows_both_as_targets(self):
        # A 60.00 seed misses both cuts -- both remain visible as targets.
        result = lookup("Girls 11-12 50 LC Meter Breaststroke", "60.00", state="AZ", age="12")
        self.assertEqual(
            result.lsc_summary,
            "AZSI 11-12 Girls LCM: target State 42.79, Regional 51.99",
        )

    def test_all_twelve_wzag_combinations_show_state_met_with_no_regional(self):
        # Regression pin for the 2026 WZAG Boise psych-sheet verification session: every one of
        # Cova's and Stein's 6 events showed "State met" under the old format (which still listed
        # Regional despite the swimmer no longer being Regional-eligible). All 12 must now show
        # zero Regional line.
        combos = [
            ("Girls 11-12 50 LC Meter Breaststroke", "39.82", "12"),
            ("Girls 11-12 100 LC Meter Freestyle", "1:03.41", "12"),
            ("Girls 11-12 200 LC Meter Freestyle", "2:20.36", "12"),
            ("Girls 11-12 100 LC Meter Breaststroke", "1:28.02", "12"),
            ("Girls 11-12 400 LC Meter Freestyle", "4:54.19", "12"),
            ("Girls 11-12 50 LC Meter Freestyle", "28.62", "12"),
            ("Girls 13-14 800 LC Meter Freestyle", "9:45.52", "13"),
            ("Girls 13-14 200 LC Meter Freestyle", "2:10.67", "13"),
            ("Girls 13-14 100 LC Meter Butterfly", "1:10.99", "13"),
            ("Girls 13-14 50 LC Meter Butterfly", "30.17", "13"),
            ("Girls 13-14 400 LC Meter Freestyle", "4:40.52", "13"),
            ("Girls 13-14 50 LC Meter Freestyle", "28.27", "13"),
        ]
        for event_name, seed_time, age in combos:
            result = lookup(event_name, seed_time, state="AZ", age=age)
            where = f"{event_name} seed={seed_time} age={age}"
            self.assertIn("State met", result.lsc_summary, where)
            self.assertNotIn("Regional", result.lsc_summary, where)

    def test_age_19_resolves_to_neither_layer(self):
        result = lookup("Girls 15-16 100 LC Meter Freestyle", "1:02.00", state="AZ", age="19")
        self.assertEqual(result.lsc_summary, "LSC: standards not configured for this state/event")

    def test_senior_is_arizona_only(self):
        result = lookup("Girls 15-16 100 LC Meter Freestyle", "1:02.00", state="CA", age="16")
        self.assertEqual(result.lsc_summary, "LSC: standards not configured for this state/event")


class SectionalLookupTest(unittest.TestCase):
    """Speedo Sectional targets: AZ swimmers, AGE-OPEN (both meet flyers gate eligibility purely
    on qualifying time, no age floor/ceiling), both meets named individually and never merged into
    a generic "Sectional". Reported on the dedicated sectional_summary line, separate from the AZSI
    lsc_summary and absent from the confidence string.
    """

    def test_names_both_meets_individually_when_both_offer_the_event(self):
        result = lookup("Girls 15-16 50 Yard Freestyle", "24.50", state="AZ", age="16")
        self.assertIsNotNone(result.sectional_summary)
        self.assertIn("Four Corners Spring Speedo Sectional: met 24.99", result.sectional_summary)
        self.assertIn("Western Region Summer Speedo Sectional: met 24.99", result.sectional_summary)

    def test_target_when_seed_is_slower_than_the_cut(self):
        result = lookup("Girls 15-16 50 Yard Freestyle", "26.00", state="AZ", age="16")
        self.assertIn("Four Corners Spring Speedo Sectional: target 24.99", result.sectional_summary)
        self.assertIn("Western Region Summer Speedo Sectional: target 24.99", result.sectional_summary)

    def test_fifty_of_stroke_shows_summer_meet_only(self):
        # Four Corners omits the 50s of stroke, so only the Summer meet should appear.
        result = lookup("Boys 17-18 50 Yard Backstroke", "25.00", state="AZ", age="17")
        self.assertIn("Western Region Summer Speedo Sectional", result.sectional_summary)
        self.assertNotIn("Four Corners", result.sectional_summary)

    def test_age_open_below_the_senior_range_still_gets_sectional(self):
        # Age-open: a 14-year-old AZ swimmer sees the Sectional target too (not just 15-18),
        # while the AZSI Age Group LSC line stays on its own 13-14 band.
        result = lookup("Girls 13-14 50 Yard Freestyle", "24.50", state="AZ", age="14")
        self.assertIsNotNone(result.sectional_summary)
        self.assertIn("Four Corners Spring Speedo Sectional: met 24.99", result.sectional_summary)
        self.assertIn("AZSI 13-14 Girls SCY", result.lsc_summary)

    def test_age_open_above_the_motivational_range_still_gets_sectional(self):
        # A 19-year-old is past every USA-S/AZSI band, but Sectionals are age-open, so an AZ
        # swimmer who met the time still sees the target -- the only benchmark that applies.
        result = lookup("Girls 15-16 50 Yard Freestyle", "24.50", state="AZ", age="19")
        self.assertIsNotNone(result.sectional_summary)
        self.assertIn("Western Region Summer Speedo Sectional: met 24.99", result.sectional_summary)
        self.assertEqual(result.lsc_summary, "LSC: standards not configured for this state/event")

    def test_sectional_shows_even_when_age_is_unknown(self):
        # Eligibility does not depend on age, so an unknown age does not suppress the target.
        result = lookup("Girls 50 Yard Freestyle", "24.50", state="AZ", age=None)
        self.assertIsNotNone(result.sectional_summary)

    def test_sectional_is_arizona_only(self):
        result = lookup("Girls 15-16 50 Yard Freestyle", "24.50", state="CA", age="16")
        self.assertIsNone(result.sectional_summary)

    def test_sectional_does_not_alter_the_confidence_line(self):
        # Confidence tracks USA-S and LSC only; the Sectional line is reported separately.
        result = lookup("Girls 15-16 50 Yard Freestyle", "24.50", state="AZ", age="16")
        self.assertNotIn("ectional", result.confidence_summary)

    def test_reports_when_course_cannot_be_read_from_the_event_name(self):
        result = lookup("Girls 11-12 50 Breaststroke", "39.50", state="AZ", age="12")

        self.assertEqual(result.usa_summary, "USA-S: could not determine course from the event name")

    def test_reports_when_gender_cannot_be_read_from_the_event_name(self):
        result = lookup("11-12 50 LC Meter Breaststroke", "39.50", state="AZ", age="12")

        self.assertEqual(result.usa_summary, "USA-S: could not determine gender from the event name")


class BeyondAAAAAdvancedSummaryTest(unittest.TestCase):
    """Once USA-S hits AAAA, advanced_summary shows only the single next unmet elite standard --
    the same "just the next rung" pattern used everywhere else (e.g. "AA; next AAA 39.09"), no
    trailing "then ..." list. The rung is sourced from the live AZ Sectional + national datasets
    (data/sectional_standards.json + data/national_standards.json), NOT the legacy generic
    data/advanced_standards.json -- so for an AZ swimmer the next rung after AAAA is the real
    Four Corners / Western Region Summer Sectional cut, not the stale national max reference.
    """

    SECTIONAL = "Four Corners Spring Speedo Sectional / Western Region Summer Speedo Sectional"

    def test_shows_only_the_next_rung_with_no_trailing_then_list(self):
        # Girls LCM 100 Free at AAAA: the real AZ Sectional cut is 1:01.26 (both meets), not the
        # old advanced_standards.json "Speedo Sectionals" 1:00.69 national reference.
        result = lookup("Girls 11-12 100 LC Meter Freestyle", "1:03.41", state="AZ", age="12")

        self.assertEqual(result.usa_summary, "USA-S 11-12 Girls LCM: AAAA")
        self.assertEqual(result.advanced_summary, f"Beyond AAAA: next {self.SECTIONAL} 1:01.26")
        self.assertNotIn("then", result.advanced_summary)
        self.assertNotIn("28.09", result.advanced_summary)

    def test_all_three_wzag_cova_aaaa_events_route_through_real_az_sectional(self):
        # Regression pin for the 2026 WZAG Boise verification session: Cova's three AAAA events
        # each show one "Beyond AAAA: next ..." line whose value is the real AZ Four Corners /
        # Western Region Summer Sectional cut (identical across the two meets for 2026).
        combos = [
            ("Girls 11-12 100 LC Meter Freestyle", "1:03.41", "1:01.26"),
            ("Girls 11-12 400 LC Meter Freestyle", "4:54.19", "4:43.21"),
            ("Girls 11-12 50 LC Meter Freestyle", "28.62", "28.44"),
        ]
        for event_name, seed_time, sectional_time in combos:
            result = lookup(event_name, seed_time, state="AZ", age="12")
            where = f"{event_name} seed={seed_time}"
            self.assertEqual(result.usa_summary, "USA-S 11-12 Girls LCM: AAAA", where)
            self.assertEqual(result.advanced_summary, f"Beyond AAAA: next {self.SECTIONAL} {sectional_time}", where)
            self.assertNotIn("then", result.advanced_summary, where)

    def test_next_rung_advances_to_national_once_the_sectional_cut_is_beaten(self):
        # A 28.20 seed is faster than the 28.44 AZ Sectional cut, so the next unmet rung is the
        # next-hardest elite standard -- the national TYR Futures 27.39 -- not the Sectional.
        result = lookup("Girls 11-12 50 LC Meter Freestyle", "28.20", state="AZ", age="12")
        self.assertEqual(result.advanced_summary, "Beyond AAAA: next TYR Futures Championships 27.39")

    def test_beyond_aaaa_string_drives_the_advanced_verified_confidence_bucket(self):
        # lookup() classifies the confidence tag off the "Beyond AAAA" prefix; pin that coupling so
        # a future reword of the summary can't silently drop it to "advanced partial".
        result = lookup("Girls 11-12 50 LC Meter Freestyle", "28.62", state="AZ", age="12")
        self.assertIn("advanced verified", result.confidence_summary)

    def test_two_national_meets_sharing_a_cut_are_named_together(self):
        # A 26.30 seed sits between rungs where Toyota Nationals and U.S. Open both list 26.19;
        # the joined-name logic must name both (and works for national ties, not just sectionals).
        result = lookup("Girls 11-12 50 LC Meter Freestyle", "26.30", state="AZ", age="12")
        self.assertEqual(
            result.advanced_summary,
            "Beyond AAAA: next Toyota National Championships / Toyota U.S. Open Championships 26.19",
        )

    def test_beating_every_rung_reports_met_all_and_drops_to_advanced_partial(self):
        # Faster than the hardest configured cut: distinct "met all" message, and the confidence
        # bucket falls to "advanced partial" (it no longer starts with "Beyond AAAA").
        result = lookup("Girls 11-12 50 LC Meter Freestyle", "20.00", state="AZ", age="12")
        self.assertEqual(result.advanced_summary, "Advanced standards loaded; swimmer has met all configured targets")
        self.assertIn("advanced partial", result.confidence_summary)

    def test_non_az_swimmer_gets_national_rungs_only_no_az_sectional(self):
        # The AZ Sectional rung is Arizona-only; a non-AZ swimmer's ladder is national-only, so
        # the next rung is TYR Futures, never Four Corners / Western Region.
        result = lookup("Girls 11-12 50 LC Meter Freestyle", "28.62", state="CA", age="12")
        self.assertEqual(result.advanced_summary, "Beyond AAAA: next TYR Futures Championships 27.39")
        self.assertNotIn("Four Corners", result.advanced_summary)
        self.assertNotIn("Western Region", result.advanced_summary)


if __name__ == "__main__":
    unittest.main()
