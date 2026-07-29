"""Source-tracked time-standard lookup for calendar benchmark notes.

The catalog intentionally starts small and explicit. It covers the events that
have been verified in the current repo fixtures and can be expanded by adding
source-tracked rows without changing extraction code.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re


SOURCES = [
    {
        "name": "USA Swimming 2024-2028 Single Age Motivational Standards",
        "url": "https://www.usaswimming.org/docs/default-source/timesdocuments/time-standards/2025/2028-motivational-standards-single-age.pdf",
    },
    {
        "name": "AZSI 2025-2026 Age Group State Qualifying Time Standards",
        "url": "https://www.azswimming.org/wp-content/uploads/2025/08/AZSI-Age-Group-State-Qualifying-Time-Standards-2025-2026.pdf",
    },
    {
        "name": "AZSI 2025-2026 Age Group Regional Qualifying Time Standards",
        "url": "https://www.azswimming.org/wp-content/uploads/2025/09/AZSI-Age-Group-Regional-Qualifying-Time-Standards-2025-2026.pdf",
    },
]


@dataclass(frozen=True)
class StandardResult:
    event_key: str
    usa_summary: str
    lsc_summary: str
    advanced_summary: str | None
    confidence_summary: str


def parse_time(value: str) -> float | None:
    value = value.strip().upper().rstrip("YLS")
    if value in {"", "NT"}:
        return None
    parts = value.split(":")
    try:
        if len(parts) == 1:
            return float(parts[0])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
    except ValueError:
        return None
    return None


def format_time(seconds: float) -> str:
    minutes = int(seconds // 60)
    rest = seconds - minutes * 60
    if minutes:
        return f"{minutes}:{rest:05.2f}"
    return f"{rest:.2f}"


def canonical_event_key(event_name: str) -> str:
    text = re.sub(r"\s+", " ", event_name.lower())
    distance = None
    for candidate in ("800", "400", "200", "100", "50"):
        if candidate in text:
            distance = candidate
            break

    stroke = None
    if "free" in text:
        stroke = "free"
    elif "back" in text:
        stroke = "back"
    elif "breast" in text:
        stroke = "breast"
    elif "fly" in text or "butterfly" in text:
        stroke = "fly"
    elif " im" in f" {text}" or "individual medley" in text:
        stroke = "im"

    if distance and stroke:
        return f"{distance} {stroke}"
    return text


MOTIVATIONAL_GIRLS_LCM: dict[int, dict[str, dict[str, str]]] = {
    11: {
        "50 free": {"AAAA": "30.59", "AAA": "31.99", "AA": "33.39", "A": "34.79", "BB": "37.59", "B": "40.39"},
        "100 free": {"AAAA": "1:06.89", "AAA": "1:10.09", "AA": "1:13.29", "A": "1:16.49", "BB": "1:22.79", "B": "1:29.19"},
        "200 free": {"AAAA": "2:24.79", "AAA": "2:31.69", "AA": "2:38.49", "A": "2:45.39", "BB": "2:59.19", "B": "3:12.99"},
        "400 free": {"AAAA": "5:06.59", "AAA": "5:21.19", "AA": "5:35.79", "A": "5:50.29", "BB": "6:19.49", "B": "6:48.69"},
        "800 free": {"AAAA": "10:51.19", "AAA": "11:22.29", "AA": "11:53.29", "A": "12:24.29", "BB": "13:26.29", "B": "14:28.29"},
        "50 back": {"AAAA": "35.09", "AAA": "36.79", "AA": "38.49", "A": "40.09", "BB": "43.49", "B": "46.79"},
        "100 back": {"AAAA": "1:16.69", "AAA": "1:20.79", "AA": "1:24.99", "A": "1:29.19", "BB": "1:37.49", "B": "1:45.79"},
        "50 breast": {"AAAA": "39.29", "AAA": "41.09", "AA": "42.99", "A": "44.89", "BB": "48.59", "B": "52.29"},
        "100 breast": {"AAAA": "1:25.89", "AAA": "1:30.19", "AA": "1:34.49", "A": "1:38.79", "BB": "1:47.29", "B": "1:55.89"},
        "50 fly": {"AAAA": "32.69", "AAA": "34.29", "AA": "35.89", "A": "37.39", "BB": "40.49", "B": "43.59"},
        "100 fly": {"AAAA": "1:14.49", "AAA": "1:18.59", "AA": "1:22.69", "A": "1:26.89", "BB": "1:35.09", "B": "1:43.39"},
        "200 im": {"AAAA": "2:45.49", "AAA": "2:53.29", "AA": "3:01.19", "A": "3:09.09", "BB": "3:24.89", "B": "3:40.59"},
        "400 im": {"AAAA": "5:56.99", "AAA": "6:13.99", "AA": "6:30.99", "A": "6:47.99", "BB": "7:21.99", "B": "7:55.89"},
    },
    12: {
        "50 free": {"AAAA": "29.29", "AAA": "30.69", "AA": "31.99", "A": "33.39", "BB": "35.99", "B": "38.69"},
        "100 free": {"AAAA": "1:04.29", "AAA": "1:07.29", "AA": "1:10.39", "A": "1:13.49", "BB": "1:19.59", "B": "1:25.69"},
        "200 free": {"AAAA": "2:20.09", "AAA": "2:26.79", "AA": "2:33.49", "A": "2:40.09", "BB": "2:53.49", "B": "3:06.79"},
        "400 free": {"AAAA": "4:54.69", "AAA": "5:08.69", "AA": "5:22.79", "A": "5:36.79", "BB": "6:04.79", "B": "6:32.89"},
        "800 free": {"AAAA": "10:16.79", "AAA": "10:46.19", "AA": "11:15.59", "A": "11:44.89", "BB": "12:43.69", "B": "13:42.39"},
        "50 back": {"AAAA": "33.69", "AAA": "35.29", "AA": "36.89", "A": "38.49", "BB": "41.69", "B": "44.89"},
        "100 back": {"AAAA": "1:12.89", "AAA": "1:16.89", "AA": "1:20.79", "A": "1:24.79", "BB": "1:32.69", "B": "1:40.59"},
        "50 breast": {"AAAA": "37.39", "AAA": "39.09", "AA": "40.89", "A": "42.69", "BB": "46.19", "B": "49.79"},
        "100 breast": {"AAAA": "1:21.79", "AAA": "1:25.89", "AA": "1:29.99", "A": "1:33.99", "BB": "1:42.19", "B": "1:50.39"},
        "50 fly": {"AAAA": "31.39", "AAA": "32.89", "AA": "34.29", "A": "35.79", "BB": "38.79", "B": "41.79"},
        "100 fly": {"AAAA": "1:10.89", "AAA": "1:14.79", "AA": "1:18.79", "A": "1:22.69", "BB": "1:30.59", "B": "1:38.39"},
        "200 im": {"AAAA": "2:38.09", "AAA": "2:45.59", "AA": "2:53.19", "A": "3:00.69", "BB": "3:15.79", "B": "3:30.79"},
        "400 im": {"AAAA": "5:36.69", "AAA": "5:52.69", "AA": "6:08.69", "A": "6:24.79", "BB": "6:56.79", "B": "7:28.89"},
    },
}


AZSI_11_12_GIRLS_LCM: dict[str, dict[str, str]] = {
    "50 free": {"state": "32.59", "regional": "37.89"},
    "100 free": {"state": "1:11.99", "regional": "1:24.79"},
    "200 free": {"state": "2:39.39", "regional": "3:03.49"},
    "400 free": {"state": "5:25.79", "regional": "6:23.39"},
    "800 free": {"state": "11:52.49", "regional": "12:21.49"},
    "50 back": {"state": "39.19", "regional": "47.19"},
    "100 back": {"state": "1:21.09", "regional": "1:42.19"},
    "50 breast": {"state": "44.09", "regional": "53.19"},
    "100 breast": {"state": "1:33.79", "regional": "1:54.59"},
    "50 fly": {"state": "36.39", "regional": "44.49"},
    "100 fly": {"state": "1:22.79", "regional": "1:41.89"},
    "200 im": {"state": "2:56.29", "regional": "3:31.59"},
    "400 im": {"state": "6:04.49", "regional": "7:16.49"},
}


ROOT = Path(__file__).resolve().parents[1]
ADVANCED_STANDARDS_PATH = ROOT / "data" / "advanced_standards.json"


def load_advanced_catalog(path: Path = ADVANCED_STANDARDS_PATH) -> tuple[list[dict], dict[str, list[dict[str, str]]]]:
    if not path.exists():
        return [], {}
    data = json.loads(path.read_text(encoding="utf-8"))
    sources = data.get("sources", [])
    cuts = data.get("cuts", {})
    normalized: dict[str, list[dict[str, str]]] = {}
    for event_name, rows in cuts.items():
        key = canonical_event_key(event_name)
        normalized[key] = [row for row in rows if row.get("time")]
        normalized[key].sort(key=lambda row: parse_time(row["time"]) or -1, reverse=True)
    return sources, normalized


ADVANCED_SOURCES, ADVANCED_LADDER = load_advanced_catalog()
SOURCES.extend(ADVANCED_SOURCES)


def achieved_tier(seed_seconds: float, standards: dict[str, str]) -> tuple[str | None, str | None]:
    ordered = ["AAAA", "AAA", "AA", "A", "BB", "B"]
    for idx, tier in enumerate(ordered):
        cut = parse_time(standards[tier])
        if cut is not None and seed_seconds <= cut:
            next_tier = ordered[idx - 1] if idx > 0 else None
            return tier, next_tier
    return None, "B"


def lookup(event_name: str, seed_time: str, state: str = "AZ", age: str | int | None = None) -> StandardResult:
    event_key = canonical_event_key(event_name)
    seed_seconds = parse_time(seed_time)
    swimmer_age = parse_age(age)
    gender = event_gender(event_name)
    usa_standards = motivational_standards(event_key, swimmer_age, gender)
    azsi = AZSI_11_12_GIRLS_LCM.get(event_key) if state.upper() == "AZ" and gender == "girls" and swimmer_age in {11, 12} else None

    usa_summary = "USA-S: standard not configured for this event"
    advanced_summary = None
    confidence_parts: list[str] = []
    if seed_seconds is None:
        return StandardResult(event_key, "USA-S: seed time not parseable", "LSC: seed time not parseable", None, "Standards confidence: not calculated")
    if gender != "girls":
        usa_summary = "USA-S: not configured for this gender"
        lsc_summary = "LSC: not configured for this gender/state"
        return StandardResult(event_key, usa_summary, lsc_summary, None, "Standards confidence: not configured")
    if swimmer_age not in {11, 12}:
        usa_summary = "USA-S: not configured for this swimmer age"
        lsc_summary = "LSC: not configured for this swimmer age/state"
        return StandardResult(event_key, usa_summary, lsc_summary, None, "Standards confidence: not configured")

    if seed_seconds is not None and usa_standards:
        tier, next_tier = achieved_tier(seed_seconds, usa_standards)
        source_label = f"USA-S {swimmer_age} Girls LCM"
        if tier:
            if next_tier:
                usa_summary = f"{source_label}: {tier}; next {next_tier} {usa_standards[next_tier]}"
            else:
                usa_summary = f"{source_label}: AAAA"
                advanced_summary = next_advanced_target(event_key, seed_seconds)
        else:
            usa_summary = f"{source_label}: below B; B target {usa_standards['B']}"
        confidence_parts.append("USA-S verified")
    elif not usa_standards:
        confidence_parts.append("USA-S not configured")

    lsc_summary = "LSC: standards not configured for this state/event"
    if seed_seconds is not None and azsi:
        state_cut = parse_time(azsi["state"])
        regional_cut = parse_time(azsi["regional"])
        if state_cut is not None and seed_seconds <= state_cut:
            lsc_summary = f"AZSI 11-12 Girls LCM: State met; State {azsi['state']}, Regional {azsi['regional']}"
        elif regional_cut is not None and seed_seconds <= regional_cut:
            lsc_summary = f"AZSI 11-12 Girls LCM: Regional met; State target {azsi['state']}, Regional {azsi['regional']}"
        else:
            lsc_summary = f"AZSI 11-12 Girls LCM: target State {azsi['state']}, Regional {azsi['regional']}"
        confidence_parts.append("AZSI verified")
    else:
        confidence_parts.append("LSC not configured")

    if advanced_summary and advanced_summary.startswith("Beyond AAAA"):
        confidence_parts.append("advanced verified")
    elif advanced_summary:
        confidence_parts.append("advanced partial")
    return StandardResult(event_key, usa_summary, lsc_summary, advanced_summary, f"Standards confidence: {', '.join(confidence_parts)}")


def parse_age(age: str | int | None) -> int | None:
    if isinstance(age, int):
        return age
    match = re.search(r"\d{1,2}", str(age or ""))
    return int(match.group(0)) if match else None


def event_gender(event_name: str) -> str | None:
    text = event_name.lower()
    if re.search(r"\b(girls|women)\b", text):
        return "girls"
    if re.search(r"\b(boys|men)\b", text):
        return "boys"
    return None


def motivational_standards(event_key: str, swimmer_age: int | None, gender: str | None) -> dict[str, str] | None:
    if gender != "girls" or swimmer_age is None:
        return None
    return MOTIVATIONAL_GIRLS_LCM.get(swimmer_age, {}).get(event_key)


def next_advanced_target(event_key: str, seed_seconds: float) -> str | None:
    remaining: list[dict[str, str]] = []
    for row in ADVANCED_LADDER.get(event_key, []):
        cut = parse_time(row["time"])
        if cut is not None and seed_seconds > cut:
            remaining.append(row)
    if remaining:
        next_row = remaining[0]
        then_rows = remaining[1:5]
        summary = f"Beyond AAAA: next {next_row['name']} {next_row['time']}"
        if then_rows:
            then = ", ".join(f"{row['name']} {row['time']}" for row in then_rows)
            summary = f"{summary}; then {then}"
        return summary
    if event_key in ADVANCED_LADDER:
        return "Advanced standards loaded; swimmer has met all configured targets"
    return "Advanced cuts beyond AAAA are not configured for this event yet"
