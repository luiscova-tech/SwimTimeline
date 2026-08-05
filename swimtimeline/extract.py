"""PDF extraction and calendar payload generation for the local web app."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta
from functools import lru_cache
import hashlib
import json
from math import ceil
from pathlib import Path
import re
from typing import Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pypdf import PdfReader

from .ics import build_ics
from .standards import SOURCES, event_gender, has_lsc_standards, lookup, parse_age


DEFAULT_TZ = "America/Phoenix"
DEFAULT_STATE = "AZ"

# Added to every calendar event's description when the meet's timeline is a pre-meet projection
# (timeline_type == "projected") rather than a settled final timeline. Mirrors the meet-level
# banner but on each individual event, and pairs with STATUS:TENTATIVE in the generated .ics.
PROJECTED_TIMELINE_NOTE = (
    "Heads up: these times come from a pre-meet projected timeline and may still shift; "
    "confirm against the final meet timeline once it is posted."
)

# One representative IANA zone per state/DC, used only as a fallback when a
# meet record has no explicit "timezone". Several states straddle two zones
# (FL and ID panhandles; also TX, KS, NE, SD, ND, OR, MI, IN, KY, TN county
# splits) and are mapped here to their majority zone, so a venue in the
# minority zone needs an explicit per-meet "timezone" override to be correct.
STATE_TIMEZONES: dict[str, str] = {
    "AL": "America/Chicago",
    "AK": "America/Anchorage",
    "AZ": "America/Phoenix",
    "AR": "America/Chicago",
    "CA": "America/Los_Angeles",
    "CO": "America/Denver",
    "CT": "America/New_York",
    "DE": "America/New_York",
    "DC": "America/New_York",
    "FL": "America/New_York",
    "GA": "America/New_York",
    "HI": "Pacific/Honolulu",
    "ID": "America/Boise",
    "IL": "America/Chicago",
    "IN": "America/Indiana/Indianapolis",
    "IA": "America/Chicago",
    "KS": "America/Chicago",
    "KY": "America/New_York",
    "LA": "America/Chicago",
    "ME": "America/New_York",
    "MD": "America/New_York",
    "MA": "America/New_York",
    "MI": "America/Detroit",
    "MN": "America/Chicago",
    "MS": "America/Chicago",
    "MO": "America/Chicago",
    "MT": "America/Denver",
    "NE": "America/Chicago",
    "NV": "America/Los_Angeles",
    "NH": "America/New_York",
    "NJ": "America/New_York",
    "NM": "America/Denver",
    "NY": "America/New_York",
    "NC": "America/New_York",
    "ND": "America/Chicago",
    "OH": "America/New_York",
    "OK": "America/Chicago",
    "OR": "America/Los_Angeles",
    "PA": "America/New_York",
    "RI": "America/New_York",
    "SC": "America/New_York",
    "SD": "America/Chicago",
    "TN": "America/Chicago",
    "TX": "America/Chicago",
    "UT": "America/Denver",
    "VT": "America/New_York",
    "VA": "America/New_York",
    "WA": "America/Los_Angeles",
    "WV": "America/New_York",
    "WI": "America/Chicago",
    "WY": "America/Denver",
}


def resolve_meet_timezone(state: str | None = None, explicit_timezone: str | None = None) -> str:
    """Resolve an IANA timezone for a meet, preferring an explicit override over the state table."""
    candidates = (explicit_timezone, STATE_TIMEZONES.get((state or "").strip().upper()))
    for candidate in candidates:
        if not candidate:
            continue
        try:
            ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError):
            continue
        return candidate
    return DEFAULT_TZ


def lsc_from_team_code(team: str | None) -> str | None:
    """Pull the swimmer's 2-letter LSC out of a parsed psych-sheet team code, or None.

    USA Swimming team codes carry the LSC as a 2-letter token: as a "CLUB-LSC" suffix on local
    meets (MAC-AZ, AASC-AZ, GM-AZ) or standing alone on zone/all-star sheets (AZ, SR). It is the
    trailing 2-letter token, so we scan from the end and take the first one -- a longer club
    abbreviation ahead of it is skipped. A bare club code with no LSC (e.g. "MAC") yields None, so
    the caller auto-detects nothing and the swimmer falls through exactly as a blank field does.
    """
    for token in reversed(re.split(r"[^A-Za-z0-9]+", (team or "").strip())):
        if re.fullmatch(r"[A-Za-z]{2}", token):
            return token.upper()
    return None


# USA Swimming LSC code -> the team display name that zone/all-star psych sheets print in place of a
# club code. A club meet prints the swimmer's own "CLUB-LSC" code on relay rows (MAC-AZ); a zone meet
# like WZAG groups athletes by LSC and prints the LSC's name ("Arizona"). This map is consulted only
# to match a swimmer to their OWN LSC's team-level relay rows -- an LSC missing here (or a name that
# does not match) simply yields no tentative team relay, never a false match, so the map degrades
# safely and can be extended as new zone meets appear. Western Zone LSCs (the ones that appear in the
# WZAG fixtures) are covered; names are the full canonical form so a column-truncated row still
# prefix-matches.
LSC_TEAM_NAMES = {
    "AZ": "Arizona",
    "PC": "Pacific",
    "PN": "Pacific Northwest",
    "SN": "Sierra Nevada Swimming",
    "SR": "Snake River",
    "CO": "Colorado",
    "OR": "Oregon",
    "UT": "Utah",
    "NM": "New Mexico",
    "SI": "San Diego-Imperial",
    "CC": "Central California",
    "IE": "Inland Empire",
    "HI": "Hawaii",
    "MT": "Montana",
    "AK": "Alaska",
    "WY": "Wyoming",
}


def _normalize_team_token(value: str) -> str:
    """Fold a team/LSC string to comparable form: lowercase, alphanumerics only (drops the spaces,
    hyphens, and punctuation that differ between 'San Diego-Imperial' and a printed 'San Diego Imperi')."""
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def relay_team_matches_swimmer(relay_team: str, swimmer_team: str) -> bool:
    """True when a team-level relay row belongs to the searched swimmer's own team.

    Two meet styles, told apart by the swimmer's own parsed team code:
      * club meet -- the swimmer's code is "CLUB-LSC" (e.g. MAC-AZ) and relay rows print the same
        code; an exact club-LSC match is required, so a different Arizona club (GM-AZ) never matches.
      * zone/all-star meet -- the swimmer's code is a bare LSC (e.g. AZ) and relay rows print the
        LSC's display name ("Arizona"); the LSC code or that name matches, normalized and
        prefix-aware so a column-truncated "Sierra Nevada Sw" still matches "Sierra Nevada Swimming".
    """
    swimmer_code = (swimmer_team or "").strip().upper()
    relay_token = _normalize_team_token(relay_team)
    if not swimmer_code or not relay_token:
        return False
    lsc = lsc_from_team_code(swimmer_team)
    is_bare_lsc = bool(lsc) and swimmer_code == lsc
    if not is_bare_lsc:
        return _normalize_team_token(swimmer_team) == relay_token
    if relay_token == _normalize_team_token(lsc):
        return True
    name = LSC_TEAM_NAMES.get(lsc)
    if not name:
        return False
    norm_name = _normalize_team_token(name)
    # Exact, or the row is a truncation of the full name (>= 8 chars so short codes must match
    # exactly -- "Pacific" never swallowed by "Pacific Northwest").
    return relay_token == norm_name or (len(relay_token) >= 8 and norm_name.startswith(relay_token))


def relay_age_eligible(event_name: str, age: int | None) -> bool:
    """Whether a swimmer of ``age`` could swim a relay of this event group ("12 & Under", "13-14",
    "15 & Over"). Unknown/open groups do not exclude. Age unknown never excludes."""
    if age is None:
        return True
    text = event_name.lower()
    match = re.search(r"(\d{1,2})\s*&\s*(?:under|younger)", text)
    if match:
        return age <= int(match.group(1))
    match = re.search(r"(\d{1,2})\s*&\s*(?:over|older)", text)
    if match:
        return age >= int(match.group(1))
    match = re.search(r"\b(\d{1,2})\s*-\s*(\d{1,2})\b", text)
    if match:
        return int(match.group(1)) <= age <= int(match.group(2))
    return True


def relay_gender_eligible(event_name: str, gender: str | None) -> bool:
    """Whether a swimmer of ``gender`` could swim this relay. A Mixed relay (no gender word) admits
    both; a Girls/Women or Boys/Men relay admits only that gender. Unknown swimmer gender never
    excludes."""
    relay_gender = event_gender(event_name)
    return relay_gender is None or gender is None or relay_gender == gender


# A team-level relay entry row on a psych sheet: "A 2:18.00Arizona16" (zone: LSC name) or
# "A 2:05.74MAC-AZ8" (club: club-LSC code), optionally with a "W54"/"M56" gender-group prefix
# ("A 2:18.50W54MAC-AZ4"). Groups: relay letter, seed, optional prefix, team, trailing rank.
TEAM_RELAY_ROW = re.compile(
    r"^(?P<label>[A-Z])\s+(?P<seed>\d+(?::\d+)?\.\d+)(?P<prefix>[WMX]\d+)?"
    r"(?P<team>[A-Za-z][A-Za-z .&'/-]*?)(?P<rank>\d+)$"
)


def extract_team_relay_entries(
    psych_pdf: Path, swimmer_team: str, swimmer_age: int | None, swimmer_gender: str | None
) -> list[RelayEntry]:
    """Tentative "your team is entered, leg unknown" relays read from the psych sheet's team-level
    relay rows -- the middle ground when no leg-naming source exists for an event.

    One entry per relay event the swimmer's OWN team is entered in and the swimmer is age/gender
    eligible for. Never names a roster (there is none in this data) and never asserts a specific
    relay letter or leg -- it only says the team is entered, so the caller renders it tentative.
    """
    if not swimmer_team:
        return []
    pages = extract_text_pages(psych_pdf)
    event_header_re = re.compile(r"(?:#|Event)\s*(\d+)\s+(.+)$", re.IGNORECASE)
    seen_events: set[int] = set()
    entries: list[RelayEntry] = []
    current: tuple[int, str] | None = None
    for page_number, text in enumerate(pages, start=1):
        for raw_line in text.splitlines():
            line = normalize_space(raw_line)
            header = event_header_re.match(line)
            if header:
                current = (int(header.group(1)), normalize_event_header_name(header.group(2)))
                continue
            if current is None:
                continue
            event_number, event_name = current
            if "relay" not in event_name.lower() or event_number in seen_events:
                continue
            row = TEAM_RELAY_ROW.match(line)
            if row is None or not relay_team_matches_swimmer(row.group("team"), swimmer_team):
                continue
            if not (relay_age_eligible(event_name, swimmer_age) and relay_gender_eligible(event_name, swimmer_gender)):
                continue
            seen_events.add(event_number)
            entries.append(
                RelayEntry(
                    event_number=event_number,
                    event_name=event_name,
                    relay_label="",
                    entry_time="",
                    leg=0,
                    page=page_number,
                    source_line=(
                        f"Psych sheet: {row.group('team').strip()} is entered in this relay event; "
                        "individual swimmers are not listed."
                    ),
                    source_label="Psych sheet (team entry)",
                    is_team_entry=True,
                )
            )
    return entries


def swimmer_relay_identity(entries: list[PsychEntry]) -> tuple[str, int | None, str | None]:
    """The swimmer's own (team code, age, gender), read from their matched individual psych entries,
    for matching team-level relay rows. Returns ('', None, None) when no individual events matched.

    The team code carries the granularity that relay matching needs -- a bare LSC ("AZ") at a zone
    meet vs a club-LSC ("MAC-AZ") at a club meet -- so it is taken verbatim, not reduced to its LSC.
    """
    if not entries:
        return "", None, None
    teams = Counter(entry.team for entry in entries if entry.team)
    swimmer_team = teams.most_common(1)[0][0] if teams else ""
    age = next((parse_age(entry.age) for entry in entries if parse_age(entry.age) is not None), None)
    gender = next((event_gender(entry.event_name) for entry in entries if event_gender(entry.event_name)), None)
    return swimmer_team, age, gender


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
    facility: str | None = None


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
    heat_is_estimated: bool = False
    estimate_note: str | None = None
    # A heat sheet said this heat runs in the evening finals session ("Swimming with Finals").
    # Informational: the flyer footnote rule still decides which timeline window is used, so this
    # records what the heat sheet stated without overriding logic that is already tested.
    swims_with_finals: bool = False
    # Which document supplied a REAL heat/lane, when one did. None means the heat/lane is either
    # absent or estimated -- see heat_is_estimated.
    heat_document: str | None = None


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
    timing_rule: "EventTimingRule | None" = None


@dataclass
class RelayEntry:
    event_number: int
    event_name: str
    relay_label: str
    entry_time: str
    leg: int
    page: int
    source_line: str
    source_label: str = "Relay document"
    is_private_source: bool = False
    # True for the "team entered, leg unknown" state: read from the psych sheet's team-level relay
    # rows (no roster, no specific leg), surfaced as a tentative calendar entry. Confirmed-leg
    # relays (a named relay PDF or private roster add-on) always take precedence over these.
    is_team_entry: bool = False
    # True when the roster source marked this relay's lineup as pending a change (e.g. a listed
    # swimmer withdrew and the replacement was not yet published). Confirmed, but flagged as subject
    # to change so it is never presented as settled as the rest.
    lineup_pending: bool = False


@dataclass
class RelayEvent:
    relay: RelayEntry
    timeline: TimelineEvent
    finals_note: str


@dataclass(frozen=True)
class EventTimingRule:
    event_numbers: frozenset[int]
    kind: str
    top_seed_count: int | None
    note: str
    source: str


def extract_text_pages(path: Path) -> list[str]:
    cache_key = pdf_cache_key(path)
    return list(cached_text_pages(*cache_key))


@lru_cache(maxsize=96)
def cached_text_pages(path: str, mtime_ns: int, size: int) -> tuple[str, ...]:
    del mtime_ns, size
    reader = PdfReader(str(path))
    return tuple(page.extract_text() or "" for page in reader.pages)


def extract_fragments(path: Path) -> list[Fragment]:
    cache_key = pdf_cache_key(path)
    return list(cached_fragments(*cache_key))


@lru_cache(maxsize=48)
def cached_fragments(path: str, mtime_ns: int, size: int) -> tuple[Fragment, ...]:
    del mtime_ns, size
    reader = PdfReader(str(path))
    fragments: list[Fragment] = []
    for page_index, page in enumerate(reader.pages, start=1):
        def visitor(text: str, _cm, tm, _font_dict, _font_size) -> None:
            clean = text.strip("\n")
            if clean.strip():
                fragments.append(Fragment(page_index, float(tm[4]), float(tm[5]), clean))

        page.extract_text(visitor_text=visitor)
    return tuple(fragments)


def pdf_cache_key(path: Path) -> tuple[str, int, int]:
    resolved = path.resolve()
    stat = resolved.stat()
    return str(resolved), stat.st_mtime_ns, stat.st_size


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


def single_indel(left: str, right: str) -> bool:
    """True when the two strings differ by exactly one inserted or deleted character (a dropped or
    doubled letter -- the common typo shape, e.g. 'mil'/'mila', 'rosetti'/'rossetti').

    Deliberately EXCLUDES same-length single substitutions: those too often distinguish two real,
    different swimmers ('Marco'/'Mario', 'Prima'/'Priya', 'Amy'/'Andy'), so treating them as a typo
    silently merged distinct people. Verified against the full WZAG psych sheet: indel-only matching
    produces zero different-swimmer collisions while still catching real dropped/doubled-letter typos.
    """
    return abs(len(left) - len(right)) == 1 and levenshtein(left, right) == 1


def close_name_pair(query: tuple[str, str], candidate: tuple[str, str]) -> bool:
    query_first, query_last = query
    candidate_first, candidate_last = candidate
    # One name component must match exactly and the other differ by a single indel. This is tighter
    # than the old rule (which allowed a substitution on one half, or a first-letters-match +
    # edit-distance-2 fallback) -- both of those merged distinct real swimmers.
    if query_first == candidate_first and single_indel(query_last, candidate_last):
        return True
    if query_last == candidate_last and single_indel(query_first, candidate_first):
        return True
    return False


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
    clean, heat, round_name = normalize_entry_line(line)
    return parse_entry_fields(clean, heat, round_name)


def parse_entry_fields(clean: str, heat: int | None, round_name: str | None) -> PsychLine | None:
    """Parse an already-normalized entry row into a PsychLine.

    ``heat``/``round_name`` are the resolved heat context for this row -- either a
    header found on the row itself or one carried forward by the caller's cursor from
    an earlier ``Heat N of M`` line in the same event. When a heat is in effect the row
    comes from a heat sheet and the trailing number is the swimmer's lane; otherwise the
    row is from a psych/seeded list and the trailing number is the seed place.
    """
    match = re.search(
        r"(?P<team>[A-Z0-9-]+?)\s*"
        r"(?P<seed>(?:NT|(?:\d+:)?\d{1,2}\.\d{2}[A-Z]?))\s*"
        r"(?P<age>\d{1,2})\s*"
        r"(?P<name>.+?)\s*"
        r"(?P<place>\d+)"
        # A HY-TEK program with the time-standard column enabled prints the standard as its own
        # trailing token AFTER the lane ("Arizona 39.82L 12Cova, Mila2 B"). Without this the row
        # failed to match at all and was dropped silently -- losing the swim, not just the marker
        # (427 rows on the real WZAG Wednesday program alone). Optional and anchored after the
        # lane, so rows that already end in the number ("...Mila6", and the psych sheet's
        # marker-before-place form "...Mila B29") parse byte-identically to before.
        # (?-i:...) keeps the marker strictly uppercase despite the enclosing IGNORECASE, which
        # the team/name groups need -- otherwise title-case prose lines start matching as rows.
        r"(?:\s+(?P<standard>(?-i:[A-Z]{1,4})))?\s*$",
        clean,
        flags=re.IGNORECASE,
    )
    if not match:
        return parse_para_psych_line(clean, heat, round_name)
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


def parse_para_psych_line(line: str, heat: int | None, round_name: str | None) -> PsychLine | None:
    match = re.search(
        r"^(?:(?P<class_prefix>[A-Z]{1,3}\d{1,2})-)?"
        r"(?P<team>.+?)\s+"
        r"(?P<seed>NT|(?:\d+:)?\d{1,2}\.\d{2})\s*"
        r"(?P<name>.+?,\s*[A-Za-z][A-Za-z' .-]*?)\s+"
        r"(?P<class>[A-Z]{1,3}\d{1,2})\s+"
        r"(?P<age>\d{1,2})(?:\s+.*)?$",
        line,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    team = normalize_space(match.group("team"))
    class_prefix = match.group("class_prefix")
    if class_prefix:
        team = f"{class_prefix.upper()}-{team}"
    return PsychLine(
        team=team,
        seed=match.group("seed"),
        age=match.group("age"),
        swimmer_name=clean_swimmer_name(match.group("name")),
        seed_place=0,
        document_type="heat" if heat is not None else "psych",
        heat=heat,
        lane=None,
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


# The heat number must be followed by whitespace, "(", or end of line -- never by a decimal
# point -- so a swimmer row whose team code is literally "HEAT" ("HEAT 25.52 ..." with the
# seed glued on) is not misread as a heat header.
_HEAT_HEADER_RE = re.compile(r"^Heat\s+(?P<heat>\d+)(?=[\s(]|$)(?:\s+of\s+\d+)?\s*(?P<rest>.*)$", re.IGNORECASE)
# "(#N Event Name)" continuation reference. Widened for combined blocks, which reference BOTH
# events and may carry no name at all ("(#21 / 22 )").
_EVENT_PAREN_RE = re.compile(r"\(\s*#\s*(?P<num>\d+(?:\s*/\s*\d+)*)\s*(?P<name>[^)]*?)\s*\)")
# A combined girls/boys block labels each heat with the event's OWN heat number and gender:
# "Heat 5   (Heat 3 Girls 800 Free)" is girls heat 3, not heat 5. Requires the literal "Heat"
# straight after "(" and forbids "#", so it can never collide with the continuation form above.
_HEAT_PAREN_RE = re.compile(r"\(\s*Heat\s+(?P<heat>\d+)\s+(?P<label>[^)#]*?)\s*\)", re.IGNORECASE)
# "Swimming with Finals" marks a heat that runs in the evening finals session. It is a session
# qualifier appended to the round label ("Finals - Swimming with Finals"), not a round word.
_SWIMS_WITH_FINALS_RE = re.compile(r"(?:\s*-\s*)?\bSwimming\s+with\s+Finals", re.IGNORECASE)
_GENDER_WORD_RE = re.compile(r"\b(Girls|Boys|Women|Men|Mixed)\b", re.IGNORECASE)
# Event header, possibly naming several events at once: "Event 21 / 22  Girls / Boys 13-14 800 Free".
_EVENT_HEADER_RE = re.compile(r"(?:#|Event)\s*(?P<nums>\d+(?:\s*/\s*\d+)*)\s+(?P<name>.+)$", re.IGNORECASE)
_DUAL_GENDER_NAME_RE = re.compile(
    r"^(?P<genders>(?:Girls|Boys|Women|Men|Mixed)(?:\s*/\s*(?:Girls|Boys|Women|Men|Mixed))+)\s+(?P<rest>.+)$",
    re.IGNORECASE,
)


def split_event_header(line: str) -> list[tuple[int, str]] | None:
    """Every event named by an event header, as [(event_number, event_name), ...], or None.

    Normally one event. A HY-TEK program may also run two events as ONE combined block, naming
    both in order: "Event 21 / 22   Girls / Boys 13-14 800 Free" -> event 21 "Girls 13-14 800
    Free" and event 22 "Boys 13-14 800 Free". When the shape is not recognizable (gender count
    does not match event count) the full name is shared rather than guessed at.
    """
    match = _EVENT_HEADER_RE.match(line)
    if not match:
        return None
    numbers = [int(part) for part in re.split(r"\s*/\s*", match.group("nums"))]
    name = normalize_event_header_name(match.group("name"))
    if len(numbers) == 1:
        return [(numbers[0], name)]
    dual = _DUAL_GENDER_NAME_RE.match(name)
    if not dual:
        return [(number, name) for number in numbers]
    genders = re.split(r"\s*/\s*", dual.group("genders"))
    if len(genders) != len(numbers):
        return [(number, name) for number in numbers]
    return [
        (number, normalize_space(f"{gender.title()} {dual.group('rest')}"))
        for number, gender in zip(numbers, genders)
    ]
_SEED_TOKEN_RE = re.compile(r"(?:NT|(?:\d+:)?\d{1,2}\.\d{2})", re.IGNORECASE)
# Used ONLY to peel a round word off the front of a concatenated header line so the first
# swimmer's team code stays clean (e.g. "PrelimsSYS-FL 2:13..." -> round "Prelims", team
# "SYS-FL"). Recognition of the heat header itself does NOT depend on this list. Each entry
# ends in a fixed spelling (no optional trailing "s") so it can never swallow a team code's
# leading letter; arbitrary/lettered round labels come through the own-line branch instead.
_KNOWN_ROUND_RE = re.compile(
    r"(?P<round>Timed\s+Finals|Prelims|Finals|Semi-?finals|Swim-?off)",
    re.IGNORECASE,
)


@dataclass
class HeatHeader:
    heat: int
    round_name: str | None
    event_number: int | None
    event_name: str | None
    swimmer_remainder: str
    # Combined-block support. sub_heat/sub_gender come from a "(Heat N Girls ...)" label: the
    # event's OWN heat number and which event of the block this heat belongs to. event_numbers is
    # set instead of event_number when a continuation reference names several events ("(#21 / 22 )").
    sub_heat: int | None = None
    sub_gender: str | None = None
    event_numbers: tuple[int, ...] | None = None
    swims_with_finals: bool = False


def parse_heat_header(clean: str) -> HeatHeader | None:
    """Recognize a heat header purely from ``Heat N [of M] ...`` structure.

    Everything after the heat number is treated as an OPAQUE round label -- it does not
    have to match a known list, so "Prelims", "Timed Finals", and "C - Final" are all
    recognized. A HY-TEK program may print the header on its own line (round label only) or
    concatenate the heat's first swimmer onto the same line, and a "(#N Event Name)"
    continuation reference may be present (it is how an event's later heats are labeled when
    they spill onto a new page). Returns the heat number, round label, any event reference,
    and the leftover swimmer text ("" when the header sits on its own line). Returns None
    when the line is not a heat header.
    """
    match = _HEAT_HEADER_RE.match(clean)
    if not match:
        return None
    heat = int(match.group("heat"))
    rest = match.group("rest")

    # A combined block's per-heat label comes first: it carries the event's own heat number and the
    # gender that selects which event of the block this heat is.
    sub_heat = sub_gender = None
    heat_paren = _HEAT_PAREN_RE.search(rest)
    if heat_paren:
        sub_heat = int(heat_paren.group("heat"))
        gender = _GENDER_WORD_RE.search(heat_paren.group("label"))
        sub_gender = gender.group(1).title() if gender else None
        rest = normalize_space(rest[: heat_paren.start()] + " " + rest[heat_paren.end() :])

    event_number = event_name = None
    event_numbers = None
    paren = _EVENT_PAREN_RE.search(rest)
    if paren:
        numbers = [int(part) for part in re.split(r"\s*/\s*", paren.group("num"))]
        raw_name = paren.group("name").strip()
        if len(numbers) == 1:
            event_number = numbers[0]
            # An empty name ("(#21 )") must not clobber a known event name.
            event_name = normalize_event_header_name(raw_name) if raw_name else None
        else:
            # References the whole combined block, not one event -- the per-heat label above is
            # what resolves the event, so do not pin event_number here.
            event_numbers = tuple(numbers)
        rest = normalize_space(rest[: paren.start()] + " " + rest[paren.end() :])

    swims_with_finals = bool(_SWIMS_WITH_FINALS_RE.search(rest))
    if swims_with_finals:
        rest = normalize_space(_SWIMS_WITH_FINALS_RE.sub(" ", rest))

    if not _SEED_TOKEN_RE.search(rest):
        # Header on its own line: the whole remainder is the round label; no swimmer here.
        return HeatHeader(
            heat, rest.strip() or None, event_number, event_name, "",
            sub_heat, sub_gender, event_numbers, swims_with_finals,
        )

    # A swimmer is concatenated onto this line. Peel a known round word off the front so the
    # team code stays clean; if the label is unfamiliar, leave the row intact (best effort).
    known = _KNOWN_ROUND_RE.match(rest)
    if known:
        round_name = known.group("round").strip()
        remainder = rest[known.end() :].strip()
    else:
        round_name = None
        remainder = rest.strip()
    return HeatHeader(
        heat, round_name or None, event_number, event_name, remainder,
        sub_heat, sub_gender, event_numbers, swims_with_finals,
    )


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
    auto_place_by_event: dict[int, int] = {}

    # Heat sheets (HY-TEK Meet Programs) print a "Heat N [of M] <Round>" header per heat --
    # sometimes on its own line, sometimes concatenated onto the first swimmer's row -- and
    # the rest of the heat follows on bare rows with no heat marker. Two forward cursors
    # carry context the way a reader's eye does:
    #   * heat/round: so every swimmer in a heat gets its heat and lane, not just the first.
    #   * event: so an event that spills across a page break -- where the next page begins
    #     with a "Heat N (#event)" continuation header instead of a repeated "Event N" line
    #     -- still associates its rows correctly. A page-scoped header lookup used to drop
    #     those rows silently.
    # The event cursor persists across pages; it is replaced by a new event header or by a
    # continuation reference. The heat cursor is cleared by a new event header, an
    # "Alternates" section, or the next heat header. Neither is cleared by page-break junk,
    # so a heat or event split across a page boundary survives.
    # A third cursor holds the current COMBINED block, when the header named two events at once
    # ("Event 21 / 22  Girls / Boys 13-14 800 Free"). Inside such a block the event is not known
    # until a heat's own "(Heat N Girls ...)" label says which event that heat belongs to.
    current_event: tuple[int, str] | None = None
    current_block: list[tuple[int, str]] | None = None
    current_heat: int | None = None
    current_round: str | None = None
    current_swims_with_finals = False

    for page_number, text in enumerate(pages, start=1):
        lines = text.splitlines()
        count = 0
        for index, line in enumerate(lines):
            normalized = normalize_space(line)

            event_split = split_event_header(normalized)
            if event_split:
                current_block = event_split
                # A combined block waits for a per-heat label to resolve the event.
                current_event = event_split[0] if len(event_split) == 1 else None
                current_heat = None
                current_round = None
                current_swims_with_finals = False
                continue

            if re.match(r"Alternates?\b", normalized, flags=re.IGNORECASE):
                # Finals-sheet alternates are not assigned to a heat; end the heat block.
                current_heat = None
                current_round = None
                current_swims_with_finals = False
                continue

            heat_header = parse_heat_header(normalized)
            if heat_header is not None:
                current_heat = heat_header.heat
                current_round = heat_header.round_name
                current_swims_with_finals = heat_header.swims_with_finals
                if heat_header.sub_gender and current_block:
                    # Combined block: the heat's own label is authoritative for BOTH which event
                    # this heat belongs to and its real heat number (block heat 5 = girls heat 3).
                    for number, name in current_block:
                        if name.lower().startswith(heat_header.sub_gender.lower()):
                            current_event = (number, name)
                            break
                    if heat_header.sub_heat is not None:
                        current_heat = heat_header.sub_heat
                elif heat_header.event_number is not None:
                    # A continuation reference may omit the name; keep the one already in hand.
                    current_event = (
                        heat_header.event_number,
                        heat_header.event_name or (current_event[1] if current_event else ""),
                    )
                clean = heat_header.swimmer_remainder
                if not clean:
                    continue  # header on its own line; swimmers follow on later rows
            else:
                clean, _, _ = normalize_entry_line(line)

            row = parse_entry_fields(clean, current_heat, current_round)
            if row is None:
                continue
            if current_event is None:
                continue
            event_number, event_name = current_event
            seed_place = row.seed_place
            if seed_place <= 0:
                seed_place = auto_place_by_event.get(event_number, 0) + 1
            auto_place_by_event[event_number] = max(auto_place_by_event.get(event_number, 0), seed_place)
            match_type = match_swimmer_name(row.swimmer_name, patterns, query_pairs, allow_fuzzy=allow_fuzzy)
            if not match_type:
                continue
            count += 1
            entries.append(
                PsychEntry(
                    day="",
                    event_number=event_number,
                    event_name=event_name,
                    seed_time=row.seed,
                    seed_place=seed_place,
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
                    swims_with_finals=current_swims_with_finals if row.heat is not None else False,
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
        # A partial query is matched as a SUBSTRING, so a short surname can be a prefix of a longer
        # one ("Stein" also matches "Steinbis"). That counts as an exact match, so the fuzzy path's
        # ambiguity guard never saw it, and the different swimmers' events were silently merged into
        # one calendar under the typed name -- mixing sessions, warm-up lanes, ages and genders.
        candidates = ambiguous_swimmer_candidates(entries)
        if candidates:
            return [], [], [
                f"'{swimmer_name}' matches more than one swimmer at this meet "
                f"({', '.join(candidates)}). Their events would be merged into a single calendar, "
                "so none was generated -- search a more specific name (include the first name)."
            ]
        return entries, page_counts, []

    entries, page_counts = collect_psych_entries(pages, fragments, patterns, query_pairs, allow_fuzzy=True)
    return resolve_fuzzy_match(swimmer_name, entries, page_counts)


def ambiguous_swimmer_candidates(entries: list[PsychEntry]) -> list[str] | None:
    """Display names of the distinct swimmers these entries resolve to, when there is MORE than one.

    None when they all resolve to a single swimmer -- one real swimmer legitimately appears as
    several rows whose printed names differ ("Stein, Layla B", "Stein, Layla WZAG"), and
    distinct_swimmer_pairs already folds those together.
    """
    if len(distinct_swimmer_pairs(entries)) <= 1:
        return None
    return sorted({display_first_last(entry.matched_name) or entry.matched_name for entry in entries})


def resolve_fuzzy_match(
    swimmer_name: str, entries: list[PsychEntry], page_counts: list[dict]
) -> tuple[list[PsychEntry], list[dict], list[str]]:
    """Decide what a fuzzy (no-exact-match) fallback should return.

    If the fuzzy pass matched more than one DIFFERENT swimmer, refuse: merging their events under
    one calendar (labeled with just the first swimmer's name -- see resolved_swimmer_name) would
    silently combine strangers, so surface the ambiguity and ask for a more specific name instead.
    A single resolved swimmer passes through with a high-confidence notice.
    """
    if not entries:
        return entries, page_counts, []
    candidates = ambiguous_swimmer_candidates(entries)
    if candidates:
        return [], [], [
            f"'{swimmer_name}' closely matches more than one swimmer ({', '.join(candidates)}). "
            "No exact match was found; please search a more specific name (include the first name)."
        ]
    matched_names = sorted({entry.matched_name for entry in entries if entry.matched_name})
    warnings = (
        ["No exact swimmer-name match was found. Used high-confidence match: " + ", ".join(matched_names) + "."]
        if matched_names
        else []
    )
    return entries, page_counts, warnings


def distinct_swimmer_pairs(entries: list[PsychEntry]) -> set[tuple[str, str]]:
    """The set of distinct (first, last) swimmers among matched entries, by normalized name. One
    real swimmer can appear as several psych rows ('Cova, Mila B', 'Cova, Mila WZAG') that all
    normalize to a single pair; more than one pair means the query resolved to different people."""
    pairs: set[tuple[str, str]] = set()
    for entry in entries:
        matched = name_pairs(entry.matched_name)
        if matched:
            pairs.add(matched[0])
    return pairs


def resolved_relay_query(swimmer_name: str, entries: list[PsychEntry]) -> str:
    """The name to hand to relay matching. Individual events match by substring/regex, so a
    last-name-only query ('Cova') still finds the right PsychEntry -- but relay matching hashes a
    full (first, last) pair, which a partial query cannot produce (name_pairs('Cova') == []). When
    the found entries resolve to exactly ONE swimmer, use that swimmer's authoritative full name so
    relays resolve too. When the query is ambiguous (several distinct swimmers) or found nothing,
    fall back to the raw query -- never guess one of several swimmers."""
    pairs = distinct_swimmer_pairs(entries)
    if len(pairs) == 1 and entries:
        return entries[0].matched_name or swimmer_name
    return swimmer_name


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
        r"(?P<event_name>.+?Relay)(?:\s+<=\S+)*\s+Relay\s+(?P<label>[A-Z])\s+\(Entry:\s*(?P<entry>[^)]+)\)",
        re.IGNORECASE,
    )
    relay_event_header = re.compile(
        r"^(?P<event>\d+)[A-Z]?\s+\d+\s+(?P<session>\d+)\s+(?P<course>[A-Z]+)\s+(?P<group>[A-Z])\s+"
        r"(?P<event_name>.+?Relay)(?:\s+<=\S+)?$",
        re.IGNORECASE,
    )
    relay_continuation = re.compile(r"^Relay\s+(?P<label>[A-Z])\s+\(Entry:\s*(?P<entry>[^)]+)\)", re.IGNORECASE)
    swimmer_line = re.compile(r"^(?:<=\S+\s+)*(?P<leg>[1-8])\.\s+(?P<name>.+)$")

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

            event_header = relay_event_header.match(line)
            if event_header:
                current = {
                    "event_number": int(event_header.group("event")),
                    "session_number": int(event_header.group("session")),
                    "course": event_header.group("course").upper(),
                    "event_name": relay_event_name(event_header.group("group"), event_header.group("event_name")),
                    "relay_label": "",
                    "entry_time": "",
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


def extract_internal_relay_entries(relay_sources: Iterable[Path] | None, swimmer_name: str) -> tuple[list[RelayEntry], list[str]]:
    if not relay_sources:
        return [], []

    relays: list[RelayEntry] = []
    warnings: list[str] = []
    for source in relay_sources:
        data = json.loads(source.read_text(encoding="utf-8"))
        label = str(data.get("source_label") or data.get("label") or "Relay add-on")
        salt = str(data.get("salt") or "")
        query_hashes = relay_hashes_for_swimmer(salt, swimmer_name)
        matched_count = 0
        pending_matched = 0
        for row in data.get("entries", []):
            matched_leg = matching_relay_leg(row.get("swimmers", []), query_hashes)
            if matched_leg is None:
                continue
            matched_count += 1
            lineup_pending = bool(row.get("lineup_pending"))
            if lineup_pending:
                pending_matched += 1
            note = (
                f"{label}: swimmer match verified without displaying roster names."
                if not lineup_pending
                else f"{label}: swimmer match verified; this relay's lineup is pending a change, confirm with the coach."
            )
            relays.append(
                RelayEntry(
                    event_number=int(row["event_number"]),
                    event_name=str(row["event_name"]),
                    relay_label=str(row["relay_label"]),
                    entry_time=str(row["entry_time"]),
                    leg=matched_leg,
                    page=int(row.get("page") or 0),
                    source_line=note,
                    source_label=label,
                    is_private_source=True,
                    lineup_pending=lineup_pending,
                )
            )
        if matched_count:
            warnings.append(f"{label} selected. Relay lineups may change; confirm final assignments with coach or official postings.")
        else:
            warnings.append(f"{label} selected, but no relay rows matched the swimmer name.")
        if pending_matched:
            warnings.append(
                f"{pending_matched} of your relays are flagged as pending a lineup change (a listed "
                "swimmer withdrew); confirm the final lineup with your coach."
            )
    return dedupe_relay_entries(relays), warnings


def relay_roster_event_numbers(
    relay_pdf: Path | None, internal_relay_sources: Iterable[Path] | None
) -> set[int]:
    """Every event number for which a real roster (a named relay PDF or a private roster add-on)
    enumerates a lineup -- regardless of which swimmer is being searched.

    A roster proves exactly who is on an event, so once one covers an event the team-entered
    tentative heuristic must be suppressed there for EVERY swimmer, not only those who happen to
    match a leg. That is what this set feeds.
    """
    covered: set[int] = set()
    for source in internal_relay_sources or []:
        try:
            data = json.loads(Path(source).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for row in data.get("entries", []):
            try:
                covered.add(int(row["event_number"]))
            except (KeyError, TypeError, ValueError):
                continue
    if relay_pdf is not None:
        covered |= relay_pdf_event_numbers(relay_pdf)
    return covered


def relay_pdf_event_numbers(relay_pdf: Path) -> set[int]:
    """Event numbers a relay PDF enumerates a lineup for (any event with at least one named leg),
    independent of the searched swimmer -- the PDF analogue of the roster-coverage set."""
    pages = extract_text_pages(relay_pdf)
    relay_header = re.compile(
        r"^(?P<event>\d+)[A-Z]?\s+\d+\s+\d+\s+[A-Z]+\s+[A-Z]\s+.+?Relay",
        re.IGNORECASE,
    )
    swimmer_line = re.compile(r"^(?:<=\S+\s+)*[1-8]\.\s+.+$")
    covered: set[int] = set()
    current_event: int | None = None
    for text in pages:
        for raw_line in text.splitlines():
            line = normalize_space(raw_line)
            header = relay_header.match(line)
            if header:
                current_event = int(header.group("event"))
                continue
            if current_event is not None and swimmer_line.match(line):
                covered.add(current_event)
    return covered


def matching_relay_leg(swimmers: object, query_hashes: set[str]) -> int | None:
    if not isinstance(swimmers, list):
        return None
    for swimmer in swimmers:
        if not isinstance(swimmer, dict):
            continue
        stored_hashes = {str(value) for value in swimmer.get("hashes", [])}
        if stored_hashes & query_hashes:
            return int(swimmer.get("leg") or 0)
    return None


def relay_hashes_for_swimmer(salt: str, swimmer_name: str) -> set[str]:
    return {relay_name_hash(salt, first, last) for first, last in name_pairs(swimmer_name)}


def relay_name_hash(salt: str, first: str, last: str) -> str:
    return hashlib.sha256(f"{salt}|{first} {last}".encode("utf-8")).hexdigest()


def dedupe_relay_entries(relays: list[RelayEntry]) -> list[RelayEntry]:
    by_key: dict[tuple[int, str, int], RelayEntry] = {}
    for relay in relays:
        by_key.setdefault((relay.event_number, relay.relay_label, relay.leg), relay)
    return sorted(by_key.values(), key=lambda relay: (relay.event_number, relay.relay_label, relay.leg))


def relay_event_name(group_code: str, event_name: str) -> str:
    group_map = {"G": "Girls", "B": "Boys", "W": "Women", "M": "Men", "X": "Mixed"}
    prefix = group_map.get(group_code.upper(), group_code.upper())
    return normalize_space(f"{prefix} {event_name}")


def parse_date_range(text: str) -> tuple[date, date] | None:
    match = re.search(
        r"(\d{1,2})/(\d{1,2})/(\d{4})\s+to\s+(\d{1,2})/(\d{1,2})/(\d{4})",
        text,
    )
    if match:
        sm, sd, sy, em, ed, ey = map(int, match.groups())
        return date(sy, sm, sd), date(ey, em, ed)

    named_match = re.search(
        r"\b([A-Za-z]+)\s+(\d{1,2})\s*(?:-|–|—)\s*(?:(?:[A-Za-z]+)\s+)?(\d{1,2}),\s*(\d{4})",
        text,
    )
    if named_match:
        month_name, start_day, end_day, year = named_match.groups()
        month = month_number(month_name)
        if month:
            return date(int(year), month, int(start_day)), date(int(year), month, int(end_day))
    return None


def month_number(month_name: str) -> int | None:
    months = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }
    return months.get(month_name.casefold())


def parse_meet_name(text: str) -> str:
    full_text = normalize_space(text)
    championship_match = re.search(
        r"\b(20\d{2}\s+U\.S\.\s+Paralympics Swimming National Championships)\b",
        full_text,
        flags=re.IGNORECASE,
    )
    if championship_match:
        return championship_match.group(1)
    for line in text.splitlines():
        clean = normalize_space(line)
        if " - " in clean and re.search(r"\d{1,2}/\d{1,2}/\d{4}\s+to\s+\d{1,2}/\d{1,2}/\d{4}", clean):
            return clean.split(" - ", 1)[0]
    for line in text.splitlines():
        clean = normalize_space(line)
        lower = clean.lower()
        if "invite" in lower or "invitational" in lower or " open" in lower or "championship" in lower or "nationals" in lower:
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
    grouped_line_pattern = re.compile(
        r"Sessions?\s+(?P<sessions>[IVX,\s]+)\s+.+?:\s*Warm[- ]?up:?\s*(?P<warm>\d{1,2}:\d{2})\s*(?P<warm_ampm>[ap]\.?m\.?)\s*Meet\s+Start:?\s*(?P<start>\d{1,2}:\d{2})\s*(?P<start_ampm>[ap]\.?m\.?)",
        flags=re.IGNORECASE,
    )
    for line in text.splitlines():
        clean = normalize_space(line).replace("a m", "am").replace("p m", "pm")
        match = line_pattern.search(clean)
        if match:
            num = int(match.group("num"))
            facility = normalize_space(match.group("facility")).upper().replace("SKYLINE", "Skyline")
            sessions[num] = {
                "warmup_time": normalize_pdf_time(match.group("warm"), match.group("warm_ampm")),
                "start_time": normalize_pdf_time(match.group("start"), match.group("start_ampm")),
                "facility": facility.title(),
            }
            continue
        grouped_match = grouped_line_pattern.search(clean)
        if grouped_match:
            for num in parse_roman_session_numbers(grouped_match.group("sessions")):
                session = sessions.setdefault(num, {})
                session["warmup_time"] = normalize_pdf_time(grouped_match.group("warm"), grouped_match.group("warm_ampm"))
                session["start_time"] = normalize_pdf_time(grouped_match.group("start"), grouped_match.group("start_ampm"))
    return sessions


def parse_flyer_location(text: str) -> str | None:
    for line in text.splitlines():
        clean = normalize_space(line)
        match = re.search(r"\bMeet Location:\s*(.+)", clean, flags=re.IGNORECASE)
        if match:
            return normalize_space(match.group(1)).rstrip(".")
    return None


def parse_meet_timing_rules(flyer_text: str) -> dict[int, EventTimingRule]:
    text = normalize_space(flyer_text)
    rules: dict[int, EventTimingRule] = {}
    for rule in explicit_timed_final_rules(text):
        for event_number in rule.event_numbers:
            rules[event_number] = rule
    for rule in footnoted_timed_final_rules(text):
        for event_number in rule.event_numbers:
            rules.setdefault(event_number, rule)
    return rules


def explicit_timed_final_rules(text: str) -> list[EventTimingRule]:
    rules: list[EventTimingRule] = []
    pattern = re.compile(
        r"\bEvents?\s+(\d{1,3})(?:\s*(?:-|–|—|to|through)\s*(\d{1,3}))?"
        r"(?P<body>.{0,360}?timed final events?.{0,360}?)(?=\s+[a-z]\.\s+Events?\s+\d|\s+\d+\.\s|$)",
        flags=re.IGNORECASE,
    )
    for match in pattern.finditer(text):
        body = normalize_space(match.group("body"))
        if "timed final" not in body.lower():
            continue
        event_numbers = range_from_match(match)
        top_seed_count = top_seed_count_from_text(body)
        kind = "timed_final_fastest_heat_finals" if top_seed_count else "timed_final"
        note = (
            f"Meet flyer rule detected: timed final; top {top_seed_count} seeded swimmer(s) in each applicable age/gender group swim during finals."
            if top_seed_count
            else "Meet flyer rule detected: timed final event."
        )
        rules.append(EventTimingRule(frozenset(event_numbers), kind, top_seed_count, note, "meet flyer"))
    return rules


def footnoted_timed_final_rules(text: str) -> list[EventTimingRule]:
    if "fastest seeded heat" not in text.lower() or "(A)" not in text and "(B)" not in text:
        return []
    footnote_kinds: dict[str, tuple[str, int | None, str]] = {}
    if re.search(r"\(A\).*?timed finals?.*?fastest seeded heat.*?finals", text, flags=re.IGNORECASE):
        footnote_kinds["A"] = (
            "timed_final_fastest_heat_finals",
            8,
            "Meet flyer footnote A detected: timed final; fastest seeded heat swims in finals.",
        )
    if re.search(r"\(B\).*?800M/1500M.*?timed finals?.*?fastest seeded heat.*?finals", text, flags=re.IGNORECASE):
        footnote_kinds["B"] = (
            "timed_final_fastest_heat_finals",
            8,
            "Meet flyer footnote B detected: timed final distance event; fastest seeded heat swims in finals and other heats swim after prelims.",
        )
    rules: list[EventTimingRule] = []
    for marker, (kind, top_seed_count, note) in footnote_kinds.items():
        event_numbers = event_numbers_for_footnote(text, marker)
        if event_numbers:
            rules.append(EventTimingRule(frozenset(event_numbers), kind, top_seed_count, note, f"meet flyer footnote {marker}"))
    return rules


def event_numbers_for_footnote(text: str, marker: str) -> set[int]:
    numbers: set[int] = set()
    marker_pattern = re.escape(f"({marker})")
    for marker_match in re.finditer(marker_pattern, text):
        before = text[max(0, marker_match.start() - 120):marker_match.start()].strip()
        after = text[marker_match.end():marker_match.end() + 20]
        event_matches = list(
            re.finditer(
                r"\b(\d{1,3})\s+((?:10&U|11-12|12&U|13-14|14&U)\s+(?:\d{2,4}\s+)?(?:Individual Medley|Freestyle|Backstroke|Breaststroke|Butterfly))$",
                before,
                flags=re.IGNORECASE,
            )
        )
        boys_match = re.match(r"\s+(\d{1,3})\b", after)
        if not event_matches or not boys_match:
            continue
        match = event_matches[-1]
        event_name = match.group(2)
        if event_name_looks_like_individual_event(event_name):
            numbers.add(int(match.group(1)))
            numbers.add(int(boys_match.group(1)))
    return numbers


def event_name_looks_like_individual_event(value: str) -> bool:
    return bool(re.search(r"\b(Free|Freestyle|Back|Backstroke|Breast|Breaststroke|Fly|Butterfly|Medley|IM)\b", value, flags=re.IGNORECASE))


def range_from_match(match: re.Match) -> set[int]:
    start = int(match.group(1))
    end = int(match.group(2) or start)
    if end < start:
        start, end = end, start
    if end - start > 200:
        return {start}
    return set(range(start, end + 1))


def top_seed_count_from_text(text: str) -> int | None:
    match = re.search(r"\btop\s+(\d{1,2})\s+swimmers?\b", text, flags=re.IGNORECASE)
    if match:
        return int(match.group(1))
    if re.search(r"\bfastest seeded heat\b", text, flags=re.IGNORECASE):
        return 8
    return None


def parse_roman_session_numbers(value: str) -> list[int]:
    roman_map = {
        "I": 1,
        "II": 2,
        "III": 3,
        "IV": 4,
        "V": 5,
        "VI": 6,
        "VII": 7,
        "VIII": 8,
    }
    return [roman_map[token.upper()] for token in re.findall(r"\b[IVX]+\b", value) if token.upper() in roman_map]


def normalize_pdf_time(value: str, meridiem: str) -> str:
    meridiem = meridiem.upper().replace(".", "")
    hour, minute = parse_clock(f"{value} {meridiem}")
    return f"{hour:02d}:{minute:02d}"


def session_is_finals(session_name: str) -> bool:
    lower = session_name.lower()
    return "final" in lower and "prelim" not in lower and "distance" not in lower


def parse_timeline(
    timeline_pdf: Path, flyer_text: str = "", meet_venue: str | None = None
) -> tuple[str, dict[int, SessionInfo], list[TimelineEvent]]:
    cache_key = pdf_cache_key(timeline_pdf)
    return cached_timeline(*cache_key, flyer_text, meet_venue)


@lru_cache(maxsize=48)
def cached_timeline(
    path: str,
    mtime_ns: int,
    size: int,
    flyer_text: str = "",
    meet_venue: str | None = None,
) -> tuple[str, dict[int, SessionInfo], list[TimelineEvent]]:
    del mtime_ns, size
    timeline_pdf = Path(path)
    pages = extract_text_pages(timeline_pdf)
    text = "\n".join(pages)
    date_range = parse_date_range(text) or parse_date_range(flyer_text)
    if date_range is None:
        raise ValueError("Could not find meet date range in the timeline or flyer.")
    start_date, _end_date = date_range
    flyer_sessions = parse_flyer_sessions(flyer_text, start_date) if flyer_text else {}
    flyer_location = parse_flyer_location(flyer_text) if flyer_text else None
    meet_name = parse_meet_name(text)
    flyer_meet_name = parse_meet_name(flyer_text) if flyer_text else "Swim Meet"
    if "sarastoa" in meet_name.lower() and flyer_meet_name != "Swim Meet":
        meet_name = flyer_meet_name

    sessions: dict[int, SessionInfo] = {}
    events: list[TimelineEvent] = []

    session_header = re.compile(r"Session:\s*(\d+)\s+(.+)")
    day_header = re.compile(r"Day of Meet:\s*(\d+)\s+Starts at\s+(\d{1,2}:\d{2}\s*[AP]M)", re.IGNORECASE)
    event_line = re.compile(
        r"^(Prelims|Finals(?:-[A-Za-z0-9]+)?)\s+(\d+)\s+(.+?)\s+(\d+)\s+(\d+)\s+_+\s*(\d{1,2}:\d{2})\s*([AP]M)u?$",
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
                # Facility comes from the meet's OWN documents (flyer session line or "Meet
                # Location:" line) first, then the meet record's explicit venue. Never guessed
                # from the session name -- a "Finals" session is not evidence of any venue, and
                # guessing produced a fixed Arizona address on non-AZ meets (see meet_venue docs).
                facility = flyer_session.get("facility") or flyer_location or meet_venue
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
                        facility=current_session.facility,
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

    if not events:
        packet_sessions, packet_events = parse_meet_packet_schedule(text, meet_name, meet_venue)
        if packet_events:
            return meet_name, packet_sessions, packet_events

    return meet_name, sessions, events


def parse_meet_packet_schedule(text: str, meet_name: str, meet_venue: str | None = None) -> tuple[dict[int, SessionInfo], list[TimelineEvent]]:
    sessions: dict[int, SessionInfo] = {}
    events_by_session: dict[int, list[tuple[int, str]]] = {}
    current_session: SessionInfo | None = None
    current_warmup: str | None = None
    current_start: str | None = None
    next_session_number = 1
    facility = packet_facility(text) or meet_venue

    day_header = re.compile(
        r"^Day\s+(\d+):\s+([A-Za-z]+),\s+([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})\s*(?:-|–|—)\s*(.+?Session)\s*$",
        re.IGNORECASE,
    )
    warmup_line = re.compile(r"^Warmups?:\s*(\d{1,2}:\d{2})\s*([AP]M)", re.IGNORECASE)
    start_line = re.compile(r"^Start:\s*(\d{1,2}:\d{2})\s*([AP]M)", re.IGNORECASE)
    event_line = re.compile(r"^(\d{1,3})\.\s+(.+)$")

    for raw_line in text.splitlines():
        line = normalize_space(raw_line)
        if not line:
            continue
        day_match = day_header.match(line)
        if day_match:
            day_of_meet = int(day_match.group(1))
            month = month_number(day_match.group(3))
            if not month:
                current_session = None
                continue
            session_date = date(int(day_match.group(5)), month, int(day_match.group(4)))
            session_name = normalize_space(day_match.group(6))
            current_session = SessionInfo(
                number=next_session_number,
                name=session_name,
                day_of_meet=day_of_meet,
                date=session_date,
                start_time="",
                warmup_time=None,
                facility=facility,
            )
            sessions[current_session.number] = current_session
            events_by_session[current_session.number] = []
            next_session_number += 1
            current_warmup = None
            current_start = None
            continue

        if current_session is None:
            continue

        warmup_match = warmup_line.match(line)
        if warmup_match:
            current_warmup = normalize_pdf_time(warmup_match.group(1), warmup_match.group(2))
            current_session.warmup_time = current_warmup
            continue

        start_match = start_line.match(line)
        if start_match:
            current_start = normalize_pdf_time(start_match.group(1), start_match.group(2))
            current_session.start_time = current_start
            continue

        event_match = event_line.match(line)
        if event_match and current_start:
            event_number = int(event_match.group(1))
            event_name = normalize_space(event_match.group(2))
            events_by_session[current_session.number].append((event_number, event_name))

    events: list[TimelineEvent] = []
    for session in sessions.values():
        if not session.start_time:
            continue
        cursor = combine_date_time(session.date, session.start_time)
        round_name = "Finals" if "final" in session.name.lower() and "prelim" not in session.name.lower() else "Prelims"
        for event_number, event_name in events_by_session.get(session.number, []):
            duration = timedelta(minutes=estimated_schedule_event_minutes(event_name))
            start = cursor
            end = start + duration
            events.append(
                TimelineEvent(
                    event_number=event_number,
                    event_name=event_name,
                    round_name=round_name,
                    session_number=session.number,
                    session_name=session.name,
                    date=session.date,
                    start=start,
                    end=end,
                    entries=None,
                    heats=None,
                    facility=session.facility,
                )
            )
            cursor = end
        if events_by_session.get(session.number):
            session.finish_time = cursor.strftime("%H:%M")
    return sessions, events


def packet_facility(text: str) -> str | None:
    if "Idaho Central Aquatic Center" in text:
        return "Idaho Central Aquatic Center, Boise, ID"
    return None


def estimated_schedule_event_minutes(event_name: str) -> int:
    lower = event_name.lower()
    if "1500" in lower:
        return 60
    if "800" in lower:
        return 35
    if "400" in lower:
        return 25
    if "200" in lower:
        return 18
    if "150m" in lower or "150 m" in lower:
        return 14
    if "100" in lower:
        return 12
    if "relay" in lower:
        return 16
    return 8


def normalize_timeline_line(value: str) -> str:
    line = normalize_space(value)
    line = re.sub(r"^(Prelims|Finals(?:-[A-Za-z0-9]+)?)\s+(\d+)(?=[A-Za-z])", r"\1 \2 ", line, flags=re.IGNORECASE)
    line = re.sub(r"(?<=\d)(Girls|Boys|Women|Men)\b", r" \1", line, flags=re.IGNORECASE)
    line = re.sub(r"([A-Za-z)])(?=\d+\s+\d+\s+_+\s*\d{1,2}:\d{2})", r"\1 ", line)
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


def estimate_heat_lanes_for_entries(entries: list[PsychEntry], timeline_events: list[TimelineEvent], flyer_text: str) -> list[str]:
    if not entries:
        return []
    excluded_events, has_unscoped_seeded_rule = circle_or_deck_seeded_events(flyer_text, timeline_events)
    if has_unscoped_seeded_rule and not excluded_events:
        return [
            "Estimated heat/lane was requested, but this meet appears to mention circle or deck seeding without a parseable event range. Heat/lane estimates were not added."
        ]

    by_event = primary_timeline_by_event(timeline_events)
    estimated_count = 0
    skipped_events: set[int] = set()
    missing_heat_count_events: set[int] = set()
    for entry in entries:
        if entry.event_number in excluded_events:
            skipped_events.add(entry.event_number)
            continue
        if entry.heat is not None or entry.lane is not None:
            continue
        timeline = by_event.get(entry.event_number)
        if not timeline or entry.seed_place <= 0:
            continue
        if not timeline.heats or not timeline.entries:
            missing_heat_count_events.add(entry.event_number)
            continue
        lanes = max(1, min(10, ceil(timeline.entries / timeline.heats)))
        heat = timeline.heats - ((entry.seed_place - 1) // lanes)
        if heat < 1:
            continue
        rank_in_heat = ((entry.seed_place - 1) % lanes) + 1
        lanes_by_rank = lane_order(lanes)
        entry.heat = heat
        entry.lane = lanes_by_rank[rank_in_heat - 1]
        entry.heat_is_estimated = True
        entry.estimate_note = "Estimated from psych sheet seed order and timeline heat count."
        estimated_count += 1

    warnings: list[str] = []
    if estimated_count:
        warnings.append(
            "Estimated heat/lane values are not final. Scratches, deck entries, positive check-in, circle seeding, and meet-management changes can alter actual heat/lane assignments."
        )
    if skipped_events:
        warnings.append(f"Heat/lane estimates skipped for deck/circle-seeded event(s): {event_list_label(skipped_events)}.")
    if missing_heat_count_events:
        warnings.append(
            f"Heat/lane estimates unavailable for event(s) {event_list_label(missing_heat_count_events)} because the timeline source does not include entry and heat counts."
        )
    return warnings


def circle_or_deck_seeded_events(flyer_text: str, timeline_events: list[TimelineEvent]) -> tuple[set[int], bool]:
    chunks = re.split(r"(?<=[.;])\s+|\n+", flyer_text)
    event_numbers: set[int] = set()
    found_seeded_rule = False
    for chunk in chunks:
        lower = chunk.lower()
        if not re.search(r"\b(?:circle|deck)[- ]?seed", lower):
            continue
        found_seeded_rule = True
        event_numbers.update(event_numbers_from_seeded_rule(chunk))
        if re.search(r"\bprelim\s*/?\s*final events\b", lower):
            event_numbers.update(final_timeline_by_event(timeline_events))
    return event_numbers, found_seeded_rule


def event_numbers_from_seeded_rule(text: str) -> set[int]:
    event_numbers: set[int] = set()
    for match in re.finditer(
        r"\bevents?\s*#?\s*(\d{1,3})(?:\s*(?:-|–|—|to|through)\s*#?\s*(\d{1,3}))?",
        text,
        flags=re.IGNORECASE,
    ):
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if end < start:
            start, end = end, start
        if end - start <= 200:
            event_numbers.update(range(start, end + 1))
    return event_numbers


def event_list_label(event_numbers: set[int]) -> str:
    ordered = sorted(event_numbers)
    if not ordered:
        return ""
    ranges: list[str] = []
    start = previous = ordered[0]
    for number in ordered[1:]:
        if number == previous + 1:
            previous = number
            continue
        ranges.append(f"#{start}" if start == previous else f"#{start}-#{previous}")
        start = previous = number
    ranges.append(f"#{start}" if start == previous else f"#{start}-#{previous}")
    return ", ".join(ranges)


def lane_order(lanes: int) -> list[int]:
    if lanes == 10:
        return [5, 6, 4, 7, 3, 8, 2, 9, 1, 10]
    if lanes == 8:
        return [4, 5, 3, 6, 2, 7, 1, 8]
    if lanes == 6:
        return [3, 4, 2, 5, 1, 6]
    center_left = (lanes + 1) // 2
    order: list[int] = []
    for offset in range(lanes):
        left = center_left - offset
        right = center_left + 1 + offset
        if 1 <= left <= lanes:
            order.append(left)
        if 1 <= right <= lanes:
            order.append(right)
    return order[:lanes]


def primary_timeline_by_event(timeline_events: list[TimelineEvent]) -> dict[int, TimelineEvent]:
    result: dict[int, TimelineEvent] = {}
    for event in timeline_events:
        if event.event_number not in result and not session_is_finals(event.session_name):
            result[event.event_number] = event
    for event in timeline_events:
        result.setdefault(event.event_number, event)
    return result


def final_timeline_by_event(timeline_events: list[TimelineEvent]) -> dict[int, TimelineEvent]:
    result: dict[int, TimelineEvent] = {}
    for event in timeline_events:
        if session_is_finals(event.session_name):
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
        prefix = "estimated heat" if entry.heat_is_estimated else "heat"
        return f"seed {entry.seed_time} | {prefix} {entry.heat}, lane {entry.lane}"
    return f"seed {entry.seed_time} | seed place {entry.seed_place}"


def entry_position_line(entry: PsychEntry) -> str:
    if entry.heat is not None and entry.lane is not None:
        label = "Estimated heat/lane" if entry.heat_is_estimated else "Heat/lane"
        return f"{label}: heat {entry.heat}, lane {entry.lane}"
    return f"Seed place: {entry.seed_place}"


def entry_source_label(entry: PsychEntry) -> str:
    if entry.heat_is_estimated:
        return "Psych/entry sheet + estimated heat/lane"
    return "Heat sheet" if entry.heat is not None else "Psych/entry sheet"


def entry_column_display(column: str | None) -> str:
    """The user-facing psych-sheet column word ('Left'/'Middle'/'Right'), or '' when it wasn't
    located. "Unknown" is an internal parsing state -- page_column_for_line() couldn't resolve the
    entry's x-position to a column -- and is never shown to families; callers omit the column
    entirely instead of surfacing a meaningless "unknown column"."""
    if not column or column.strip().lower() == "unknown":
        return ""
    return column.strip()


def entry_column_clause(column: str | None) -> str:
    """The ', <left/middle/right> column' suffix for a source line, or '' when not known."""
    word = entry_column_display(column)
    return f", {word.lower()} column" if word else ""


def entry_source_line(entry: PsychEntry) -> str:
    """Parent-facing 'where this entry came from' line: source + page, plus the column only when it
    was actually located on the page."""
    return f"{entry_source_label(entry)}: page {entry.page}{entry_column_clause(entry.column)}"


def event_format_label(swim: SwimEvent) -> str:
    if swim.timing_rule:
        return "Timed final"
    if event_name_is_timed_final(swim.psych.event_name) or event_name_is_timed_final(swim.timeline.event_name):
        return "Timed final"
    if swim.final_timeline and swim.final_timeline.session_number != swim.timeline.session_number:
        return "Prelim/final"
    if swim.timeline.round_name.lower().startswith("finals"):
        return "Timed final"
    lower_session = swim.timeline.session_name.lower()
    if "distance" in lower_session or "afternoon" in lower_session:
        return "Timed final"
    return "Prelim only"


def event_name_is_timed_final(event_name: str) -> bool:
    return bool(re.search(r"\bTF\b|timed[- ]?final", event_name, flags=re.IGNORECASE))


def location_for_session(session: SessionInfo | TimelineEvent) -> str:
    # `facility` is only ever set from the meet's own data now (parsed from its flyer/timeline,
    # or the meet record's explicit venue) -- never guessed from the session name. When nothing
    # is known we return the neutral "Meet facility" rather than assert a specific wrong address.
    facility = getattr(session, "facility", None)
    # Expand the short Kino/Skyline names that the Arizona flyers print to their full addresses.
    # This only fires when the facility was genuinely parsed as one of those AZ venues (e.g. the
    # Narwhal Invite), so it cannot leak an AZ address onto a meet held elsewhere.
    if facility and "kino" in facility.lower():
        return "Kino Aquatic Complex, 848 N. Horne, Mesa, AZ 85203"
    if facility and "skyline" in facility.lower():
        return "Skyline Aquatic Center, 845 S. Crismon Rd., Mesa, AZ"
    if facility:
        return facility
    return "Meet facility"


def _seed_key(value: str) -> str:
    """A seed time reduced to its digits so '39.82L' and '39.82' compare equal across documents."""
    return re.sub(r"[^0-9]", "", value or "")


def heat_sheet_label(pdf: Path) -> str:
    """Human-readable name for a heat-sheet document, used in warnings and the source line."""
    return pdf.stem


def overlay_heat_sheet_entries(
    entries: list[PsychEntry],
    heat_sheet_pdfs: Iterable[Path] | None,
    swimmer_name: str,
) -> list[str]:
    """Copy REAL heat/lane from heat sheet(s) onto the psych-sheet entries that already exist.

    The psych sheet stays the spine: this never creates, deletes, or reorders an entry, and never
    touches seed_place or event_name (the psych sheet's fuller event name is what the standards
    lookup needs). Only heat, lane, round_name, swims_with_finals and heat_document are set.

    A heat sheet usually covers ONE day of a multi-day meet, so entries it does not mention are
    deliberately left alone -- the existing estimate (or nothing) still applies to them. Anything
    ambiguous also leaves the entry untouched and records a warning, because a wrong-but-confident
    lane is worse than an honest estimate.
    """
    if not heat_sheet_pdfs:
        return []
    warnings: list[str] = []
    by_event = {entry.event_number: entry for entry in entries}
    for pdf in heat_sheet_pdfs:
        label = heat_sheet_label(Path(pdf))
        heat_rows, _, _ = extract_psych_entries(Path(pdf), swimmer_name)
        rows_by_event: dict[int, list[PsychEntry]] = {}
        for row in heat_rows:
            rows_by_event.setdefault(row.event_number, []).append(row)
        for event_number, rows in sorted(rows_by_event.items()):
            target = by_event.get(event_number)
            if target is None:
                warnings.append(
                    f"{label} lists event #{event_number} for this swimmer but the entry sheet does "
                    "not, so no heat/lane was applied. Confirm with your coach."
                )
                continue
            if len(rows) > 1:
                warnings.append(
                    f"{label} shows {len(rows)} rows for event #{event_number}; heat/lane was left "
                    "as an estimate rather than guessing which row is right."
                )
                continue
            row = rows[0]
            if row.heat is None or row.lane is None:
                continue  # a seeded list with no heat headers -- nothing real to copy
            if _seed_key(row.seed_time) != _seed_key(target.seed_time):
                warnings.append(
                    f"{label} shows seed {row.seed_time} for event #{event_number} but the entry "
                    f"sheet shows {target.seed_time}; heat/lane was left as an estimate."
                )
                continue
            target.heat = row.heat
            target.lane = row.lane
            target.round_name = row.round_name or target.round_name
            target.swims_with_finals = row.swims_with_finals
            target.heat_is_estimated = False
            target.estimate_note = None
            target.heat_document = label
    return warnings


# A distance-session timeline lists one row per heat: "12:04 PM 21 Girls 13-14 800 Freestyle -- Heat
# 3 of 5". Heats that swim in another session are simply absent, and no time is invented for them.
_DISTANCE_HEAT_ROW_RE = re.compile(
    r"^(?P<time>\d{1,2}:\d{2}\s*[AP]M)\s+(?P<event>\d+)\s+.*?\bHeat\s+(?P<heat>\d+)\s+of\s+(?P<total>\d+)",
    re.IGNORECASE,
)
_DISTANCE_FINISH_RE = re.compile(r"Est\.?\s*finish\s+(?P<time>\d{1,2}:\d{2}\s*[AP]M)", re.IGNORECASE)


def parse_distance_heat_times(distance_pdf: Path | None) -> dict[tuple[int, int], tuple[str, str | None]]:
    """Per-heat start/end clock times from a distance-session timeline, keyed by (event, heat).

    Each heat's end is the next listed heat's start (the document gives no end times), and the last
    heat ends at the stated estimated finish. Only heats the document actually lists appear here --
    a heat that swims in another session has no entry and therefore gets no time.
    """
    if distance_pdf is None:
        return {}
    text = "\n".join(extract_text_pages(Path(distance_pdf)))
    rows: list[tuple[int, int, str]] = []
    finish: str | None = None
    for raw in text.splitlines():
        line = normalize_space(raw)
        match = _DISTANCE_HEAT_ROW_RE.match(line)
        if match:
            rows.append((int(match.group("event")), int(match.group("heat")), match.group("time")))
            continue
        end = _DISTANCE_FINISH_RE.search(line)
        if end:
            finish = end.group("time")
    windows: dict[tuple[int, int], tuple[str, str | None]] = {}
    for index, (event_number, heat, start) in enumerate(rows):
        next_start = rows[index + 1][2] if index + 1 < len(rows) else finish
        windows[(event_number, heat)] = (start, next_start)
    return windows


def refine_timeline_for_heat(
    entry: PsychEntry, timeline: TimelineEvent, heat_windows: dict[tuple[int, int], tuple[str, str | None]]
) -> TimelineEvent:
    """Narrow an event-wide window to this swimmer's own heat, when a document actually states it.

    Requires a REAL heat (from a heat sheet) plus a matching row in the distance timeline. An
    estimated heat is never used to pick a per-heat time, and a heat the document omits keeps the
    event-wide window -- no time is fabricated.
    """
    if entry.heat is None or entry.heat_is_estimated or entry.heat_document is None:
        return timeline
    window = heat_windows.get((entry.event_number, entry.heat))
    if not window:
        return timeline
    start_clock, end_clock = window
    start = combine_date_time(timeline.date, start_clock)
    end = combine_date_time(timeline.date, end_clock) if end_clock else timeline.end
    if end <= start:
        return timeline
    return replace(timeline, start=start, end=end)


def build_swim_events(
    entries: list[PsychEntry],
    timeline_events: list[TimelineEvent],
    state: str,
    flyer_text: str = "",
    heat_windows: dict[tuple[int, int], tuple[str, str | None]] | None = None,
) -> list[SwimEvent]:
    primary = primary_timeline_by_event(timeline_events)
    finals = final_timeline_by_event(timeline_events)
    timing_rules = parse_meet_timing_rules(flyer_text)
    swim_events: list[SwimEvent] = []
    for entry in entries:
        primary_timeline = primary.get(entry.event_number)
        rule = timing_rules.get(entry.event_number)
        final_timeline = finals.get(entry.event_number)
        timeline = timeline_for_timing_rule(entry, primary_timeline, final_timeline, rule)
        if not timeline:
            continue
        # A real heat plus a document that states that heat's time narrows the event-wide window.
        if heat_windows:
            timeline = refine_timeline_for_heat(entry, timeline, heat_windows)
        # Precedence: an explicitly entered State/LSC always wins; only when it is blank do we fall
        # back to the LSC parsed from this swimmer's own team code. Detection is per entry, so a
        # combined family calendar (and even a single lookup that fuzzy-matches swimmers from
        # different LSCs) resolves each swimmer against their own code, never one shared value.
        effective_state = state if state.strip() else (lsc_from_team_code(entry.team) or state)
        standard = lookup(entry.event_name, entry.seed_time, state=effective_state, age=entry.age)
        final_note = finals_note(entry, timeline, final_timeline, rule)
        checkin = checkin_note(entry.event_number, flyer_text)
        swim_events.append(
            SwimEvent(
                psych=entry,
                timeline=timeline,
                final_timeline=final_timeline,
                benchmarks={
                    "usa": standard.usa_summary,
                    "lsc": standard.lsc_summary,
                    "sectional": standard.sectional_summary,
                    "national": standard.national_summary,
                    "advanced": standard.advanced_summary,
                    "confidence": standard.confidence_summary,
                    "sources": standard.sources,
                },
                finals_note=final_note,
                checkin_note=checkin,
                timing_rule=rule,
            )
        )
    return sorted(swim_events, key=lambda item: item.timeline.start)


def build_relay_events(
    relay_entries: list[RelayEntry],
    timeline_events: list[TimelineEvent],
    flyer_text: str = "",
) -> list[RelayEvent]:
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
                finals_note=relay_finals_note(timeline, flyer_text),
            )
        )
    return sorted(relay_events, key=lambda item: item.timeline.start)


def relay_finals_note(timeline: TimelineEvent, flyer_text: str = "") -> str:
    lower = flyer_text.lower()
    if "all relay events are timed final events" in lower and "preliminary sessions" in lower:
        return "Timed final relay; meet flyer says all relays are swum during preliminary sessions. Timeline window is estimated."
    if timeline.round_name.lower().startswith("finals"):
        return "Timed final relay based on the timeline. Confirm final relay timing with official postings."
    return "Relay timing is estimated from the timeline. Confirm final relay assignment and timing with coach or official postings."


def timeline_for_timing_rule(
    entry: PsychEntry,
    primary_timeline: TimelineEvent | None,
    final_timeline: TimelineEvent | None,
    rule: EventTimingRule | None,
) -> TimelineEvent | None:
    if not rule:
        return primary_timeline
    if rule.kind == "timed_final_fastest_heat_finals" and rule.top_seed_count and final_timeline:
        if 0 < entry.seed_place <= rule.top_seed_count:
            return final_timeline
    return primary_timeline or final_timeline


def finals_note(
    entry: PsychEntry,
    timeline: TimelineEvent,
    final_timeline: TimelineEvent | None,
    rule: EventTimingRule | None = None,
) -> str:
    if rule and rule.kind == "timed_final_fastest_heat_finals":
        if rule.top_seed_count and 0 < entry.seed_place <= rule.top_seed_count and final_timeline:
            return (
                f"Timed final; {rule.source} says the fastest seeded heat swims during finals. "
                f"This entry is seed place {entry.seed_place}, so the finals-session window is used. Confirm with the official heat sheet."
            )
        if rule.top_seed_count and entry.seed_place > rule.top_seed_count:
            return (
                f"Timed final; {rule.source} says the fastest seeded heat swims during finals and other heats swim in the preliminary session. "
                f"This entry is seed place {entry.seed_place}, so the preliminary-session window is used. Confirm with the official heat sheet."
            )
        return (
            f"Timed final; {rule.source} says the fastest seeded heat swims during finals. "
            "Seed placement was not enough to choose the finals heat confidently; confirm with the official heat sheet."
        )
    if rule and rule.kind == "timed_final":
        return f"Timed final based on {rule.source}; no separate qualifying final."
    if event_name_is_timed_final(timeline.event_name):
        return "Timed final; no separate finals swim."
    if final_timeline and final_timeline.session_number != timeline.session_number:
        return f"Possible if qualifies; finals event starts at {display_time(final_timeline.start)} at {location_for_session(final_timeline)}."
    lower_session = timeline.session_name.lower()
    if timeline.round_name.lower().startswith("finals") or "distance" in lower_session:
        return "Timed final; no separate finals swim."
    return "No separate finals event found in the timeline."


def checkin_note(event_number: int, flyer_text: str = "") -> str | None:
    lower = flyer_text.lower()
    if "long course age group state" in lower and event_number in {
        *range(1, 7),
        *range(35, 39),
        *range(69, 73),
        *range(111, 115),
    }:
        if event_number in range(1, 7):
            return "Positive check-in required; meet flyer says check-in closes 30 minutes after Session I warm-up begins."
        return "Positive check-in required; meet flyer says check-in closes one hour after the start of competition for the applicable preliminary session."
    if 53 <= event_number <= 72 and "events 53-72" in lower and "session #7" in lower:
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
    timezone: str = DEFAULT_TZ,
    timeline_projected: bool = False,
) -> dict:
    events: list[dict] = []
    event_status = "TENTATIVE" if timeline_projected else "CONFIRMED"
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
            f"Format: {event_format_label(swim)}",
            f"Seed time: {psych.seed_time}",
            entry_position_line(psych),
            f"Timeline event window: {display_window(timeline.start, timeline.end)}",
        ]
        if timeline_projected:
            lines.append(PROJECTED_TIMELINE_NOTE)
        lines.extend(
            [
                "",
                f"Finals: {swim.finals_note}",
            ]
        )
        if swim.checkin_note:
            lines.append(f"Check-in: {swim.checkin_note}")
        benchmark_sources = swim.benchmarks.get("sources") or {}
        lines.extend(
            [
                "",
                "Benchmarks:",
                benchmark_line_with_sources(swim.benchmarks["usa"] or "USA-S: n/a", benchmark_sources.get("usa")),
                benchmark_line_with_sources(swim.benchmarks["lsc"] or "LSC: n/a", benchmark_sources.get("lsc")),
            ]
        )
        if swim.benchmarks.get("advanced"):
            lines.append(benchmark_line_with_sources(swim.benchmarks["advanced"], benchmark_sources.get("advanced")))
        if swim.benchmarks.get("confidence"):
            lines.append(swim.benchmarks["confidence"] or "")
        lines.extend(
            [
                "",
                "Source verification:",
                entry_source_line(psych),
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
                "status": event_status,
            }
        )
    for relay_event in sorted(relays, key=lambda item: item.timeline.start):
        relay = relay_event.relay
        timeline = relay_event.timeline
        common_head = [
            swimmer_name,
            short_name,
            "",
            f"Day: {meet_day_text(timeline.date, day_numbers.get(timeline.date, 1))}",
            f"Session: #{timeline.session_number} - {timeline.session_name}",
        ]
        if relay.is_team_entry:
            # Tentative: the swimmer's team is entered in this relay, but no leg-naming source
            # confirmed the swimmer on it. Never asserts a relay letter, entry time, or leg -- only
            # that the team is entered -- and is always STATUS:TENTATIVE (relay lineups shift at any
            # meet, projected timeline or not).
            lines = [
                *common_head,
                f"Pool/course: {location_for_session(timeline)}",
                "",
                f"Relay: #{relay.event_number} - {relay.event_name}",
                "Format: Timed final relay",
                "Status: TENTATIVE - your team is entered; specific swimmers and leg are not listed.",
                f"Timeline event window: {display_window(timeline.start, timeline.end)}",
                "",
                "Important:",
                "- Your team is entered in this relay, but relay lineups are set by the coach and",
                "  change often. Confirm whether and where the swimmer is swimming with the coach.",
                "- Timeline-derived relay windows are estimates.",
                *(["- " + PROJECTED_TIMELINE_NOTE] if timeline_projected else []),
                "",
                f"Finals: {relay_event.finals_note}",
                "",
                "Benchmarks: n/a for relay calendar event.",
                "",
                "Source verification:",
                f"{relay.source_label}: {relay.source_line}",
                f"Timeline: event #{relay.event_number}",
            ]
            uid_suffix = f"relay-team-{relay.event_number}"
        else:
            lines = [
                *common_head,
                f"Pool/course: {location_for_session(timeline)}; relay document lists entry as {relay.entry_time[-1:] if relay.entry_time else 'provided'}",
                "",
                f"Relay: #{relay.event_number} - {relay.event_name}",
                "Format: Timed final relay" + (" (lineup pending a change)" if relay.lineup_pending else ""),
                f"Team: {relay.relay_label}",
                f"Entry time: {relay.entry_time}",
                f"Leg: {relay.leg}",
                f"Timeline event window: {display_window(timeline.start, timeline.end)}",
                "",
                "Important:",
                *(["- This relay's lineup is pending a change (a listed swimmer withdrew and the "
                   "replacement was not yet published); confirm the final lineup with your coach."]
                  if relay.lineup_pending else []),
                "- Relay lineup and timing may change; confirm with coach or official postings.",
                "- Timeline-derived relay windows are estimates.",
                *(["- " + PROJECTED_TIMELINE_NOTE] if timeline_projected else []),
                "",
                f"Finals: {relay_event.finals_note}",
                "",
                "Benchmarks: n/a for relay calendar event.",
                "",
                "Source verification:",
                f"{relay.source_label}: page {relay.page}; swimmer match verified without displaying roster names.",
                f"Timeline: event #{relay.event_number}",
                "Psych sheet source: n/a for relay assignment",
            ]
            uid_suffix = f"relay-{relay.event_number}-{relay.relay_label.lower().replace(' ', '-')}"
        events.append(
            {
                "uid": f"{meet_id}-{swimmer_slug}-{uid_suffix}@swimtimeline",
                "title": (
                    f"{swimmer_name} - Relay {relay.event_number} (team entered): {event_short_name(relay.event_name)}"
                    if relay.is_team_entry
                    else f"{swimmer_name} - Relay {relay.event_number}: {event_short_name(relay.event_name)}"
                ),
                "start": timeline.start.isoformat(timespec="seconds"),
                "end": timeline.end.isoformat(timespec="seconds"),
                "location": location_for_session(timeline),
                "description_lines": lines,
                "status": "TENTATIVE" if (relay.is_team_entry or timeline_projected) else "CONFIRMED",
            }
        )
    events.sort(key=lambda event: event["start"])
    return {
        "calendar": {"name": f"{swimmer_name} - {short_name}", "timezone": timezone},
        "meet": {"id": meet_id, "name": meet_name, "short_name": short_name},
        "events": events,
    }


# ---------------------------------------------------------------------------
# Warm-up windows (first line of the daily calendar), two independent sources:
#
#   (1) COMPLEX -- a per-meet warm-up-assignments PDF (parse_warmup_assignments): a per-day,
#       per-team prelim matrix plus a universal finals window. A swimmer's window resolves by their
#       own LSC x day-of-week x session type (prelims vs finals). This is the zone-meet case (WZAG).
#   (2) SIMPLE -- one universal window per meet (a manually set field, or a flyer-stated range),
#       shown the same on every day.
#
# The complex doc wins when it resolves a window for the swimmer; the simple window is the fallback.
# A meet with NEITHER yields no warm-up first line at all -- the daily calendar simply omits it, and
# the existing per-session "Warm-up:" line (flyer-derived, or a start-minus-60 estimate) is shown
# instead, exactly as before. NOTE: the per-session line already surfaces flyer-stated warm-up times
# for meets whose flyer parses (e.g. AZ State); this feature adds an *authoritative window* on top.
# ---------------------------------------------------------------------------

# The warm-up doc abbreviates teams its own way (PAC, PNS, SNS, SRS, SDI, ...) -- neither the LSC
# code (PC, PN, SN, SR, SI) nor the psych display name. Resolve to the 2-letter LSC so a swimmer
# matched via lsc_from_team_code() lines up: exact code, a suffix-"S" form (PNS->PN, SRS->SR, ...),
# or one of two irregulars. An unrecognized token resolves to None and is ignored.
WARMUP_TOKEN_ALIASES = {"PAC": "PC", "SDI": "SI"}

WARMUP_DAY_HEADER = re.compile(
    r"^(?P<day>Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b.*\bPreliminary",
    re.IGNORECASE,
)
WARMUP_TIME_ROW = re.compile(
    r"^(?P<start>\d{1,2}:\d{2}\s*[ap]\.?m\.?)\s*[‐-―\-]\s*"
    r"(?P<end>\d{1,2}:\d{2}\s*[ap]\.?m\.?)\s+(?P<rest>\S.*)$",
    re.IGNORECASE,
)


def warmup_token_to_lsc(token: str) -> str | None:
    cleaned = re.sub(r"[^A-Za-z]", "", token).upper()
    if not cleaned:
        return None
    if cleaned in LSC_TEAM_NAMES:
        return cleaned
    if cleaned.endswith("S") and cleaned[:-1] in LSC_TEAM_NAMES:
        return cleaned[:-1]
    return WARMUP_TOKEN_ALIASES.get(cleaned)


def _clock_minutes(clock: str) -> int:
    hour, minute = parse_clock(clock)
    return hour * 60 + minute


def parse_warmup_assignments(warmup_pdf: Path | None) -> dict | None:
    """Parse a warm-up-assignments PDF into
    {'prelim': {weekday: {lsc: (start_clock, end_clock)}}, 'finals': (start_clock, end_clock) | None}.

    Prelim windows vary by day-of-week AND team; the 'ALL Finals Sessions' section is universal
    (its lane tokens are equipment labels, not teams), so only its overall time span is kept.
    Returns None when there is no file or nothing parseable.
    """
    if warmup_pdf is None:
        return None
    text = "\n".join(extract_text_pages(warmup_pdf))
    prelim: dict[str, dict[str, tuple[str, str]]] = {}
    finals_bounds: list[tuple[str, str]] = []
    mode: tuple[str, str | None] | None = None
    for raw in text.splitlines():
        line = normalize_space(raw)
        if not line:
            continue
        day_header = WARMUP_DAY_HEADER.match(line)
        if day_header:
            weekday = day_header.group("day").title()
            mode = ("prelim", weekday)
            prelim.setdefault(weekday, {})
            continue
        if (
            re.search(r"\bFinals?\b", line, re.IGNORECASE)
            and "Preliminary" not in line
            and not WARMUP_TIME_ROW.match(line)
        ):
            mode = ("finals", None)
            continue
        row = WARMUP_TIME_ROW.match(line)
        if row is None or mode is None:
            continue
        start, end = row.group("start"), row.group("end")
        if mode[0] == "finals":
            finals_bounds.append((start, end))
            continue
        weekday = mode[1]
        for token in row.group("rest").split():
            for part in token.split("/"):  # combined lanes like "AK/SRS" list two teams
                lsc = warmup_token_to_lsc(part)
                if lsc:
                    prelim[weekday].setdefault(lsc, (start, end))
    finals = None
    if finals_bounds:
        start = min((b[0] for b in finals_bounds), key=_clock_minutes)
        end = max((b[1] for b in finals_bounds), key=_clock_minutes)
        finals = (start, end)
    if not any(prelim.values()) and finals is None:
        return None
    return {"prelim": prelim, "finals": finals}


FLYER_WARMUP_WINDOW = re.compile(
    r"warm[\s-]*up[^0-9]{0,20}(?P<s>\d{1,2}:\d{2})\s*(?P<sap>[ap]\.?m\.?)?\s*(?:[‐-―\-]|to)\s*"
    r"(?P<e>\d{1,2}:\d{2})\s*(?P<eap>[ap]\.?m\.?)",
    re.IGNORECASE,
)


def extract_flyer_warmup_window(flyer_text: str) -> str | None:
    """Best-effort: a single universal warm-up window stated as an explicit range in the flyer
    (e.g. 'warm-up 5:45-6:30 PM'). The end time must carry AM/PM; the start borrows it when it does
    not. Returns None when no clear RANGE is present -- it deliberately does not fire on the
    per-session 'Warm-up: 7:00 am, Meet Start: 8:30 am' lines (a single time, no range), which
    parse_flyer_sessions already handles. The manual field is used when this finds nothing.
    """
    match = FLYER_WARMUP_WINDOW.search(flyer_text or "")
    if not match:
        return None
    ref = date(2000, 1, 1)
    end_ap = match.group("eap")
    start = f"{match.group('s')} {match.group('sap') or end_ap}"
    end = f"{match.group('e')} {end_ap}"
    return display_window(combine_date_time(ref, start), combine_date_time(ref, end))


def _format_warmup_window(start: str, end: str, qualifier: str) -> dict:
    ref = date(2000, 1, 1)
    display = display_window(combine_date_time(ref, start), combine_date_time(ref, end))
    return {"display": display, "start_clock": start, "end_clock": end, "qualifier": qualifier}


def _simple_warmup_window(window: str) -> dict:
    """A manually set / flyer-extracted universal window string, shown verbatim; the first parsed
    clock (if any) seeds the calendar's arrive-by time."""
    times = re.findall(r"\d{1,2}:\d{2}\s*[ap]\.?m\.?", window, re.IGNORECASE)
    return {
        "display": window.strip(),
        "start_clock": times[0] if times else None,
        "end_clock": times[1] if len(times) > 1 else None,
        "qualifier": "",
    }


def build_warmup_resolver(warmup_pdf: Path | None, universal_window: str | None, swimmer_team: str):
    """A resolver fn(day, is_finals) -> warm-up dict | None for one swimmer, or None if no data.

    Precedence: the per-team/day doc window (matched by the swimmer's own LSC) wins; a universal
    window is the fallback; otherwise None.
    """
    parsed = parse_warmup_assignments(warmup_pdf)
    if not (parsed or universal_window):
        return None
    lsc = lsc_from_team_code(swimmer_team)

    def resolve(day: date, is_finals: bool) -> dict | None:
        if parsed:
            if is_finals and parsed["finals"]:
                start, end = parsed["finals"]
                return _format_warmup_window(start, end, "finals")
            if not is_finals and lsc:
                weekday = day.strftime("%A")
                window = parsed["prelim"].get(weekday, {}).get(lsc)
                if window:
                    start, end = window
                    return _format_warmup_window(start, end, f"{lsc} · {weekday} prelims")
        if universal_window:
            return _simple_warmup_window(universal_window)
        return None

    return resolve


def build_daily_payload(
    meet_id: str,
    meet_name: str,
    short_name: str,
    swimmer_name: str,
    swims: list[SwimEvent],
    relays: list[RelayEvent],
    sessions: dict[int, SessionInfo],
    timezone: str = DEFAULT_TZ,
    timeline_projected: bool = False,
    warmup_resolver=None,
) -> dict:
    events: list[dict] = []
    event_status = "TENTATIVE" if timeline_projected else "CONFIRMED"
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
        # Authoritative warm-up window (per-team/day doc, or a universal meet window) for this day's
        # FIRST session -- its type (prelims vs finals) drives which window applies. When present it
        # becomes the calendar's arrive-by time and the prominent first line, replacing the derived
        # per-session estimate below. Absent -> the estimate stays and no window line is shown.
        warmup_hit = (
            warmup_resolver(day, session_is_finals(first.timeline.session_name))
            if warmup_resolver is not None
            else None
        )
        if warmup_hit and warmup_hit.get("start_clock"):
            session_warmup = combine_date_time(day, warmup_hit["start_clock"])
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

        # The header is always exactly 9 lines so build_weekend_payload's description_lines[9:] slice
        # stays correct: an authoritative window adds a first line AND drops the derived "Warm-up:"
        # line; with no window, there is no first line but the derived line stays. Net 9 either way.
        warmup_first_line = None
        if warmup_hit:
            qualifier = warmup_hit.get("qualifier")
            warmup_first_line = f"Warm-up: {warmup_hit['display']}" + (f" ({qualifier})" if qualifier else "")
        lines = [
            *( [warmup_first_line] if warmup_first_line else [] ),
            swimmer_name,
            short_name,
            "",
            f"Day: {meet_day_text(day, day_number)}",
            f"Session: #{first.timeline.session_number} - {first.timeline.session_name}",
            *( [] if warmup_first_line else [f"Warm-up: {display_time(session_warmup)}"] ),
            f"Meet start: {display_time(session_start)}",
            f"Pool/course: {location_for_session(first.timeline)}; entry sheet lists events as LC Meter",
            "",
        ]
        # Kept inside the weekend view's inherited slice (description_lines[9:]) so the whole-meet
        # calendar carries the same projection caveat per day.
        if timeline_projected:
            lines.extend([PROJECTED_TIMELINE_NOTE, ""])
        lines.append(f"{possessive_name(swimmer_name)} swims:")
        for item in day_items:
            if isinstance(item, RelayEvent):
                relay = item.relay
                if relay.is_team_entry:
                    lines.append(
                        f"#{relay.event_number} Relay - {event_short_name(relay.event_name)} | tentative: team entered, leg TBD | confirm with coach | {display_window(item.timeline.start, item.timeline.end)} estimated"
                    )
                else:
                    pending = " | lineup pending, confirm with coach" if relay.lineup_pending else ""
                    lines.append(
                        f"#{relay.event_number} Relay - {event_short_name(relay.event_name)} | timed final relay | {relay.relay_label}, leg {relay.leg}{pending} | {display_window(item.timeline.start, item.timeline.end)} estimated"
                    )
            else:
                lines.append(
                    f"#{item.psych.event_number} {event_short_name(item.psych.event_name)} | {event_format_label(item)} | {entry_seed_summary(item.psych)} | {display_window(item.timeline.start, item.timeline.end)}"
                )
        possible_finals = [
            f"#{item.psych.event_number} {event_short_name(item.psych.event_name)} at {display_time(item.final_timeline.start)} at {location_for_session(item.final_timeline)} if qualifies"
            for item in day_items
            if isinstance(item, SwimEvent)
            and item.final_timeline
            and item.final_timeline.session_number != item.timeline.session_number
            and not item.timing_rule
        ]
        if possible_finals:
            lines.extend(["", "Possible finals:", *possible_finals])
        if checkin_lines:
            lines.extend(["", "Check-in:", *checkin_lines])
        lines.extend(["", "Benchmarks:"])
        for item in day_items:
            if isinstance(item, RelayEvent):
                continue
            item_sources = item.benchmarks.get("sources") or {}
            benchmark = benchmark_line_with_sources(item.benchmarks["usa"] or "USA-S: n/a", item_sources.get("usa"))
            lsc = benchmark_line_with_sources(item.benchmarks["lsc"] or "LSC: n/a", item_sources.get("lsc"))
            lines.append(f"#{item.psych.event_number} {benchmark} | {lsc}")
            if item.benchmarks.get("advanced"):
                advanced = benchmark_line_with_sources(item.benchmarks["advanced"], item_sources.get("advanced"))
                lines.append(f"#{item.psych.event_number} {advanced}")
            if item.benchmarks.get("confidence"):
                lines.append(f"#{item.psych.event_number} {item.benchmarks['confidence']}")
        if any(isinstance(item, RelayEvent) for item in day_items):
            lines.extend(
                [
                    "Relays: benchmarks n/a.",
                    "",
                    "Relay notes:",
                    "Relay lineup and timing may change; confirm with coach or official postings. Relay windows are estimated from the meet timeline.",
                ]
            )
        lines.extend(["", "Source verification: entry sheet and timeline verified; review the audit report before import."])
        events.append(
            {
                "uid": f"{meet_id}-{swimmer_slug}-{day.isoformat()}@swimtimeline",
                "title": f"{swimmer_name} - {short_name}: Day {day_number} ({day.strftime('%A')})",
                "start": calendar_start.isoformat(timespec="seconds"),
                "end": max(item.timeline.end for item in day_items).isoformat(timespec="seconds"),
                "location": location_for_session(first.timeline),
                "description_lines": lines,
                "status": event_status,
            }
        )
    return {
        "calendar": {"name": f"{swimmer_name} - {short_name} Daily", "timezone": timezone},
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
    timezone: str = DEFAULT_TZ,
    timeline_projected: bool = False,
    warmup_resolver=None,
) -> dict:
    swimmer_slug = swimmer_uid_part(swimmer_name)
    daily = build_daily_payload(
        meet_id, meet_name, short_name, swimmer_name, swims, relays, sessions,
        timezone=timezone, timeline_projected=timeline_projected, warmup_resolver=warmup_resolver,
    )["events"]
    if not daily:
        return {"calendar": {"name": f"{swimmer_name} - {short_name} Weekend", "timezone": timezone}, "events": []}
    start = min(datetime.fromisoformat(event["start"]) for event in daily)
    end = max(datetime.fromisoformat(event["end"]) for event in daily)
    lines = [swimmer_name, short_name, "", "Meet summary:"]
    for event in daily:
        lines.extend(["", event["title"].removeprefix(f"{swimmer_name} - "), *event["description_lines"][9:]])
    return {
        "calendar": {"name": f"{swimmer_name} - {short_name} Weekend", "timezone": timezone},
        "meet": {"id": meet_id, "name": meet_name, "short_name": short_name},
        "events": [
            {
                "uid": f"{meet_id}-{swimmer_slug}-weekend@swimtimeline",
                "title": f"{swimmer_name} - {short_name}: Whole Meet",
                "start": start.isoformat(timespec="seconds"),
                "end": end.isoformat(timespec="seconds"),
                "location": "Multiple meet facilities",
                "description_lines": lines,
                "status": "TENTATIVE" if timeline_projected else "CONFIRMED",
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
    lines.append("| Day | Event # | Event Name | Format | Seed Time | Position | Page | Column | Source |")
    lines.append("| --- | ---: | --- | --- | --- | --- | ---: | --- | --- |")
    for swim in swims:
        psych = swim.psych
        if psych.heat is not None and psych.lane is not None:
            prefix = "estimated heat" if psych.heat_is_estimated else "heat"
            position = f"{prefix} {psych.heat}, lane {psych.lane}"
        else:
            position = f"seed place {psych.seed_place}"
        lines.append(
            f"| {swim.timeline.date.strftime('%A')} | {psych.event_number} | {psych.event_name} | {event_format_label(swim)} | {psych.seed_time} | {position} | {psych.page} | {entry_column_display(psych.column)} | {entry_source_label(psych)} |"
        )
    lines.extend(["", "## Relays", ""])
    if relays:
        lines.append("| Day | Event # | Relay Event | Status | Relay | Entry Time | Leg | Page |")
        lines.append("| --- | ---: | --- | --- | --- | --- | ---: | ---: |")
        for relay_event in relays:
            relay = relay_event.relay
            if relay.is_team_entry:
                status, label, entry, leg = "Tentative (team entered)", "--", "--", "--"
            else:
                status, label, entry, leg = "Confirmed leg", relay.relay_label, relay.entry_time, str(relay.leg)
            lines.append(
                f"| {relay_event.timeline.date.strftime('%A')} | {relay.event_number} | {relay.event_name} | {status} | {label} | {entry} | {leg} | {relay.page} |"
            )
    else:
        lines.append("No relays found.")
    confirmed_count = sum(1 for item in relays if not item.relay.is_team_entry)
    tentative_count = len(relays) - confirmed_count
    lines.extend(
        [
            "",
            "## Standards Sources",
            "",
            *[f"- {source['name']}: {source['url']}" for source in SOURCES],
            "",
            f"Total verified events found: {len(swims)}",
            "",
            f"Total relays found: {len(relays)} ({confirmed_count} confirmed leg, {tentative_count} tentative team entry)",
            "",
            "Confirmed-leg relays require a relay document that names the swimmer or a matching private "
            "relay add-on. Tentative relays are events the swimmer's own team is entered in (from the "
            "psych sheet's team-level rows); the specific swimmers and leg are not listed -- confirm with the coach.",
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
    internal_relay_sources: Iterable[Path] | None = None,
    state: str = DEFAULT_STATE,
    modes: Iterable[str] = ("daily",),
    estimate_heat_lanes: bool = False,
    meet_timezone: str | None = None,
    meet_venue: str | None = None,
    timeline_projected: bool = False,
    warmup_pdf: Path | None = None,
    meet_warmup_window: str | None = None,
    heat_sheet_pdfs: Iterable[Path] | None = None,
    distance_timeline_pdf: Path | None = None,
) -> dict:
    resolved_timezone = meet_timezone or resolve_meet_timezone(state)
    flyer_text = "\n".join(extract_text_pages(flyer_pdf)) if flyer_pdf else ""
    meet_name, sessions, timeline_events = parse_timeline(timeline_pdf, flyer_text=flyer_text, meet_venue=meet_venue)
    entries, page_counts, name_warnings = extract_psych_entries(psych_pdf, swimmer_name)
    # Relay matching hashes a full (first, last) pair, so a last-name-only query would find the
    # individual events (substring match) but no relays. Resolve the swimmer's full name from the
    # matched entries first, so a partial-but-unambiguous search gets relays too (ambiguous queries
    # fall back to the raw query and simply do not resolve relays -- never guess one swimmer).
    relay_query = resolved_relay_query(swimmer_name, entries)
    relay_entries, relay_warnings = extract_relay_entries(relay_pdf, relay_query)
    internal_relay_entries, internal_relay_warnings = extract_internal_relay_entries(internal_relay_sources, relay_query)
    relay_entries = dedupe_relay_entries([*relay_entries, *internal_relay_entries])
    relay_warnings.extend(internal_relay_warnings)
    # Tentative "team entered, leg unknown" relays from the psych sheet's own team-level rows -- the
    # middle ground when no leg-naming source covered an event. Precedence: a real roster proves who
    # is actually on an event, so tentative matching is suppressed for EVERY event any roster covers
    # -- not merely the events THIS swimmer was confirmed on. Otherwise a swimmer whose team is
    # entered but who is not on the published lineup would still get a false "team entered" tentative
    # for an event the roster already settled.
    swimmer_team, swimmer_age, swimmer_gender = swimmer_relay_identity(entries)
    roster_covered_events = relay_roster_event_numbers(relay_pdf, internal_relay_sources)
    suppressed_relay_events = roster_covered_events | {relay.event_number for relay in relay_entries}
    team_relay_entries = [
        entry
        for entry in extract_team_relay_entries(psych_pdf, swimmer_team, swimmer_age, swimmer_gender)
        if entry.event_number not in suppressed_relay_events
    ]
    relay_entries = [*relay_entries, *team_relay_entries]
    if team_relay_entries:
        relay_warnings.append(
            f"{len(team_relay_entries)} relay(s) list your team entered but no confirmed lineup was "
            "provided; these appear as tentative. Confirm relay assignments with your coach."
        )
    assign_days(entries, timeline_events)
    # Real heat/lane from any heat sheet(s) supplied, BEFORE estimation: estimate_heat_lanes_for_entries
    # skips entries that already carry a heat/lane, so a day with a real heat sheet keeps its real
    # values while every other day is estimated exactly as before, in the same run.
    overlay_warnings = overlay_heat_sheet_entries(entries, heat_sheet_pdfs, swimmer_name)
    estimate_warnings = estimate_heat_lanes_for_entries(entries, timeline_events, flyer_text) if estimate_heat_lanes else []
    heat_windows = parse_distance_heat_times(distance_timeline_pdf)
    swims = build_swim_events(
        entries, timeline_events, state=state, flyer_text=flyer_text, heat_windows=heat_windows
    )
    relays = build_relay_events(relay_entries, timeline_events, flyer_text=flyer_text)
    # Warm-up first line: the per-team/day assignments doc (complex) wins, else a universal window
    # from the meet field or a flyer-stated range (simple). Keyed off the swimmer's own team code.
    warmup_resolver = build_warmup_resolver(
        warmup_pdf,
        meet_warmup_window or extract_flyer_warmup_window(flyer_text),
        swimmer_team,
    )
    short_name = short_meet_name(meet_name)
    meet_id = slugify(meet_name)
    output_swimmer_name = resolved_swimmer_name(swimmer_name, entries)
    selected_modes = [mode for mode in modes if mode in {"daily", "weekend", "detailed"}]
    selected_payloads: dict[str, dict] = {}
    if "daily" in selected_modes:
        selected_payloads["daily"] = build_daily_payload(
            meet_id,
            meet_name,
            short_name,
            output_swimmer_name,
            swims,
            relays,
            sessions,
            timezone=resolved_timezone,
            timeline_projected=timeline_projected,
            warmup_resolver=warmup_resolver,
        )
    if "weekend" in selected_modes:
        selected_payloads["weekend"] = build_weekend_payload(
            meet_id,
            meet_name,
            short_name,
            output_swimmer_name,
            swims,
            relays,
            sessions,
            timezone=resolved_timezone,
            timeline_projected=timeline_projected,
            warmup_resolver=warmup_resolver,
        )
    if "detailed" in selected_modes:
        selected_payloads["detailed"] = build_detailed_payload(
            meet_id,
            meet_name,
            short_name,
            output_swimmer_name,
            swims,
            relays,
            day_numbers_for_items(swims, relays),
            timezone=resolved_timezone,
            timeline_projected=timeline_projected,
        )
    files = write_outputs(output_dir, meet_name, output_swimmer_name, entries, swims, relays, page_counts, selected_payloads)

    return {
        "meet": {"id": meet_id, "name": meet_name, "short_name": short_name},
        "swimmer": output_swimmer_name,
        "requested_swimmer": swimmer_name,
        "verified_event_count": len(swims),
        # "verified" counts only leg-confirmed relays; tentative team entries are reported separately
        # so the confirmed count keeps its meaning.
        "verified_relay_count": sum(1 for relay in relays if not relay.relay.is_team_entry),
        "tentative_relay_count": sum(1 for relay in relays if relay.relay.is_team_entry),
        "psych_match_pages": page_counts,
        "events": [summarize_swim(swim) for swim in swims],
        "relays": [summarize_relay(relay) for relay in relays],
        "items": sorted(
            [summarize_swim(swim) for swim in swims] + [summarize_relay(relay) for relay in relays],
            key=lambda item: item["sort_start"],
        ),
        "files": files,
        "sessions": [serialize_session(session) for session in sessions.values()],
        "warnings": build_warnings(
            entries,
            swims,
            relay_entries,
            relays,
            relay_warnings,
            name_warnings
            + overlay_warnings
            + estimate_warnings
            + timeline_source_warnings(timeline_events)
            + auto_detect_state_warnings(state, entries),
        ),
    }


def resolved_swimmer_name(swimmer_name: str, entries: list[PsychEntry]) -> str:
    if not entries or not any(entry.name_match_type == "fuzzy" for entry in entries):
        return swimmer_name
    return display_first_last(entries[0].matched_name) or swimmer_name


def serialize_session(session: SessionInfo) -> dict:
    data = asdict(session)
    data["date"] = session.date.isoformat()
    return data


def benchmark_line_with_sources(text: str, sources_for_line: list[dict] | None) -> str:
    """Append a plain-text source URL to a benchmark line for non-HTML surfaces (.ics descriptions).

    The web table linkifies the label itself from the same source data; plain text can't carry a
    clickable link, so the URL is appended so the number stays checkable against its document.
    """
    if not text or not sources_for_line:
        return text
    urls = [source["url"] for source in sources_for_line if source.get("url")]
    return f"{text} (source: {'; '.join(urls)})" if urls else text


def summarize_swim(swim: SwimEvent) -> dict:
    return {
        "type": "individual",
        "event_number": swim.psych.event_number,
        "event_name": swim.psych.event_name,
        "seed_time": swim.psych.seed_time,
        "seed_place": swim.psych.seed_place,
        "heat": swim.psych.heat,
        "lane": swim.psych.lane,
        "heat_is_estimated": swim.psych.heat_is_estimated,
        "estimate_note": swim.psych.estimate_note,
        "entry_position": entry_position_line(swim.psych),
        "source_document": entry_source_label(swim.psych),
        "event_format": event_format_label(swim),
        "day": swim.timeline.date.strftime("%A"),
        "window": display_window(swim.timeline.start, swim.timeline.end),
        "page": swim.psych.page,
        "column": entry_column_display(swim.psych.column),
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
        # A tentative team entry knows no specific leg (leg is null, not 0), and is flagged so the UI
        # can render it distinctly from a confirmed leg assignment.
        "leg": None if relay.is_team_entry else relay.leg,
        "is_team_entry": relay.is_team_entry,
        # A confirmed leg whose lineup the roster flagged as pending a change is distinct from both a
        # settled confirmed relay and a tentative team entry.
        "relay_status": (
            "tentative" if relay.is_team_entry else ("confirmed_pending" if relay.lineup_pending else "confirmed")
        ),
        "lineup_pending": relay.lineup_pending,
        "day": relay_event.timeline.date.strftime("%A"),
        "window": display_window(relay_event.timeline.start, relay_event.timeline.end),
        "page": relay.page,
        "column": relay.source_label,
        "source_document": relay.source_label,
        "benchmarks": {"usa": "n/a for relay", "lsc": "n/a for relay", "sectional": None, "national": None, "advanced": None, "confidence": "Standards confidence: n/a for relay", "sources": {}},
        "finals_note": relay_event.finals_note,
        "event_format": (
            "Relay: team entered, leg TBD" if relay.is_team_entry
            else ("Timed final relay (lineup pending)" if relay.lineup_pending else "Timed final relay")
        ),
        "checkin_note": None,
        "sort_start": relay_event.timeline.start.isoformat(timespec="seconds"),
    }


def auto_detect_state_warnings(state: str, entries: list[PsychEntry]) -> list[str]:
    """One note when a blank State/LSC field was filled in from the swimmers' own team codes.

    Only fires for LSCs the app actually has standards for (has_lsc_standards): those are the cases
    where auto-detection changes what the family sees, so the note explains why AZSI/Sectional lines
    appeared without them typing anything. A non-supported code (or no detectable LSC) produces the
    same output as before, so there is nothing to announce.
    """
    if state.strip():
        return []
    detected = sorted(
        {lsc for entry in entries if (lsc := lsc_from_team_code(entry.team)) and has_lsc_standards(lsc)}
    )
    if not detected:
        return []
    label = ", ".join(detected)
    return [
        f"State/LSC was blank, so it was auto-detected as {label} from the team code on your "
        f"entries — that's what surfaces the {label} qualifying-time lines. Type a State/LSC to override."
    ]


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


def timeline_source_warnings(timeline_events: list[TimelineEvent]) -> list[str]:
    if timeline_events and any(event.entries is None or event.heats is None for event in timeline_events):
        return [
            "The timeline source appears to be a meet-packet schedule rather than a final timeline. Event windows are estimated from session order and are less precise."
        ]
    return []


def short_meet_name(meet_name: str) -> str:
    cleaned = meet_name
    if "Paralympics Swimming National Championships" in cleaned:
        return "Para Nationals"
    cleaned = re.sub(r"^\d{4}\s+", "", cleaned)
    cleaned = re.sub(r"^\d+(?:st|nd|rd|th)\s+Annual\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace("MAC ", "")
    cleaned = cleaned.replace("Arizona ", "")
    cleaned = cleaned.replace("Invitational", "Invite")
    if "Speedo" in cleaned and "Invite" in cleaned:
        return "Speedo Invite"
    return normalize_space(cleaned)
