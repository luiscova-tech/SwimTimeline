# Speedo Invite Calendar Output

This folder will hold generated calendar files for the Speedo Invite.

Current status: generated from verified individual events and relays.

Outputs:

- `mila-speedo-invite-daily.ics`: preferred Google Calendar import file with one calendar event per meet day.
- `mila-speedo-invite.ics`: detailed calendar import file with 12 swim/relay events.
- Event preview table: `../extracted/mila-timeline.md`
- Later: Google Calendar API payloads for direct publishing and attendee invitations.

The `.ics` file includes verified morning/timed-final events and relays only. Possible finals are included in event descriptions, but not added as separate calendar events because qualification is not yet known.
