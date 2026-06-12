# Local Web App

The local web app turns the current one-off extraction workflow into a repeatable upload flow.

## Run

```bash
/Users/lcova/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 webapp/server.py --port 8765
```

Open `http://127.0.0.1:8765`.

For hosted environments, the server also reads `HOST` and `PORT` from the environment.

## Public Hosting

The app is deployable as a small Python web service. `requirements.txt` and `render.yaml` are included for a free Render web-service deployment:

- build command: `pip install -r requirements.txt`
- start command: `python webapp/server.py --host 0.0.0.0`

`data/current_meets.json` keeps reusable hosted meets listed, including Narwhal Invite, Shark Open, and Para Nationals. Uploaded meets can be promoted into Current Meets from the app after a successful parse.

## Current Flow

1. Enter one or more swimmer names. State/LSC is optional; hosted meets can supply their saved state, and blank uploads still generate calendars without local standards.
2. Use a hosted item from `Current Meets` when the meet documents are already listed.
3. If the meet is not listed, upload a meet flyer, psych sheet or heat sheet, timeline PDF, and optional relay PDF.
4. Choose family options:
   - combine swimmers into one family calendar, on by default
   - estimate heat/lane from a psych sheet, off by default because estimates are not final
5. Choose calendar outputs:
   - daily
   - whole meet
   - swim by swim
6. Review the extracted swims and warnings.
7. Download family `.ics` files, individual swimmer `.ics` files, and swimmer audits.
8. For uploaded meets, use `Save To Current Meets` after a successful parse to make those documents reusable for other swimmers in the same meet.

## Current Meets

Hosted meets are tracked in `data/current_meets.json`. The app lists those entries under `Current Meets` and sends the selected swimmer names to the backend without requiring another upload.

Narwhal Invite and Shark Open are preloaded with final timeline-style documents. Para Nationals is preloaded from a meet packet and psych sheet, so it is marked schedule-only and produces estimated event windows from session order.

When `Save To Current Meets` is used, the backend copies the uploaded PDFs into `meets/current-hosted/<meet-id>/input/`, appends an entry to `data/current_meets.json`, and refreshes the Current Meets list. It does not silently publish every upload; the user has to promote a parsed meet deliberately.

Current Meets entries store `start_date`, `end_date`, and `expires_at`. The public Current Meets list hides an entry once `expires_at` arrives, which is the day after the meet ends.

## Extraction Behavior

- Swimmer matching supports `First Last` input against psych-sheet or heat-sheet names like `Last, First M`. If exact matching finds no entries, the parser can use a high-confidence typo match such as one extra letter in the first or last name and shows a warning.
- Psych sheet extraction counts matching names first, then records event number, event name, seed time, seed place, page, and column.
- Heat sheet extraction supports HY-TEK `Meet Program` documents with `Event  1 ...` headers. When the row includes heat/lane information, the app records heat and lane instead of displaying that value as seed place.
- Optional heat/lane estimates use psych-sheet seed order and timeline heat counts. If the meet flyer identifies deck-seeded or circle-seeded event ranges, those events are skipped while other events can still receive estimated heat/lane values. Estimated values are labeled as estimated in the table, audit, and calendar descriptions.
- Timeline extraction matches swims by event number and uses the next timeline row as the event-window end. It tolerates glued HY-TEK rows such as `Prelims 10Boys`.
- Meet packet schedules can be used when no final timeline is available. Those event windows are estimated from session order and are less precise because the packet does not include heat counts or projected event times.
- Event rows include a format label such as `Prelim/final`, `Timed final`, or `Prelim only` based on the timeline sessions available for that event.
- Daily events start at the parsed session warm-up time. Distance check-in events can pull the daily calendar event earlier when the flyer requires check-in before another session's warm-up.
- Possible finals are included in descriptions as `if qualifies`; separate finals events are not generated until qualification is known.
- Relay uploads are parsed conservatively. Relays are included only when the relay document explicitly names the swimmer under a relay team.

## Usage Stats

The backend records aggregate usage in `data/usage_stats.json`, which is ignored by git. It stores total lookups and hashes of normalized swimmer names, not swimmer names in plain text.

`GET /api/usage` returns:

- `total_lookups`
- `unique_swimmer_names`
- `last_lookup_at`

On Render's free filesystem, these runtime stats can reset on restart or redeploy unless the app is later connected to persistent storage.

## Standards

The standards engine is source-tracked and deliberately avoids guessing. It currently includes the 12 Girls LCM USA Swimming motivational and AZSI 11-12 Girls LCM event rows needed by the existing fixtures. If a swim reaches AAAA, the engine tries to show the next configured advanced target from `data/advanced_standards.json`. If no source-tracked advanced cut is loaded for that event, it emits a warning instead of inventing a Sectionals, Futures, Juniors, or Nationals time.

The advanced standard ladder should be expanded with official source/version metadata before public use; see `docs/standards-data.md`.
