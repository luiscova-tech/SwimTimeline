"""Officials' badge cards: parsing additions, card assembly, and PDF output.

Both real Session Report fixtures in this repo are used throughout, because they differ in every
way that matters to this feature:

  * Herculean Invitational -- SCY, 6 sessions, two pools running in parallel. Its three 12&Over
    sessions have a CONSTANT age qualifier; its three 11&Under sessions are genuinely MIXED, and
    session 6 is the only place in either fixture where "N Year Olds" appears. Heat interval is
    the 2-part form.
  * WZAG Boise -- LCM, 8 sessions, prelims/finals. Every session is mixed. Prelim sessions carry
    the 3-part heat interval ("... / Chase -30"); finals carry a 2-part one. Its relays are the
    only "Mixed" gender token in either fixture, and its morning prelims run past noon.

No synthetic PDFs: every assertion below is pinned to values read out of those two documents.
"""

from io import BytesIO
from pathlib import Path
import unittest

from pypdf import PdfReader

from swimtimeline.badges import (
    CARD_H,
    CARD_W,
    MAROON,
    NAVY,
    abbreviate_age_qualifier,
    abbreviate_stroke,
    badge_event_name,
    badge_meet_name,
    build_session_cards,
    cards_for_timeline,
    compact_heat_interval,
    constant_age_qualifier,
    events_by_session,
    gender_color,
    parse_event_name,
    parse_heat_intervals,
    render_cards_pdf,
    row_time_label,
    session_crosses_noon,
)
from swimtimeline.extract import extract_text_pages, parse_timeline

ROOT = Path(__file__).resolve().parents[1]
HERC_DIR = ROOT / "meets/2026-herculean-invitational/input"
HERC_TIMELINE = HERC_DIR / "2026-herculean-invitational-timeline.pdf"
HERC_FLYER = HERC_DIR / "2026-herculean-invitational-flyer.pdf"
WZAG_DIR = ROOT / "meets/2026-wzag-championships-boise/input"
WZAG_TIMELINE = WZAG_DIR / "wzag timelines v4.pdf"
WZAG_FLYER = WZAG_DIR / "Sanctioned_2026 WZAG Championships - Boise (v5.pdf"


def flyer_text(path: Path) -> str:
    return "\n".join(extract_text_pages(path))


def parsed(timeline: Path, flyer: Path):
    return parse_timeline(timeline, flyer_text=flyer_text(flyer), meet_venue=None)


class HeatIntervalTest(unittest.TestCase):
    """The heat interval was captured by nothing before this feature -- extract.py's day_header
    regex stops at the start time, and the interval sits after it on the same line.
    """

    def test_herculean_two_part_interval_on_every_session(self):
        intervals = parse_heat_intervals(HERC_TIMELINE)
        self.assertEqual(sorted(intervals), [1, 2, 3, 4, 5, 6])
        for session_number, interval in intervals.items():
            self.assertEqual(interval, "25 Seconds / Back +15 Seconds", session_number)

    def test_wzag_three_part_prelims_and_two_part_finals(self):
        intervals = parse_heat_intervals(WZAG_TIMELINE)
        self.assertEqual(sorted(intervals), [1, 2, 3, 4, 5, 6, 7, 8])
        # Prelims (odd sessions) carry the third "Chase" part; finals (even) do not.
        for session_number in (1, 3, 5, 7):
            self.assertEqual(
                intervals[session_number],
                "20 Seconds / Back +10 Seconds / Chase -30",
                session_number,
            )
        self.assertEqual(intervals[2], "50 Seconds / Back +25 Seconds")
        self.assertEqual(intervals[4], "50 Seconds / Back +40 Seconds")
        self.assertEqual(intervals[6], "50 Seconds / Back +40 Seconds")
        self.assertEqual(intervals[8], "50 Seconds / Back +40 Seconds")

    def test_compaction_follows_the_sample_convention_for_both_shapes(self):
        self.assertEqual(compact_heat_interval("25 Seconds / Back +15 Seconds"), "25s/Back+15")
        self.assertEqual(
            compact_heat_interval("20 Seconds / Back +10 Seconds / Chase -30"),
            "20s/Back+10/Chase-30",
        )
        self.assertEqual(compact_heat_interval(None), "")
        self.assertEqual(compact_heat_interval(""), "")

    def test_extract_py_parsing_is_untouched_by_the_interval_scan(self):
        """The interval is read by a separate pass over the same pages, so the session/event data
        the family-facing calendar depends on is byte-identical with or without it."""
        for timeline, flyer in ((HERC_TIMELINE, HERC_FLYER), (WZAG_TIMELINE, WZAG_FLYER)):
            name, sessions, events = parsed(timeline, flyer)
            parse_heat_intervals(timeline)
            name_after, sessions_after, events_after = parsed(timeline, flyer)
            self.assertEqual(name, name_after)
            self.assertEqual(sessions, sessions_after)
            self.assertEqual(events, events_after)


class AgeQualifierTest(unittest.TestCase):
    def test_abbreviation_rule_is_general_not_a_lookup_table(self):
        self.assertEqual(abbreviate_age_qualifier("10 & Under"), "10&U")
        self.assertEqual(abbreviate_age_qualifier("11 & Under"), "11&U")
        self.assertEqual(abbreviate_age_qualifier("12 & Over"), "12&O")
        self.assertEqual(abbreviate_age_qualifier("14 & Under"), "14&U")
        self.assertEqual(abbreviate_age_qualifier("10-11"), "10-11")
        self.assertEqual(abbreviate_age_qualifier("13-14"), "13-14")
        self.assertEqual(abbreviate_age_qualifier("11 Year Olds"), "11yo")
        # An unseen number flows through the same rule with no new code.
        self.assertEqual(abbreviate_age_qualifier("8 & Under"), "8&U")
        self.assertEqual(abbreviate_age_qualifier("15 Year Olds"), "15yo")

    def test_herculean_constant_sessions_are_the_12_and_over_pool(self):
        _name, sessions, events = parsed(HERC_TIMELINE, HERC_FLYER)
        grouped = events_by_session(events)
        constants = {number: constant_age_qualifier(grouped[number]) for number in sorted(grouped)}
        # The 12&Over pool (sessions 1/3/5) really is constant; the 11&Under pool is not.
        self.assertEqual(constants[1], "12 & Over")
        self.assertEqual(constants[3], "12 & Over")
        self.assertEqual(constants[5], "12 & Over")
        self.assertIsNone(constants[2])
        self.assertIsNone(constants[4])
        self.assertIsNone(constants[6])
        self.assertEqual(sessions[1].name, "Friday PM 12&Over")

    def test_every_wzag_session_is_mixed(self):
        _name, _sessions, events = parsed(WZAG_TIMELINE, WZAG_FLYER)
        grouped = events_by_session(events)
        for number in sorted(grouped):
            self.assertIsNone(constant_age_qualifier(grouped[number]), number)

    def test_wzag_mixed_session_produces_the_actual_expected_tags(self):
        """Not just "it's mixed" -- these are the tags WZAG's Wednesday Prelims really renders."""
        _name, _sessions, events = parsed(WZAG_TIMELINE, WZAG_FLYER)
        session_one = events_by_session(events)[1]
        names = [badge_event_name(event.event_name, include_age=True) for event in session_one]
        self.assertIn("Girls 11-12 400 IM", names)
        self.assertIn("Boys 11-12 400 IM", names)
        self.assertIn("Girls 10&U 50 Breast", names)
        self.assertIn("Girls 13-14 200 Back", names)
        self.assertIn("Girls 13-14 800 Free", names)
        tags = {name.split()[1] for name in names}
        self.assertEqual(tags, {"11-12", "10&U", "13-14"})

    def test_herculean_year_olds_tag_appears_in_its_only_real_session(self):
        """"11 Year Olds" -> "11yo" occurs nowhere else in either fixture -- only Herculean S6."""
        _name, _sessions, events = parsed(HERC_TIMELINE, HERC_FLYER)
        session_six = events_by_session(events)[6]
        names = [badge_event_name(event.event_name, include_age=True) for event in session_six]
        self.assertIn("Girls 11yo 400 IM", names)
        tags = {name.split()[1] for name in names}
        self.assertEqual(tags, {"10&U", "10-11", "11&U", "11yo"})


class EventNameTest(unittest.TestCase):
    def test_stroke_abbreviation(self):
        self.assertEqual(abbreviate_stroke("100 Freestyle"), "100 Free")
        self.assertEqual(abbreviate_stroke("50 Backstroke"), "50 Back")
        self.assertEqual(abbreviate_stroke("200 Breaststroke"), "200 Breast")
        self.assertEqual(abbreviate_stroke("200 Butterfly"), "200 Fly")
        self.assertEqual(abbreviate_stroke("400 IM"), "400 IM")
        self.assertEqual(abbreviate_stroke("200 Freestyle Relay"), "200 Free Relay")

    def test_every_real_event_name_in_both_fixtures_parses(self):
        for timeline, flyer in ((HERC_TIMELINE, HERC_FLYER), (WZAG_TIMELINE, WZAG_FLYER)):
            _name, _sessions, events = parsed(timeline, flyer)
            unparsed = [e.event_name for e in events if parse_event_name(e.event_name) is None]
            self.assertEqual(unparsed, [], f"{timeline.name}: {unparsed[:5]}")

    def test_constant_session_drops_the_age_mixed_session_keeps_it(self):
        self.assertEqual(
            badge_event_name("Girls 12 & Over 100 Freestyle", include_age=False), "Girls 100 Free"
        )
        self.assertEqual(
            badge_event_name("Girls 12 & Over 100 Freestyle", include_age=True),
            "Girls 12&O 100 Free",
        )

    def test_gender_is_kept_unlike_event_short_name(self):
        """extract.py's event_short_name() strips gender words; gender_color() needs them, so this
        must be a separate function -- if it ever starts stripping gender, every row goes maroon."""
        from swimtimeline.extract import event_short_name

        raw = "Boys 12 & Over 50 Backstroke"
        self.assertNotIn("Boys", event_short_name(raw))
        self.assertTrue(badge_event_name(raw, include_age=False).startswith("Boys"))

    def test_gender_color_prefix_contract_holds_on_every_real_event(self):
        """gender_color() keys off a literal "Boys" prefix, so the abbreviation step must preserve
        it on real data from both fixtures -- including WZAG's "Mixed" relays, which correctly take
        the non-Boys branch."""
        seen_boys = seen_girls = seen_mixed = 0
        for timeline, flyer in ((HERC_TIMELINE, HERC_FLYER), (WZAG_TIMELINE, WZAG_FLYER)):
            _name, _sessions, events = parsed(timeline, flyer)
            for event in events:
                for include_age in (True, False):
                    label = badge_event_name(event.event_name, include_age=include_age)
                    self.assertTrue(
                        label.startswith(("Girls", "Boys", "Women", "Men", "Mixed")),
                        f"{event.event_name!r} -> {label!r}",
                    )
                    if event.event_name.startswith("Boys"):
                        self.assertIs(gender_color(label), NAVY, label)
                    else:
                        self.assertIs(gender_color(label), MAROON, label)
                if event.event_name.startswith("Boys"):
                    seen_boys += 1
                elif event.event_name.startswith("Girls"):
                    seen_girls += 1
                elif event.event_name.startswith("Mixed"):
                    seen_mixed += 1
        self.assertTrue(seen_boys and seen_girls)
        self.assertEqual(seen_mixed, 6)  # WZAG's six Mixed relays


class SessionGroupingTest(unittest.TestCase):
    def test_herculean_six_sessions_with_exact_real_event_and_heat_counts(self):
        _name, _sessions, events = parsed(HERC_TIMELINE, HERC_FLYER)
        grouped = events_by_session(events)
        self.assertEqual(sorted(grouped), [1, 2, 3, 4, 5, 6])
        # (event count, summed heat count) straight off the Session Report's own rows.
        expected = {1: (10, 71), 2: (14, 77), 3: (12, 57), 4: (12, 61), 5: (10, 52), 6: (14, 67)}
        for number, (event_count, heat_total) in expected.items():
            session_events = grouped[number]
            self.assertEqual(len(session_events), event_count, number)
            self.assertEqual(sum(e.heats for e in session_events), heat_total, number)

    def test_wzag_eight_sessions_with_exact_real_event_counts(self):
        _name, _sessions, events = parsed(WZAG_TIMELINE, WZAG_FLYER)
        grouped = events_by_session(events)
        self.assertEqual(sorted(grouped), [1, 2, 3, 4, 5, 6, 7, 8])
        expected = {1: 22, 2: 25, 3: 22, 4: 28, 5: 20, 6: 23, 7: 20, 8: 26}
        self.assertEqual({n: len(evs) for n, evs in grouped.items()}, expected)

    def test_grouping_preserves_every_event_and_orders_by_start(self):
        for timeline, flyer in ((HERC_TIMELINE, HERC_FLYER), (WZAG_TIMELINE, WZAG_FLYER)):
            _name, _sessions, events = parsed(timeline, flyer)
            grouped = events_by_session(events)
            self.assertEqual(sum(len(v) for v in grouped.values()), len(events))
            for session_events in grouped.values():
                starts = [e.start for e in session_events]
                self.assertEqual(starts, sorted(starts))


class NoonBoundaryTest(unittest.TestCase):
    """The spec assumed a session never crosses noon and asked for a sanity check. It does not
    hold: WZAG's Wednesday/Thursday/Saturday prelims start 8:30 AM and finish after noon, so those
    rows keep a meridiem marker rather than printing an ambiguous bare clock."""

    def test_herculean_sessions_never_cross_noon(self):
        _name, _sessions, events = parsed(HERC_TIMELINE, HERC_FLYER)
        for number, session_events in events_by_session(events).items():
            self.assertFalse(session_crosses_noon(session_events), number)

    def test_wzag_morning_prelims_do_cross_noon(self):
        _name, sessions, events = parsed(WZAG_TIMELINE, WZAG_FLYER)
        grouped = events_by_session(events)
        for number in (1, 3, 7):
            self.assertTrue(session_crosses_noon(grouped[number]), number)
            self.assertTrue(sessions[number].finish_time > "12:00", number)
        self.assertFalse(session_crosses_noon(grouped[2]))

    def test_row_times_drop_the_meridiem_only_when_unambiguous(self):
        _name, _sessions, events = parsed(WZAG_TIMELINE, WZAG_FLYER)
        grouped = events_by_session(events)
        crossing = grouped[1]
        self.assertEqual(row_time_label(crossing[0].start, True), "8:30a")
        self.assertEqual(row_time_label(crossing[0].start, False), "8:30")
        first_pm = next(e for e in crossing if e.start.strftime("%p") == "PM")
        self.assertTrue(row_time_label(first_pm.start, True).endswith("p"))


class SessionCardTest(unittest.TestCase):
    def test_herculean_cards_carry_constant_age_in_the_header(self):
        meet_name, cards = cards_for_timeline(HERC_TIMELINE, flyer_text=flyer_text(HERC_FLYER))
        self.assertEqual(meet_name, "2026 Herculean Invitational")
        self.assertEqual(len(cards), 6)
        session_one = cards[0]
        self.assertEqual(session_one.session_label, "SESSION 1 — 12 & OVER")
        self.assertEqual(session_one.date_label, "Fri, Sept 11")
        self.assertEqual(session_one.start_label, "5:30 PM")
        self.assertEqual(session_one.finish_label, "7:22 PM")
        self.assertEqual(session_one.heat_interval, "25s/Back+15")
        self.assertEqual(session_one.age_qualifier, "12 & Over")
        # Constant age -> rows omit it entirely.
        self.assertEqual(
            [(row["num"], row["name"], row["heats"], row["time"]) for row in session_one.events[:4]],
            [
                (1, "Girls 100 Free", 11, "5:30"),
                (2, "Boys 100 Free", 9, "5:48"),
                (3, "Girls 50 Back", 9, "6:02"),
                (4, "Boys 50 Back", 7, "6:13"),
            ],
        )

    def test_herculean_mixed_session_labels_by_session_name_and_tags_rows(self):
        _meet_name, cards = cards_for_timeline(HERC_TIMELINE, flyer_text=flyer_text(HERC_FLYER))
        session_two = cards[1]
        self.assertIsNone(session_two.age_qualifier)
        self.assertEqual(session_two.session_label, "SESSION 2 — FRIDAY PM 11&UNDER")
        self.assertEqual(
            [(row["num"], row["name"]) for row in session_two.events[:3]],
            [(101, "Girls 10&U 25 Breast"), (102, "Boys 10&U 25 Breast"), (103, "Girls 10&U 25 Fly")],
        )

    def test_wzag_cards_use_the_three_part_interval_and_meridiem_rows(self):
        meet_name, cards = cards_for_timeline(WZAG_TIMELINE, flyer_text=flyer_text(WZAG_FLYER))
        self.assertIn("Western Zone Age Group", meet_name)
        self.assertEqual(len(cards), 8)
        prelims = cards[0]
        self.assertEqual(prelims.session_label, "SESSION 1 — WEDNESDAY PRELIMS")
        self.assertEqual(prelims.heat_interval, "20s/Back+10/Chase-30")
        self.assertEqual(prelims.start_label, "8:30 AM")
        self.assertEqual(prelims.finish_label, "12:49 PM")
        self.assertEqual(prelims.events[0]["time"], "8:30a")
        self.assertEqual(cards[1].heat_interval, "50s/Back+25")

    def test_every_card_row_has_the_four_keys_draw_card_reads(self):
        for timeline, flyer in ((HERC_TIMELINE, HERC_FLYER), (WZAG_TIMELINE, WZAG_FLYER)):
            _meet_name, cards = cards_for_timeline(timeline, flyer_text=flyer_text(flyer))
            for card in cards:
                self.assertTrue(card.events)
                for row in card.events:
                    self.assertEqual(set(row), {"num", "name", "heats", "time"})

    def test_build_session_cards_survives_a_missing_heat_interval(self):
        """An interval is optional -- a timeline without one still produces cards."""
        _name, sessions, events = parsed(HERC_TIMELINE, HERC_FLYER)
        cards = build_session_cards("Test Meet", sessions, events, heat_intervals={})
        self.assertEqual(len(cards), 6)
        self.assertEqual({card.heat_interval for card in cards}, {""})


class MeetNameFitTest(unittest.TestCase):
    """draw_card's shrink-to-fit floors at 3.6pt and then draws anyway, so an over-long meta line
    clips at both ends instead of failing loudly. WZAG's parsed meet name carries a sanction
    number (", Sanction #: SR2608-CH01") and really did overflow before badge_meet_name trimmed it.
    """

    def test_sanction_clause_is_dropped_and_short_name_normalization_reused(self):
        self.assertEqual(
            badge_meet_name("2026 Western Zone Age Group LCM Championships, Sanction #: SR2608-CH01"),
            "Western Zone Age Group LCM Championships",
        )
        # short_meet_name's own normalization still applies (leading year, Invitational -> Invite).
        self.assertEqual(badge_meet_name("2026 Herculean Invitational"), "Herculean Invite")

    def test_short_meet_name_itself_is_left_alone(self):
        """The family calendars' titles come from short_meet_name; trimming had to be additive."""
        from swimtimeline.extract import short_meet_name

        self.assertIn(
            "Sanction",
            short_meet_name("2026 Western Zone Age Group LCM Championships, Sanction #: SR2608-CH01"),
        )

    def test_every_real_card_header_fits_above_the_shrink_to_fit_floor(self):
        from reportlab.pdfbase.pdfmetrics import stringWidth

        max_w = CARD_W * 0.94
        for timeline, flyer in ((HERC_TIMELINE, HERC_FLYER), (WZAG_TIMELINE, WZAG_FLYER)):
            _meet_name, cards = cards_for_timeline(timeline, flyer_text=flyer_text(flyer))
            for card in cards:
                # Mirrors draw_card's own two loops, including their font floors.
                meta = f"{card.meet_name} • {card.date_label} • Start {card.start_label}"
                size = max(4.6, CARD_W * 0.038)
                while stringWidth(meta, "Helvetica", size) > max_w and size > 3.6:
                    size -= 0.2
                self.assertLessEqual(
                    stringWidth(meta, "Helvetica", size), max_w, f"meta overflow: {meta!r}"
                )
                label_size = max(8.5, CARD_W * 0.068)
                while stringWidth(card.session_label, "Helvetica-Bold", label_size) > max_w and label_size > 6.5:
                    label_size -= 0.3
                self.assertLessEqual(
                    stringWidth(card.session_label, "Helvetica-Bold", label_size),
                    max_w,
                    f"label overflow: {card.session_label!r}",
                )


class RenderedPdfTest(unittest.TestCase):
    """Page count and page size are asserted by reading the generated bytes back with pypdf, not
    by trusting what was handed to draw_card()."""

    def test_combined_pdf_has_one_page_per_session_at_exact_card_size(self):
        for timeline, flyer, expected_pages in (
            (HERC_TIMELINE, HERC_FLYER, 6),
            (WZAG_TIMELINE, WZAG_FLYER, 8),
        ):
            _meet_name, cards = cards_for_timeline(timeline, flyer_text=flyer_text(flyer))
            self.assertEqual(len(cards), expected_pages)
            reader = PdfReader(BytesIO(render_cards_pdf(cards)))
            self.assertEqual(len(reader.pages), expected_pages, timeline.name)
            for index, page in enumerate(reader.pages):
                self.assertAlmostEqual(float(page.mediabox.width), CARD_W, places=2, msg=index)
                self.assertAlmostEqual(float(page.mediabox.height), CARD_H, places=2, msg=index)
        self.assertEqual((CARD_W, CARD_H), (144.0, 216.0))

    def test_single_session_pdf_is_one_page_at_the_same_size(self):
        _meet_name, cards = cards_for_timeline(HERC_TIMELINE, flyer_text=flyer_text(HERC_FLYER))
        for card in cards:
            reader = PdfReader(BytesIO(render_cards_pdf([card])))
            self.assertEqual(len(reader.pages), 1, card.session_number)
            page = reader.pages[0]
            self.assertAlmostEqual(float(page.mediabox.width), CARD_W, places=2)
            self.assertAlmostEqual(float(page.mediabox.height), CARD_H, places=2)

    def test_largest_real_session_still_renders(self):
        """WZAG's Thursday Finals is the biggest real session (28 events) -- the row-height and
        shrink-to-fit math has to survive it, not just the 10-14 the spec sampled."""
        _meet_name, cards = cards_for_timeline(WZAG_TIMELINE, flyer_text=flyer_text(WZAG_FLYER))
        biggest = max(cards, key=lambda card: card.event_count)
        self.assertEqual(biggest.event_count, 28)
        reader = PdfReader(BytesIO(render_cards_pdf([biggest])))
        self.assertEqual(len(reader.pages), 1)

    def test_rendering_no_cards_is_refused(self):
        with self.assertRaises(ValueError):
            render_cards_pdf([])


if __name__ == "__main__":
    unittest.main()
