# Subscriber Notifications

Email families when a meet they follow gets its documents loaded. This runs **from your own
machine, by hand** — it is deliberately not part of the deployed web service.

## Why local-only

Two independent reasons, both of which would have to stop being true before this could move server-side:

- **The subscriber list can't be committed.** It holds real parent email addresses and real
  children's names. The relay rosters in `data/internal_relay_sources/` solve the same privacy
  problem by storing salted hashes, but that trick doesn't transfer: you can't send an email to a
  hash. So the whole file stays out of this public repo, and the notifier that reads it has to run
  where the file is — your laptop.
- **Render's disk is ephemeral.** A subscriber list or an "already notified" log stored on the web
  service would be silently wiped by the next deploy. The first symptom would be every subscriber
  getting re-emailed about a meet they were already told about.

## One-time setup

### 1. Create your local subscriber file

```bash
cp data/subscribers.local.example.json data/subscribers.local.json
```

Then edit `data/subscribers.local.json`. Both it and `data/notify_log.local.json` are gitignored —
verify any time with:

```bash
git check-ignore -v data/subscribers.local.json data/notify_log.local.json
```

Format:

```json
{
  "subscribers": [
    {
      "email": "parent@example.com",
      "swimmers": [
        { "name": "Cova, Mila L", "team": "AZ" },
        { "name": "Cova, Sam" }
      ]
    }
  ]
}
```

- `name` — use the form the psych sheet prints (`Last, First`) when you can. A bare surname works,
  but is **refused** if it matches more than one swimmer at that meet (see "Ambiguous names").
- `team` — optional. Only ever *narrows* a match; it can never create one. A bare LSC (`AZ`) matches
  a zone sheet's `Arizona`; a club code (`MAC-AZ`) requires that exact club. `state` is accepted as
  an alias.
- One entry per email address. Put all of a family's swimmers in that entry's `swimmers` list —
  duplicate addresses are rejected rather than merged, because they'd double-email that person.

The file is validated strictly: a malformed entry stops the run instead of being skipped, so a typo
can never masquerade as "nobody matched".

### 2. Get a Resend API key

Create one at <https://resend.com> → **API Keys**. It is read only from the `RESEND_API_KEY`
environment variable — never hardcoded, never written to any file in this repo. Pass it per-command
(below) or export it in your shell profile.

### 3. Sender address — read this before emailing anyone but yourself

The default sender is Resend's shared sandbox address, `onboarding@resend.dev`. It needs no setup,
but **Resend only delivers from it to your own Resend account's email address** — for this project
that is `swimtimelineapp@gmail.com`. Mail to any other address, including `lcova@asu.edu`, is
rejected by Resend until a sending domain is verified. So the sandbox sender is good for testing the
pipeline against yourself, and cannot be used to notify actual families.

To email real families, verify a domain in Resend and set:

```bash
export NOTIFY_FROM_EMAIL="SwimTimeline <meets@yourdomain.com>"
```

## Running it

Preview first — no API key needed, nothing is sent, and it prints the exact emails:

```bash
./.venv312/bin/python scripts/notify_subscribers.py 2026-wzag-championships-boise --dry-run
```

Then send:

```bash
RESEND_API_KEY=re_your_key ./.venv312/bin/python scripts/notify_subscribers.py 2026-wzag-championships-boise
```

`meet_id` must exist in `data/current_meets.json`. The script refuses to run for a meet that isn't
ready for lookup yet — telling families a meet is ready when the site can't search it would send them
to a dead end.

Expect roughly a second per configured swimmer: each one is matched by re-reading the meet's psych
sheet through the site's own search path.

### Re-running

Every send is recorded in `data/notify_log.local.json`, keyed by `(email, meet_id)`. Re-running the
same meet skips anyone already notified, so it is safe to run again after adding subscribers — only
the new people get email. The log is flushed to disk after *each* send, so an interrupted run can
never re-send on the next attempt.

To deliberately re-send anyway:

```bash
RESEND_API_KEY=re_your_key ./.venv312/bin/python scripts/notify_subscribers.py <meet_id> --force
```

## Ambiguous names

Matching calls `extract_psych_entries()` — the same entry point the website's swimmer search uses —
so it inherits the guard that stops `Stein` from silently matching the *Steinbis* children. A name
that resolves to more than one real swimmer is **never emailed about**. It's reported at the end of
the run so you can make it specific:

```
  Ambiguous names -- NOT emailed, fix these in data/subscribers.local.json:
    * parent@example.com: 'Yang' -> 'Yang' matches more than one swimmer at this meet
      (Richelle Yang, Roddy Yang, Yi Yang) ...
```

Fix by making the **name** specific — `Yang, Richelle`. Adding a `team` will *not* help: ambiguity is
resolved before the team filter is even consulted, deliberately, because using a hand-typed team to
pick between two real children would be second-guessing the very guard that exists to prevent
exactly that. The name has to identify one swimmer on its own.

## Troubleshooting

**`403 Forbidden: error code: 1010`** — not an authentication problem, despite the 403. Resend sits
behind Cloudflare, which rejects Python's default `Python-urllib/…` User-Agent as a banned browser
signature before the request reaches the API. The script sends an explicit `User-Agent` to avoid
this; if you copy the request shape into another tool, carry that header with it.

**`401 restricted_api_key`** — expected when reading. The project's key is scoped to *sending only*,
so `GET /emails/{id}` is refused. The proof a send succeeded is the `POST` response itself: HTTP 200
with a message id, which the script prints and records in the notify log. To inspect delivery status
after the fact, use the Resend dashboard or issue a full-access key.

**A subscriber who should have matched shows as "no match"** — check the run's tail. A name that
resolves to several swimmers is listed under *Ambiguous names*, and a declared team that contradicts
the sheet is listed under *Team mismatches*. Both are deliberate refusals, reported rather than
silently emailed.

## Back up your local files

Because they're gitignored, `data/subscribers.local.json` and `data/notify_log.local.json` are **not**
protected by git. If the machine is lost, the subscriber list goes with it — and so does the record of
who has already been emailed, which means the next run would notify everyone again. Keep a copy
somewhere private (password manager, encrypted backup) — not in this repo.

## Unsubscribing

Each email ends with a plain-text line asking the reader to reply to be removed. At this scale that's
handled by hand: delete the person from `data/subscribers.local.json`. There is no automated
unsubscribe endpoint yet; add one before this grows past a list you can manage personally.
