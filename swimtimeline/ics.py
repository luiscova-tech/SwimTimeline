"""Build reviewable ICS calendars from SwimTimeline event payloads."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Any year works here; only used to sample a winter and summer offset/name.
_TZ_SAMPLE_YEAR = 2024


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


def offset_str(offset: timedelta) -> str:
    total_minutes = round(offset.total_seconds() / 60)
    sign = "-" if total_minutes < 0 else "+"
    total_minutes = abs(total_minutes)
    return f"{sign}{total_minutes // 60:02d}{total_minutes % 60:02d}"


def vtimezone_lines(tzid: str) -> list[str]:
    try:
        zone = ZoneInfo(tzid)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("America/Phoenix")
        tzid = "America/Phoenix"

    winter = datetime(_TZ_SAMPLE_YEAR, 1, 15, tzinfo=zone)
    summer = datetime(_TZ_SAMPLE_YEAR, 7, 15, tzinfo=zone)
    winter_offset = winter.utcoffset() or timedelta(0)
    summer_offset = summer.utcoffset() or timedelta(0)

    lines = ["BEGIN:VTIMEZONE", f"TZID:{tzid}"]
    if winter_offset == summer_offset:
        lines.extend(
            [
                "BEGIN:STANDARD",
                "DTSTART:19700101T000000",
                f"TZOFFSETFROM:{offset_str(winter_offset)}",
                f"TZOFFSETTO:{offset_str(winter_offset)}",
                f"TZNAME:{winter.tzname() or 'STD'}",
                "END:STANDARD",
            ]
        )
    else:
        # US zones have used "2nd Sunday in March" / "1st Sunday in November"
        # DST transitions since 2007; every zone in STATE_TIMEZONES that
        # observes DST follows this rule.
        lines.extend(
            [
                "BEGIN:STANDARD",
                "DTSTART:19701101T020000",
                "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU",
                f"TZOFFSETFROM:{offset_str(summer_offset)}",
                f"TZOFFSETTO:{offset_str(winter_offset)}",
                f"TZNAME:{winter.tzname() or 'STD'}",
                "END:STANDARD",
                "BEGIN:DAYLIGHT",
                "DTSTART:19700308T020000",
                "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU",
                f"TZOFFSETFROM:{offset_str(winter_offset)}",
                f"TZOFFSETTO:{offset_str(summer_offset)}",
                f"TZNAME:{summer.tzname() or 'DST'}",
                "END:DAYLIGHT",
            ]
        )
    lines.append("END:VTIMEZONE")
    return lines


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
    for tz_line in vtimezone_lines(tzid):
        add_line(lines, tz_line)

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
