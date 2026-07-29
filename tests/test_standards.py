import unittest

from swimtimeline.standards import lookup


class StandardsLookupTest(unittest.TestCase):
    def test_uses_11_girls_lcm_table_when_age_is_11(self):
        result = lookup("Girls 11-12 50 Breast", "39.50", state="AZ", age="11")

        self.assertEqual(result.event_key, "50 breast")
        self.assertIn("USA-S 11 Girls LCM", result.usa_summary)
        self.assertIn("AAA", result.usa_summary)
        self.assertIn("next AAAA 39.29", result.usa_summary)
        self.assertIn("AZSI 11-12 Girls LCM", result.lsc_summary)
        self.assertIn("Standards confidence: USA-S verified, AZSI verified", result.confidence_summary)

    def test_uses_12_girls_lcm_table_when_age_is_12(self):
        result = lookup("Girls 11-12 50 Breast", "39.50", state="AZ", age="12")

        self.assertIn("USA-S 12 Girls LCM", result.usa_summary)
        self.assertIn("AA", result.usa_summary)
        self.assertIn("next AAA 39.09", result.usa_summary)

    def test_refuses_unconfigured_ages(self):
        result = lookup("Girls 13-14 100 Free", "1:02.00", state="AZ", age="13")

        self.assertEqual(result.usa_summary, "USA-S: not configured for this swimmer age")
        self.assertEqual(result.confidence_summary, "Standards confidence: not configured")


if __name__ == "__main__":
    unittest.main()
