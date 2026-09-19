# SwimTimeline Ideas & Backlog

Pull from this when scoping the next Claude Code prompt. Add new
ideas here as they come up so they don't get lost.

## Open backlog

- Multi-LSC auto-detection — deprioritized until there's a non-AZ
  user base.
- ~~Cold start mitigation~~ — done. Decided against the Render Starter
  compute upgrade; instead set up three cron-job.org jobs hitting
  https://swimtimeline.org/api/health every 10 minutes, scoped to when
  people actually check the site rather than running continuously:
  Mon-Thu 10am-10pm, Fri-Sat 5am-midnight, Sun 5am-6pm (America/Phoenix
  time). That's roughly 99 hrs/week (~430 hrs/month), well under
  Render's 750 free instance-hours/month cap.
- Custom domain: decided to keep the "SwimTimeline" name rather than
  rebrand for a .com — registering swimtimeline.org. (Custom domains
  are free on Render's Hobby workspace plan; no compute-plan change
  needed for this.)
- Go-live prerequisites for subscriber notifications: verify a domain
  with Resend and set NOTIFY_FROM_EMAIL, replace the test entry in
  data/subscribers.local.json with the real Mesa Aquatics Club
  roster, keep a private backup of data/notify_log.local.json outside
  git.

## Known, deliberately deferred gaps

- parse_date_range() doesn't handle a single spelled-out date
  ("September 12, 2026" alone, no range) — only a single numeric date
  and the two range shapes are handled. No real fixture exists for
  this shape yet.
- Badge cards: heat_interval is parsed and available on every
  SessionCard but not rendered on the card itself — draw_card()'s
  header only shows the session label and meet/date/start. A one-line
  addition to the meta line would surface it.

## Future ideas (unscoped)

- OCR a photo/scan of a meet document into readable text (requested by
  Luis). Real design tension: the parser depends on native PDF text plus
  its column/position layout (see docs/extraction-rules.md), not OCR'd
  text, and OCR errors on event numbers or times would be silently wrong
  in a way that conflicts with this app's no-guessing philosophy. This
  would need a genuinely new, separately-validated extraction path, not
  just an OCR-to-text swap. Not building this now — logging it only.
