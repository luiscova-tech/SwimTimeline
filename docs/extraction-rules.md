# Swimmer Timeline Extraction Rules

Extract swim meet schedules for the swimmer names provided by the user from the documents for the current meet only.

## Hard Rules

* Use only the files for the current meet.
* Do not use prior chats, prior meet folders, or earlier meets.
* Do not guess or infer event assignments.
* Do not reuse seed times across events.
* If the required documents are incomplete or unreadable, say so plainly.

## Psych Sheet Reading Order

* Process page by page.
* Each page is three columns.
* Read each page in this order:

  * Left column top to bottom.
  * Middle column top to bottom.
  * Right column top to bottom.
* Continue to the next page only after finishing all three columns.

## Extraction Rules

* Only include an event when the swimmer appears explicitly in that event or matches the parser's high-confidence name correction rules.
* For each verified occurrence, record:

  * day
  * event number
  * full event name
  * seed time
  * seed place
  * page number
  * column
* If the swimmer's entry spans a column break or page break, trace upward within the same column flow until the event header is found.
* If the event header cannot be verified, mark the entry uncertain and do not guess.

## Relay Rules

* Use only the relay document or selected private relay add-on for relay assignments.
* Include relays only when the swimmer is explicitly listed by name in a relay document or matched by a private relay add-on.
* Read the meet flyer before assigning relay timing or finals behavior.
* If the flyer says relays are timed finals swum during preliminary sessions, place those relay windows in the preliminary session and clearly label the time as estimated.
* Do not display full relay roster names from private relay add-ons in the UI, calendar descriptions, or audit files.

## Timeline Rules

* Use the timeline document to estimate event windows and finals times.
* Match timeline entries by event number.
* Treat all timeline-derived swim times and event windows as estimates unless an official heat sheet provides an exact time.
* Timed-final events must still be included in the calendar output even though there is no separate finals session.
* If the flyer says an individual event is a timed final with the fastest seeded heat, top heat, or top swimmers competing during finals, do not label it as a prelim/final event.
  * Use the finals-session timeline when the swimmer's seed place is within the flyer-defined top seeded group.
  * Use the preliminary-session timeline for other seeded heats.
  * Clearly state that the event is a timed final and that the official heat sheet controls the final assignment.
* If the meet documentation states that an event or session requires positive check-in (positive checking), include that requirement in the extracted event notes and calendar output.
* Calendar entries for timed-final events or events requiring positive check-in must clearly state that:

  * the listed swim time is an estimate based on the published timeline;
  * actual swim time may vary during the session; and
  * positive check-in is required when specified by the meet information.
* Assume top 8 per age group advance to finals unless the timeline or meet document states otherwise.
* Optional heat/lane estimates may be shown only when clearly labeled as estimates. Do not treat estimated heat/lane as verified heat-sheet data.

## Timezone Rules

* Every entry in `data/current_meets.json` must set an explicit `timezone` field (an IANA zone name, e.g. `America/Phoenix`) based on the meet's actual venue. Do not leave it unset and do not infer it from the swimmer's state or LSC.
* The `STATE_TIMEZONES` fallback table in `swimtimeline/extract.py` only covers each state's majority zone, so it is not reliable for meets in a state that spans two zones. Confirm the venue directly (city/venue name on the meet flyer, or a maps lookup) before setting `timezone` for a meet in any of:
  * Florida — panhandle counties west of the Apalachicola River are Central; the rest of the state is Eastern.
  * Idaho — the northern panhandle counties are Pacific; the rest of the state, including Boise, is Mountain.
  * Texas, Kansas, Nebraska, South Dakota, North Dakota, Oregon — each has a smaller western or eastern county-level exception to the state's dominant zone.
  * Michigan, Indiana, Kentucky, Tennessee — each has a county-level Eastern/Central split.
* When a meet is promoted from an upload via "Save To Current Meets," the generated `timezone` field is inferred from the uploader's State/LSC form field, not the meet's venue. Check it by hand before treating the new hosted meet entry as correct.

## Verification Rules

* Make one full pass through the psych sheet or heat sheet and log swimmer occurrences with page and column.
* Make a second pass to confirm the count and check for missed entries.
* If both passes cannot be completed, mark the result incomplete.

## Output Format

* Group by day and by session.
* For each event, show:

  * Event #
  * Event Name
  * Seed Time
  * Seed Place
  * Est. Morning Time
  * Est. Finals Time (when applicable)
  * Positive Check-In Required (when applicable)
  * Timeline Estimate Disclaimer (when applicable)
* Put relays in a separate relay section.
* End with:

```text
Total verified events found: X
Total verified relays found: X
No additional entries for the swimmer were found after full document verification.
```
