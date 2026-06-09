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


MOTIVATIONAL_12_GIRLS_LCM: dict[str, dict[str, str]] = {
    "50 free": {"AAAA": "29.29", "AAA": "30.49", "AA": "31.69", "A": "32.89", "BB": "35.29"},
    "100 free": {"AAAA": "1:04.29", "AAA": "1:07.09", "AA": "1:09.89", "A": "1:12.69", "BB": "1:18.19"},
    "200 free": {"AAAA": "2:20.09", "AAA": "2:26.79", "AA": "2:33.49", "A": "2:40.19", "BB": "2:53.59"},
    "400 free": {"AAAA": "4:59.29", "AAA": "5:10.99", "AA": "5:22.79", "A": "5:34.49", "BB": "5:57.99"},
    "800 free": {"AAAA": "10:30.59", "AAA": "10:53.09", "AA": "11:15.59", "A": "11:38.19", "BB": "12:23.19"},
    "50 back": {"AAAA": "33.69", "AAA": "35.29", "AA": "36.89", "A": "38.49", "BB": "41.69"},
    "100 back": {"AAAA": "1:15.19", "AAA": "1:18.39", "AA": "1:21.59", "A": "1:24.79", "BB": "1:31.19"},
    "50 breast": {"AAAA": "37.69", "AAA": "39.29", "AA": "40.89", "A": "42.49", "BB": "45.69"},
    "100 breast": {"AAAA": "1:22.49", "AAA": "1:25.89", "AA": "1:29.29", "A": "1:32.69", "BB": "1:39.49"},
    "50 fly": {"AAAA": "32.09", "AAA": "33.29", "AA": "34.49", "A": "35.79", "BB": "38.19"},
    "100 fly": {"AAAA": "1:12.09", "AAA": "1:15.39", "AA": "1:18.69", "A": "1:21.99", "BB": "1:28.59"},
    "200 im": {"AAAA": "2:40.09", "AAA": "2:47.09", "AA": "2:54.09", "A": "3:01.09", "BB": "3:15.09"},
    "400 im": {"AAAA": "5:36.29", "AAA": "5:50.79", "AA": "6:05.19", "A": "6:19.59", "BB": "6:48.49"},
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
    ordered = ["AAAA", "AAA", "AA", "A", "BB"]
    for idx, tier in enumerate(ordered):
        cut = parse_time(standards[tier])
        if cut is not None and seed_seconds <= cut:
            next_tier = ordered[idx - 1] if idx > 0 else None
            return tier, next_tier
    return None, "BB"


def lookup(event_name: str, seed_time: str, state: str = "AZ") -> StandardResult:
    event_key = canonical_event_key(event_name)
    seed_seconds = parse_time(seed_time)
    usa_standards = MOTIVATIONAL_12_GIRLS_LCM.get(event_key)
    azsi = AZSI_11_12_GIRLS_LCM.get(event_key) if state.upper() == "AZ" else None

    usa_summary = "USA-S: standard not configured for this event"
    advanced_summary = None
    if seed_seconds is not None and usa_standards:
        tier, next_tier = achieved_tier(seed_seconds, usa_standards)
        if tier:
            if next_tier:
                usa_summary = f"USA-S {tier}; next {next_tier} {usa_standards[next_tier]}"
            else:
                usa_summary = "USA-S AAAA"
                advanced_summary = next_advanced_target(event_key, seed_seconds)
        else:
            usa_summary = f"USA-S below BB; BB target {usa_standards['BB']}"

    lsc_summary = "LSC: standards not configured for this state/event"
    if seed_seconds is not None and azsi:
        state_cut = parse_time(azsi["state"])
        regional_cut = parse_time(azsi["regional"])
        if state_cut is not None and seed_seconds <= state_cut:
            lsc_summary = f"AZSI State met; State {azsi['state']}, Regional {azsi['regional']}"
        elif regional_cut is not None and seed_seconds <= regional_cut:
            lsc_summary = f"AZSI Regional met; State target {azsi['state']}, Regional {azsi['regional']}"
        else:
            lsc_summary = f"AZSI target State {azsi['state']}, Regional {azsi['regional']}"

    return StandardResult(event_key, usa_summary, lsc_summary, advanced_summary)


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
