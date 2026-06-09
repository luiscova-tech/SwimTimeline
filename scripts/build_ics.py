#!/usr/bin/env python3
"""Build a reviewable ICS calendar from a swimmer event JSON file."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def escape_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def fold_line(line: str) -> list[str]:
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return [line]

    folded: list[str] = []
    current = ""
    current_len = 0
    for char in line:
        char_len = len(char.encode("utf-8"))
        if current and current_len + char_len > 75:
            folded.append(current)
            current = " " + char
            current_len = 1 + char_len
        else:
            current += char
            current_len += char_len
    if current:
        folded.append(current)
    return folded


def add_line(lines: list[str], line: str) -> None:
    lines.extend(fold_line(line))


def local_dt(value: str) -> str:
    return datetime.fromisoformat(value).strftime("%Y%m%dT%H%M%S")


def build_ics(data: dict) -> str:
    now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tzid = data["calendar"].get("timezone", "America/Phoenix")
    cal_name = data["calendar"].get("name", "Swimmer Timeline")

    lines: list[str] = []
    add_line(lines, "BEGIN:VCALENDAR")
    add_line(lines, "VERSION:2.0")
    add_line(lines, "PRODID:-//SwimTimeline//Swimmer Calendar//EN")
    add_line(lines, "CALSCALE:GREGORIAN")
    add_line(lines, "METHOD:PUBLISH")
    add_line(lines, f"X-WR-CALNAME:{escape_text(cal_name)}")
    add_line(lines, f"X-WR-TIMEZONE:{tzid}")
    add_line(lines, "BEGIN:VTIMEZONE")
    add_line(lines, f"TZID:{tzid}")
    add_line(lines, "BEGIN:STANDARD")
    add_line(lines, "DTSTART:19700101T000000")
    add_line(lines, "TZOFFSETFROM:-0700")
    add_line(lines, "TZOFFSETTO:-0700")
    add_line(lines, "TZNAME:MST")
    add_line(lines, "END:STANDARD")
    add_line(lines, "END:VTIMEZONE")

    for event in data["events"]:
        description = "\n".join(event["description_lines"])
        add_line(lines, "BEGIN:VEVENT")
        add_line(lines, f"UID:{event['uid']}")
        add_line(lines, f"DTSTAMP:{now}")
        add_line(lines, f"SUMMARY:{escape_text(event['title'])}")
        add_line(lines, f"DTSTART;TZID={tzid}:{local_dt(event['start'])}")
        add_line(lines, f"DTEND;TZID={tzid}:{local_dt(event['end'])}")
        add_line(lines, f"LOCATION:{escape_text(event['location'])}")
        add_line(lines, f"DESCRIPTION:{escape_text(description)}")
        add_line(lines, "STATUS:CONFIRMED")
        add_line(lines, "TRANSP:OPAQUE")
        add_line(lines, "END:VEVENT")

    add_line(lines, "END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    data = json.loads(args.input.read_text())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_ics(data))


if __name__ == "__main__":
    main()
