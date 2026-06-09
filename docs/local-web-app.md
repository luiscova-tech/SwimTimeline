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

`data/current_meets.json` keeps the Narwhal Invite listed as a hosted meet. Uploaded meets can be promoted into Current Meets from the app after a successful parse.

## Current Flow

1. Enter the swimmer name and state/LSC.
2. Use a hosted item from `Current Meets` when the meet documents are already listed.
3. If the meet is not listed, upload a meet flyer, psych sheet, timeline PDF, and optional relay PDF.
4. Choose calendar outputs:
   - daily
   - whole meet
   - swim by swim
5. Review the extracted swims and warnings.
6. Download `.ics` files and the audit.
7. For uploaded meets, use `Save To Current Meets` after a successful parse to make those documents reusable for other swimmers in the same meet.

## Current Meets

Hosted meets are tracked in `data/current_meets.json`. The app lists those entries under `Current Meets` and sends the selected swimmer name to the backend without requiring another upload.

The Narwhal Invite is preloaded with its meet flyer, final psych sheet, and final timeline from `meets/2026-narwhal-invite/input/`.

When `Save To Current Meets` is used, the backend copies the uploaded PDFs into `meets/current-hosted/<meet-id>/input/`, appends an entry to `data/current_meets.json`, and refreshes the Current Meets list. It does not silently publish every upload; the user has to promote a parsed meet deliberately.

## Extraction Behavior

- Swimmer matching supports `First Last` input against psych-sheet names like `Last, First M`.
- Psych sheet extraction counts matching names first, then records event number, event name, seed time, seed place, page, and column.
- Timeline extraction matches swims by event number and uses the next timeline row as the event-window end.
- Daily events start at the parsed session warm-up time. Distance check-in events can pull the daily calendar event earlier when the flyer requires check-in before another session's warm-up.
- Possible finals are included in descriptions as `if qualifies`; separate finals events are not generated until qualification is known.
- Relay uploads are parsed conservatively. Relays are included only when the relay document explicitly names the swimmer under a relay team.

## Standards

The standards engine is source-tracked and deliberately avoids guessing. It currently includes the 12 Girls LCM USA Swimming motivational and AZSI 11-12 Girls LCM event rows needed by the existing fixtures. If a swim reaches AAAA, the engine tries to show the next configured advanced target from `data/advanced_standards.json`. If no source-tracked advanced cut is loaded for that event, it emits a warning instead of inventing a Sectionals, Futures, Juniors, or Nationals time.

The advanced standard ladder should be expanded with official source/version metadata before public use; see `docs/standards-data.md`.
