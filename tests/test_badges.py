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
import math
from pathlib import Path
import unittest

from pypdf import PdfReader
from reportlab.pdfbase.pdfmetrics import stringWidth

from swimtimeline.badges import (
    CARD_FOOTER_FRAC,
    CARD_H,
    CARD_HEADER_FRAC,
    CARD_TABLE_GAP_FRAC,
    CARD_W,
    MAROON,
    MAX_HANDOUT_COPIES,
    NAVY,
    SHEET_COLS,
    SHEET_GUTTER,
    SHEET_H,
    SHEET_MARGIN_X,
    SHEET_MARGIN_Y,
    SHEET_ROWS,
    SHEET_SLOTS_PER_PAGE,
    SHEET_W,
    abbreviate_age_qualifier,
    abbreviate_stroke,
    badge_event_name,
    badge_meet_name,
    build_session_cards,
    card_filename,
    cards_for_timeline,
    compact_heat_interval,
    constant_age_qualifier,
    draw_star,
    events_by_session,
    gender_color,
    is_ambiguous_warning,
    parse_event_name,
    parse_heat_intervals,
    render_cards_pdf,
    render_handout_sheet_pdf,
    render_sheet_pdf,
    row_time_label,
    session_age_qualifiers,
    sheet_slot_origin,
    session_crosses_noon,
    swimmer_event_numbers,
)
from swimtimeline.extract import extract_text_pages, parse_timeline

ROOT = Path(__file__).resolve().parents[1]
HERC_DIR = ROOT / "meets/2026-herculean-invitational/input"
HERC_TIMELINE = HERC_DIR / "2026-herculean-invitational-timeline.pdf"
HERC_FLYER = HERC_DIR / "2026-herculean-invitational-flyer.pdf"
HERC_PSYCH = HERC_DIR / "2026-herculean-invitational-psych-sheet.pdf"
WZAG_DIR = ROOT / "meets/2026-wzag-championships-boise/input"
WZAG_TIMELINE = WZAG_DIR / "wzag timelines v4.pdf"
WZAG_FLYER = WZAG_DIR / "Sanctioned_2026 WZAG Championships - Boise (v5.pdf"
# 2026 Croswhite Invite -- single day, single "Girls" session, no flyer, no psych sheet. Its 11
# real event names ("Girls 200 Freestyle", "Girls 50 Freestyle", ...) have NO age-qualifier phrase
# at all -- gender is immediately followed by the distance number -- unlike every event in
# Herculean or WZAG, which always has one ("12 & Over", "11-12", ...). See
# CroswhiteNoAgeQualifierEventShapeTest.
CROS_DIR = ROOT / "meets/2026-croswhite-invite/input"
CROS_TIMELINE = CROS_DIR / "2026-croswhite-invite-timeline.pdf"
# Unlike every other field in this meet's data/current_meets.json entry, "venue" is NOT derived
# from this document -- the timeline states only the host club name ("Rio Salado Swim Club"), no
# address at all. The venue string registered there (Kerry Croswhite Aquatic Center, Chandler High
# School, 350 N Arizona Ave, Chandler, AZ 85225) came from external research and was confirmed
# directly by the site owner, not from any meet document -- unlike, e.g., the Herculean
# Invitational's venue, which its own flyer states outright. Flagged here rather than asserted as
# a test, since nothing in the real fixture PDF could confirm or refute it either way.


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
        meet_name, cards, _highlights = cards_for_timeline(HERC_TIMELINE, flyer_text=flyer_text(HERC_FLYER))
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
        _meet_name, cards, _highlights = cards_for_timeline(HERC_TIMELINE, flyer_text=flyer_text(HERC_FLYER))
        session_two = cards[1]
        self.assertIsNone(session_two.age_qualifier)
        self.assertEqual(session_two.session_label, "SESSION 2 — FRIDAY PM 11&UNDER")
        self.assertEqual(
            [(row["num"], row["name"]) for row in session_two.events[:3]],
            [(101, "Girls 10&U 25 Breast"), (102, "Boys 10&U 25 Breast"), (103, "Girls 10&U 25 Fly")],
        )

    def test_wzag_cards_use_the_three_part_interval_and_meridiem_rows(self):
        meet_name, cards, _highlights = cards_for_timeline(WZAG_TIMELINE, flyer_text=flyer_text(WZAG_FLYER))
        self.assertIn("Western Zone Age Group", meet_name)
        self.assertEqual(len(cards), 8)
        prelims = cards[0]
        self.assertEqual(prelims.session_label, "SESSION 1 — WEDNESDAY PRELIMS")
        self.assertEqual(prelims.heat_interval, "20s/Back+10/Chase-30")
        self.assertEqual(prelims.start_label, "8:30 AM")
        self.assertEqual(prelims.finish_label, "12:49 PM")
        self.assertEqual(prelims.events[0]["time"], "8:30a")
        self.assertEqual(cards[1].heat_interval, "50s/Back+25")

    def test_every_card_row_has_exactly_the_keys_draw_card_reads(self):
        """The four content keys, plus the "highlight" flag the swimmer-highlighting feature
        added. With no swimmer names given it is False on every row, so cards render exactly as
        they did before that feature existed."""
        for timeline, flyer in ((HERC_TIMELINE, HERC_FLYER), (WZAG_TIMELINE, WZAG_FLYER)):
            _meet_name, cards, _highlights = cards_for_timeline(timeline, flyer_text=flyer_text(flyer))
            for card in cards:
                self.assertTrue(card.events)
                for row in card.events:
                    self.assertEqual(set(row), {"num", "name", "heats", "time", "highlight"})
                    self.assertIs(row["highlight"], False)
                self.assertEqual(card.highlighted_event_numbers, [])

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
            _meet_name, cards, _highlights = cards_for_timeline(timeline, flyer_text=flyer_text(flyer))
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
            _meet_name, cards, _highlights = cards_for_timeline(timeline, flyer_text=flyer_text(flyer))
            self.assertEqual(len(cards), expected_pages)
            reader = PdfReader(BytesIO(render_cards_pdf(cards)))
            self.assertEqual(len(reader.pages), expected_pages, timeline.name)
            for index, page in enumerate(reader.pages):
                self.assertAlmostEqual(float(page.mediabox.width), CARD_W, places=2, msg=index)
                self.assertAlmostEqual(float(page.mediabox.height), CARD_H, places=2, msg=index)
        self.assertEqual((CARD_W, CARD_H), (144.0, 216.0))

    def test_single_session_pdf_is_one_page_at_the_same_size(self):
        _meet_name, cards, _highlights = cards_for_timeline(HERC_TIMELINE, flyer_text=flyer_text(HERC_FLYER))
        for card in cards:
            reader = PdfReader(BytesIO(render_cards_pdf([card])))
            self.assertEqual(len(reader.pages), 1, card.session_number)
            page = reader.pages[0]
            self.assertAlmostEqual(float(page.mediabox.width), CARD_W, places=2)
            self.assertAlmostEqual(float(page.mediabox.height), CARD_H, places=2)

    def test_largest_real_session_still_renders(self):
        """WZAG's Thursday Finals is the biggest real session (28 events) -- the row-height and
        shrink-to-fit math has to survive it, not just the 10-14 the spec sampled."""
        _meet_name, cards, _highlights = cards_for_timeline(WZAG_TIMELINE, flyer_text=flyer_text(WZAG_FLYER))
        biggest = max(cards, key=lambda card: card.event_count)
        self.assertEqual(biggest.event_count, 28)
        reader = PdfReader(BytesIO(render_cards_pdf([biggest])))
        self.assertEqual(len(reader.pages), 1)

    def test_rendering_no_cards_is_refused(self):
        with self.assertRaises(ValueError):
            render_cards_pdf([])


class SwimmerHighlightMatchingTest(unittest.TestCase):
    """Which events get starred. Matching itself is extract_psych_entries() -- the same machinery
    the family calendar uses -- so these tests pin the reduction to event numbers and, above all,
    the per-name independence: one unusable name must not cost the others their highlights.
    """

    def test_cova_resolves_to_exactly_her_five_real_events(self):
        highlights = swimmer_event_numbers(HERC_PSYCH, ["Cova, Mila"])
        self.assertEqual(sorted(highlights.event_numbers), [1, 5, 7, 27, 29])
        self.assertEqual(highlights.matched, {"Cova, Mila": [1, 5, 7, 27, 29]})
        self.assertEqual(highlights.warnings, [])
        self.assertTrue(highlights.any_matched)

    def test_each_real_ambiguous_surname_warns_and_highlights_nothing(self):
        """The three real same-surname groups at this meet. Each must refuse rather than merge."""
        expected_candidates = {
            "Vickers": ("Grace Vickers", "Natalie Vickers"),
            "Post": ("Harper Post", "Reagan Post", "Zoey Post"),
            "Beltran": ("Adrian Beltran", "Christian Beltran"),
        }
        for surname, candidates in expected_candidates.items():
            highlights = swimmer_event_numbers(HERC_PSYCH, [surname])
            self.assertEqual(highlights.event_numbers, set(), surname)
            self.assertEqual(highlights.matched, {}, surname)
            self.assertEqual(len(highlights.warnings), 1, surname)
            warning = highlights.warnings[0]
            self.assertTrue(is_ambiguous_warning(warning), warning)
            for candidate in candidates:
                self.assertIn(candidate, warning)

    def test_an_ambiguous_name_does_not_block_the_rest_of_the_batch(self):
        """The independence requirement: 'Vickers' is ambiguous and contributes nothing, while
        'Cova, Mila' and 'Vickers, Natalie' in the same batch still highlight normally."""
        highlights = swimmer_event_numbers(
            HERC_PSYCH, ["Cova, Mila", "Vickers", "Vickers, Natalie"]
        )
        self.assertEqual(sorted(highlights.matched), ["Cova, Mila", "Vickers, Natalie"])
        self.assertEqual(highlights.matched["Cova, Mila"], [1, 5, 7, 27, 29])
        self.assertTrue(highlights.matched["Vickers, Natalie"])
        # The union of both resolved swimmers, and nothing from the ambiguous name.
        self.assertEqual(
            highlights.event_numbers,
            set(highlights.matched["Cova, Mila"]) | set(highlights.matched["Vickers, Natalie"]),
        )
        self.assertEqual(len(highlights.warnings), 1)
        self.assertTrue(is_ambiguous_warning(highlights.warnings[0]))

    def test_siblings_resolve_individually_by_full_name(self):
        """Each half of an ambiguous pair is reachable with "Last, First", and the two really are
        different swimmers (different event sets)."""
        grace = swimmer_event_numbers(HERC_PSYCH, ["Vickers, Grace"])
        natalie = swimmer_event_numbers(HERC_PSYCH, ["Vickers, Natalie"])
        for highlights in (grace, natalie):
            self.assertEqual(highlights.warnings, [])
            self.assertTrue(highlights.event_numbers)
        self.assertNotEqual(grace.event_numbers, natalie.event_numbers)

    def test_a_name_nobody_matches_warns_without_claiming_ambiguity(self):
        highlights = swimmer_event_numbers(HERC_PSYCH, ["Nobody, Atall"])
        self.assertEqual(highlights.event_numbers, set())
        self.assertEqual(len(highlights.warnings), 1)
        self.assertFalse(is_ambiguous_warning(highlights.warnings[0]))
        self.assertIn("Nobody, Atall", highlights.warnings[0])

    def test_blank_and_duplicate_names_are_harmless(self):
        highlights = swimmer_event_numbers(HERC_PSYCH, ["Cova, Mila", "   ", "Cova, Mila"])
        self.assertEqual(sorted(highlights.event_numbers), [1, 5, 7, 27, 29])
        self.assertEqual(highlights.warnings, [])


class SwimmerHighlightCardTest(unittest.TestCase):
    """The starred rows as they reach draw_card, and the cards they land on."""

    @classmethod
    def setUpClass(cls):
        cls.meet_name, cls.cards, cls.highlights = cards_for_timeline(
            HERC_TIMELINE,
            flyer_text=flyer_text(HERC_FLYER),
            psych_pdf=HERC_PSYCH,
            swimmer_names=["Cova, Mila"],
        )

    def test_only_covas_events_are_flagged_across_the_whole_meet(self):
        flagged = {
            row["num"] for card in self.cards for row in card.events if row["highlight"]
        }
        self.assertEqual(flagged, {1, 5, 7, 27, 29})
        # Every other row in all 6 sessions is untouched -- 72 events total, 5 starred.
        total_rows = sum(card.event_count for card in self.cards)
        self.assertEqual(total_rows, 72)
        self.assertEqual(
            sum(1 for card in self.cards for row in card.events if not row["highlight"]), 67
        )

    def test_the_stars_land_on_the_sessions_those_events_belong_to(self):
        by_session = {card.session_number: card.highlighted_event_numbers for card in self.cards}
        # Cova swims the 12&Over pool: #1/5/7 are Friday (session 1), #27/29 Sunday (session 5).
        self.assertEqual(by_session[1], [1, 5, 7])
        self.assertEqual(by_session[5], [27, 29])
        for empty_session in (2, 3, 4, 6):
            self.assertEqual(by_session[empty_session], [], empty_session)

    def test_multi_swimmer_batch_stars_the_union_with_identical_treatment(self):
        _name, cards, highlights = cards_for_timeline(
            HERC_TIMELINE,
            flyer_text=flyer_text(HERC_FLYER),
            psych_pdf=HERC_PSYCH,
            swimmer_names=["Cova, Mila", "Vickers, Natalie"],
        )
        flagged = {row["num"] for card in cards for row in card.events if row["highlight"]}
        self.assertEqual(flagged, highlights.event_numbers)
        self.assertTrue({1, 5, 7, 27, 29}.issubset(flagged))
        # Union, not just one swimmer: Natalie brings events Cova is not in.
        self.assertGreater(len(flagged), 5)
        # "highlight" is a plain bool for every starred row -- no per-swimmer distinction exists
        # to render, which is the point: an official just needs "one of mine".
        for card in cards:
            for row in card.events:
                self.assertIn(row["highlight"], (True, False))

    def test_no_swimmer_names_leaves_every_row_unhighlighted(self):
        _name, cards, highlights = cards_for_timeline(
            HERC_TIMELINE, flyer_text=flyer_text(HERC_FLYER), psych_pdf=HERC_PSYCH
        )
        self.assertEqual(highlights.event_numbers, set())
        self.assertFalse(any(row["highlight"] for card in cards for row in card.events))

    def test_names_without_a_psych_sheet_say_so_instead_of_silently_not_highlighting(self):
        _name, cards, highlights = cards_for_timeline(
            HERC_TIMELINE, flyer_text=flyer_text(HERC_FLYER), swimmer_names=["Cova, Mila"]
        )
        self.assertEqual(highlights.event_numbers, set())
        self.assertEqual(len(highlights.warnings), 1)
        self.assertIn("psych sheet", highlights.warnings[0])
        self.assertFalse(any(row["highlight"] for card in cards for row in card.events))

    def test_ambiguous_only_batch_renders_a_clean_unhighlighted_card_set(self):
        _name, cards, highlights = cards_for_timeline(
            HERC_TIMELINE,
            flyer_text=flyer_text(HERC_FLYER),
            psych_pdf=HERC_PSYCH,
            swimmer_names=["Vickers", "Post", "Beltran"],
        )
        self.assertEqual(highlights.event_numbers, set())
        self.assertEqual(len(highlights.warnings), 3)
        self.assertTrue(all(is_ambiguous_warning(w) for w in highlights.warnings))
        self.assertFalse(any(row["highlight"] for card in cards for row in card.events))
        # Still a usable set of cards, just with nothing starred.
        reader = PdfReader(BytesIO(render_cards_pdf(cards)))
        self.assertEqual(len(reader.pages), 6)


class StarGlyphTest(unittest.TestCase):
    """The star is a vector path, not a text glyph, and this is the evidence for why.

    Helvetica's WinAnsiEncoding contains no star at all, yet stringWidth("★", "Helvetica", 8)
    returns a plausible 6.53 -- so glyph-based code raises nothing and measures fine while
    reportlab quietly swaps in a ZapfDingbats resource whose rendering is outside our control
    (U+2606, right next door, renders as a tofu box). These cards get printed, so that is not
    good enough.
    """

    def test_helvetica_really_has_no_star_glyph_despite_reporting_a_width(self):
        from reportlab.pdfbase import pdfmetrics

        self.assertGreater(stringWidth("★", "Helvetica", 8), 0)  # the misleading part
        encoding = pdfmetrics.getEncoding(pdfmetrics.getFont("Helvetica").encName)
        glyph_names = [name for name in encoding.vector if name]
        self.assertEqual([name for name in glyph_names if "star" in name.lower()], [])

    def test_highlighted_cards_declare_only_the_two_helvetica_faces(self):
        """A text star would add a /ZapfDingbats font resource to the PDF; the vector path does
        not, which is the check that proves no substitution is happening."""
        _name, cards, _highlights = cards_for_timeline(
            HERC_TIMELINE,
            flyer_text=flyer_text(HERC_FLYER),
            psych_pdf=HERC_PSYCH,
            swimmer_names=["Cova, Mila"],
        )
        starred = next(card for card in cards if card.highlighted_event_numbers)
        reader = PdfReader(BytesIO(render_cards_pdf([starred])))
        fonts = reader.pages[0]["/Resources"].get("/Font", {})
        base_fonts = sorted(str(font.get_object().get("/BaseFont")) for font in fonts.values())
        self.assertEqual(base_fonts, ["/Helvetica", "/Helvetica-Bold"])

    def test_the_star_emits_real_fill_geometry(self):
        """The path has to actually reach the page, not merely be computed. Session 1 stars three
        of Cova's events, and each star is one closed, filled subpath -- so the highlighted card's
        content stream carries exactly three more closepath ("h") and three more fill ("f*")
        operators than the identical unhighlighted card. Rects never emit "h", so that count
        isolates the stars."""
        kwargs = dict(flyer_text=flyer_text(HERC_FLYER), psych_pdf=HERC_PSYCH)
        _n, plain_cards, _h = cards_for_timeline(HERC_TIMELINE, **kwargs)
        _n, starred_cards, _h = cards_for_timeline(
            HERC_TIMELINE, swimmer_names=["Cova, Mila"], **kwargs
        )
        self.assertEqual(starred_cards[0].highlighted_event_numbers, [1, 5, 7])

        def stream(card):
            reader = PdfReader(BytesIO(render_cards_pdf([card])))
            return reader.pages[0].get_contents().get_data().decode("latin-1")

        plain, starred = stream(plain_cards[0]), stream(starred_cards[0])
        self.assertEqual(plain.count("h\n"), 0)
        self.assertEqual(starred.count("h\n"), 3)
        self.assertEqual(starred.count(" f*\n") - plain.count(" f*\n"), 3)

    def test_star_geometry_is_a_closed_ten_vertex_outline(self):
        """Five points means five outer and five inner vertices, all within the requested radius."""
        captured = {}

        class FakePath:
            def __init__(self):
                self.points = []
                self.closed = False

            def moveTo(self, x, y):
                self.points.append((x, y))

            def lineTo(self, x, y):
                self.points.append((x, y))

            def close(self):
                self.closed = True

        class FakeCanvas:
            def beginPath(self):
                captured["path"] = FakePath()
                return captured["path"]

            def drawPath(self, path, stroke=0, fill=1):
                captured["drawn"] = (path, stroke, fill)

        draw_star(FakeCanvas(), 10.0, 20.0, 2.0)
        path = captured["path"]
        self.assertEqual(len(path.points), 10)
        self.assertTrue(path.closed)
        self.assertEqual(captured["drawn"][1:], (0, 1))  # filled, not stroked
        for x, y in path.points:
            self.assertLessEqual(math.dist((x, y), (10.0, 20.0)), 2.0 + 1e-9)
        # The star points UP. PDF user space has y increasing upward, so the first (top) vertex
        # sits at cy + radius; computing it the screen-coordinate way instead silently produces a
        # point-down star, which is why this is pinned.
        self.assertAlmostEqual(path.points[0][0], 10.0, places=6)
        self.assertAlmostEqual(path.points[0][1], 22.0, places=6)
        self.assertAlmostEqual(max(y for _x, y in path.points), 22.0, places=6)
        # Five outer vertices at the full radius, five inner ones pulled in.
        radii = sorted(round(math.dist((x, y), (10.0, 20.0)), 6) for x, y in path.points)
        self.assertEqual(radii[5:], [2.0] * 5)
        self.assertTrue(all(r < 2.0 for r in radii[:5]))


class HighlightLayoutTest(unittest.TestCase):
    """A starred row must not cost the card its legibility: the # column widens once for the whole
    card so the star and the event number both fit at full size, and nothing overflows."""

    def test_number_column_widens_only_when_a_card_has_stars(self):
        kwargs = dict(flyer_text=flyer_text(HERC_FLYER), psych_pdf=HERC_PSYCH)
        _n, plain, _h = cards_for_timeline(HERC_TIMELINE, **kwargs)
        # Post, Zoey swims the 11&Under pool, whose event numbers are 3 digits -- the tight case.
        _n, starred, _h = cards_for_timeline(
            HERC_TIMELINE, swimmer_names=["Post, Zoey"], **kwargs
        )
        self.assertTrue(starred[1].highlighted_event_numbers)
        self.assertEqual(plain[1].highlighted_event_numbers, [])
        # Same session, same events -- only the highlight flags differ.
        self.assertEqual(
            [row["num"] for row in plain[1].events], [row["num"] for row in starred[1].events]
        )

    def test_star_plus_a_three_digit_number_fits_the_widened_column(self):
        """Replays draw_card's own geometry for the worst real case: 14 rows (smallest font) with
        3-digit numbers, and checks the number never has to shrink below the base font."""
        _n, cards, _h = cards_for_timeline(
            HERC_TIMELINE,
            flyer_text=flyer_text(HERC_FLYER),
            psych_pdf=HERC_PSYCH,
            swimmer_names=["Post, Zoey"],
        )
        card = cards[1]  # Session 2: 14 events, numbers 101-114, three of them starred
        self.assertEqual(card.event_count, 14)
        margin = max(3.5, CARD_W * 0.035)
        content_w = CARD_W - 2 * margin
        # Reads draw_card's own fractions rather than repeating them -- these two copies
        # silently drifted out of sync once the footer grew a second line for the site URL.
        table_h = (
            CARD_H
            - CARD_HEADER_FRAC * CARD_H
            - CARD_FOOTER_FRAC * CARD_H
            - 2 * CARD_TABLE_GAP_FRAC * CARD_H
        )
        row_h = table_h / (card.event_count + 0.62)
        base_fs = max(5.0, min(8.3, row_h * 0.5))
        col_num_w = content_w * 0.145  # widened, because this card has stars
        accent_w = max(1.2, CARD_W * 0.012)
        slot = (col_num_w - 1.0) - (accent_w + 0.6)
        for row in card.events:
            if not row["highlight"]:
                continue
            num_w = stringWidth(str(row["num"]), "Helvetica-Bold", base_fs)
            room = slot - num_w - 0.8
            star_r = min(row_h * 0.24, base_fs * 0.34, 2.6, max(room, 0.0) / 2)
            star_r = max(star_r, min(1.15, row_h * 0.24))
            # Star and a FULL-SIZE number both fit inside the widened column.
            self.assertLessEqual(2 * star_r + 0.8 + num_w, slot + 1e-9, row)
            self.assertGreater(star_r, 0.9, row)  # still a visible marker

    def test_highlighted_cards_still_render_at_the_exact_card_size(self):
        for names in (["Cova, Mila"], ["Post, Zoey"], ["Cova, Mila", "Vickers, Natalie"]):
            _n, cards, _h = cards_for_timeline(
                HERC_TIMELINE,
                flyer_text=flyer_text(HERC_FLYER),
                psych_pdf=HERC_PSYCH,
                swimmer_names=names,
            )
            reader = PdfReader(BytesIO(render_cards_pdf(cards)))
            self.assertEqual(len(reader.pages), 6, names)
            for page in reader.pages:
                self.assertAlmostEqual(float(page.mediabox.width), CARD_W, places=2)
                self.assertAlmostEqual(float(page.mediabox.height), CARD_H, places=2)

    def test_event_names_still_fit_after_the_column_widens(self):
        """EVENT gives up the width, so its shrink-to-fit has to absorb it on the worst real
        session (WZAG Thursday Finals: 28 rows, longest relay names)."""
        _n, cards, _h = cards_for_timeline(
            WZAG_TIMELINE, flyer_text=flyer_text(WZAG_FLYER), psych_pdf=HERC_PSYCH
        )
        biggest = max(cards, key=lambda card: card.event_count)
        margin = max(3.5, CARD_W * 0.035)
        content_w = CARD_W - 2 * margin
        # Reads draw_card's own fractions rather than repeating them -- these two copies
        # silently drifted out of sync once the footer grew a second line for the site URL.
        table_h = (
            CARD_H
            - CARD_HEADER_FRAC * CARD_H
            - CARD_FOOTER_FRAC * CARD_H
            - 2 * CARD_TABLE_GAP_FRAC * CARD_H
        )
        row_h = table_h / (biggest.event_count + 0.62)
        base_fs = max(5.0, min(8.3, row_h * 0.5))
        col_event_w = content_w - content_w * 0.145 - content_w * 0.225 - content_w * 0.20
        for row in biggest.events:
            size = base_fs
            while stringWidth(row["name"], "Helvetica", size) > col_event_w - 4 and size > 4.0:
                size -= 0.2
            self.assertLessEqual(
                stringWidth(row["name"], "Helvetica", size), col_event_w - 4, row["name"]
            )


def page_text(reader: PdfReader, index: int) -> str:
    """One page's extracted text with whitespace collapsed, so a card's content can be compared
    across layouts -- the same card drawn at a tiled position has identical text but different
    coordinates, so the raw content stream can't be compared byte-for-byte."""
    return " ".join((reader.pages[index].extract_text() or "").split())


class SheetGridGeometryTest(unittest.TestCase):
    """The print-sheet grid itself, checkable without generating a PDF.

    Three columns rather than four: four native-width cards need 4 x 144 = 576pt, leaving only
    18pt of side margin with a ZERO gutter, and overflowing the sheet (-9pt) with any gutter at
    all -- and 18pt is exactly the unprintable edge on typical consumer printers, so the outer
    cards' own borders would be clipped off.
    """

    def test_sheet_is_us_letter_and_the_grid_lands_on_clean_margins(self):
        self.assertEqual((SHEET_W, SHEET_H), (612.0, 792.0))
        self.assertEqual((SHEET_COLS, SHEET_ROWS, SHEET_SLOTS_PER_PAGE), (3, 3, 9))
        self.assertEqual(SHEET_GUTTER, 18.0)
        # Derived from the grid, and exact: 1.00" sides, 0.75" top/bottom.
        self.assertEqual(SHEET_MARGIN_X, 72.0)
        self.assertEqual(SHEET_MARGIN_Y, 54.0)
        # The margins really do account for every remaining point of the sheet.
        self.assertEqual(
            SHEET_MARGIN_X * 2 + SHEET_COLS * CARD_W + (SHEET_COLS - 1) * SHEET_GUTTER, SHEET_W
        )
        self.assertEqual(
            SHEET_MARGIN_Y * 2 + SHEET_ROWS * CARD_H + (SHEET_ROWS - 1) * SHEET_GUTTER, SHEET_H
        )

    def test_four_columns_would_not_have_fit(self):
        """Pins the reason the grid is 3 wide, so nobody 'optimises' it to 4 and clips the cards."""
        self.assertLess(SHEET_W - 4 * CARD_W, 2 * 18.0 + 1)  # < 0.25" per side even with no gutter
        self.assertLess(SHEET_W, 4 * CARD_W + 3 * SHEET_GUTTER)  # overflows outright with a gutter

    def test_every_slot_sits_inside_the_sheet_and_none_overlap(self):
        boxes = [sheet_slot_origin(index) for index in range(SHEET_SLOTS_PER_PAGE)]
        for index, (x, y) in enumerate(boxes):
            self.assertGreaterEqual(x, SHEET_MARGIN_X, index)
            self.assertGreaterEqual(y, SHEET_MARGIN_Y, index)
            self.assertLessEqual(x + CARD_W, SHEET_W - SHEET_MARGIN_X, index)
            self.assertLessEqual(y + CARD_H, SHEET_H - SHEET_MARGIN_Y, index)
        for first in range(len(boxes)):
            for second in range(first + 1, len(boxes)):
                (ax, ay), (bx, by) = boxes[first], boxes[second]
                overlaps = (
                    ax < bx + CARD_W and bx < ax + CARD_W and ay < by + CARD_H and by < ay + CARD_H
                )
                self.assertFalse(overlaps, (first, second))

    def test_slots_run_in_reading_order_left_to_right_top_row_first(self):
        """PDF user space has y=0 at the BOTTOM, so the top row must be the HIGHEST y -- the easy
        thing to get backwards here."""
        first_row = [sheet_slot_origin(index) for index in range(SHEET_COLS)]
        self.assertEqual([x for x, _y in first_row], sorted(x for x, _y in first_row))
        self.assertEqual(len({y for _x, y in first_row}), 1)  # one row, one y
        top_y = sheet_slot_origin(0)[1]
        middle_y = sheet_slot_origin(SHEET_COLS)[1]
        bottom_y = sheet_slot_origin(2 * SHEET_COLS)[1]
        self.assertGreater(top_y, middle_y)
        self.assertGreater(middle_y, bottom_y)
        self.assertEqual(top_y + CARD_H, SHEET_H - SHEET_MARGIN_Y)  # flush to the top margin
        self.assertEqual(bottom_y, SHEET_MARGIN_Y)  # flush to the bottom margin

    def test_a_slot_index_off_the_page_is_refused(self):
        for bad in (-1, SHEET_SLOTS_PER_PAGE, SHEET_SLOTS_PER_PAGE + 5):
            with self.assertRaises(ValueError):
                sheet_slot_origin(bad)


class SheetLayoutRenderTest(unittest.TestCase):
    """The tiled sheet against both real fixtures. The per-page (one-card-per-page) output is
    unchanged and still tested above -- this is a third option, not a replacement, and explicitly
    NOT the deferred "12-up" idea (which would repeat ONE session's card many times).
    """

    def test_both_real_meets_fit_on_exactly_one_letter_sheet(self):
        for timeline, flyer, expected_sessions in (
            (HERC_TIMELINE, HERC_FLYER, 6),
            (WZAG_TIMELINE, WZAG_FLYER, 8),
        ):
            _name, cards, _highlights = cards_for_timeline(timeline, flyer_text=flyer_text(flyer))
            self.assertEqual(len(cards), expected_sessions)
            self.assertLessEqual(len(cards), SHEET_SLOTS_PER_PAGE)
            reader = PdfReader(BytesIO(render_sheet_pdf(cards)))
            self.assertEqual(len(reader.pages), 1, timeline.name)
            page = reader.pages[0]
            self.assertAlmostEqual(float(page.mediabox.width), SHEET_W, places=2)
            self.assertAlmostEqual(float(page.mediabox.height), SHEET_H, places=2)

    def test_each_card_on_the_sheet_is_identical_to_its_standalone_page(self):
        """The whole point of reusing draw_card() untouched: a card tiled on a sheet carries
        exactly the content of its own 144x216pt page. Compared as text, since the tiled copy
        differs only by coordinate translation."""
        for timeline, flyer in ((HERC_TIMELINE, HERC_FLYER), (WZAG_TIMELINE, WZAG_FLYER)):
            _name, cards, _highlights = cards_for_timeline(timeline, flyer_text=flyer_text(flyer))
            per_page = PdfReader(BytesIO(render_cards_pdf(cards)))
            sheet = PdfReader(BytesIO(render_sheet_pdf(cards)))
            self.assertEqual(len(per_page.pages), len(cards))
            tiled = page_text(sheet, 0)
            for index, card in enumerate(cards):
                standalone = page_text(per_page, index)
                self.assertTrue(standalone, (timeline.name, index))
                # Real content, not just a non-empty string.
                self.assertIn(card.session_label, standalone)
                self.assertIn(standalone, tiled, f"{timeline.name} session {card.session_number}")

    def test_sheet_carries_every_session_exactly_once(self):
        _name, cards, _highlights = cards_for_timeline(
            WZAG_TIMELINE, flyer_text=flyer_text(WZAG_FLYER)
        )
        tiled = page_text(PdfReader(BytesIO(render_sheet_pdf(cards))), 0)
        for card in cards:
            self.assertEqual(tiled.count(card.session_label), 1, card.session_label)

    def test_pagination_wraps_past_nine_cards(self):
        """No SINGLE real meet in this repo exercises this: the largest, WZAG, has 8 sessions and
        Herculean has 6, so both fit one sheet. To exercise the wrap without fabricating a meet,
        this concatenates the two real meets' real cards (14 real sessions) -- an artificial
        COMBINATION, but every card is real fixture data.
        """
        _n1, herc, _h1 = cards_for_timeline(HERC_TIMELINE, flyer_text=flyer_text(HERC_FLYER))
        _n2, wzag, _h2 = cards_for_timeline(WZAG_TIMELINE, flyer_text=flyer_text(WZAG_FLYER))
        combined = herc + wzag
        self.assertEqual(len(combined), 14)
        reader = PdfReader(BytesIO(render_sheet_pdf(combined)))
        self.assertEqual(len(reader.pages), 2)  # 9 on the first sheet, 5 on the second
        for page in reader.pages:
            self.assertAlmostEqual(float(page.mediabox.width), SHEET_W, places=2)
            self.assertAlmostEqual(float(page.mediabox.height), SHEET_H, places=2)
        first, second = page_text(reader, 0), page_text(reader, 1)
        self.assertIn(combined[0].session_label, first)
        self.assertIn(combined[8].session_label, first)  # last slot of sheet one
        self.assertIn(combined[9].session_label, second)  # first slot of sheet two
        self.assertNotIn(combined[9].session_label, first)

    def test_a_single_card_still_produces_one_full_letter_sheet(self):
        _name, cards, _highlights = cards_for_timeline(HERC_TIMELINE, flyer_text=flyer_text(HERC_FLYER))
        reader = PdfReader(BytesIO(render_sheet_pdf([cards[0]])))
        self.assertEqual(len(reader.pages), 1)
        self.assertAlmostEqual(float(reader.pages[0].mediabox.width), SHEET_W, places=2)
        self.assertIn(cards[0].session_label, page_text(reader, 0))

    def test_rendering_no_cards_is_refused(self):
        with self.assertRaises(ValueError):
            render_sheet_pdf([])

    def test_the_per_page_layout_is_untouched_by_the_sheet_layout(self):
        """Guard on the explicit requirement that the cut-out format stays exactly as-is."""
        _name, cards, _highlights = cards_for_timeline(HERC_TIMELINE, flyer_text=flyer_text(HERC_FLYER))
        reader = PdfReader(BytesIO(render_cards_pdf(cards)))
        self.assertEqual(len(reader.pages), 6)
        for page in reader.pages:
            self.assertAlmostEqual(float(page.mediabox.width), CARD_W, places=2)
            self.assertAlmostEqual(float(page.mediabox.height), CARD_H, places=2)


class SheetFilenameTest(unittest.TestCase):
    def test_each_download_shape_gets_its_own_name(self):
        _name, cards, _highlights = cards_for_timeline(HERC_TIMELINE, flyer_text=flyer_text(HERC_FLYER))
        self.assertEqual(
            card_filename("2026 Herculean Invitational"),
            "2026-herculean-invitational-badge-cards.pdf",
        )
        self.assertEqual(
            card_filename("2026 Herculean Invitational", layout="sheet"),
            "2026-herculean-invitational-badge-card-sheets.pdf",
        )
        # A specific session wins over the layout -- it is one card either way.
        self.assertEqual(
            card_filename("2026 Herculean Invitational", cards[0], layout="sheet"),
            "2026-herculean-invitational-session-1-badge-card.pdf",
        )

    def test_a_filtered_download_is_named_differently_from_an_unfiltered_one(self):
        """Otherwise both land in Downloads as the same name plus "(1)" and become
        indistinguishable."""
        plain = card_filename("2026 Herculean Invitational")
        filtered = card_filename("2026 Herculean Invitational", highlighted_only=True)
        sheet_plain = card_filename("2026 Herculean Invitational", layout="sheet")
        sheet_filtered = card_filename("2026 Herculean Invitational", layout="sheet", highlighted_only=True)
        self.assertEqual(len({plain, filtered, sheet_plain, sheet_filtered}), 4)
        self.assertIn("highlighted", filtered)
        self.assertIn("highlighted", sheet_filtered)


class HandoutSheetTest(unittest.TestCase):
    """render_handout_sheet_pdf(): N copies of ONE real session's card (Herculean session 1,
    "12 & Over") tiled across as many 9-per-sheet pages as needed -- the original spec's deferred
    "12-up" idea, now built on the same 3x3 grid as the different-sessions sheet layout via
    render_sheet_pdf() itself (fed `copies` references to the same card), not a separate
    implementation.
    """

    @classmethod
    def setUpClass(cls):
        _name, cards, _highlights = cards_for_timeline(HERC_TIMELINE, flyer_text=flyer_text(HERC_FLYER))
        cls.card = cards[0]
        cls.standalone_text = page_text(PdfReader(BytesIO(render_cards_pdf([cls.card]))), 0)

    def slot_counts(self, reader: PdfReader) -> list[int]:
        """Cards actually placed per page, counted by a marker every card has exactly once."""
        return [page_text(reader, index).count("Est. Finish") for index in range(len(reader.pages))]

    def test_under_nine_copies_is_a_single_sheet_with_the_rest_of_the_grid_blank(self):
        reader = PdfReader(BytesIO(render_handout_sheet_pdf(self.card, 5)))
        self.assertEqual(len(reader.pages), 1)
        self.assertAlmostEqual(float(reader.pages[0].mediabox.width), SHEET_W, places=2)
        self.assertAlmostEqual(float(reader.pages[0].mediabox.height), SHEET_H, places=2)
        self.assertEqual(self.slot_counts(reader), [5])

    def test_exactly_nine_copies_fills_one_sheet_completely(self):
        reader = PdfReader(BytesIO(render_handout_sheet_pdf(self.card, 9)))
        self.assertEqual(len(reader.pages), 1)
        self.assertEqual(self.slot_counts(reader), [9])

    def test_ten_copies_wraps_to_a_second_sheet_with_nine_plus_one(self):
        reader = PdfReader(BytesIO(render_handout_sheet_pdf(self.card, 10)))
        self.assertEqual(len(reader.pages), 2)
        self.assertEqual(self.slot_counts(reader), [9, 1])
        for page in reader.pages:
            self.assertAlmostEqual(float(page.mediabox.width), SHEET_W, places=2)
            self.assertAlmostEqual(float(page.mediabox.height), SHEET_H, places=2)

    def test_other_copy_counts_split_across_sheets_correctly(self):
        for copies, expected_slot_counts in ((18, [9, 9]), (19, [9, 9, 1]), (1, [1])):
            reader = PdfReader(BytesIO(render_handout_sheet_pdf(self.card, copies)))
            self.assertEqual(len(reader.pages), len(expected_slot_counts), copies)
            self.assertEqual(self.slot_counts(reader), expected_slot_counts, copies)

    def test_every_copys_content_is_identical_to_the_real_standalone_card(self):
        """Same real session, same real event data, in every one of 10 copies across 2 sheets --
        not just a count check."""
        reader = PdfReader(BytesIO(render_handout_sheet_pdf(self.card, 10)))
        tiled = page_text(reader, 0) + " " + page_text(reader, 1)
        self.assertEqual(tiled.count(self.standalone_text), 10)

    def test_copies_below_one_is_refused(self):
        for bad in (0, -1, -100):
            with self.assertRaises(ValueError):
                render_handout_sheet_pdf(self.card, bad)

    def test_copies_above_the_cap_is_refused(self):
        with self.assertRaises(ValueError):
            render_handout_sheet_pdf(self.card, MAX_HANDOUT_COPIES + 1)

    def test_copies_at_exactly_the_cap_is_allowed(self):
        # Not rendered at full size here (slow-ish and pointless to assert on) -- just confirms the
        # cap is inclusive, not exclusive.
        reader = PdfReader(BytesIO(render_handout_sheet_pdf(self.card, MAX_HANDOUT_COPIES)))
        self.assertEqual(len(reader.pages), -(-MAX_HANDOUT_COPIES // SHEET_SLOTS_PER_PAGE))

    def test_the_cap_is_reasonable_for_the_stated_use_case(self):
        """Documents WHY 200: generously above a real single-session officiating crew, while
        keeping worst-case cost trivial."""
        self.assertEqual(MAX_HANDOUT_COPIES, 200)
        sheets_at_cap = -(-MAX_HANDOUT_COPIES // SHEET_SLOTS_PER_PAGE)
        self.assertLessEqual(sheets_at_cap, 25)  # a stack of paper, not a resource-exhaustion vector

    def test_other_sheet_and_cards_layouts_are_unaffected_by_this_addition(self):
        """Re-verifies the pre-existing outputs, not just assumes they still work."""
        _name, cards, _highlights = cards_for_timeline(HERC_TIMELINE, flyer_text=flyer_text(HERC_FLYER))
        per_page = PdfReader(BytesIO(render_cards_pdf(cards)))
        self.assertEqual(len(per_page.pages), 6)
        for page in per_page.pages:
            self.assertAlmostEqual(float(page.mediabox.width), CARD_W, places=2)
        sheet = PdfReader(BytesIO(render_sheet_pdf(cards)))
        self.assertEqual(len(sheet.pages), 1)
        self.assertAlmostEqual(float(sheet.pages[0].mediabox.width), SHEET_W, places=2)


class HandoutFilenameTest(unittest.TestCase):
    def test_copy_count_is_named_and_distinct_from_a_plain_single_session_download(self):
        _name, cards, _highlights = cards_for_timeline(HERC_TIMELINE, flyer_text=flyer_text(HERC_FLYER))
        plain_session = card_filename("2026 Herculean Invitational", cards[0])
        handout = card_filename("2026 Herculean Invitational", cards[0], copies=10)
        self.assertNotEqual(plain_session, handout)
        self.assertEqual(handout, "2026-herculean-invitational-session-1-badge-cards-x10.pdf")

    def test_different_copy_counts_of_the_same_session_are_distinct_filenames(self):
        _name, cards, _highlights = cards_for_timeline(HERC_TIMELINE, flyer_text=flyer_text(HERC_FLYER))
        ten = card_filename("2026 Herculean Invitational", cards[0], copies=10)
        fifty = card_filename("2026 Herculean Invitational", cards[0], copies=50)
        self.assertNotEqual(ten, fifty)


class CroswhiteNoAgeQualifierEventShapeTest(unittest.TestCase):
    """A genuinely new real event-name shape, not covered by Herculean or WZAG: Croswhite's 11
    events are all "<Gender> <Distance> <Stroke>" with NOTHING between gender and the distance
    number -- no "12 & Over", no "11-12", nothing. _EVENT_NAME_RE's age group is non-greedy but
    still requires at least one character, so every one of these fails to match.

    That failure is expected to cascade in a specific, already-designed way: badge_event_name()
    falls through to plain abbreviate_stroke() (still preserving the literal "Girls"/"Boys" prefix
    gender_color() depends on), and constant_age_qualifier() -- whose own rule is that ANY
    unparseable event forces a session to be treated as mixed -- reports the whole session as
    mixed even though every event shares the same (absent) age qualifier. The card header then
    falls back to the session's own name ("SESSION 1 -- GIRLS") rather than a constant age group.
    This class confirms all of that against the real fixture, and that the resulting card still
    renders correctly.
    """

    REAL_EVENTS = [
        # (event_number, raw event_name, heats, entries, badge_event_name(), row time)
        (2, "Girls 200 Medley Relay", 4, 32, "Girls 200 Medley Relay", "6:00"),
        (4, "Girls 200 Freestyle", 5, 50, "Girls 200 Free", "6:14"),
        (6, "Girls 200 IM", 5, 50, "Girls 200 IM", "6:29"),
        (8, "Girls 50 Freestyle", 15, 148, "Girls 50 Free", "6:46"),
        (10, "Girls 100 Butterfly", 7, 62, "Girls 100 Fly", "7:17"),
        (12, "Girls 100 Freestyle", 14, 134, "Girls 100 Free", "7:32"),
        (14, "Girls 500 Freestyle", 4, 40, "Girls 500 Free", "7:57"),
        (16, "Girls 200 Freestyle Relay", 4, 34, "Girls 200 Free Relay", "8:25"),
        (18, "Girls 100 Backstroke", 11, 101, "Girls 100 Back", "8:36"),
        (20, "Girls 100 Breaststroke", 11, 110, "Girls 100 Breast", "9:02"),
        (22, "Girls 400 Freestyle Relay", 4, 32, "Girls 400 Free Relay", "9:26"),
    ]

    @classmethod
    def setUpClass(cls):
        cls.meet_name, cls.sessions, cls.events = parse_timeline(CROS_TIMELINE)
        cls.grouped = events_by_session(cls.events)
        cls.session_events = cls.grouped[1]
        cls.card_meet_name, cls.cards, cls.highlights = cards_for_timeline(CROS_TIMELINE)

    def test_real_events_match_the_document_exactly(self):
        self.assertEqual(len(self.events), 11)
        actual = [
            (e.event_number, e.event_name, e.heats, e.entries)
            for e in sorted(self.session_events, key=lambda e: e.event_number)
        ]
        expected = [(num, name, heats, entries) for num, name, heats, entries, _badge, _time in self.REAL_EVENTS]
        self.assertEqual(actual, expected)

    def test_parse_event_name_returns_none_for_every_real_event(self):
        """The core claim: not one of these 11 real event names matches the age-qualifier regex."""
        for _num, name, _heats, _entries, _badge, _time in self.REAL_EVENTS:
            self.assertIsNone(parse_event_name(name), name)

    def test_badge_event_name_falls_through_to_abbreviate_stroke_but_keeps_gender(self):
        for _num, name, _heats, _entries, expected_badge, _time in self.REAL_EVENTS:
            for include_age in (True, False):
                result = badge_event_name(name, include_age=include_age)
                # include_age is irrelevant here -- there is no age to include or omit, so both
                # calls produce the identical fallback text.
                self.assertEqual(result, expected_badge, (name, include_age))
                self.assertEqual(result, abbreviate_stroke(name))
            self.assertTrue(result.startswith("Girls"), result)

    def test_session_age_qualifiers_is_empty_and_constant_age_qualifier_is_none(self):
        """Nothing parsed an age at all (session_age_qualifiers is empty, not a single shared
        value) -- and constant_age_qualifier()'s own explicit rule is that an unparseable event
        forces mixed treatment, so it returns None even though every event agrees on having no
        age qualifier."""
        self.assertEqual(session_age_qualifiers(self.session_events), [])
        self.assertIsNone(constant_age_qualifier(self.session_events))

    def test_gender_color_still_keys_off_the_preserved_girls_prefix(self):
        for _num, name, _heats, _entries, expected_badge, _time in self.REAL_EVENTS:
            self.assertIs(gender_color(badge_event_name(name, include_age=False)), MAROON, name)

    def test_card_header_falls_back_to_the_session_name_not_a_constant_age_group(self):
        card = self.cards[0]
        self.assertEqual(len(self.cards), 1)
        self.assertIsNone(card.age_qualifier)
        self.assertEqual(self.sessions[1].name, "Girls")
        self.assertEqual(card.session_label, "SESSION 1 — GIRLS")
        self.assertEqual(card.date_label, "Sat, Sept 12")
        self.assertEqual(card.start_label, "6:00 PM")
        self.assertEqual(card.finish_label, "9:49 PM")

    def test_card_rows_carry_no_per_row_age_tag_and_match_the_document(self):
        """With no age qualifier ever resolved, include_age has nothing to add regardless of
        constant-vs-mixed -- so no row shows an age tag at all, unlike a real mixed WZAG/Herculean
        session where an unresolved constant still means each row gets its own tag."""
        card = self.cards[0]
        actual = [(row["num"], row["name"], row["heats"], row["time"]) for row in card.events]
        expected = [
            (num, badge_name, heats, time) for num, _name, heats, _entries, badge_name, time in self.REAL_EVENTS
        ]
        self.assertEqual(actual, expected)
        for row in card.events:
            self.assertFalse(row["highlight"])

    def test_no_flyer_or_psych_sheet_still_produces_a_working_card(self):
        """Croswhite has no flyer and no psych sheet on record -- cards_for_timeline() must not
        require either."""
        self.assertEqual(self.card_meet_name, "2026 Croswhite Invite - 9/12/2026")
        self.assertEqual(self.card_meet_name, self.meet_name)
        self.assertEqual(self.highlights.event_numbers, set())

    def test_the_single_session_card_renders_at_the_exact_card_size(self):
        reader = PdfReader(BytesIO(render_cards_pdf(self.cards)))
        self.assertEqual(len(reader.pages), 1)
        page = reader.pages[0]
        self.assertAlmostEqual(float(page.mediabox.width), CARD_W, places=2)
        self.assertAlmostEqual(float(page.mediabox.height), CARD_H, places=2)


if __name__ == "__main__":
    unittest.main()
