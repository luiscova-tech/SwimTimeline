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

    def test_reports_when_course_cannot_be_read_from_the_event_name(self):
        result = lookup("Girls 11-12 50 Breaststroke", "39.50", state="AZ", age="12")

        self.assertEqual(result.usa_summary, "USA-S: could not determine course from the event name")

    def test_reports_when_gender_cannot_be_read_from_the_event_name(self):
        result = lookup("11-12 50 LC Meter Breaststroke", "39.50", state="AZ", age="12")

        self.assertEqual(result.usa_summary, "USA-S: could not determine gender from the event name")


if __name__ == "__main__":
    unittest.main()
