"""The swims_with_finals / footnote precedence question, closed out.

PsychEntry.swims_with_finals (a heat sheet's own "Swimming with Finals" label on a specific heat)
is informational only -- the flyer's footnote-based EventTimingRule is what actually decides which
timeline window a swim gets (see build_swim_events -> timeline_for_timing_rule, which never reads
swims_with_finals at all). This is now confirmed against THREE independent real WZAG sessions,
spanning two footnote letters:

  - Wednesday: Layla Stein's 800 Free (event 21, footnote B). Her heat-sheet row is flagged
    swims_with_finals, and her calendar lands in the 6:37 PM finals window -- not the 11:52 AM
    prelims-session window the timeline's own primary session for that event would otherwise give.
  - Friday: Mila Cova's 400 Free (event 70, footnote A). Same shape: heat 1, "Swimming with
    Finals", calendar lands at 6:19 PM, not the 10:37 AM prelims window.
  - Friday: event 71, the paired boys 400 Free (footnote A) -- same footnote, same heat-1 flag.

This file also closes the one real gap the precedence could have: an event where a heat sheet says
swims_with_finals=True but the flyer's footnotes never mention that event number at all, so
build_swim_events has no EventTimingRule to use and silently falls back to the primary (prelims-
session) timeline. Searched every real PDF fixture in this repo, not just WZAG's, for "Swimming
with Finals": it appears in exactly two documents, the Wednesday and Friday WZAG heat sheets,
covering event numbers {1, 2, 21, 22, 70, 71}. Every one of those six has a footnote rule. The
combination this file guards against -- swims_with_finals with NO rule -- does not occur anywhere
in this repo's real fixtures today.

That absence is worth taking seriously rather than treating as "case closed": for all six real
occurrences, the footnote-chosen finals window differs from the naive primary-timeline fallback by
several HOURS (morning vs. evening), so if this combination is ever encountered for real, trusting
the current fallback without re-examining it would likely place that swim at the wrong time. Rather
than invent an untested code change for a case with zero real evidence to validate it against (this
project's standing practice throughout), SwimsWithFinalsGapAuditTest below is a permanent canary:
it fails loudly the day a real meet's heat sheet first exhibits this combination, forcing a
conscious decision with real data in hand instead of letting it slip through silently.
"""

from pathlib import Path
import tempfile
import unittest

from swimtimeline.extract import (
    analyze_uploads,
    extract_text_pages,
    parse_meet_timing_rules,
    swims_with_finals_event_numbers,
)

ROOT = Path(__file__).resolve().parents[1]
WZAG = ROOT / "meets/2026-wzag-championships-boise/input"
FLYER = WZAG / "Sanctioned_2026 WZAG Championships - Boise (v5.pdf"
PSYCH = WZAG / "wzag psych sheet v3.pdf"
TIMELINE = WZAG / "wzag timelines v4.pdf"
WEDNESDAY_HEATS = WZAG / "wzag wednesday prelim program.pdf"
THURSDAY_HEATS = WZAG / "wzag thursday prelim program v2.pdf"
FRIDAY_HEATS = WZAG / "wzag friday prelim program.pdf"
HEAT_SHEETS = [WEDNESDAY_HEATS, THURSDAY_HEATS, FRIDAY_HEATS]


def analyze(swimmer_name: str) -> dict:
    return analyze_uploads(
        flyer_pdf=FLYER,
        psych_pdf=PSYCH,
        timeline_pdf=TIMELINE,
        swimmer_name=swimmer_name,
        output_dir=Path(tempfile.mkdtemp()),
        state="",
        meet_timezone="America/Boise",
        meet_venue="Idaho Central Aquatic Center, Boise, ID",
        modes=["daily"],
        heat_sheet_pdfs=HEAT_SHEETS,
    )


class FootnotePrecedenceOverSwimsWithFinalsTest(unittest.TestCase):
    """swims_with_finals is informational; the footnote decides. Pinned as permanent, intended
    behavior across two independent real sessions and footnote letters, not something that just
    happens to pass today."""

    def test_wednesday_800_free_lands_in_the_finals_window(self):
        payload = analyze("Stein, Layla")
        event = next(i for i in payload["items"] if i["event_number"] == 21)
        self.assertEqual(event["day"], "Wednesday")
        self.assertEqual(event["window"], "6:37 PM-6:48 PM")
        self.assertEqual(event["event_format"], "Timed final")
        self.assertIn("footnote", event["finals_note"].lower())
        # Not the naive fallback a bare primary/prelims-session timeline would have produced.
        self.assertNotIn("11:52 AM", event["window"])

    def test_friday_event_70_lands_in_the_finals_window(self):
        payload = analyze("Cova, Mila L")
        event = next(i for i in payload["items"] if i["event_number"] == 70)
        self.assertEqual(event["day"], "Friday")
        self.assertEqual(event["window"], "6:19 PM-6:25 PM")
        self.assertEqual(event["event_format"], "Timed final")
        self.assertIn("footnote", event["finals_note"].lower())
        self.assertNotIn("10:37 AM", event["window"])

    def test_events_70_and_71_are_both_footnoted_and_swims_with_finals_flagged(self):
        # The paired girls/boys 400 Free: same footnote (A), same heat-1 "Swimming with Finals"
        # heat-sheet label. 71 has no name-matched swimmer test above, so pinned structurally here.
        flyer_text = "\n".join(extract_text_pages(FLYER))
        rules = parse_meet_timing_rules(flyer_text)
        flagged = swims_with_finals_event_numbers(extract_text_pages(FRIDAY_HEATS))
        for number in (70, 71):
            self.assertIn(number, flagged, f"event #{number} heat sheet no longer flags swims_with_finals")
            self.assertIn(number, rules, f"event #{number} lost its flyer footnote rule")
            self.assertEqual(rules[number].kind, "timed_final_fastest_heat_finals")


class SwimsWithFinalsGapAuditTest(unittest.TestCase):
    """The one real gap the precedence could have: a heat sheet flags swims_with_finals for an
    event the flyer's footnotes never mention at all. See the module docstring for why this is a
    canary rather than a fix -- there is no real fixture with this combination to validate a fix
    against, so build_swim_events's fallback path for it stays exactly as it is today (informational
    swims_with_finals, primary-timeline default), and this test exists to force a conscious
    decision the day that changes."""

    def test_every_swims_with_finals_event_in_every_real_wzag_heat_sheet_has_a_footnote_rule(self):
        flyer_text = "\n".join(extract_text_pages(FLYER))
        rules = parse_meet_timing_rules(flyer_text)
        for heat_sheet in HEAT_SHEETS:
            flagged = swims_with_finals_event_numbers(extract_text_pages(heat_sheet))
            for event_number in flagged:
                self.assertIn(
                    event_number,
                    rules,
                    f"{heat_sheet.name} flags event #{event_number} as swims_with_finals with no "
                    f"flyer footnote rule -- the precedence gap this file guards against now has a "
                    f"REAL instance. Do not assume the primary-timeline fallback is correct for it; "
                    f"see timeline_for_timing_rule and this file's module docstring before deciding.",
                )

    def test_the_gap_combination_is_confirmed_absent_today(self):
        # Documents the current, verified state explicitly rather than leaving it implicit in the
        # loop above: exactly six real swims_with_finals events exist in this repo, all footnoted.
        flyer_text = "\n".join(extract_text_pages(FLYER))
        rules = parse_meet_timing_rules(flyer_text)
        all_flagged: set[int] = set()
        for heat_sheet in HEAT_SHEETS:
            all_flagged |= swims_with_finals_event_numbers(extract_text_pages(heat_sheet))
        self.assertEqual(all_flagged, {1, 2, 21, 22, 70, 71})
        self.assertTrue(all_flagged.issubset(rules.keys()))
        self.assertEqual(swims_with_finals_event_numbers(extract_text_pages(THURSDAY_HEATS)), set())


if __name__ == "__main__":
    unittest.main()
