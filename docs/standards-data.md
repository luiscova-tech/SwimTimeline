# Standards Data

SwimTimeline keeps standards source-tracked because cuts change by season, course, gender, age group, state/LSC, and championship series.

## Built In

The current built-in catalog covers the fixture events already present in this repo:

- USA Swimming 2024-2028 Single Age Motivational Standards, 12 Girls LCM
- AZSI 2025-2026 State and Regional Qualifying Time Standards, Women 11-12 LCM

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
