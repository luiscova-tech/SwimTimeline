from pathlib import Path
import unittest

from swimtimeline.extract import analyze_uploads, extract_text_pages, parse_meet_timing_rules


ROOT = Path(__file__).resolve().parents[1]


class MeetTimingRulesTest(unittest.TestCase):
    def test_age_group_state_flyer_marks_400_events_as_timed_final_top_seeded_heat(self):
        flyer_text = "\n".join(extract_text_pages(ROOT / "meets/2026-az-lc-age-group-state/input/age-group-state-meet-flyer.pdf"))

        rules = parse_meet_timing_rules(flyer_text)

        for event_number in (35, 38, 69, 72):
            self.assertIn(event_number, rules)
            self.assertEqual(rules[event_number].kind, "timed_final_fastest_heat_finals")
            self.assertEqual(rules[event_number].top_seed_count, 8)

    def test_wzag_flyer_marks_footnoted_distance_events_as_timed_final_top_seeded_heat(self):
        flyer_text = "\n".join(
            extract_text_pages(ROOT / "meets/2026-wzag-championships-boise/input/Sanctioned_2026 WZAG Championships - Boise (v5.pdf")
        )

        rules = parse_meet_timing_rules(flyer_text)

        for event_number in (1, 2, 21, 22, 70, 71, 95, 96):
            self.assertIn(event_number, rules)
            self.assertEqual(rules[event_number].kind, "timed_final_fastest_heat_finals")

    def test_age_group_state_400_free_uses_timed_final_wording_not_prelim_final(self):
        result = analyze_uploads(
            flyer_pdf=ROOT / "meets/2026-az-lc-age-group-state/input/age-group-state-meet-flyer.pdf",
            psych_pdf=ROOT / "meets/2026-az-lc-age-group-state/input/age-group-state-psych-sheet.pdf",
            timeline_pdf=ROOT / "meets/2026-az-lc-age-group-state/input/age-group-state-timeline.pdf",
            swimmer_name="Mila Cova",
            output_dir=Path("/private/tmp/swimtimeline-meet-rules-test"),
            state="AZ",
            modes=["daily", "detailed"],
        )

        event_69 = next(item for item in result["items"] if item["event_number"] == 69)

        self.assertEqual(event_69["event_format"], "Timed final")
        self.assertIn("fastest seeded heat", event_69["finals_note"])
        self.assertNotIn("Possible if qualifies", event_69["finals_note"])


if __name__ == "__main__":
    unittest.main()
