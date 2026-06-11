"""PDF extraction and calendar payload generation for the local web app."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
import re
from typing import Iterable

from pypdf import PdfReader

from .ics import build_ics
from .standards import SOURCES, lookup


DEFAULT_TZ = "America/Phoenix"
DEFAULT_STATE = "AZ"


@dataclass
class Fragment:
    page: int
    x: float
    y: float
    text: str


@dataclass
class SessionInfo:
    number: int
    name: str
    day_of_meet: int
    date: date
    start_time: str
    warmup_time: str | None
    facility: str | None
    finish_time: str | None = None


@dataclass
class TimelineEvent:
    event_number: int
    event_name: str
    round_name: str
    session_number: int
    session_name: str
    date: date
    start: datetime
    end: datetime
    entries: int | None
    heats: int | None


@dataclass
class PsychEntry:
    day: str
    event_number: int
    event_name: str
    seed_time: str
    seed_place: int
    age: str
    team: str
    page: int
    column: str
    source_line: str
    matched_name: str = ""
    name_match_type: str = "exact"
    document_type: str = "psych"
    heat: int | None = None
    lane: int | None = None
    round_name: str | None = None


@dataclass(frozen=True)
class PsychLine:
    team: str
    seed: str
    age: str
    seed_place: int
    swimmer_name: str
    document_type: str = "psych"
    heat: int | None = None
    lane: int | None = None
    round_name: str | None = None


@dataclass
class SwimEvent:
    psych: PsychEntry
    timeline: TimelineEvent
    final_timeline: TimelineEvent | None
    benchmarks: dict[str, str | None]
    finals_note: str
    checkin_note: str | None


@dataclass
class RelayEntry:
    event_number: int
    event_name: str
    relay_label: str
    entry_time: str
    leg: int
    page: int
    source_line: str


@dataclass
class RelayEvent:
    relay: RelayEntry
    timeline: TimelineEvent
    finals_note: str


def extract_text_pages(path: Path) -> list[str]:
    reader = PdfReader(str(path))
    return [page.extract_text() or "" for page in reader.pages]


def extract_fragments(path: Path) -> list[Fragment]:
    reader = PdfReader(str(path))
    fragments: list[Fragment] = []
    for page_index, page in enumerate(reader.pages, start=1):
        def visitor(text: str, _cm, tm, _font_dict, _font_size) -> None:
            clean = text.strip("\n")
            if clean.strip():
                fragments.append(Fragment(page_index, float(tm[4]), float(tm[5]), clean))

        page.extract_text(visitor_text=visitor)
    return fragments


def normalize_space(value: str) -> str:
    value = value.replace("\u03d0", "f").replace("\ufb01", "fi").replace("\ufb02", "fl")
    return re.sub(r"\s+", " ", value).strip()


def clean_swimmer_name(value: str) -> str:
    cleaned = re.sub(r"[*\u2022\u2020\u2021]+", " ", value)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" ,")


def name_pairs(value: str) -> list[tuple[str, str]]:
    cleaned = clean_swimmer_name(value)
    pairs: list[tuple[str, str]] = []
    if "," in cleaned:
        last_part, first_part = cleaned.split(",", 1)
        first_tokens = re.findall(r"[A-Za-z][A-Za-z'-]*", first_part)
        last_tokens = re.findall(r"[A-Za-z][A-Za-z'-]*", last_part)
        if first_tokens and last_tokens:
            pairs.append((normalize_name_token(first_tokens[0]), normalize_name_token(last_tokens[-1])))
    else:
        tokens = re.findall(r"[A-Za-z][A-Za-z'-]*", cleaned)
        if len(tokens) >= 2:
            pairs.append((normalize_name_token(tokens[0]), normalize_name_token(tokens[-1])))
            if len(tokens) == 2:
                pairs.append((normalize_name_token(tokens[-1]), normalize_name_token(tokens[0])))
    return list(dict.fromkeys(pair for pair in pairs if pair[0] and pair[1]))


def normalize_name_token(value: str) -> str:
    return re.sub(r"[^a-z]", "", value.casefold())


def display_first_last(value: str) -> str | None:
    pairs = name_pairs(value)
    if not pairs:
        return None
    first, last = pairs[0]
    return f"{first.title()} {last.title()}"


def levenshtein(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            current.append(
                min(
                    previous[right_index] + 1,
                    current[right_index - 1] + 1,
                    previous[right_index - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def close_name_pair(query: tuple[str, str], candidate: tuple[str, str]) -> bool:
    query_first, query_last = query
    candidate_first, candidate_last = candidate
    if query_first == candidate_first and levenshtein(query_last, candidate_last) <= 1:
        return True
    if query_last == candidate_last and levenshtein(query_first, candidate_first) <= 1:
        return True
    full_query = f"{query_first}{query_last}"
    full_candidate = f"{candidate_first}{candidate_last}"
    return (
        query_first[:1] == candidate_first[:1]
        and query_last[:1] == candidate_last[:1]
        and levenshtein(full_query, full_candidate) <= 2
    )


def make_name_patterns(swimmer_name: str) -> list[re.Pattern[str]]:
    raw = normalize_space(swimmer_name)
    patterns: list[str] = []
    if "," in raw:
        escaped = re.escape(raw)
        patterns.append(escaped)
    else:
        parts = raw.split()
        if len(parts) >= 2:
            first = re.escape(parts[0])
            last = re.escape(parts[-1])
            patterns.append(rf"{last},\s*{first}(?:\s+[A-Z][A-Za-z]*)?")
            patterns.append(rf"{first}\s+[A-Za-z ]*{last}")
        patterns.append(re.escape(raw))
    return [re.compile(pattern, re.IGNORECASE) for pattern in dict.fromkeys(patterns)]


def line_matches_name(line: str, patterns: Iterable[re.Pattern[str]]) -> bool:
    return any(pattern.search(line) for pattern in patterns)


def match_swimmer_name(
    candidate_name: str,
    patterns: Iterable[re.Pattern[str]],
    query_pairs: list[tuple[str, str]],
    allow_fuzzy: bool,
) -> str | None:
    cleaned = clean_swimmer_name(candidate_name)
    if line_matches_name(cleaned, patterns):
        return "exact"
    candidate_pairs = name_pairs(cleaned)
    if any(query == candidate for query in query_pairs for candidate in candidate_pairs):
        return "exact"
    if allow_fuzzy and any(close_name_pair(query, candidate) for query in query_pairs for candidate in candidate_pairs):
        return "fuzzy"
    return None


def page_column_for_line(page: int, line: str, fragments: list[Fragment]) -> str:
    candidates = [fragment for fragment in fragments if fragment.page == page and line.strip() in fragment.text]
    if not candidates:
        name_part = re.sub(r"\d+$", "", line.strip())
        candidates = [fragment for fragment in fragments if fragment.page == page and name_part[-20:] in fragment.text]
    if not candidates:
        return "Unknown"
    x = min(candidates, key=lambda fragment: len(fragment.text)).x
    if x < 200:
        return "Left"
    if x < 400:
        return "Middle"
    return "Right"


def parse_psych_line(line: str) -> PsychLine | None:
    line, heat, round_name = normalize_entry_line(line)
    match = re.search(
        r"(?P<team>[A-Z0-9-]+)\s+"
        r"(?P<seed>(?:NT|(?:\d+:)?\d{1,2}\.\d{2}[A-Z]?))\s+"
        r"(?P<age>\d{1,2})\s*"
        r"(?P<name>.+?)\s*"
        r"(?P<place>\d+)\s*$",
        line,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    return PsychLine(
        team=match.group("team"),
        seed=match.group("seed"),
        age=match.group("age"),
        swimmer_name=clean_swimmer_name(match.group("name")),
        seed_place=int(match.group("place")),
        document_type="heat" if heat is not None else "psych",
        heat=heat,
        lane=int(match.group("place")) if heat is not None else None,
        round_name=round_name,
    )


def normalize_entry_line(line: str) -> tuple[str, int | None, str | None]:
    clean = normalize_space(line)
    clean = re.sub(r"^Age\s+TeamName\s+Seed\s+Time", "", clean, flags=re.IGNORECASE).strip()
    heat = None
    round_name = None
    heat_match = re.match(
        r"^Heat\s+(?P<heat>\d+)(?:\s+of\s+\d+)?\s+"
        r"(?P<round>Prelims|Finals)"
        r"(?:\s+\(#[^)]+\))?\s*",
        clean,
        flags=re.IGNORECASE,
    )
    if heat_match:
        heat = int(heat_match.group("heat"))
        round_name = heat_match.group("round").title()
        clean = clean[heat_match.end() :].strip()
    return clean, heat, round_name


def parse_psych_entry_line(
    line: str,
    patterns: Iterable[re.Pattern[str]],
    query_pairs: list[tuple[str, str]],
    allow_fuzzy: bool = False,
) -> tuple[PsychLine, str] | None:
    row = parse_psych_line(line)
    if row is None:
        return None
    match_type = match_swimmer_name(row.swimmer_name, patterns, query_pairs, allow_fuzzy=allow_fuzzy)
    if match_type:
        return row, match_type
    return None


def scan_event_header(lines: list[str], start_index: int) -> tuple[int, str] | None:
    for index in range(start_index, -1, -1):
        line = normalize_space(lines[index])
        match = re.match(r"(?:#|Event)\s*(\d+)\s+(.+)$", line, flags=re.IGNORECASE)
        if match:
            return int(match.group(1)), normalize_event_header_name(match.group(2))
    return None


def normalize_event_header_name(value: str) -> str:
    name = normalize_space(value)
    continuation = re.match(r"\.\.\.\((.+)\)$", name)
    if continuation:
        return normalize_space(continuation.group(1))
    return name


def collect_psych_entries(
    pages: list[str],
    fragments: list[Fragment],
    patterns: list[re.Pattern[str]],
    query_pairs: list[tuple[str, str]],
    allow_fuzzy: bool,
) -> tuple[list[PsychEntry], list[dict]]:
    entries: list[PsychEntry] = []
    page_counts: list[dict] = []

    for page_number, text in enumerate(pages, start=1):
        lines = text.splitlines()
        count = 0
        for index, line in enumerate(lines):
            parsed = parse_psych_entry_line(line, patterns, query_pairs, allow_fuzzy=allow_fuzzy)
            if parsed is None:
                continue
            row, match_type = parsed
            count += 1
            header = scan_event_header(lines, index)
            if header is None:
                continue
            event_number, event_name = header
            entries.append(
                PsychEntry(
                    day="",
                    event_number=event_number,
                    event_name=event_name,
                    seed_time=row.seed,
                    seed_place=row.seed_place,
                    age=row.age,
                    team=row.team,
                    page=page_number,
                    column=page_column_for_line(page_number, line, fragments),
                    source_line=normalize_space(line),
                    matched_name=row.swimmer_name,
                    name_match_type=match_type,
                    document_type=row.document_type,
                    heat=row.heat,
                    lane=row.lane,
                    round_name=row.round_name,
                )
            )
        if count:
            page_counts.append({"page": page_number, "count": count})

    entries.sort(key=lambda entry: (entry.event_number, entry.page, entry.seed_place))
    return entries, page_counts


def extract_psych_entries(psych_pdf: Path, swimmer_name: str) -> tuple[list[PsychEntry], list[dict], list[str]]:
    patterns = make_name_patterns(swimmer_name)
    query_pairs = name_pairs(swimmer_name)
    pages = extract_text_pages(psych_pdf)
    fragments = extract_fragments(psych_pdf)

    entries, page_counts = collect_psych_entries(pages, fragments, patterns, query_pairs, allow_fuzzy=False)
    if entries:
        return entries, page_counts, []

    entries, page_counts = collect_psych_entries(pages, fragments, patterns, query_pairs, allow_fuzzy=True)
    warnings: list[str] = []
    if entries:
        matched_names = sorted({entry.matched_name for entry in entries if entry.matched_name})
        if matched_names:
            warnings.append(
                "No exact swimmer-name match was found. Used high-confidence match: "
                + ", ".join(matched_names)
                + "."
            )
    return entries, page_counts, warnings


def extract_relay_entries(relay_pdf: Path | None, swimmer_name: str) -> tuple[list[RelayEntry], list[str]]:
    if relay_pdf is None:
        return [], []

    patterns = make_name_patterns(swimmer_name)
    query_pairs = name_pairs(swimmer_name)
    pages = extract_text_pages(relay_pdf)
    relays: list[RelayEntry] = []
    fuzzy_relays: list[RelayEntry] = []
    warnings: list[str] = []
    current: dict[str, str | int] | None = None

    relay_header = re.compile(
        r"^(?P<event>\d+)[A-Z]?\s+\d+\s+(?P<session>\d+)\s+(?P<course>[A-Z]+)\s+(?P<group>[A-Z])\s+"
        r"(?P<event_name>.+?Relay)\s+Relay\s+(?P<label>[A-Z])\s+\(Entry:\s*(?P<entry>[^)]+)\)",
        re.IGNORECASE,
    )
    relay_continuation = re.compile(r"^Relay\s+(?P<label>[A-Z])\s+\(Entry:\s*(?P<entry>[^)]+)\)", re.IGNORECASE)
    swimmer_line = re.compile(r"^(?P<leg>[1-8])\.\s+(?P<name>.+)$")

    for page_number, text in enumerate(pages, start=1):
        for raw_line in text.splitlines():
            line = normalize_space(raw_line)
            if not line:
                continue

            header = relay_header.match(line)
            if header:
                current = {
                    "event_number": int(header.group("event")),
                    "session_number": int(header.group("session")),
                    "course": header.group("course").upper(),
                    "event_name": relay_event_name(header.group("group"), header.group("event_name")),
                    "relay_label": f"Relay {header.group('label').upper()}",
                    "entry_time": header.group("entry"),
                }
                continue

            continuation = relay_continuation.match(line)
            if continuation and current:
                current = {
                    **current,
                    "relay_label": f"Relay {continuation.group('label').upper()}",
                    "entry_time": continuation.group("entry"),
                }
                continue

            swimmer = swimmer_line.match(line)
            if swimmer and current:
                match_type = match_swimmer_name(swimmer.group("name"), patterns, query_pairs, allow_fuzzy=False)
                fuzzy_match_type = None if match_type else match_swimmer_name(
                    swimmer.group("name"),
                    patterns,
                    query_pairs,
                    allow_fuzzy=True,
                )
                relay = RelayEntry(
                    event_number=int(current["event_number"]),
                    event_name=str(current["event_name"]),
                    relay_label=str(current["relay_label"]),
                    entry_time=str(current["entry_time"]),
                    leg=int(swimmer.group("leg")),
                    page=page_number,
                    source_line=line,
                )
                if match_type:
                    relays.append(relay)
                elif fuzzy_match_type:
                    fuzzy_relays.append(relay)

    if not relays and fuzzy_relays:
        relays = fuzzy_relays
        warnings.append("No exact relay-name match was found. Used a high-confidence relay name match.")
    if not relays:
        warnings.append("Relay document uploaded, but no relay rows explicitly named the swimmer.")
    relays.sort(key=lambda relay: (relay.event_number, relay.relay_label, relay.leg))
    return relays, warnings


def relay_event_name(group_code: str, event_name: str) -> str:
    group_map = {"G": "Girls", "B": "Boys", "W": "Women", "M": "Men"}
    prefix = group_map.get(group_code.upper(), group_code.upper())
    return normalize_space(f"{prefix} {event_name}")


def parse_date_range(text: str) -> tuple[date, date] | None:
    match = re.search(
        r"(\d{1,2})/(\d{1,2})/(\d{4})\s+to\s+(\d{1,2})/(\d{1,2})/(\d{4})",
        text,
    )
    if not match:
        return None
    sm, sd, sy, em, ed, ey = map(int, match.groups())
    return date(sy, sm, sd), date(ey, em, ed)


def parse_meet_name(text: str) -> str:
    for line in text.splitlines():
        clean = normalize_space(line)
        if " - " in clean and re.search(r"\d{1,2}/\d{1,2}/\d{4}\s+to\s+\d{1,2}/\d{1,2}/\d{4}", clean):
            return clean.split(" - ", 1)[0]
    for line in text.splitlines():
        clean = normalize_space(line)
        if "invite" in clean.lower() or "invitational" in clean.lower():
            return clean
    return "Swim Meet"


def parse_clock(value: str) -> tuple[int, int]:
    match = re.match(r"(\d{1,2}):(\d{2})\s*([AP]M)?", value.strip(), re.IGNORECASE)
    if not match:
        raise ValueError(f"Unsupported time: {value}")
    hour, minute = int(match.group(1)), int(match.group(2))
    meridiem = (match.group(3) or "").upper()
    if meridiem == "PM" and hour != 12:
        hour += 12
    elif meridiem == "AM" and hour == 12:
        hour = 0
    return hour, minute


def combine_date_time(day: date, value: str) -> datetime:
    hour, minute = parse_clock(value)
    return datetime(day.year, day.month, day.day, hour, minute)


def display_time(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def display_window(start: datetime, end: datetime) -> str:
    return f"{display_time(start)}-{display_time(end)}"


def parse_flyer_sessions(text: str, start_date: date) -> dict[int, dict[str, str]]:
    sessions: dict[int, dict[str, str]] = {}
    line_pattern = re.compile(
        r"Session\s+#?(?P<num>\d+),?\s+(?P<day>[A-Za-z]+),?\s+(?P<month>[A-Za-z]+)\s+(?P<dom>\d+)\s+(?P<name>.+?)\s+at\s+(?P<facility>[^:]+):\s*Warm[- ]?up:?\s*(?P<warm>\d{1,2}:\d{2})\s*(?P<warm_ampm>[ap]\.?m\.?)\s*,?\s*Meet\s+Start:?\s*(?P<start>\d{1,2}:\d{2})\s*(?P<start_ampm>[ap]\.?m\.?)",
        flags=re.IGNORECASE,
    )
    for line in text.splitlines():
        clean = normalize_space(line).replace("a m", "am").replace("p m", "pm")
        match = line_pattern.search(clean)
        if not match:
            continue
        num = int(match.group("num"))
        facility = normalize_space(match.group("facility")).upper().replace("SKYLINE", "Skyline")
        sessions[num] = {
            "warmup_time": normalize_pdf_time(match.group("warm"), match.group("warm_ampm")),
            "start_time": normalize_pdf_time(match.group("start"), match.group("start_ampm")),
            "facility": facility.title(),
        }
    return sessions


def normalize_pdf_time(value: str, meridiem: str) -> str:
    meridiem = meridiem.upper().replace(".", "")
    hour, minute = parse_clock(f"{value} {meridiem}")
    return f"{hour:02d}:{minute:02d}"


def infer_facility(session_name: str) -> str:
    lower = session_name.lower()
    if "final" in lower or "sr" in lower or "senior" in lower:
        return "Skyline Aquatic Center"
    if "ag" in lower or "age group" in lower:
        return "Kino Aquatic Complex"
    return "Meet facility"


def parse_timeline(timeline_pdf: Path, flyer_text: str = "") -> tuple[str, dict[int, SessionInfo], list[TimelineEvent]]:
    pages = extract_text_pages(timeline_pdf)
    text = "\n".join(pages)
    date_range = parse_date_range(text) or parse_date_range(flyer_text)
    if date_range is None:
        raise ValueError("Could not find meet date range in the timeline or flyer.")
    start_date, _end_date = date_range
    flyer_sessions = parse_flyer_sessions(flyer_text, start_date) if flyer_text else {}
    meet_name = parse_meet_name(text)

    sessions: dict[int, SessionInfo] = {}
    events: list[TimelineEvent] = []

    session_header = re.compile(r"Session:\s*(\d+)\s+(.+)")
    day_header = re.compile(r"Day of Meet:\s*(\d+)\s+Starts at\s+(\d{1,2}:\d{2}\s*[AP]M)", re.IGNORECASE)
    event_line = re.compile(
        r"^(Prelims|Finals)\s+(\d+)\s+(.+?)\s+(\d+)\s+(\d+)\s+_+\s*(\d{1,2}:\d{2})\s*([AP]M)u?$",
        re.IGNORECASE,
    )
    finish_line = re.compile(r"Finish Time\s+_+(\d{1,2}:\d{2}\s*[AP]M)", re.IGNORECASE)

    for page_text in pages:
        current_session: SessionInfo | None = None
        pending_session: tuple[int, str] | None = None
        page_events: list[TimelineEvent] = []
        for raw_line in page_text.splitlines():
            line = normalize_timeline_line(raw_line)
            if not line:
                continue
            session_match = session_header.search(line)
            if session_match:
                pending_session = (int(session_match.group(1)), normalize_space(session_match.group(2)))
                continue
            day_match = day_header.search(line)
            if day_match and pending_session:
                number, name = pending_session
                day_of_meet = int(day_match.group(1))
                session_date = start_date + timedelta(days=day_of_meet - 1)
                start_time = normalize_time_string(day_match.group(2))
                flyer_session = flyer_sessions.get(number, {})
                warmup = flyer_session.get("warmup_time") or time_minus_minutes(start_time, 60)
                facility = flyer_session.get("facility") or infer_facility(name)
                current_session = SessionInfo(
                    number=number,
                    name=name,
                    day_of_meet=day_of_meet,
                    date=session_date,
                    start_time=start_time,
                    warmup_time=warmup,
                    facility=facility,
                )
                sessions[number] = current_session
                continue
            finish_match = finish_line.search(line)
            if finish_match and current_session:
                current_session.finish_time = normalize_time_string(finish_match.group(1))
                continue
            event_match = event_line.match(line)
            if event_match and current_session:
                round_name, event_num, event_name, entries, heats, clock, meridiem = event_match.groups()
                start_dt = combine_date_time(current_session.date, f"{clock} {meridiem}")
                page_events.append(
                    TimelineEvent(
                        event_number=int(event_num),
                        event_name=normalize_space(event_name),
                        round_name=round_name.title(),
                        session_number=current_session.number,
                        session_name=current_session.name,
                        date=current_session.date,
                        start=start_dt,
                        end=start_dt,
                        entries=int(entries),
                        heats=int(heats),
                    )
                )
        for index, item in enumerate(page_events):
            if index + 1 < len(page_events):
                item.end = page_events[index + 1].start
            elif current_session and current_session.finish_time:
                item.end = combine_date_time(item.date, current_session.finish_time)
            else:
                item.end = item.start + timedelta(minutes=20)
        events.extend(page_events)

    return meet_name, sessions, events


def normalize_timeline_line(value: str) -> str:
    line = normalize_space(value)
    line = re.sub(r"^(Prelims|Finals)\s+(\d+)(?=[A-Za-z])", r"\1 \2 ", line, flags=re.IGNORECASE)
    line = re.sub(r"(?<=\d)(Girls|Boys|Women|Men)\b", r" \1", line, flags=re.IGNORECASE)
    return line


def normalize_time_string(value: str) -> str:
    dt = combine_date_time(date(2000, 1, 1), value)
    return dt.strftime("%H:%M")


def time_minus_minutes(value: str, minutes: int) -> str:
    dt = combine_date_time(date(2000, 1, 1), value) - timedelta(minutes=minutes)
    return dt.strftime("%H:%M")


def session_day_name(session: SessionInfo) -> str:
    return session.date.strftime("%A")


def assign_days(entries: list[PsychEntry], timeline_events: list[TimelineEvent]) -> None:
    by_event = primary_timeline_by_event(timeline_events)
    for entry in entries:
        timeline = by_event.get(entry.event_number)
        if timeline:
            entry.day = timeline.date.strftime("%A")


def primary_timeline_by_event(timeline_events: list[TimelineEvent]) -> dict[int, TimelineEvent]:
    result: dict[int, TimelineEvent] = {}
    for event in timeline_events:
        lower = event.session_name.lower()
        is_evening_final = "finals" in lower and "prelim" not in lower and "distance" not in lower
        if event.event_number not in result and not is_evening_final:
            result[event.event_number] = event
    for event in timeline_events:
        result.setdefault(event.event_number, event)
    return result


def final_timeline_by_event(timeline_events: list[TimelineEvent]) -> dict[int, TimelineEvent]:
    result: dict[int, TimelineEvent] = {}
    for event in timeline_events:
        lower = event.session_name.lower()
        if "finals" in lower and "prelim" not in lower:
            result[event.event_number] = event
    return result


def event_short_name(event_name: str) -> str:
    text = event_name
    text = re.sub(r"\b(Girls|Boys|Women|Men)\b", "", text)
    text = re.sub(r"\bLC Meter\b", "", text)
    text = text.replace("Freestyle", "Free").replace("Backstroke", "Back")
    text = text.replace("Breaststroke", "Breast").replace("Butterfly", "Fly")
    text = text.replace("Individual Medley", "IM")
    return normalize_space(text.replace(" & Under", "&U"))


def entry_seed_summary(entry: PsychEntry) -> str:
    if entry.heat is not None and entry.lane is not None:
        return f"seed {entry.seed_time} | heat {entry.heat}, lane {entry.lane}"
    return f"seed {entry.seed_time} | seed place {entry.seed_place}"


def entry_position_line(entry: PsychEntry) -> str:
    if entry.heat is not None and entry.lane is not None:
        return f"Heat/lane: heat {entry.heat}, lane {entry.lane}"
    return f"Seed place: {entry.seed_place}"


def entry_source_label(entry: PsychEntry) -> str:
    return "Heat sheet" if entry.heat is not None else "Psych/entry sheet"


def location_for_session(session: SessionInfo | TimelineEvent) -> str:
    facility = getattr(session, "facility", None)
    if facility is None and hasattr(session, "session_name"):
        facility = infer_facility(session.session_name)
    if facility and "kino" in facility.lower():
        return "Kino Aquatic Complex, 848 N. Horne, Mesa, AZ 85203"
    if facility and "skyline" in facility.lower():
        return "Skyline Aquatic Center, 845 S. Crismon Rd., Mesa, AZ"
    return "Meet facility"


def build_swim_events(entries: list[PsychEntry], timeline_events: list[TimelineEvent], state: str) -> list[SwimEvent]:
    primary = primary_timeline_by_event(timeline_events)
    finals = final_timeline_by_event(timeline_events)
    swim_events: list[SwimEvent] = []
    for entry in entries:
        timeline = primary.get(entry.event_number)
        if not timeline:
            continue
        final_timeline = finals.get(entry.event_number)
        standard = lookup(entry.event_name, entry.seed_time, state=state)
        final_note = finals_note(timeline, final_timeline)
        checkin = checkin_note(entry.event_number)
        swim_events.append(
            SwimEvent(
                psych=entry,
                timeline=timeline,
                final_timeline=final_timeline,
                benchmarks={
                    "usa": standard.usa_summary,
                    "lsc": standard.lsc_summary,
                    "advanced": standard.advanced_summary,
                },
                finals_note=final_note,
                checkin_note=checkin,
            )
        )
    return sorted(swim_events, key=lambda item: item.timeline.start)


def build_relay_events(relay_entries: list[RelayEntry], timeline_events: list[TimelineEvent]) -> list[RelayEvent]:
    primary = primary_timeline_by_event(timeline_events)
    relay_events: list[RelayEvent] = []
    for relay in relay_entries:
        timeline = primary.get(relay.event_number)
        if not timeline:
            continue
        relay_events.append(
            RelayEvent(
                relay=relay,
                timeline=timeline,
                finals_note="Relay is timed final unless the meet document says otherwise.",
            )
        )
    return sorted(relay_events, key=lambda item: item.timeline.start)


def finals_note(timeline: TimelineEvent, final_timeline: TimelineEvent | None) -> str:
    if final_timeline and final_timeline.session_number != timeline.session_number:
        return f"Possible if qualifies; finals event starts at {display_time(final_timeline.start)} at {location_for_session_name(final_timeline.session_name)}."
    lower_session = timeline.session_name.lower()
    if timeline.round_name.lower() == "finals" or "distance" in lower_session:
        return "Timed final; no separate finals swim."
    return "No separate finals event found in the timeline."


def location_for_session_name(session_name: str) -> str:
    return "Skyline" if "final" in session_name.lower() or "sr" in session_name.lower() else "Kino"


def checkin_note(event_number: int) -> str | None:
    if 53 <= event_number <= 72:
        return "Event is in Events 53-72; meet flyer says check in before Session #7 warm-up."
    return None


def day_label(day: date) -> str:
    return day.strftime("%A, %B %-d, %Y") if "%" else day.isoformat()


def safe_day_label(day: date) -> str:
    return f"{day.strftime('%A')}, {day.strftime('%B')} {day.day}, {day.year}"


def swimmer_uid_part(swimmer_name: str) -> str:
    return slugify(swimmer_name)


def possessive_name(swimmer_name: str) -> str:
    return f"{swimmer_name}'" if swimmer_name.endswith("s") else f"{swimmer_name}'s"


def meet_day_text(day: date, day_number: int) -> str:
    return f"Day {day_number} - {safe_day_label(day)}"


def day_numbers_for_items(swims: list[SwimEvent], relays: list[RelayEvent]) -> dict[date, int]:
    days = sorted({item.timeline.date for item in swims} | {item.timeline.date for item in relays})
    return {day: index for index, day in enumerate(days, start=1)}


def build_detailed_payload(
    meet_id: str,
    meet_name: str,
    short_name: str,
    swimmer_name: str,
    swims: list[SwimEvent],
    relays: list[RelayEvent],
    day_numbers: dict[date, int],
) -> dict:
    events: list[dict] = []
    swimmer_slug = swimmer_uid_part(swimmer_name)
    for swim in sorted(swims, key=lambda item: item.timeline.start):
        psych = swim.psych
        timeline = swim.timeline
        lines = [
            swimmer_name,
            short_name,
            "",
            f"Day: {meet_day_text(timeline.date, day_numbers.get(timeline.date, 1))}",
            f"Session: #{timeline.session_number} - {timeline.session_name}",
            f"Pool/course: {location_for_session(timeline)}; entry sheet lists event as LC Meter",
            "",
            f"Event: #{psych.event_number} - {psych.event_name}",
            f"Seed time: {psych.seed_time}",
            entry_position_line(psych),
            f"Timeline event window: {display_window(timeline.start, timeline.end)}",
            "",
            f"Finals: {swim.finals_note}",
        ]
        if swim.checkin_note:
            lines.append(f"Check-in: {swim.checkin_note}")
        lines.extend(
            [
                "",
                "Benchmarks:",
                swim.benchmarks["usa"] or "USA-S: n/a",
                swim.benchmarks["lsc"] or "LSC: n/a",
            ]
        )
        if swim.benchmarks.get("advanced"):
            lines.append(swim.benchmarks["advanced"] or "")
        lines.extend(
            [
                "",
                "Source verification:",
                f"{entry_source_label(psych)}: page {psych.page}, {psych.column.lower()} column",
                f"Timeline: event #{psych.event_number}",
                "Relay source: n/a",
            ]
        )
        events.append(
            {
                "uid": f"{meet_id}-{swimmer_slug}-event-{psych.event_number}@swimtimeline",
                "title": f"{swimmer_name} - Event {psych.event_number}: {event_short_name(psych.event_name)}",
                "start": timeline.start.isoformat(timespec="seconds"),
                "end": timeline.end.isoformat(timespec="seconds"),
                "location": location_for_session(timeline),
                "description_lines": lines,
            }
        )
    for relay_event in sorted(relays, key=lambda item: item.timeline.start):
        relay = relay_event.relay
        timeline = relay_event.timeline
        lines = [
            swimmer_name,
            short_name,
            "",
            f"Day: {meet_day_text(timeline.date, day_numbers.get(timeline.date, 1))}",
            f"Session: #{timeline.session_number} - {timeline.session_name}",
            f"Pool/course: {location_for_session(timeline)}; relay document lists entry as {relay.entry_time[-1:] if relay.entry_time else 'provided'}",
            "",
            f"Relay: #{relay.event_number} - {relay.event_name}",
            f"Team: {relay.relay_label}",
            f"Entry time: {relay.entry_time}",
            f"Leg: {relay.leg}",
            f"Timeline event window: {display_window(timeline.start, timeline.end)}",
            "",
            f"Finals: {relay_event.finals_note}",
            "",
            "Benchmarks: n/a for relay calendar event.",
            "",
            "Source verification:",
            f"Relay document: page {relay.page}; swimmer listed as {relay.source_line.split('.', 1)[-1].strip()}",
            f"Timeline: event #{relay.event_number}",
            "Psych sheet source: n/a for relay assignment",
        ]
        events.append(
            {
                "uid": f"{meet_id}-{swimmer_slug}-relay-{relay.event_number}-{relay.relay_label.lower().replace(' ', '-')}@swimtimeline",
                "title": f"{swimmer_name} - Relay {relay.event_number}: {event_short_name(relay.event_name)}",
                "start": timeline.start.isoformat(timespec="seconds"),
                "end": timeline.end.isoformat(timespec="seconds"),
                "location": location_for_session(timeline),
                "description_lines": lines,
            }
        )
    events.sort(key=lambda event: event["start"])
    return {
        "calendar": {"name": f"{swimmer_name} - {short_name}", "timezone": DEFAULT_TZ},
        "meet": {"id": meet_id, "name": meet_name, "short_name": short_name},
        "events": events,
    }


def build_daily_payload(
    meet_id: str,
    meet_name: str,
    short_name: str,
    swimmer_name: str,
    swims: list[SwimEvent],
    relays: list[RelayEvent],
    sessions: dict[int, SessionInfo],
) -> dict:
    events: list[dict] = []
    by_day: dict[date, list[SwimEvent | RelayEvent]] = {}
    for swim in swims:
        by_day.setdefault(swim.timeline.date, []).append(swim)
    for relay in relays:
        by_day.setdefault(relay.timeline.date, []).append(relay)

    swimmer_slug = swimmer_uid_part(swimmer_name)
    for day_number, (day, day_items) in enumerate(sorted(by_day.items()), start=1):
        day_items.sort(key=lambda item: item.timeline.start)
        first = day_items[0]
        session = sessions.get(first.timeline.session_number)
        session_warmup = (
            combine_date_time(day, session.warmup_time)
            if session and session.warmup_time
            else first.timeline.start - timedelta(hours=1)
        )
        session_start = (
            combine_date_time(day, session.start_time)
            if session
            else first.timeline.start
        )
        calendar_start = session_warmup
        checkin_lines: list[str] = []
        for item in day_items:
            if isinstance(item, SwimEvent) and item.checkin_note:
                swim = item
                checkin_lines.append(f"#{swim.psych.event_number} {swim.checkin_note}")
        if checkin_lines:
            checkin_session = sessions.get(7)
            checkin_time = (
                combine_date_time(day, checkin_session.warmup_time)
                if checkin_session and checkin_session.date == day and checkin_session.warmup_time
                else datetime(day.year, day.month, day.day, 6, 30)
            )
            calendar_start = min(calendar_start, checkin_time)

        lines = [
            swimmer_name,
            short_name,
            "",
            f"Day: {meet_day_text(day, day_number)}",
            f"Session: #{first.timeline.session_number} - {first.timeline.session_name}",
            f"Warm-up: {display_time(session_warmup)}",
            f"Meet start: {display_time(session_start)}",
            f"Pool/course: {location_for_session(first.timeline)}; entry sheet lists events as LC Meter",
            "",
            f"{possessive_name(swimmer_name)} swims:",
        ]
        for item in day_items:
            if isinstance(item, RelayEvent):
                relay = item.relay
                lines.append(
                    f"#{relay.event_number} Relay - {event_short_name(relay.event_name)} | {relay.relay_label}, leg {relay.leg} | {display_window(item.timeline.start, item.timeline.end)}"
                )
            else:
                lines.append(
                    f"#{item.psych.event_number} {event_short_name(item.psych.event_name)} | {entry_seed_summary(item.psych)} | {display_window(item.timeline.start, item.timeline.end)}"
                )
        possible_finals = [
            f"#{item.psych.event_number} {event_short_name(item.psych.event_name)} at {display_time(item.final_timeline.start)} at {location_for_session_name(item.final_timeline.session_name)} if qualifies"
            for item in day_items
            if isinstance(item, SwimEvent)
            and item.final_timeline
            and item.final_timeline.session_number != item.timeline.session_number
        ]
        if possible_finals:
            lines.extend(["", "Possible finals:", *possible_finals])
        if checkin_lines:
            lines.extend(["", "Check-in:", *checkin_lines])
        lines.extend(["", "Benchmarks:"])
        for item in day_items:
            if isinstance(item, RelayEvent):
                continue
            benchmark = item.benchmarks["usa"] or "USA-S: n/a"
            lsc = item.benchmarks["lsc"] or "LSC: n/a"
            lines.append(f"#{item.psych.event_number} {benchmark} | {lsc}")
            if item.benchmarks.get("advanced"):
                lines.append(f"#{item.psych.event_number} {item.benchmarks['advanced']}")
        if any(isinstance(item, RelayEvent) for item in day_items):
            lines.append("Relays: benchmarks n/a.")
        lines.extend(["", "Source verification: entry sheet and timeline verified; review the audit report before import."])
        events.append(
            {
                "uid": f"{meet_id}-{swimmer_slug}-{day.isoformat()}@swimtimeline",
                "title": f"{swimmer_name} - {short_name}: Day {day_number} ({day.strftime('%A')})",
                "start": calendar_start.isoformat(timespec="seconds"),
                "end": max(item.timeline.end for item in day_items).isoformat(timespec="seconds"),
                "location": location_for_session(first.timeline),
                "description_lines": lines,
            }
        )
    return {
        "calendar": {"name": f"{swimmer_name} - {short_name} Daily", "timezone": DEFAULT_TZ},
        "meet": {"id": meet_id, "name": meet_name, "short_name": short_name},
        "events": events,
    }


def build_weekend_payload(
    meet_id: str,
    meet_name: str,
    short_name: str,
    swimmer_name: str,
    swims: list[SwimEvent],
    relays: list[RelayEvent],
    sessions: dict[int, SessionInfo],
) -> dict:
    swimmer_slug = swimmer_uid_part(swimmer_name)
    daily = build_daily_payload(meet_id, meet_name, short_name, swimmer_name, swims, relays, sessions)["events"]
    if not daily:
        return {"calendar": {"name": f"{swimmer_name} - {short_name} Weekend", "timezone": DEFAULT_TZ}, "events": []}
    start = min(datetime.fromisoformat(event["start"]) for event in daily)
    end = max(datetime.fromisoformat(event["end"]) for event in daily)
    lines = [swimmer_name, short_name, "", "Meet summary:"]
    for event in daily:
        lines.extend(["", event["title"].removeprefix(f"{swimmer_name} - "), *event["description_lines"][9:]])
    return {
        "calendar": {"name": f"{swimmer_name} - {short_name} Weekend", "timezone": DEFAULT_TZ},
        "meet": {"id": meet_id, "name": meet_name, "short_name": short_name},
        "events": [
            {
                "uid": f"{meet_id}-{swimmer_slug}-weekend@swimtimeline",
                "title": f"{swimmer_name} - {short_name}: Whole Meet",
                "start": start.isoformat(timespec="seconds"),
                "end": end.isoformat(timespec="seconds"),
                "location": "Multiple meet facilities",
                "description_lines": lines,
            }
        ],
    }


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "swim-meet"


def write_outputs(
    output_dir: Path,
    meet_name: str,
    swimmer_name: str,
    psych_entries: list[PsychEntry],
    swims: list[SwimEvent],
    relays: list[RelayEvent],
    page_counts: list[dict],
    payloads: dict[str, dict],
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    for mode, payload in payloads.items():
        json_path = output_dir / f"{mode}.json"
        ics_path = output_dir / f"{mode}.ics"
        json_path.write_text(to_json(payload), encoding="utf-8")
        ics_path.write_text(build_ics(payload), encoding="utf-8")
        files[f"{mode}_json"] = json_path.name
        files[f"{mode}_ics"] = ics_path.name

    audit_path = output_dir / "audit.md"
    audit_path.write_text(build_audit(meet_name, swimmer_name, psych_entries, swims, relays, page_counts), encoding="utf-8")
    files["audit"] = audit_path.name
    return files


def to_json(data: dict) -> str:
    import json

    return json.dumps(data, indent=2)


def build_audit(
    meet_name: str,
    swimmer_name: str,
    entries: list[PsychEntry],
    swims: list[SwimEvent],
    relays: list[RelayEvent],
    page_counts: list[dict],
) -> str:
    lines = [
        f"# Extraction Audit: {swimmer_name}",
        "",
        f"Meet: {meet_name}",
        "",
        "## Psych Sheet Occurrences",
        "",
        "| Page | Exact/Suggested Matches |",
        "| --- | ---: |",
    ]
    for row in page_counts:
        lines.append(f"| {row['page']} | {row['count']} |")
    lines.extend(["", f"Total psych entries parsed: {len(entries)}", "", "## Verified Events", ""])
    lines.append("| Day | Event # | Event Name | Seed Time | Position | Page | Column | Source |")
    lines.append("| --- | ---: | --- | --- | --- | ---: | --- | --- |")
    for swim in swims:
        psych = swim.psych
        position = f"heat {psych.heat}, lane {psych.lane}" if psych.heat is not None and psych.lane is not None else f"seed place {psych.seed_place}"
        lines.append(
            f"| {swim.timeline.date.strftime('%A')} | {psych.event_number} | {psych.event_name} | {psych.seed_time} | {position} | {psych.page} | {psych.column} | {entry_source_label(psych)} |"
        )
    lines.extend(["", "## Verified Relays", ""])
    if relays:
        lines.append("| Day | Event # | Relay Event | Relay | Entry Time | Leg | Page |")
        lines.append("| --- | ---: | --- | --- | --- | ---: | ---: |")
        for relay_event in relays:
            relay = relay_event.relay
            lines.append(
                f"| {relay_event.timeline.date.strftime('%A')} | {relay.event_number} | {relay.event_name} | {relay.relay_label} | {relay.entry_time} | {relay.leg} | {relay.page} |"
            )
    else:
        lines.append("No verified relays found.")
    lines.extend(
        [
            "",
            "## Standards Sources",
            "",
            *[f"- {source['name']}: {source['url']}" for source in SOURCES],
            "",
            f"Total verified events found: {len(swims)}",
            "",
            f"Total verified relays found: {len(relays)}",
            "",
            "No relay events are included unless a relay document explicitly names the swimmer.",
        ]
    )
    return "\n".join(lines) + "\n"


def analyze_uploads(
    flyer_pdf: Path | None,
    psych_pdf: Path,
    timeline_pdf: Path,
    swimmer_name: str,
    output_dir: Path,
    relay_pdf: Path | None = None,
    state: str = DEFAULT_STATE,
    modes: Iterable[str] = ("daily", "weekend", "detailed"),
) -> dict:
    flyer_text = "\n".join(extract_text_pages(flyer_pdf)) if flyer_pdf else ""
    meet_name, sessions, timeline_events = parse_timeline(timeline_pdf, flyer_text=flyer_text)
    entries, page_counts, name_warnings = extract_psych_entries(psych_pdf, swimmer_name)
    relay_entries, relay_warnings = extract_relay_entries(relay_pdf, swimmer_name)
    assign_days(entries, timeline_events)
    swims = build_swim_events(entries, timeline_events, state=state)
    relays = build_relay_events(relay_entries, timeline_events)
    short_name = short_meet_name(meet_name)
    meet_id = slugify(meet_name)
    output_swimmer_name = resolved_swimmer_name(swimmer_name, entries)
    day_numbers = day_numbers_for_items(swims, relays)
    payload_map = {
        "daily": build_daily_payload(meet_id, meet_name, short_name, output_swimmer_name, swims, relays, sessions),
        "weekend": build_weekend_payload(meet_id, meet_name, short_name, output_swimmer_name, swims, relays, sessions),
        "detailed": build_detailed_payload(meet_id, meet_name, short_name, output_swimmer_name, swims, relays, day_numbers),
    }
    selected_payloads = {mode: payload_map[mode] for mode in modes if mode in payload_map}
    files = write_outputs(output_dir, meet_name, output_swimmer_name, entries, swims, relays, page_counts, selected_payloads)

    return {
        "meet": {"id": meet_id, "name": meet_name, "short_name": short_name},
        "swimmer": output_swimmer_name,
        "requested_swimmer": swimmer_name,
        "verified_event_count": len(swims),
        "verified_relay_count": len(relays),
        "psych_match_pages": page_counts,
        "events": [summarize_swim(swim) for swim in swims],
        "relays": [summarize_relay(relay) for relay in relays],
        "items": sorted(
            [summarize_swim(swim) for swim in swims] + [summarize_relay(relay) for relay in relays],
            key=lambda item: item["sort_start"],
        ),
        "files": files,
        "sessions": [serialize_session(session) for session in sessions.values()],
        "warnings": build_warnings(entries, swims, relay_entries, relays, relay_warnings, name_warnings),
    }


def resolved_swimmer_name(swimmer_name: str, entries: list[PsychEntry]) -> str:
    if not entries or not any(entry.name_match_type == "fuzzy" for entry in entries):
        return swimmer_name
    return display_first_last(entries[0].matched_name) or swimmer_name


def serialize_session(session: SessionInfo) -> dict:
    data = asdict(session)
    data["date"] = session.date.isoformat()
    return data


def summarize_swim(swim: SwimEvent) -> dict:
    return {
        "type": "individual",
        "event_number": swim.psych.event_number,
        "event_name": swim.psych.event_name,
        "seed_time": swim.psych.seed_time,
        "seed_place": swim.psych.seed_place,
        "heat": swim.psych.heat,
        "lane": swim.psych.lane,
        "entry_position": entry_position_line(swim.psych),
        "source_document": entry_source_label(swim.psych),
        "day": swim.timeline.date.strftime("%A"),
        "window": display_window(swim.timeline.start, swim.timeline.end),
        "page": swim.psych.page,
        "column": swim.psych.column,
        "benchmarks": swim.benchmarks,
        "finals_note": swim.finals_note,
        "checkin_note": swim.checkin_note,
        "sort_start": swim.timeline.start.isoformat(timespec="seconds"),
    }


def summarize_relay(relay_event: RelayEvent) -> dict:
    relay = relay_event.relay
    return {
        "type": "relay",
        "event_number": relay.event_number,
        "event_name": relay.event_name,
        "seed_time": relay.entry_time,
        "seed_place": None,
        "relay_label": relay.relay_label,
        "leg": relay.leg,
        "day": relay_event.timeline.date.strftime("%A"),
        "window": display_window(relay_event.timeline.start, relay_event.timeline.end),
        "page": relay.page,
        "column": "Relay document",
        "benchmarks": {"usa": "n/a for relay", "lsc": "n/a for relay", "advanced": None},
        "finals_note": relay_event.finals_note,
        "checkin_note": None,
        "sort_start": relay_event.timeline.start.isoformat(timespec="seconds"),
    }


def build_warnings(
    entries: list[PsychEntry],
    swims: list[SwimEvent],
    relay_entries: list[RelayEntry],
    relays: list[RelayEvent],
    relay_warnings: list[str],
    name_warnings: list[str],
) -> list[str]:
    warnings: list[str] = []
    warnings.extend(name_warnings)
    if not entries:
        warnings.append("No psych sheet entries matched the swimmer name. Try Last, First or include a middle initial.")
    if len(swims) < len(entries):
        warnings.append("Some psych entries could not be matched to the timeline by event number.")
    if len(relays) < len(relay_entries):
        warnings.append("Some verified relay rows could not be matched to the timeline by event number.")
    warnings.extend(relay_warnings)
    if any((swim.benchmarks.get("advanced") or "").endswith("not configured for this event yet") for swim in swims):
        warnings.append("At least one swim reached AAAA, but advanced cuts beyond AAAA are not configured for that event yet.")
    return warnings


def short_meet_name(meet_name: str) -> str:
    cleaned = meet_name
    cleaned = re.sub(r"^\d{4}\s+", "", cleaned)
    cleaned = re.sub(r"^\d+(?:st|nd|rd|th)\s+Annual\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace("MAC ", "")
    cleaned = cleaned.replace("Arizona ", "")
    cleaned = cleaned.replace("Invitational", "Invite")
    if "Speedo" in cleaned and "Invite" in cleaned:
        return "Speedo Invite"
    return normalize_space(cleaned)
