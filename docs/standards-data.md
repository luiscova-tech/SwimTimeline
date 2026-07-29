# Standards Data

SwimTimeline keeps standards source-tracked because cuts change by season, course, gender, age group, state/LSC, and championship series.

## Built In

The current built-in catalog covers the fixture events already present in this repo:

- USA Swimming 2024-2028 Single Age Motivational Standards, 11 Girls LCM
- USA Swimming 2024-2028 Single Age Motivational Standards, 12 Girls LCM
- AZSI 2025-2026 State and Regional Qualifying Time Standards, Women 11-12 LCM

The lookup uses the swimmer age parsed from the entry row. If a swimmer age, gender, course, state/LSC, or event is not configured, the app reports `not configured` rather than comparing against a nearby age group.

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
