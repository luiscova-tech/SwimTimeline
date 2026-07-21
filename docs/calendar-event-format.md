# Calendar Event Format

This project generates an `.ics` file first so each meet can be reviewed before anything is pushed directly to Google Calendar. The same event fields should also be usable later with the Google Calendar API to add attendees and send updates.

Default calendar output for future meets:

- A daily-summary `.ics` with one calendar event per meet day.
- A detailed `.ics` with one calendar event per verified swim/relay, when useful.

Daily-summary events are the preferred Google Calendar import because they are easier to scan, easier to invite family to, and less noisy on mobile.

Use `seed place` or `seed`, never bare `place`, when referring to psych sheet ranking.

## Event Title

For daily-summary events, use:

```text
{swimmer_name} - {meet_short_name}: Day {meet_day_number} ({day_name})
```

Examples:

```text
Mila Cova - Speedo Invite: Day 1 (Friday)
Mila Cova - Speedo Invite: Day 2 (Saturday)
```

For detailed event-level output, use a short title that is useful on a phone screen:

```text
{swimmer_name} - Event {event_number}: {event_short_name}
```

Examples:

```text
Mila Cova - Event 31: 13&O 100 Breast
Mila Cova - Event 61: 13&O 50 Free
Mila Cova - Relay 37: 13&O 400 Free Relay
```

## Event Time

For daily-summary events:

* Start: first relevant warm-up time for that day.
* End: end of the last verified morning/timed-final swim window for that day.
* Include possible finals in the description, but do not create separate finals calendar events until qualification is known.
* Include timed-final events in the daily summary exactly as scheduled by the meet.

For detailed event-level output, prefer the timeline-estimated swim window, not the full session window.

* Start: estimated swim time minus 15 minutes.
* End: estimated swim time plus 15 minutes.
* If timeline precision is weak, widen the window to 20–30 minutes.
* If no timeline is available, do not guess; create no swimmer event or mark the event as pending in a review file.
* Treat all timeline-derived swim times as estimates unless an official heat sheet provides an exact time.
* If the meet requires positive check-in, include that requirement prominently in the calendar description.

## Location

Use the meet facility:

```text
Kino Aquatic Center, 848 N. Horne Ave., Mesa, AZ 85203
```

## Description Template

```text
{swimmer_name}
{meet_name}

Day: Day {meet_day_number} - {day}, {date}
Session: #{session_number} - {session_name}
Warm-up: {warmup_time}
Meet start: {session_start_time}
Pool/course: {pool_or_course}

Event: #{event_number} - {full_event_name}
Seed time: {seed_time}
Seed place: {seed_place}
Estimated swim time: {estimated_swim_time}
Estimated window: {window_start} - {window_end}

Important:
- Estimated timeline only; official heat sheets control actual swim time.
- Positive check-in required. (Only include when required by the meet.)

Finals:
{finals_note}

Benchmarks:
USA Swimming motivational: {usa_standard_summary}
Arizona Swimming: {az_standard_summary}

Source verification:
Psych sheet: page {psych_page}, {psych_column} column
Timeline: event #{event_number}
Relay source: {relay_source_note}

Notes:
{calendar_notes}
```

## Suggested Description Style

Keep the first screen dense and parent-friendly. The most important information should be visible without scrolling.

Daily summary example:

```text
Mila Cova
Speedo Invite

Day: Day 2 - Saturday, May 23, 2026
Session: #5 - Saturday Age Group Prelims
Warm-up: 6:30 AM
Meet start: 7:30 AM
Pool/course: 25-yard pool session; psych sheet lists events as LC Meter

Mila's swims:
#41 Relay - 11-12 200 Medley Relay | Relay A, leg 4 | 7:30-7:35 AM
#43 11-12 200 Free | seed 2:30.74 | seed place 4 | 7:39-7:56 AM
#47 11-12 100 Breast | seed 1:28.02 | seed place 1 | 8:19-8:28 AM
#55 Relay - 11-12 400 Free Relay | Relay A, leg 4 | 9:30-9:36 AM

Possible finals:
#43 200 Free at 5:30 PM if top 8
#47 100 Breast at 6:01 PM if top 8

Benchmarks:
#43 USA-S AA, next AAA 2:26.79 | AZSI State met
#47 USA-S AA, next AAA 1:25.89 | AZSI State met

Source verification:
Psych sheet and relay document verified; see meet audit files.
```

Detailed event example:

```text
Mila Cova
Speedo Invite

Day: Saturday, May 23, 2026
Session: #4 - Senior Prelims
Pool/course: 50m LCM

Event: #31 - 13&O 100 Breast
Seed time: 1:18.42
Seed place: 14
Estimated swim time: 9:42 AM
Estimated window: 9:27-9:57 AM

Finals:
B/A finals if top 16 in age group. Finals session starts 5:30 PM.

Benchmarks:
USA-S: achieved {tier}; next {next_tier} at {next_standard_time}
AZSI: Regional {regional_time} | State {state_time}

Source verification:
Psych sheet: page 4, middle column
Timeline: event #31
Relay source: n/a
```

The benchmark line should include only the nearest useful standards, not the entire table. A good default is:

- the swimmer's achieved tier, if any
- the next faster USA Swimming motivational standard
- the AZ Regional and State standards for the event, age group, gender, and course

## Finals Notes

Use plain language based only on meet rules and timeline data.

### Timed Finals

```text
Timed final event. No separate finals session.

Estimated swim time is based on the published timeline and may change during the session.

Positive check-in is required if specified by the meet information.
```

### Qualifying Finals (Top 16)

```text
Finals possible if top 16 in age group. Finals session starts 5:30 PM; exact swim time depends on qualification.
```

### Qualifying Finals (Top 8)

```text
Finals possible if top 8. Finals session starts 5:30 PM; exact swim time depends on qualification.
```

## Invite Strategy

The `.ics` file is the review and import artifact. Direct invitations should be a later Google Calendar API step because Google Calendar does not import guests from manually imported events.

Future direct-publish fields:

```json
{
  "summary": "Mila - Event 31: 13&O 100 Breast",
  "location": "Kino Aquatic Center, 848 N. Horne Ave., Mesa, AZ 85203",
  "description": "...",
  "start": {
    "dateTime": "2026-05-23T09:27:00-07:00",
    "timeZone": "America/Phoenix"
  },
  "end": {
    "dateTime": "2026-05-23T09:57:00-07:00",
    "timeZone": "America/Phoenix"
  },
  "attendees": [
    { "email": "person@example.com" }
  ]
}
```

When publishing through the Google Calendar API, use attendee email addresses plus an explicit send-updates setting so invite emails are sent.

## Standards Sources

Time standards are external reference data and must be versioned by season/source before use.

- USA Swimming motivational standards: use the official 2024-2028 single-age standards unless the meet requires a different standard set.
- Arizona Swimming standards: use the official AZSI qualifying standards for the meet season.

Do not calculate benchmark lines until the swimmer's age, gender, course, event, and seed time have all been verified.
