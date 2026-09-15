"""Sessions labelled with a letter suffix ("1B"/"1G") must parse, not vanish.

Real fixture: meets/2026-az-sc-age-group-state/input/timeline.pdf -- a real HY-TEK Session Report
for the 2026 AZSI SC Age Group State Championship, which runs two pools in parallel and therefore
labels its sessions 1B/1G (boys pool / girls pool), 2B/2G, 4B/4G, 6B/6G, alongside plain-numbered
combined finals 3, 5 and 7. ELEVEN sessions in total, eight of them lettered.

The bug: session_header was re.compile(r"Session:\\s*(\\d+)\\s+(.+)"), which requires whitespace
immediately after the digits. "Session: 1B   Thursday Distance (BOYS POOL)" has none, so the line
never matched at all -- pending_session stayed None, the following "Day of Meet:" line was skipped,
current_session stayed None, and every event under that session was SILENTLY DROPPED. Not misfiled:
gone. The document came back with only its three plain-numbered finals sessions and 107 of its 223
events; the other 116 were lost with no warning, no error, and nothing in the output to suggest
anything was missing.

The fix treats a session identifier as a LABEL, not a quantity -- nothing does arithmetic on one --
so the token is captured whole and stored as a str end to end (SessionInfo.number,
TimelineEvent.session_number, SessionCard.session_number).

This fixture is deliberately NOT registered in data/current_meets.json: it is test data, not a
hosted meet. The HTTP test below therefore drives it through the real upload path, exactly as an
official uploading their own Session Report would.
"""

from io import BytesIO
from pathlib import Path
import json
import sys
import threading
import unittest
import urllib.error
import urllib.request

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from swimtimeline.badges import (  # noqa: E402
    cards_for_timeline,
    events_by_session,
    parse_heat_intervals,
    render_cards_pdf,
    session_sort_key,
)
from swimtimeline.extract import parse_timeline  # noqa: E402

TIMELINE = ROOT / "meets/2026-az-sc-age-group-state/input/timeline.pdf"

# The meet's own eleven session labels, in the order its program prints them.
ALL_SESSIONS = ["1B", "1G", "2B", "2G", "3", "4B", "4G", "5", "6B", "6G", "7"]
LETTERED_SESSIONS = ["1B", "1G", "2B", "2G", "4B", "4G", "6B", "6G"]
PLAIN_SESSIONS = ["3", "5", "7"]


def page_text(reader: PdfReader, index: int) -> str:
    return reader.pages[index].extract_text() or ""


class LetteredSessionsParseTest(unittest.TestCase):
    """The sessions that used to disappear entirely."""

    @classmethod
    def setUpClass(cls):
        cls.meet_name, cls.sessions, cls.events = parse_timeline(TIMELINE)
        cls.grouped = events_by_session(cls.events)

    def test_all_eleven_sessions_parse_including_every_lettered_one(self):
        self.assertEqual(list(self.sessions), ALL_SESSIONS)
        # Before the fix this was exactly ["3", "5", "7"] -- the eight lettered sessions were gone.
        for label in LETTERED_SESSIONS:
            self.assertIn(label, self.sessions, label)

    def test_the_previously_dropped_events_are_all_back(self):
        # 223 real event rows in the document; 107 parsed before the fix, so 116 were silently lost.
        self.assertEqual(len(self.events), 223)
        self.assertEqual(sum(len(v) for v in self.grouped.values()), 223)

    def test_thursdays_distance_sessions_carry_their_real_events(self):
        """1B/1G are the two Thursday distance sessions -- previously dropped in full."""
        boys = [(e.event_number, e.event_name) for e in self.grouped["1B"]]
        girls = [(e.event_number, e.event_name) for e in self.grouped["1G"]]
        self.assertEqual(
            boys,
            [
                (2, "Boys 10 & Under 500 Freestyle"),
                (4, "Boys 11-12 1650 Freestyle"),
                (6, "Boys 13-14 1650 Freestyle"),
            ],
        )
        self.assertEqual(
            girls,
            [
                (1, "Girls 10 & Under 500 Freestyle"),
                (3, "Girls 11-12 1650 Freestyle"),
                (5, "Girls 13-14 1650 Freestyle"),
            ],
        )

    def test_fridays_prelims_sessions_carry_their_real_events(self):
        """2B/2G are Friday's actual Prelims -- 32 real events that used to vanish."""
        self.assertEqual(len(self.grouped["2B"]), 16)
        self.assertEqual(len(self.grouped["2G"]), 16)
        boys_numbers = [e.event_number for e in self.grouped["2B"]]
        girls_numbers = [e.event_number for e in self.grouped["2G"]]
        # The two pools split the event numbering: boys even, girls odd.
        self.assertTrue(all(n % 2 == 0 for n in boys_numbers), boys_numbers)
        self.assertTrue(all(n % 2 == 1 for n in girls_numbers), girls_numbers)
        # The session is named "Prelims" but really holds 12 Prelims rows plus 4 "Finals-S" rows:
        # the relays and the 400 IMs are timed finals swum inside the prelims session. Asserted
        # because it is a real structural feature of this document, not an artifact.
        rounds = [e.round_name for e in self.grouped["2B"]]
        self.assertEqual(rounds.count("Prelims"), 12)
        self.assertEqual(rounds.count("Finals-S"), 4)
        self.assertEqual(
            [e.event_number for e in self.grouped["2B"] if e.round_name == "Finals-S"],
            [38, 40, 34, 36],
        )
        by_number = {e.event_number: e for e in self.grouped["2B"]}
        self.assertEqual(by_number[10].event_name, "Boys 10 & Under 100 Butterfly")
        self.assertEqual(by_number[10].heats, 3)

    def test_every_lettered_session_has_its_own_real_metadata(self):
        """Not just present -- each carries the right day, date, times and finish."""
        expected = {
            "1B": ("Thursday Distance (BOYS POOL)", 1, "2026-03-05", "16:00", "18:33", 3),
            "1G": ("Thursday Distance (GIRLS POOL)", 1, "2026-03-05", "16:00", "18:29", 3),
            "2B": ("Friday Prelims (BOYS POOL)", 2, "2026-03-06", "08:30", "11:12", 16),
            "2G": ("Friday Prelims (GIRLS POOL)", 2, "2026-03-06", "08:30", "10:45", 16),
            "4B": ("Saturday Prelims (BOYS POOL)", 3, "2026-03-07", "08:30", "11:19", 18),
            "4G": ("Saturday Prelims (GIRLS POOL)", 3, "2026-03-07", "08:30", "11:01", 18),
            "6B": ("Sunday Prelims (BOYS POOL)", 4, "2026-03-08", "08:30", "12:21", 21),
            "6G": ("Sunday Prelims (GIRLS POOL)", 4, "2026-03-08", "08:30", "11:54", 21),
        }
        for label, (name, day, iso, start, finish, count) in expected.items():
            session = self.sessions[label]
            self.assertEqual(session.name, name, label)
            self.assertEqual(session.day_of_meet, day, label)
            self.assertEqual(session.date.isoformat(), iso, label)
            self.assertEqual(session.start_time, start, label)
            self.assertEqual(session.finish_time, finish, label)
            self.assertEqual(len(self.grouped[label]), count, label)

    def test_the_plain_numbered_finals_sessions_are_unaffected(self):
        """3/5/7 are the sessions that DID parse before -- they must be untouched."""
        expected = {
            "3": ("Friday Finals", 2, "2026-03-06", "17:00", "20:25", 33),
            "5": ("Saturday Finals", 3, "2026-03-07", "17:00", "20:24", 36),
            "7": ("Sunday Finals", 4, "2026-03-08", "16:00", "19:06", 38),
        }
        for label, (name, day, iso, start, finish, count) in expected.items():
            session = self.sessions[label]
            self.assertEqual(session.name, name, label)
            self.assertEqual(session.day_of_meet, day, label)
            self.assertEqual(session.date.isoformat(), iso, label)
            self.assertEqual(session.start_time, start, label)
            self.assertEqual(session.finish_time, finish, label)
            self.assertEqual(len(self.grouped[label]), count, label)

    def test_a_session_id_is_a_string_label_not_a_number(self):
        for label, session in self.sessions.items():
            self.assertIsInstance(label, str)
            self.assertIsInstance(session.number, str)
        for event in self.events:
            self.assertIsInstance(event.session_number, str)

    def test_heat_intervals_key_off_the_lettered_labels_too(self):
        """parse_heat_intervals is a SECOND, independent session-number parser (it re-scans the
        same pages rather than extending the family-facing timeline regex). Its own pattern had
        the same defect, so without fixing it the lettered sessions' intervals were attributed to
        whichever session was seen last."""
        intervals = parse_heat_intervals(TIMELINE)
        self.assertEqual(sorted(intervals, key=session_sort_key), ALL_SESSIONS)
        for label in LETTERED_SESSIONS:
            self.assertEqual(intervals[label], "30 Seconds / Back +15 Seconds", label)
        # The finals sessions really do print a different interval -- proof each label got its OWN
        # line rather than inheriting a neighbour's.
        for label in PLAIN_SESSIONS:
            self.assertEqual(intervals[label], "70 Seconds / Back +15 Seconds", label)


class LetteredSessionCardTest(unittest.TestCase):
    """A badge card for a lettered session, rendered and read back out of the PDF."""

    @classmethod
    def setUpClass(cls):
        cls.meet_name, cls.cards, _highlights = cards_for_timeline(TIMELINE)
        cls.by_label = {card.session_number: card for card in cls.cards}

    def test_one_card_per_session_in_real_meet_order(self):
        self.assertEqual([card.session_number for card in self.cards], ALL_SESSIONS)

    def test_the_card_label_shows_the_meets_own_lettered_id(self):
        self.assertEqual(
            self.by_label["1B"].session_label, "SESSION 1B — THURSDAY DISTANCE (BOYS POOL)"
        )
        self.assertEqual(
            self.by_label["2G"].session_label, "SESSION 2G — FRIDAY PRELIMS (GIRLS POOL)"
        )

    def test_the_rendered_pdf_really_prints_session_1b(self):
        """Rendered and read back, not just asserted on the label string: the label could be
        correct while the text is clipped out of the card's header by the shrink-to-fit loop."""
        card = self.by_label["1B"]
        reader = PdfReader(BytesIO(render_cards_pdf([card])))
        self.assertEqual(len(reader.pages), 1)
        text = page_text(reader, 0)
        self.assertIn("SESSION 1B", text)
        # The id is not silently renumbered to a plain "1", and the girls pool's is not shown.
        self.assertNotIn("SESSION 1 ", text)
        self.assertNotIn("SESSION 1G", text)
        # ...and the events that used to be dropped are actually on the card.
        for fragment in ("500 Free", "1650 Free"):
            self.assertIn(fragment, text)

    def test_every_lettered_session_renders_its_own_id(self):
        for label in LETTERED_SESSIONS:
            reader = PdfReader(BytesIO(render_cards_pdf([self.by_label[label]])))
            self.assertIn(f"SESSION {label}", page_text(reader, 0), label)

    def test_the_whole_meet_renders_one_page_per_session(self):
        reader = PdfReader(BytesIO(render_cards_pdf(self.cards)))
        self.assertEqual(len(reader.pages), 11)
        rendered = [page_text(reader, i) for i in range(11)]
        for label, text in zip(ALL_SESSIONS, rendered):
            self.assertIn(f"SESSION {label}", text, label)


class SessionOrderTest(unittest.TestCase):
    """session_sort_key exists because a plain str sort is wrong in BOTH directions."""

    def test_a_plain_numbered_meet_with_ten_sessions_keeps_numeric_order(self):
        """The regression a naive str sort would have introduced to a real hosted meet.

        Shark Open has ten sessions, and sorted() on strings puts "10" immediately after "1" --
        so its printed cards would have run 1, 10, 2, 3... This is why the sort key is natural
        rather than lexicographic, and it is asserted on the real meet.
        """
        shark = ROOT / "meets/2026-shark-open/input/2026-shark-open-timeline.pdf"
        _name, cards, _highlights = cards_for_timeline(shark)
        order = [card.session_number for card in cards]
        self.assertEqual(order, [str(n) for n in range(1, 11)])
        self.assertNotEqual(order, sorted(order))  # i.e. a lexicographic sort would differ

    def test_lettered_and_plain_ids_interleave_in_chronological_order(self):
        # Session 3 (Friday Finals) really does fall between 2G and 4B, and 5 between 4G and 6B.
        self.assertEqual(sorted(ALL_SESSIONS, key=session_sort_key), ALL_SESSIONS)
        self.assertEqual(
            sorted(["7", "1G", "10", "2", "1B"], key=session_sort_key),
            ["1B", "1G", "2", "7", "10"],
        )

    def test_an_unparseable_label_sorts_last_instead_of_raising(self):
        # A download must never 500 because a meet used a format nobody anticipated.
        self.assertEqual(sorted(["2", "odd", "1"], key=session_sort_key), ["1", "2", "odd"])


class LetteredSessionDownloadTest(unittest.TestCase):
    """?session=1B must select that session over real HTTP.

    Driven through the upload route, because this fixture is deliberately not a hosted meet.
    """

    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer

        import webapp.server as srv

        cls.srv = srv
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.SwimTimelineHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.token = cls._upload()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    @classmethod
    def _upload(cls) -> str:
        boundary = "----letteredsessionboundary"
        body = BytesIO()
        body.write(f"--{boundary}\r\n".encode())
        body.write(
            b'Content-Disposition: form-data; name="timeline_pdf"; filename="timeline.pdf"\r\n'
        )
        body.write(b"Content-Type: application/pdf\r\n\r\n" + TIMELINE.read_bytes() + b"\r\n")
        body.write(f"--{boundary}--\r\n".encode())
        request = urllib.request.Request(
            f"http://127.0.0.1:{cls.port}/api/officials/sessions",
            data=body.getvalue(),
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read())
        return payload["token"]

    def badges(self, query: str):
        url = f"http://127.0.0.1:{self.port}/api/officials/badges?token={self.token}&{query}"
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                return response.status, response.read(), response.headers
        except urllib.error.HTTPError as error:
            return error.code, error.read(), error.headers

    def test_the_upload_reports_all_eleven_sessions(self):
        """Asserts the SESSION COUNT, not merely a 200.

        A bare status check here was vacuous: the whole-meet download succeeds even with the
        root-cause bug present, it just silently returns three cards instead of eleven. Verified by
        reinstating the old (\\d+) regex -- a status-only assertion still passed, these do not.
        """
        status, body, _headers = self.badges("")
        self.assertEqual(status, 200)
        reader = PdfReader(BytesIO(body))
        self.assertEqual(len(reader.pages), 11)
        rendered = "\n".join(page_text(reader, i) for i in range(len(reader.pages)))
        for label in ALL_SESSIONS:
            self.assertIn(f"SESSION {label}", rendered, label)

    def test_a_lettered_session_id_downloads_just_that_session(self):
        status, body, headers = self.badges("session=1B")
        self.assertEqual(status, 200)
        reader = PdfReader(BytesIO(body))
        self.assertEqual(len(reader.pages), 1)
        self.assertIn("SESSION 1B", page_text(reader, 0))
        # The filename carries the lettered id, sanitized the same way the meet slug is.
        self.assertIn("session-1B", headers.get("Content-Disposition", ""))

    def test_the_two_pools_of_one_day_are_separately_selectable(self):
        """The whole point of a lettered id: 1B and 1G are different sessions, same day."""
        _s, boys, _h = self.badges("session=1B")
        _s2, girls, _h2 = self.badges("session=1G")
        boys_text = page_text(PdfReader(BytesIO(boys)), 0)
        girls_text = page_text(PdfReader(BytesIO(girls)), 0)
        self.assertIn("SESSION 1B", boys_text)
        self.assertIn("SESSION 1G", girls_text)
        # Compared as TEXT, not raw bytes: ReportLab stamps a fresh random /ID into every render,
        # so `assertNotEqual(boys, girls)` on the bytes could never fail and proved nothing.
        self.assertNotEqual(boys_text, girls_text)
        # Each card carries only its own pool's events (boys even, girls odd in this meet).
        self.assertIn("Boys", boys_text)
        self.assertNotIn("Girls", boys_text)
        self.assertIn("Girls", girls_text)
        self.assertNotIn("Boys", girls_text)

    def test_every_lettered_session_is_selectable_by_its_literal_id(self):
        for label in LETTERED_SESSIONS:
            status, body, _headers = self.badges(f"session={label}")
            self.assertEqual(status, 200, label)
            self.assertIn(f"SESSION {label}", page_text(PdfReader(BytesIO(body)), 0), label)

    def test_a_plain_numbered_session_still_downloads(self):
        status, body, _headers = self.badges("session=3")
        self.assertEqual(status, 200)
        self.assertIn("SESSION 3", page_text(PdfReader(BytesIO(body)), 0))

    def test_a_zero_padded_numeric_session_still_resolves_as_it_used_to(self):
        """?session=03 worked before the fix (int("03") == 3) and must keep working.

        Dropping to a raw string comparison would have narrowed this to a 400 for a spelling the
        old code accepted, so a purely numeric token is normalized. A lettered id is never
        reinterpreted this way.
        """
        status, body, _headers = self.badges("session=03")
        self.assertEqual(status, 200)
        self.assertIn("SESSION 3", page_text(PdfReader(BytesIO(body)), 0))

    def test_the_download_filename_carries_the_lettered_id_safely(self):
        """card_filename sanitizes the session id now that it is a meet-authored label rather
        than a bare integer -- pinned so that sanitization has actual coverage."""
        from swimtimeline.badges import card_filename, cards_for_timeline

        _name, cards, _highlights = cards_for_timeline(TIMELINE)
        by_label = {card.session_number: card for card in cards}
        self.assertEqual(
            card_filename("2026 AZSI SC Age Group State Championship", card=by_label["1B"]),
            "2026-azsi-sc-age-group-state-championship-session-1B-badge-card.pdf",
        )
        self.assertEqual(
            card_filename("2026 AZSI SC Age Group State Championship", card=by_label["2G"], copies=9),
            "2026-azsi-sc-age-group-state-championship-session-2G-badge-cards-x9.pdf",
        )
        # A plain id produces exactly the name it always did -- no churn for existing meets.
        self.assertEqual(
            card_filename("2026 AZSI SC Age Group State Championship", card=by_label["3"]),
            "2026-azsi-sc-age-group-state-championship-session-3-badge-card.pdf",
        )

    def test_copies_of_a_lettered_session_tile_onto_sheets(self):
        status, body, _headers = self.badges("layout=handout&session=2G&copies=9")
        self.assertEqual(status, 200)
        reader = PdfReader(BytesIO(body))
        self.assertEqual(len(reader.pages), 1)
        self.assertEqual(page_text(reader, 0).count("SESSION 2G"), 9)

    def test_a_session_id_that_is_not_in_this_meet_is_still_a_clean_400(self):
        # Dropping the isdigit() precondition must not turn a bad id into a 500: validity is
        # decided solely by matching a real session in this meet.
        for bogus in ("1X", "99", "abc", "1b"):
            status, body, _headers = self.badges(f"session={bogus}")
            self.assertEqual(status, 400, bogus)
            self.assertIn("not in this meet", json.loads(body)["error"], bogus)


if __name__ == "__main__":
    unittest.main()
