# Standards Data

SwimTimeline keeps standards source-tracked because cuts change by season, course, gender, age group, state/LSC, and championship series.

## Built In

The USA Swimming motivational standards are loaded from `data/motivational_standards.json`,
which covers the full published catalog:

- USA Swimming 2024-2028 Motivational Standards (two-year age group), both genders, all
  three courses (SCY/SCM/LCM), and every age group (10 & under, 11-12, 13-14, 15-16,
  17-18), all events. Single-age standards are intentionally not used.
- AZSI 2025-2026 State and Regional Qualifying Time Standards, Women 11-12 LCM (an Arizona
  LSC layer that applies only to AZ 11-12 girls swimming long-course meters).

The data file is generated from the official PDF by
`scripts/extract_motivational_standards.py`. To adopt a new quad (2029-2032, etc.), drop
the new age-group PDF into `docs/Sources/`, rerun that script, review the JSON diff, and
commit — no parsing code changes.

The lookup reads **gender** and **course** from the event name (USA Swimming psych/heat
sheets label every event, e.g. "Girls 11-12 200 LC Meter Freestyle") and maps the
swimmer's age (from the entry row) to its two-year band. If gender, course, age band, or
event cannot be resolved, the app reports `not configured` rather than comparing against
an inapplicable group.

Calendar descriptions and result tables include a `Standards confidence` line:

- `USA-S verified`: official USA Swimming motivational row is configured for the swimmer age, gender, course, and event.
- `AZSI verified`: configured Arizona Swimming row is available for the swimmer age group and event.
- `advanced verified`: configured advanced cuts are available after AAAA.
- `not configured`: the app did not calculate a standard because the matching source row is not loaded.

## Advanced Cuts

Advanced cuts beyond AAAA are loaded from:

```text
data/advanced_standards.json
```

Use `data/advanced_standards.example.json` as the shape, but do not use the example times. Add only official, source-tracked standards such as Sectionals, Futures, Winter Juniors, Summer Juniors, US Open, or Nationals.

Each row should include:

- `name`: label shown in calendar notes.
- `scope`: region or meet series, such as `Western Zone / Arizona` or `USA Swimming`.
- `time`: qualifying cut.
- `source`: ID of the source entry.

If a swimmer reaches AAAA and no advanced row exists for that event, the app warns instead of guessing.

The current advanced catalog uses USA Swimming's 2026 standards block and includes source-tracked Women LCM rows for:

- Speedo Sectionals
- TYR Futures
- Speedo Winter Juniors where that event exists in the published Winter Juniors PDF
- Speedo Junior Nationals
- Toyota Nationals 18 & Under

The app presents the closest faster cut first, then the next faster targets in ladder order.
