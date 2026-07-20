# SwimTimeline

SwimTimeline creates swimmer-specific meet timelines that can be exported to a calendar service such as Google Calendar.

## Local Web App

Run the local upload app with the bundled Python runtime:

```bash
/Users/lcova/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 webapp/server.py --port 8765
```

Then open:

```text
http://127.0.0.1:8765
```

The app accepts a meet flyer, psych sheet or heat sheet, timeline, optional relay document, one or more swimmer names, and optional state/LSC. It generates reviewable calendar files for daily, whole-meet, and swim-by-swim imports, with optional combined family calendars. Daily calendar output is selected by default; the other calendar formats are opt-in. Relays are included only when the relay document explicitly names the swimmer. Uploaded files and generated outputs are stored under `.swimtimeline-runs/`, which is ignored by git.

Advanced cuts beyond AAAA are loaded from `data/advanced_standards.json`; see `docs/standards-data.md`.

## Public Hosting

The app can run as a public Python web service. This repo includes:

- `requirements.txt` for the Python dependency.
- `render.yaml` for a free Render web-service deployment.

The hosted Current Meets registry keeps reusable meets available without re-uploading their PDFs. Featured current meets can be date-gated so a meet such as Age Group State appears prominently only while families are actively using it.

## Meet Layout

Each meet gets its own folder so source documents, extraction notes, audit logs, and calendar output stay isolated:

```text
meets/
  2026-speedo-invite/
    input/
    extracted/
    calendar/
    audit/
```

## Current Meet

- Meet: AZ 2026 Long Course Age Group State Championship
- Dates: 2026-07-23 through 2026-07-26
- Status: meet flyer, psych sheet, and final timeline received; hosted current meet is ready for swimmer lookup. No relay document is included.

See `docs/extraction-rules.md` for the extraction rules to use for each meet.

See `docs/calendar-event-format.md` for the proposed Google Calendar / `.ics` event format.
