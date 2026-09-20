"""Adjacent Girls/Boys events share one badge-card row.

WHY: a high-event-count session was illegible. Session 6 of the real AZ LC Age Group State
timeline has 42 events, which drove draw_card's row height down to 3.8pt while the font was already
pinned at its 5.0pt floor -- text taller than its own row. Combining each Girls/Boys pair into one
row halves that session to 21 rows (row height 7.5pt) without dropping a single event.

THE RULE (see combine_gender_pairs): a single left-to-right greedy scan in the events' existing
sorted order. Two ADJACENT events combine when both names parse, the pair is exactly one Girls and
one Boys, and the age qualifier and distance+stroke match exactly. Nothing is reordered and only
the immediate neighbour is ever considered, so a session whose data is not strictly interleaved
just renders fewer combined rows.

Every fixture here is real. The rendered-PDF assertions read text back out of the generated
document rather than trusting the row dicts, which is how this module's earlier bugs (an
upside-down star, an overflowing meta line) were actually caught.
"""

from io import BytesIO
from pathlib import Path
import sys
import unittest

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from swimtimeline.badges import (  # noqa: E402
    CARD_FOOTER_FRAC,
    CARD_H,
    CARD_HEADER_FRAC,
    CARD_TABLE_GAP_FRAC,
    MAROON,
    NAVY,
    badge_event_name,
    cards_for_timeline,
    combine_gender_pairs,
    events_by_session,
    events_combine,
    num_column_fraction,
    parse_event_name,
    render_cards_pdf,
    row_accent_colors,
)
from swimtimeline.extract import extract_text_pages, parse_timeline  # noqa: E402

AZ_LC_DIR = ROOT / "meets/2026-az-lc-age-group-state/input"
AZ_LC_TIMELINE = AZ_LC_DIR / "age-group-state-timeline.pdf"
AZ_LC_PSYCH = AZ_LC_DIR / "age-group-state-psych-sheet.pdf"
WZAG_TIMELINE = ROOT / "meets/2026-wzag-championships-boise/input/wzag timelines v4.pdf"
AZ_SC_TIMELINE = ROOT / "meets/2026-az-sc-age-group-state/input/timeline.pdf"
# 2026 Cummins Invitational -- single day, single all-ages session, no flyer, no psych sheet.
# Real officials feedback: none of its 22 events carry an age-qualifier phrase at all ("Boys 200
# Medley Relay"), which used to make _EVENT_NAME_RE fail to match ANY of them -- so none of its 11
# real Boys/Girls pairs combined, leaving 22 rows pinned at draw_card's 5.0pt font floor. See
# CumminsCombinedPairsFontSizeTest.
CUMMINS_TIMELINE = ROOT / "meets/2026-cummins-invitational/input/2026-cummins-invitational-timeline.pdf"


def page_text(pdf_bytes: bytes, index: int = 0) -> str:
    return PdfReader(BytesIO(pdf_bytes)).pages[index].extract_text() or ""


def session_events(timeline: Path, session: str):
    _name, _sessions, events = parse_timeline(timeline)
    return events_by_session(events)[session]


class PairingRuleTest(unittest.TestCase):
    """events_combine(), against real event pairs from real timelines."""

    @classmethod
    def setUpClass(cls):
        cls.events = {event.event_number: event for event in session_events(AZ_LC_TIMELINE, "6")}
        cls.wzag2 = {event.event_number: event for event in session_events(WZAG_TIMELINE, "2")}

    def test_a_real_girls_boys_pair_combines(self):
        # 79 "Girls 13-14 50 Backstroke" / 80 "Boys 13-14 50 Backstroke".
        self.assertTrue(events_combine(self.events[79], self.events[80]))

    def test_the_rule_is_order_agnostic(self):
        """Every real fixture prints Girls first, but nothing in the format guarantees it, so a
        Boys-first meet must combine too rather than silently doubling its row count."""
        self.assertTrue(events_combine(self.events[80], self.events[79]))

    def test_a_different_age_group_does_not_combine(self):
        # 79/80 are 13-14 50 Back; 81/82 are the 11-12 50 Back. Same stroke, different age group.
        self.assertFalse(events_combine(self.events[80], self.events[81]))
        self.assertFalse(events_combine(self.events[79], self.events[82]))

    def test_a_different_stroke_does_not_combine_even_at_the_same_age_and_distance(self):
        """Girls 13-14 200 Freestyle Relay vs Boys 13-14 200 Butterfly: opposite genders, same age
        group, same distance -- only the stroke differs, so only the distance_stroke check can
        reject it."""
        self.assertFalse(events_combine(self.events[73], self.events[88]))
        self.assertFalse(events_combine(self.events[74], self.events[87]))

    def test_a_different_distance_does_not_combine_even_at_the_same_age_and_stroke(self):
        """Girls 11-12 50 Freestyle vs Boys 11-12 800 Freestyle: same age group, same stroke."""
        self.assertFalse(events_combine(self.events[97], self.events[112]))
        self.assertFalse(events_combine(self.events[98], self.events[111]))

    def test_two_of_the_same_gender_do_not_combine(self):
        self.assertFalse(events_combine(self.events[79], self.events[81]))

    def test_mixed_relays_never_combine_with_anything(self):
        """WZAG session 2 ends with three real "Mixed ... Relay" events."""
        mixed = [self.wzag2[n] for n in (23, 24, 25)]
        for name in (event.event_name for event in mixed):
            self.assertIn("Mixed", name)
        self.assertFalse(events_combine(mixed[0], mixed[1]))
        self.assertFalse(events_combine(mixed[1], mixed[2]))
        self.assertFalse(events_combine(self.wzag2[22], mixed[0]))


class GreedyScanTest(unittest.TestCase):
    def test_the_42_event_session_becomes_21_clean_pairs(self):
        rows = combine_gender_pairs(session_events(AZ_LC_TIMELINE, "6"))
        self.assertEqual(len(rows), 21)
        self.assertTrue(all(len(row) == 2 for row in rows))

    def test_a_partially_pairable_session_keeps_its_leftovers_as_single_rows(self):
        """WZAG session 2: 11 real Girls/Boys pairs, then three Mixed relays that cannot pair."""
        rows = combine_gender_pairs(session_events(WZAG_TIMELINE, "2"))
        self.assertEqual(len(rows), 14)
        self.assertEqual(sum(1 for row in rows if len(row) == 2), 11)
        trailing = [row for row in rows if len(row) == 1]
        self.assertEqual([row[0].event_number for row in trailing], [23, 24, 25])

    def test_a_single_gender_session_produces_no_pairs_at_all(self):
        """AZ SC runs two pools in parallel, so each lettered session is one gender end to end."""
        for session in ("1B", "1G", "2B", "2G", "4B", "4G", "6B", "6G"):
            events = session_events(AZ_SC_TIMELINE, session)
            rows = combine_gender_pairs(events)
            self.assertEqual(len(rows), len(events), session)
            self.assertTrue(all(len(row) == 1 for row in rows), session)

    def test_nothing_is_reordered_and_no_event_is_lost_or_duplicated(self):
        for timeline, session in ((AZ_LC_TIMELINE, "6"), (WZAG_TIMELINE, "2"), (AZ_SC_TIMELINE, "3")):
            events = session_events(timeline, session)
            flattened = [event for row in combine_gender_pairs(events) for event in row]
            self.assertEqual([e.event_number for e in flattened], [e.event_number for e in events])


class CombinedRowContentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _name, cards, _highlights = cards_for_timeline(AZ_LC_TIMELINE)
        cls.card = {card.session_number: card for card in cards}["6"]
        cls.by_num = {row["num"]: row for row in cls.card.events}

    def test_both_event_numbers_and_both_heat_counts_in_document_order(self):
        """95/96 really do have different heat counts (4 and 5, in that order) -- the column is
        positional, so the order has to match the # column exactly."""
        row = self.by_num["95/96"]
        self.assertEqual(row["nums"], [95, 96])
        self.assertEqual(row["heats"], "4/5")

    def test_the_name_is_printed_once_without_a_gender_word(self):
        row = self.by_num["95/96"]
        self.assertEqual(row["name"], "10&U 50 Free")
        self.assertNotIn("Girls", row["name"])
        self.assertNotIn("Boys", row["name"])

    def test_the_time_is_the_earlier_of_the_two_starts(self):
        """79/80 share 6 heats each but start 8 minutes apart (9:08a / 9:16a). The time column
        tells an official when to be at their post, so the LATER time would risk missing the first
        race of the pair."""
        pair = [e for e in session_events(AZ_LC_TIMELINE, "6") if e.event_number in (79, 80)]
        self.assertEqual(
            sorted(e.start.strftime("%H:%M") for e in pair), ["09:08", "09:16"]
        )
        self.assertEqual(self.by_num["79/80"]["time"], "9:08a")

    def test_the_session_still_reports_every_real_event(self):
        """Combining rows must not quietly shrink the meet: the officials page's EVENTS column and
        its "N sessions - M events" summary both read event_count."""
        self.assertEqual(self.card.event_count, 42)
        self.assertEqual(self.card.row_count, 21)

    def test_an_age_qualifier_still_appears_when_the_session_is_mixed(self):
        # Session 6 mixes age groups, so each row keeps its own tag -- just once now, not twice.
        self.assertIsNone(self.card.age_qualifier)
        self.assertTrue(self.by_num["95/96"]["name"].startswith("10&U"))

    def test_badge_event_name_drops_gender_only_when_asked(self):
        raw = "Girls 10 & Under 50 Freestyle"
        self.assertEqual(badge_event_name(raw, include_age=True), "Girls 10&U 50 Free")
        self.assertEqual(
            badge_event_name(raw, include_age=True, include_gender=False), "10&U 50 Free"
        )
        self.assertEqual(badge_event_name(raw, include_age=False, include_gender=False), "50 Free")


class AccentBarTest(unittest.TestCase):
    def test_a_combined_row_gets_both_genders_colours_in_order(self):
        _name, cards, _highlights = cards_for_timeline(AZ_LC_TIMELINE)
        row = {r["num"]: r for r in {c.session_number: c for c in cards}["6"].events}["95/96"]
        self.assertEqual(row["genders"], ["Girls", "Boys"])
        self.assertEqual(row_accent_colors(row), [MAROON, NAVY])

    def test_a_single_row_keeps_exactly_one_colour_from_its_name(self):
        _name, cards, _highlights = cards_for_timeline(AZ_SC_TIMELINE)
        boys_card = {card.session_number: card for card in cards}["1B"]
        for row in boys_card.events:
            self.assertEqual(len(row["nums"]), 1)
            self.assertEqual(row_accent_colors(row), [NAVY], row["name"])

    def test_an_unparseable_name_still_falls_back_to_the_name_based_colour(self):
        row = {"name": "Something Unparseable", "nums": [1], "genders": []}
        self.assertEqual(row_accent_colors(row), [MAROON])


class CombinedRowHighlightTest(unittest.TestCase):
    """Combining a row must never hide a watched swimmer."""

    @classmethod
    def setUpClass(cls):
        _name, cards, cls.highlights = cards_for_timeline(
            AZ_LC_TIMELINE, psych_pdf=AZ_LC_PSYCH, swimmer_names=["Cova, Mila"]
        )
        cls.card = {card.session_number: card for card in cards}["6"]
        cls.by_num = {row["num"]: row for row in cls.card.events}

    def test_only_one_half_of_the_pair_is_hers_yet_the_row_still_stars(self):
        """Event 97 is Cova's ("Girls 11-12 50 Freestyle"); 98 is the Boys half and is not."""
        self.assertIn(97, self.highlights.event_numbers)
        self.assertNotIn(98, self.highlights.event_numbers)
        row = self.by_num["97/98"]
        self.assertEqual(row["nums"], [97, 98])
        self.assertTrue(row["highlight"])

    def test_the_starred_list_names_only_the_event_that_is_really_hers(self):
        """The page's STARRED column would otherwise claim she is in a race she never entered."""
        self.assertEqual(self.by_num["97/98"]["starred_nums"], [97])
        self.assertEqual(sorted(self.card.highlighted_event_numbers), [97, 111])

    def test_rows_with_neither_half_watched_are_untouched(self):
        self.assertFalse(self.by_num["95/96"]["highlight"])
        self.assertEqual(self.by_num["95/96"]["starred_nums"], [])

    def test_a_watched_swimmer_on_the_SECOND_half_also_stars_the_row(self):
        """The rule is EITHER half, not the first half.

        Cova is the Girls (first) half of every pair she is in, so she cannot prove this direction.
        Beltran is a real, unambiguously-matched swimmer in the same psych sheet entered in events
        104 and 112 -- the Boys halves of pairs 103/104 and 111/112 -- which does.
        """
        _name, cards, highlights = cards_for_timeline(
            AZ_LC_TIMELINE, psych_pdf=AZ_LC_PSYCH, swimmer_names=["Beltran"]
        )
        self.assertEqual(highlights.warnings, [])
        card = {card.session_number: card for card in cards}["6"]
        by_num = {row["num"]: row for row in card.events}
        for pair, boys_half in (("103/104", 104), ("111/112", 112)):
            row = by_num[pair]
            self.assertEqual(row["nums"][1], boys_half, pair)
            self.assertNotIn(row["nums"][0], highlights.event_numbers, pair)
            self.assertTrue(row["highlight"], pair)
            self.assertEqual(row["starred_nums"], [boys_half], pair)
        # And it reaches the rendered card as a real star.
        stream = (
            PdfReader(BytesIO(render_cards_pdf([card])))
            .pages[0]
            .get_contents()
            .get_data()
            .decode("latin-1")
        )
        self.assertEqual(stream.count("h\nf*\n"), 2)


class RenderedCombinedCardTest(unittest.TestCase):
    """Read the text back out of the generated PDF -- not the row dicts."""

    @classmethod
    def setUpClass(cls):
        _name, cards, _highlights = cards_for_timeline(
            AZ_LC_TIMELINE, psych_pdf=AZ_LC_PSYCH, swimmer_names=["Cova, Mila"]
        )
        cls.card = {card.session_number: card for card in cards}["6"]
        cls.text = page_text(render_cards_pdf([cls.card]))

    def test_the_combined_numbers_heats_and_single_time_really_print(self):
        for fragment in ("79/80", "6/6", "9:08a", "95/96", "4/5", "10&U 50 Free"):
            self.assertIn(fragment, self.text, fragment)

    def test_the_later_half_of_a_pairs_start_time_is_not_printed(self):
        # 79/80 start 9:08a and 9:16a; only the earlier one belongs on the card.
        self.assertNotIn("9:16a", self.text)

    def test_no_row_prints_a_gender_word_when_every_row_is_combined(self):
        self.assertNotIn("Girls", self.text)
        self.assertNotIn("Boys", self.text)

    def test_all_21_rows_and_all_42_event_numbers_are_present(self):
        for number in range(73, 115):
            self.assertRegex(self.text, rf"\b{number}\b", f"event {number} missing")

    def test_the_card_is_still_exactly_one_page_at_card_size(self):
        reader = PdfReader(BytesIO(render_cards_pdf([self.card])))
        self.assertEqual(len(reader.pages), 1)
        self.assertAlmostEqual(float(reader.pages[0].mediabox.width), 144.0, places=2)
        self.assertAlmostEqual(float(reader.pages[0].mediabox.height), 216.0, places=2)

    def test_a_partially_paired_session_prints_its_unpaired_mixed_relays_verbatim(self):
        _name, cards, _highlights = cards_for_timeline(WZAG_TIMELINE)
        text = page_text(render_cards_pdf([{c.session_number: c for c in cards}["2"]]))
        self.assertIn("1/2", text)  # a combined pair
        for number in ("23", "24", "25"):
            self.assertIn(number, text)
        # The unpaired relays keep their own gender word, so "Mixed" still reaches the card.
        self.assertIn("Mixed", text)


class NoPairingRegressionTest(unittest.TestCase):
    """The single-gender lettered cards must be untouched by this feature."""

    def test_a_single_gender_card_keeps_one_row_per_event_and_the_gender_word(self):
        _name, cards, _highlights = cards_for_timeline(AZ_SC_TIMELINE)
        by_session = {card.session_number: card for card in cards}
        for session, expected_events in (("2B", 16), ("2G", 16), ("6B", 21), ("6G", 21)):
            card = by_session[session]
            self.assertEqual(card.row_count, expected_events, session)
            self.assertEqual(card.event_count, expected_events, session)
            # No row combined, so no row's number is a "N/M" pair.
            for row in card.events:
                self.assertEqual(len(row["nums"]), 1, session)
                self.assertEqual(row["num"], row["nums"][0], session)
                self.assertNotIn("/", str(row["num"]), session)
            # ...and the gender word survives on the card, since these rows keep it.
            text = page_text(render_cards_pdf([card]))
            self.assertIn("Boys" if session.endswith("B") else "Girls", text, session)

    def test_a_card_with_no_combined_row_keeps_the_original_number_column_width(self):
        """The # column widening is what would perturb an unpaired card's layout, so it is gated
        on a combined row actually being present."""
        _name, cards, _highlights = cards_for_timeline(AZ_SC_TIMELINE)
        by_session = {card.session_number: card for card in cards}
        self.assertEqual(num_column_fraction(by_session["2B"].events), 0.10)
        self.assertEqual(num_column_fraction(by_session["3"].events), 0.13)

    def test_the_widening_buckets_are_exactly_the_four_documented_cases(self):
        plain = [{"nums": [1], "highlight": False}]
        starred = [{"nums": [1], "highlight": True}]
        combined = [{"nums": [1, 2], "highlight": False}]
        both = [{"nums": [1, 2], "highlight": True}]
        self.assertEqual(num_column_fraction(plain), 0.10)
        self.assertEqual(num_column_fraction(starred), 0.145)
        self.assertEqual(num_column_fraction(combined), 0.13)
        self.assertEqual(num_column_fraction(both), 0.185)


class CumminsCombinedPairsFontSizeTest(unittest.TestCase):
    """The real bug real officials reported: Cummins Invitational is an all-ages meet, so none of
    its 22 event names carry an age-qualifier phrase at all ("Boys 200 Medley Relay", nothing
    between gender and the distance number). _EVENT_NAME_RE used to require at least one
    character there, so parse_event_name() returned None for every single one -- and
    events_combine() requires both names to parse, so none of its 11 real Boys/Girls pairs ever
    combined. 22 uncombined rows pinned draw_card's font at its 5.0pt floor.

    Making the age group optional fixes this the same way it fixes
    CroswhiteEmptyAgeQualifierEventShapeTest's single-gender case: these now parse, with an empty
    age_qualifier, which is all events_combine() needs to pair them.
    """

    @classmethod
    def setUpClass(cls):
        cls.meet_name, cls.sessions, cls.events = parse_timeline(CUMMINS_TIMELINE)
        cls.session_events = events_by_session(cls.events)["1"]
        _name, cards, _highlights = cards_for_timeline(CUMMINS_TIMELINE)
        cls.card = cards[0]

    def test_all_22_real_events_parse_with_an_empty_age_qualifier(self):
        self.assertEqual(len(self.session_events), 22)
        for event in self.session_events:
            parsed = parse_event_name(event.event_name)
            self.assertIsNotNone(parsed, event.event_name)
            self.assertEqual(parsed.age_qualifier, "", event.event_name)

    def test_all_11_real_boys_girls_pairs_combine(self):
        rows = combine_gender_pairs(self.session_events)
        self.assertEqual(len(rows), 11)
        self.assertTrue(all(len(row) == 2 for row in rows))
        self.assertTrue(all(events_combine(*row) for row in rows))

    def test_the_card_reports_22_real_events_but_only_11_rows(self):
        self.assertEqual(self.card.event_count, 22)
        self.assertEqual(self.card.row_count, 11)

    def test_base_font_size_clears_the_5pt_floor_once_combined(self):
        """Replicates draw_card's own base_fs formula off its shared fractions (not repeated
        literals), the same way test_event_names_still_fit_after_the_column_widens in
        test_badges.py does -- once for the OLD uncombined row count (still the 5.0pt floor) and
        once for the real, now-combined row count (clears it)."""
        table_h = (
            CARD_H
            - CARD_HEADER_FRAC * CARD_H
            - CARD_FOOTER_FRAC * CARD_H
            - 2 * CARD_TABLE_GAP_FRAC * CARD_H
        )

        def base_fs(row_count: int) -> float:
            row_h = table_h / (row_count + 0.62)
            return max(5.0, min(8.3, row_h * 0.5))

        # Before the fix: 22 uncombined rows, pinned at the floor.
        self.assertEqual(base_fs(self.card.event_count), 5.0)
        # After: 11 combined rows, clearing it by almost 2pt.
        after = base_fs(self.card.row_count)
        self.assertAlmostEqual(after, 6.97, places=2)
        self.assertGreater(after, 6.5)
        self.assertLess(after, 7.5)

    def test_a_combined_row_actually_renders_readable_text_on_the_card(self):
        text = page_text(render_cards_pdf([self.card]))
        # Events 1/2: "Boys 200 Medley Relay" / "Girls 200 Medley Relay".
        self.assertIn("1/2", text)
        self.assertIn("200 Medley Relay", text)
        self.assertNotIn("Girls", text)  # dropped on every row: all 22 events combined
        self.assertNotIn("Boys", text)

    def test_no_row_carries_a_stray_blank_age_tag(self):
        """abbreviate_age_qualifier("") is itself "" -- badge_event_name()'s piece filter must
        drop it rather than leaving a stray leading/double space in the row name."""
        for row in self.card.events:
            self.assertFalse(row["name"].startswith(" "), row["name"])
            self.assertNotIn("  ", row["name"], row["name"])


class UnparseableEventNameFallbackTest(unittest.TestCase):
    """A genuinely irregular name -- not just missing an age qualifier -- must still fail to
    parse and fall through to an uncombined row, rather than the optional-age group accidentally
    making the regex swallow something it shouldn't.
    """

    def test_a_name_with_no_recognized_gender_word_does_not_parse(self):
        self.assertIsNone(parse_event_name("Exhibition 200 Freestyle"))
        self.assertIsNone(parse_event_name("200 Freestyle"))  # no gender word at all

    def test_a_name_with_no_distance_number_does_not_parse(self):
        self.assertIsNone(parse_event_name("Boys Medley Relay"))

    def test_an_unparseable_name_never_combines_with_a_real_neighbour(self):
        from swimtimeline.extract import TimelineEvent

        real = session_events(CUMMINS_TIMELINE, "1")[0]
        irregular = TimelineEvent(
            event_number=999,
            event_name="Exhibition 200 Freestyle",
            round_name="Finals",
            session_number=real.session_number,
            session_name=real.session_name,
            date=real.date,
            start=real.start,
            end=real.end,
            entries=1,
            heats=1,
            facility=None,
        )
        self.assertFalse(events_combine(real, irregular))
        self.assertFalse(events_combine(irregular, real))
        rows = combine_gender_pairs([real, irregular])
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(len(row) == 1 for row in rows))


if __name__ == "__main__":
    unittest.main()
