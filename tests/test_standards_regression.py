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

import json

from swimtimeline.standards import (
    AZSI_SENIOR_STANDARDS,
    AZSI_STANDARDS,
    MOTIVATIONAL_STANDARDS,
    NATIONAL_MEETS,
    NATIONAL_STANDARDS_PATH,
    SECTIONAL_MEETS,
    SECTIONAL_STANDARDS_PATH,
    TIER_ORDER,
    parse_time,
)


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
    """Spot-checks for the AZSI (Arizona LSC) catalog against the official 2025-2026 AZSI Age
    Group State and Regional Qualifying Time Standards (docs/Sources/azsi-*-2025-2026.pdf),
    now broadened from 11-12 Girls LCM to all three courses (SCY/SCM/LCM), both genders, and
    the three Age Group bands (10 & under, 11-12, 13-14). Values cross-checked against the raw
    PDFs. The 15-18 Senior standards live in a separate catalog (AZSI_SENIOR_STANDARDS) and are
    intentionally absent from this Age Group catalog.
    """

    def cut(self, course, gender, band, event):
        return AZSI_STANDARDS[course][gender][band][event]

    # --- 11-12 Girls LCM: the previously-corrected slice must survive the broadening ---
    def test_girls_lcm_11_12_100_free_stays_corrected(self):
        # Last session's fix: was the MEN cut 1:11.99; correct WOMEN cut is 1:09.89.
        self.assertEqual(self.cut("LCM", "girls", "11-12", "100 free"), {"state": "1:09.89", "regional": "1:25.49"})

    def test_girls_lcm_11_12_400_im_stays_corrected(self):
        self.assertEqual(self.cut("LCM", "girls", "11-12", "400 im"), {"state": "6:27.69", "regional": "7:16.29"})

    def test_girls_lcm_11_12_50_free_stays_corrected(self):
        self.assertEqual(self.cut("LCM", "girls", "11-12", "50 free"), {"state": "31.99", "regional": "38.19"})

    # --- New dimensions (Boys, SCY, SCM, 10 & under, 13-14) ---
    def test_boys_scy_13_14_100_free(self):
        self.assertEqual(self.cut("SCY", "boys", "13-14", "100 free"), {"state": "55.09", "regional": "1:06.29"})

    def test_boys_scm_13_14_200_im(self):
        self.assertEqual(self.cut("SCM", "boys", "13-14", "200 im"), {"state": "2:31.99", "regional": "2:59.69"})

    def test_boys_lcm_10_and_under_50_free(self):
        self.assertEqual(self.cut("LCM", "boys", "10 & under", "50 free"), {"state": "37.19", "regional": "46.69"})

    def test_girls_scy_11_12_100_free(self):
        self.assertEqual(self.cut("SCY", "girls", "11-12", "100 free"), {"state": "1:01.99", "regional": "1:13.59"})

    def test_events_newly_added_within_11_12_girls_lcm(self):
        # Events the old 13-event table omitted; now present.
        self.assertEqual(self.cut("LCM", "girls", "11-12", "1500 free"), {"state": "22:47.09", "regional": "25:54.89"})
        self.assertIn("200 back", AZSI_STANDARDS["LCM"]["girls"]["11-12"])

    # --- Structural invariants ---
    def test_covers_three_courses_two_genders_three_age_group_bands(self):
        expected_bands = {"10 & under", "11-12", "13-14"}
        for course in ("SCY", "SCM", "LCM"):
            self.assertIn(course, AZSI_STANDARDS)
            for gender in ("girls", "boys"):
                self.assertEqual(set(AZSI_STANDARDS[course][gender]), expected_bands)

    def test_senior_bands_are_absent_out_of_scope(self):
        for course in AZSI_STANDARDS.values():
            for gender in course.values():
                self.assertNotIn("15-16", gender)
                self.assertNotIn("17-18", gender)

    def test_state_cut_is_faster_than_regional_for_every_cell(self):
        # Arizona rule: meeting the State cut removes Regional eligibility (swim up to State),
        # which only holds if State is the faster time everywhere.
        for course, genders in AZSI_STANDARDS.items():
            for gender, bands in genders.items():
                for band, events in bands.items():
                    for event, cuts in events.items():
                        where = f"{course} {gender} {band} {event}"
                        state, regional = parse_time(cuts["state"]), parse_time(cuts["regional"])
                        self.assertIsNotNone(state, where)
                        self.assertIsNotNone(regional, where)
                        self.assertLess(state, regional, f"{where}: state {cuts['state']} not faster than regional {cuts['regional']}")


class AzsiSeniorStandardsValueTest(unittest.TestCase):
    """Spot-checks for the AZSI Senior catalog against the official 2025-2026 AZSI Senior State
    and Regional Qualifying Time Standards (docs/Sources/azsi-senior-*-2025-2026.pdf). Unlike
    Age Group, Senior has no age bands: one cut per course/gender/event. Values cross-checked
    against the raw PDFs; gender is assigned in extraction by a speed vote (men's cuts faster).
    """

    def cut(self, course, gender, event):
        return AZSI_SENIOR_STANDARDS[course][gender][event]

    def test_girls_scy_50_free(self):
        self.assertEqual(self.cut("SCY", "girls", "50 free"), {"state": "25.49", "regional": "32.29"})

    def test_boys_scy_50_free_is_faster_than_girls(self):
        # Gender-assignment guard: the boys block is the faster one.
        self.assertEqual(self.cut("SCY", "boys", "50 free"), {"state": "23.39", "regional": "28.99"})
        self.assertLess(
            parse_time(self.cut("SCY", "boys", "50 free")["state"]),
            parse_time(self.cut("SCY", "girls", "50 free")["state"]),
        )

    def test_boys_scy_100_free(self):
        self.assertEqual(self.cut("SCY", "boys", "100 free"), {"state": "50.39", "regional": "1:03.29"})

    def test_girls_lcm_400_im(self):
        self.assertEqual(self.cut("LCM", "girls", "400 im"), {"state": "5:34.59", "regional": "6:39.29"})

    def test_boys_lcm_50_free(self):
        self.assertEqual(self.cut("LCM", "boys", "50 free"), {"state": "26.69", "regional": "33.39"})

    def test_scm_800_free_source_quirk_preserved(self):
        # Lone cell where the men's cut is slower than the women's on the State sheet; the
        # majority-vote gender assignment still places it correctly. Guards against a
        # "men always faster" shortcut silently flipping this event's genders.
        self.assertEqual(self.cut("SCM", "girls", "800 free")["state"], "9:50.49")
        self.assertEqual(self.cut("SCM", "boys", "800 free")["state"], "9:54.69")

    def test_combined_distance_free_keys_are_per_course(self):
        # "400/500", "800/1000", "1500/1650" resolve to yards distances under SCY and meters
        # distances under LCM/SCM, exactly as the Age Group catalog keys them.
        self.assertIn("500 free", AZSI_SENIOR_STANDARDS["SCY"]["girls"])
        self.assertIn("1650 free", AZSI_SENIOR_STANDARDS["SCY"]["girls"])
        self.assertIn("400 free", AZSI_SENIOR_STANDARDS["LCM"]["girls"])
        self.assertIn("1500 free", AZSI_SENIOR_STANDARDS["SCM"]["girls"])


class AzsiSeniorStandardsStructureTest(unittest.TestCase):
    def test_covers_three_courses_two_genders_no_age_bands(self):
        for course in ("SCY", "SCM", "LCM"):
            self.assertIn(course, AZSI_SENIOR_STANDARDS)
            for gender in ("girls", "boys"):
                events = AZSI_SENIOR_STANDARDS[course][gender]
                self.assertEqual(len(events), 17, f"{course} {gender}")
                # Values sit directly under the event (no band level): each is a {state,
                # regional} dict, never a nested band -> event mapping.
                for event, cell in events.items():
                    self.assertEqual(set(cell), {"state", "regional"}, f"{course} {gender} {event}")

    def test_no_age_band_keys_leaked_in(self):
        for course in AZSI_SENIOR_STANDARDS.values():
            for gender in course.values():
                for band in ("10 & under", "11-12", "13-14", "15-16", "17-18"):
                    self.assertNotIn(band, gender)

    def test_catalog_totals(self):
        cells = sum(len(events) for genders in AZSI_SENIOR_STANDARDS.values() for events in genders.values())
        self.assertEqual(cells, 102)  # 17 events x 3 courses x 2 genders

    def test_state_cut_is_faster_than_regional_for_every_cell(self):
        for course, genders in AZSI_SENIOR_STANDARDS.items():
            for gender, events in genders.items():
                for event, cuts in events.items():
                    where = f"{course} {gender} senior {event}"
                    state, regional = parse_time(cuts["state"]), parse_time(cuts["regional"])
                    self.assertIsNotNone(state, where)
                    self.assertIsNotNone(regional, where)
                    self.assertLess(state, regional, f"{where}: state {cuts['state']} not faster than regional {cuts['regional']}")


class SectionalStandardsValueTest(unittest.TestCase):
    """Spot-checks for the Speedo Sectional catalog against the official 2026 documents
    (docs/Sources/sectional-*-standards-2026.pdf). Two distinct meets, no age bands, one
    qualifying cut per event/course/gender (no bonus), relays included. Values cross-checked
    against the raw PDFs.
    """

    def setUp(self):
        self.meets = {m["key"]: m for m in SECTIONAL_MEETS}
        self.raw = json.loads(SECTIONAL_STANDARDS_PATH.read_text(encoding="utf-8"))

    def cut(self, meet_key, course, gender, event):
        return self.meets[meet_key]["standards"][course][gender][event]

    def test_both_meets_present_and_distinct(self):
        self.assertEqual(set(self.meets), {"four_corners_spring", "western_region_summer"})
        fc = self.raw["meets"]["four_corners_spring"]
        wr = self.raw["meets"]["western_region_summer"]
        # Distinct identity: different names, dates, venues, qualifying periods -- not merged.
        self.assertNotEqual(fc["name"], wr["name"])
        self.assertNotEqual(fc["dates"], wr["dates"])
        self.assertNotEqual(fc["location"], wr["location"])
        self.assertNotEqual(fc["qualifying_period"], wr["qualifying_period"])
        self.assertEqual(fc["dates"], "2026-03-26 through 2026-03-29")
        self.assertEqual(wr["dates"], "2026-07-16 through 2026-07-19")

    def test_four_corners_individual_values(self):
        self.assertEqual(self.cut("four_corners_spring", "SCY", "girls", "50 free"), {"qualifying": "24.99"})
        self.assertEqual(self.cut("four_corners_spring", "SCY", "boys", "50 free"), {"qualifying": "22.41"})
        self.assertEqual(self.cut("four_corners_spring", "LCM", "girls", "400 im"), {"qualifying": "5:21.68"})

    def test_western_region_individual_values(self):
        self.assertEqual(self.cut("western_region_summer", "SCY", "girls", "50 free"), {"qualifying": "24.99"})
        self.assertEqual(self.cut("western_region_summer", "SCM", "boys", "200 free"), {"qualifying": "1:56.48"})

    def test_relay_events_are_captured(self):
        self.assertEqual(self.cut("four_corners_spring", "SCY", "girls", "200 free relay"), {"qualifying": "1:44.69"})
        self.assertEqual(self.cut("four_corners_spring", "SCY", "girls", "400 medley relay"), {"qualifying": "4:09.49"})

    def test_shared_events_are_identical_across_the_two_meets(self):
        # For 2026 the two meets publish the same cutoffs on every shared event; this pins that
        # so a future single-meet re-extraction that drifts one of them is caught.
        fc = self.meets["four_corners_spring"]["standards"]
        wr = self.meets["western_region_summer"]["standards"]
        for course in ("SCY", "SCM", "LCM"):
            for gender in ("girls", "boys"):
                shared = set(fc[course][gender]) & set(wr[course][gender])
                for event in shared:
                    self.assertEqual(fc[course][gender][event], wr[course][gender][event], f"{course} {gender} {event}")

    def test_only_summer_meet_lists_the_50s_of_stroke(self):
        fc = self.meets["four_corners_spring"]["standards"]["SCY"]["girls"]
        wr = self.meets["western_region_summer"]["standards"]["SCY"]["girls"]
        for event in ("50 back", "50 breast", "50 fly"):
            self.assertNotIn(event, fc, f"Four Corners unexpectedly lists {event}")
            self.assertIn(event, wr, f"Western Region missing {event}")
        self.assertEqual(wr["50 back"], {"qualifying": "27.29"})


class SectionalStandardsStructureTest(unittest.TestCase):
    def setUp(self):
        self.meets = {m["key"]: m for m in SECTIONAL_MEETS}

    def test_event_counts_per_meet(self):
        # Four Corners: 19 events; Western Region Summer: 22 (adds the three 50s of stroke).
        for course in ("SCY", "SCM", "LCM"):
            for gender in ("girls", "boys"):
                self.assertEqual(len(self.meets["four_corners_spring"]["standards"][course][gender]), 19)
                self.assertEqual(len(self.meets["western_region_summer"]["standards"][course][gender]), 22)

    def test_every_cell_is_a_single_qualifying_time_no_bonus(self):
        for meet in self.meets.values():
            for course, genders in meet["standards"].items():
                for gender, events in genders.items():
                    for event, cell in events.items():
                        where = f"{meet['key']} {course} {gender} {event}"
                        self.assertEqual(set(cell), {"qualifying"}, where)  # no "bonus" tier
                        self.assertIsNotNone(parse_time(cell["qualifying"]), where)

    def test_no_age_band_keys(self):
        for meet in self.meets.values():
            for course, genders in meet["standards"].items():
                for gender in ("girls", "boys"):
                    self.assertEqual(set(genders[gender]) & {"10 & under", "11-12", "13-14", "15-16", "17-18"}, set())


class NationalStandardsValueTest(unittest.TestCase):
    """Spot-checks for the national elite catalog (data/national_standards.json, generated by
    scripts/extract_national_standards.py) against all five official 2026 documents. Futures and
    Toyota Nationals use a column-major PDF table (unlike AZSI/Sectional's row-major layout)
    split into two age brackets covering every age with no gap (18 & Under / 19 & Over). Summer
    Juniors, Winter Juniors, and U.S. Open are flat with a separate Bonus tier -- Summer/Winter
    Juniors from PDF, but Winter Juniors and U.S. Open were sourced from swimstandards.com (their
    official PDFs have no extractable text) and use a DIFFERENT column order (Women/SCY,
    Women/LCM, Men/SCY, Men/LCM, confirmed from the site's own visible headers) than the PDF
    meets (Women/SCY, Women/LCM, Men/LCM, Men/SCY). Only two courses are covered (SCY/LCM, no
    SCM). Values cross-checked against the raw PDFs / scraped pages.
    """

    def setUp(self):
        self.meets = {m["key"]: m for m in NATIONAL_MEETS}
        self.raw = json.loads(NATIONAL_STANDARDS_PATH.read_text(encoding="utf-8"))

    def cut(self, meet_key, course, gender, event, bracket=None):
        node = self.meets[meet_key]["standards"][course][gender]
        return node[bracket][event] if bracket else node[event]

    def test_futures_age_brackets_and_values(self):
        self.assertEqual(set(self.meets["futures"]["standards"]["SCY"]["girls"]), {"18 & Under", "19 & Over"})
        self.assertEqual(self.cut("futures", "SCY", "girls", "50 free", "18 & Under"), {"qualifying": "23.89"})
        self.assertEqual(self.cut("futures", "SCY", "girls", "50 free", "19 & Over"), {"qualifying": "22.99"})
        # LCM relay is real for the 18 & Under bracket; SCY has no relay standard (source "x").
        self.assertEqual(self.cut("futures", "LCM", "girls", "4x100 free relay", "18 & Under"), {"qualifying": "4:04.29"})
        self.assertNotIn("4x100 free relay", self.meets["futures"]["standards"]["SCY"]["girls"]["18 & Under"])

    def test_toyota_nationals_age_brackets_and_values(self):
        self.assertEqual(set(self.meets["toyota_nationals"]["standards"]["SCY"]["girls"]), {"18 & Under", "19 & Over"})
        self.assertEqual(self.cut("toyota_nationals", "SCY", "girls", "50 free", "19 & Over"), {"qualifying": "22.19"})
        self.assertEqual(self.cut("toyota_nationals", "SCY", "girls", "50 free", "18 & Under"), {"qualifying": "22.79"})
        # Toyota Nationals' table has no relay events at all (unlike Futures/Summer Juniors).
        self.assertNotIn("4x100 free relay", self.meets["toyota_nationals"]["standards"]["LCM"]["girls"]["18 & Under"])

    def test_summer_juniors_flat_with_bonus_tier(self):
        self.assertEqual(self.cut("summer_juniors", "SCY", "girls", "50 free"), {"qualifying": "22.99", "bonus": "23.89"})
        self.assertEqual(self.cut("summer_juniors", "SCY", "boys", "400 im"), {"qualifying": "3:52.69", "bonus": "4:06.99"})
        # Footnote-confirmed: the 4x50 relay standard equals the corresponding 4x100 relay time.
        self.assertEqual(self.cut("summer_juniors", "LCM", "girls", "4x50 free relay"), {"qualifying": "3:55.69"})
        self.assertEqual(self.cut("summer_juniors", "LCM", "girls", "4x100 free relay"), {"qualifying": "3:55.69"})
        # SCY has no 4x50/4x100 relay bonus standards -- the source lists "x" there.
        self.assertNotIn("4x50 free relay", self.meets["summer_juniors"]["standards"]["SCY"]["girls"])

    def test_only_two_courses_no_scm(self):
        for meet in self.meets.values():
            self.assertEqual(set(meet["standards"]), {"SCY", "LCM"})

    def test_qualifying_faster_than_bonus_for_every_flat_bonus_meet(self):
        for meet_key in ("summer_juniors", "winter_juniors", "toyota_us_open"):
            events = self.meets[meet_key]["standards"]
            for course, genders in events.items():
                for gender, cells in genders.items():
                    for event, cell in cells.items():
                        if "bonus" in cell:
                            where = f"{meet_key} {course} {gender} {event}"
                            self.assertLessEqual(
                                parse_time(cell["qualifying"]), parse_time(cell["bonus"]), where
                            )

    def test_all_five_meets_present_none_left_not_handled(self):
        # Winter Juniors and U.S. Open were originally blocked (vector-outlined PDFs, no OCR
        # tooling); once sourced from swimstandards.com, both must appear here and 'not_handled'
        # must be empty -- not silently dropped, not left half-wired.
        self.assertEqual(set(self.meets), {
            "futures", "toyota_nationals", "summer_juniors", "winter_juniors", "toyota_us_open",
        })
        self.assertEqual(self.raw["source"]["not_handled"], [])

    def test_winter_juniors_flat_with_bonus_and_relay_naming(self):
        self.assertEqual(self.cut("winter_juniors", "SCY", "girls", "50 free"), {"qualifying": "23.29", "bonus": "23.89"})
        self.assertEqual(self.cut("winter_juniors", "LCM", "boys", "400 im"), {"qualifying": "4:35.89", "bonus": "4:42.39"})
        # Relays are named by TOTAL distance in the source ("400 Free Relay" = a 4x100), not by
        # leg count -- confirms the total-distance-to-leg-count conversion (400/4=100).
        self.assertEqual(self.cut("winter_juniors", "SCY", "girls", "4x100 free relay"), {"qualifying": "3:27.49"})
        self.assertEqual(self.cut("winter_juniors", "SCY", "girls", "4x200 free relay"), {"qualifying": "7:32.79"})
        self.assertEqual(self.cut("winter_juniors", "SCY", "girls", "4x100 medley relay"), {"qualifying": "3:47.79"})
        # Source lists "200 Free Relay"/"200 Medley Relay" as "-" (not contested) -- must be absent.
        self.assertNotIn("4x50 free relay", self.meets["winter_juniors"]["standards"]["SCY"]["girls"])
        self.assertNotIn("4x50 medley relay", self.meets["winter_juniors"]["standards"]["SCY"]["girls"])
        # No relay has a Bonus value in the source.
        self.assertNotIn("bonus", self.cut("winter_juniors", "SCY", "girls", "4x100 free relay"))

    def test_winter_juniors_age_ceiling(self):
        self.assertEqual(self.meets["winter_juniors"]["age_ceiling"], 18)
        self.assertIsNone(self.meets["winter_juniors"]["bonus_age_ceiling"])

    def test_us_open_flat_with_bonus_no_relays_has_50s_of_stroke(self):
        self.assertEqual(self.cut("toyota_us_open", "SCY", "girls", "50 free"), {"qualifying": "22.49", "bonus": "22.99"})
        self.assertEqual(self.cut("toyota_us_open", "SCY", "boys", "400 im"), {"qualifying": "3:47.69", "bonus": "3:52.69"})
        self.assertEqual(self.cut("toyota_us_open", "LCM", "boys", "400 im"), {"qualifying": "4:28.89", "bonus": "4:33.09"})
        self.assertEqual(self.cut("toyota_us_open", "SCY", "girls", "50 back"), {"qualifying": "24.39", "bonus": "25.09"})
        # Unlike Winter Juniors, U.S. Open has no relay events at all.
        for gender in ("girls", "boys"):
            self.assertFalse(any("relay" in e for e in self.meets["toyota_us_open"]["standards"]["SCY"][gender]))

    def test_us_open_qualifying_tier_unchanged_from_2025(self):
        # Cross-check requested: swimstandards.com's own 2025-vs-2026 comparison page shows every
        # Qualifying Times row identical between years (only Bonus has small changes) -- confirmed
        # by hand against the comparison page and recorded in this meet's source_note.
        self.assertIn("unchanged from", self.raw["meets"]["toyota_us_open"]["source_note"])

    def test_us_open_bonus_is_18_and_under_only_unlike_qualifying(self):
        # The one meet where Bonus has its OWN age gate, independent of (and stricter than)
        # Qualifying: confirmed from the source page's text "18&U bonus standards".
        self.assertIsNone(self.meets["toyota_us_open"]["age_ceiling"])
        self.assertEqual(self.meets["toyota_us_open"]["bonus_age_ceiling"], 18)


class NationalLookupTest(unittest.TestCase):
    """lookup()'s national_summary line: NOT state-scoped (unlike AZSI/Sectional), age-bracket
    boundary at 18/19 for Futures/Toyota Nationals (covering every age with no gap), and the real
    18-and-under entry ceiling for Summer Juniors.
    """

    def test_not_scoped_to_arizona(self):
        from swimtimeline.standards import lookup

        result = lookup("Girls 17-18 50 Yard Freestyle", "23.00", state="NY", age="18")
        self.assertIsNotNone(result.national_summary)
        self.assertIn("TYR Futures Championships", result.national_summary)
        # The AZSI/Sectional layers correctly stay Arizona-only for this non-AZ swimmer.
        self.assertEqual(result.lsc_summary, "LSC: standards not configured for this state/event")
        self.assertIsNone(result.sectional_summary)

    def test_age_18_resolves_to_18_and_under_bracket(self):
        from swimtimeline.standards import lookup

        result = lookup("Girls 17-18 50 Yard Freestyle", "23.00", state="CA", age="18")
        self.assertIn("TYR Futures Championships (18 & Under)", result.national_summary)
        self.assertIn("Toyota National Championships (18 & Under)", result.national_summary)

    def test_age_bracket_met_shows_no_cutoff_restatement(self):
        from swimtimeline.standards import lookup

        # TYR Futures Girls 18 & Under SCY 50 Free cut is 23.89; a 23.00 seed has met it. Same
        # convention as AZSI/Sectional: "met" alone, no restated value.
        result = lookup("Girls 17-18 50 Yard Freestyle", "23.00", state="CA", age="18")
        segment = meet_segment(result.national_summary, "TYR Futures Championships (18 & Under)")
        self.assertEqual(segment, "TYR Futures Championships (18 & Under): met")

    def test_age_19_resolves_to_19_and_over_bracket(self):
        from swimtimeline.standards import lookup

        result = lookup("Women 50 Yard Freestyle", "23.00", state="CA", age="19")
        self.assertIn("TYR Futures Championships (19 & Over)", result.national_summary)
        self.assertIn("Toyota National Championships (19 & Over)", result.national_summary)

    def test_unknown_age_skips_bracket_meets_but_not_summer_juniors(self):
        from swimtimeline.standards import lookup

        result = lookup("Girls 50 Yard Freestyle", "23.00", state="CA", age=None)
        self.assertNotIn("TYR Futures Championships", result.national_summary or "")
        self.assertNotIn("Toyota National Championships", result.national_summary or "")
        self.assertIn("Speedo Summer Junior National Championships", result.national_summary)

    def test_summer_juniors_shows_for_age_18_but_not_age_19(self):
        from swimtimeline.standards import lookup

        eighteen = lookup("Girls 17-18 50 Yard Freestyle", "23.00", state="CA", age="18")
        nineteen = lookup("Women 50 Yard Freestyle", "23.00", state="CA", age="19")
        self.assertIn("Speedo Summer Junior National Championships", eighteen.national_summary)
        self.assertNotIn("Speedo Summer Junior National Championships", nineteen.national_summary or "")
        # The 19-year-old still gets a national_summary line -- Futures/Toyota Nationals cover them.
        self.assertIsNotNone(nineteen.national_summary)

    def test_summer_juniors_qualifying_met_suppresses_bonus(self):
        from swimtimeline.standards import lookup

        # SCY Girls 50 Free: qualifying 22.99, bonus 23.89. A 22.50 seed meets qualifying. A met
        # tier is never restated -- "Qualifying met" alone, same convention as AZSI.
        result = lookup("Girls 17-18 50 Yard Freestyle", "22.50", state="CA", age="17")
        segment = meet_segment(result.national_summary, "Speedo Summer Junior National Championships")
        self.assertEqual(segment, "Speedo Summer Junior National Championships: Qualifying met")

    def test_summer_juniors_bonus_met_shows_both_values(self):
        from swimtimeline.standards import lookup

        # A 23.50 seed misses qualifying (22.99) but meets bonus (23.89). Bonus's own beaten value
        # is dropped; the still-unmet Qualifying target keeps its number.
        result = lookup("Girls 17-18 50 Yard Freestyle", "23.50", state="CA", age="17")
        segment = meet_segment(result.national_summary, "Speedo Summer Junior National Championships")
        self.assertEqual(
            segment,
            "Speedo Summer Junior National Championships: Bonus met; Qualifying target 22.99",
        )

    def test_winter_juniors_shares_summer_juniors_ceiling(self):
        from swimtimeline.standards import lookup

        eighteen = lookup("Girls 17-18 50 Yard Freestyle", "23.00", state="CA", age="18")
        nineteen = lookup("Women 50 Yard Freestyle", "23.00", state="CA", age="19")
        self.assertIn("Speedo Winter Junior Championships", eighteen.national_summary)
        self.assertNotIn("Speedo Winter Junior Championships", nineteen.national_summary or "")

    def test_us_open_qualifying_is_age_open_but_bonus_is_18_and_under_only(self):
        from swimtimeline.standards import lookup

        # SCY Girls 50 Free at U.S. Open: qualifying 22.49, bonus 22.99. A 22.80 seed (slower
        # than qualifying, faster than bonus) at age 19 must NOT get the age-restricted Bonus --
        # it should show a plain Qualifying target instead, unlike an 18-year-old with the same
        # seed who gets "Bonus met".
        seventeen = lookup("Girls 17-18 50 Yard Freestyle", "22.80", state="CA", age="17")
        nineteen = lookup("Women 50 Yard Freestyle", "22.80", state="CA", age="19")
        seventeen_segment = meet_segment(seventeen.national_summary, "Toyota U.S. Open Championships")
        nineteen_segment = meet_segment(nineteen.national_summary, "Toyota U.S. Open Championships")
        self.assertEqual(seventeen_segment, "Toyota U.S. Open Championships: Bonus met; Qualifying target 22.49")
        self.assertEqual(nineteen_segment, "Toyota U.S. Open Championships: target Qualifying 22.49")

    def test_us_open_qualifying_still_shown_for_age_19(self):
        from swimtimeline.standards import lookup

        # Age-open Qualifying tier: a 19-year-old still gets a U.S. Open line at all (unlike
        # Summer/Winter Juniors, which drop entirely for 19+).
        result = lookup("Women 50 Yard Freestyle", "23.00", state="CA", age="19")
        self.assertIn("Toyota U.S. Open Championships", result.national_summary)


_ALL_NATIONAL_MEET_NAMES = [
    "TYR Futures Championships",
    "Toyota National Championships",
    "Speedo Summer Junior National Championships",
    "Speedo Winter Junior Championships",
    "Toyota U.S. Open Championships",
]


def meet_segment(national_summary, meet_name):
    """Isolate one meet's segment from a national_summary line. Segments are joined with "; ",
    but a single meet's own text can ALSO contain "; " internally (e.g. "Bonus met; Qualifying
    target..."), so a naive split("; ") would cut a segment in half. Instead, find where the next
    OTHER known meet name begins and slice up to there, so assertions about one meet aren't
    tripped up by a different meet elsewhere in the line happening to contain the same word.
    """
    start = national_summary.find(meet_name)
    if start == -1:
        return None
    rest = national_summary[start:]
    cut = len(rest)
    for other in _ALL_NATIONAL_MEET_NAMES:
        if other == meet_name:
            continue
        idx = rest.find(f"; {other}")
        if idx != -1:
            cut = min(cut, idx)
    return rest[:cut]


if __name__ == "__main__":
    unittest.main()
