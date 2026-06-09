# Swimmer Timeline Extraction Rules

Extract swim meet schedules for one swimmer from the documents provided for the current meet only.

## Hard Rules

- Use only the files for the current meet.
- Do not use prior chats, prior meet folders, or earlier meets.
- Do not guess or infer event assignments.
- Do not reuse seed times across events.
- If the required documents are incomplete or unreadable, say so plainly.

## Psych Sheet Reading Order

- Process page by page.
- Each page is three columns.
- Read each page in this order:
  - Left column top to bottom.
  - Middle column top to bottom.
  - Right column top to bottom.
- Continue to the next page only after finishing all three columns.

## Extraction Rules

- Only include an event when `Cova, Mila L` appears explicitly in that event.
- For each verified occurrence, record:
  - day
  - event number
  - full event name
  - seed time
  - seed place
  - page number
  - column
- If the swimmer's entry spans a column break or page break, trace upward within the same column flow until the event header is found.
- If the event header cannot be verified, mark the entry uncertain and do not guess.

## Relay Rules

- Use the relay document only.
- Include relays only when the swimmer is explicitly listed by name.
- Relays are morning-session only unless the relay document says otherwise.

## Timeline Rules

- Use the timeline document to estimate morning and finals times.
- Match timeline entries by event number.
- Assume top 10 per age group advance to finals unless the timeline or meet document states otherwise.

## Verification Rules

- Make one full pass through the psych sheet and log every exact occurrence of `Cova, Mila L` with page and column.
- Make a second pass to confirm the count and check for missed entries.
- If both passes cannot be completed, mark the result incomplete.

## Output Format

- Group by day and by session.
- For each event, show:
  - Event #
  - Event Name
  - Seed Time
  - Seed Place
  - Est. Morning Time
  - Est. Finals Time
- Put relays in a separate relay section.
- End with:

```text
Total verified events found: X
Total verified relays found: X
No additional entries for "Cova, Mila L" were found after full document verification.
```
