"""Regenerate data/internal_relay_sources/az-2026-wzag-relays.json from the coach's relay-assignment
email, mirroring the MAC state-meet roster file.

Only name/age/leg is taken from the email. First names (needed only to hash the same way live search
does), event names, and Arizona Relay A entry times are resolved from the WZAG psych sheet -- the
authoritative entry list. Because the email is a human typing against that sheet, name resolution is
a two-step, SCOPED match:

  1. exact (normalized last name, age) lookup against Arizona swimmers, then
  2. if that misses, a fuzzy pass over ONLY the Arizona + same-age candidate slice, accepting a
     correction ONLY when exactly one candidate is within edit-distance 1 (this is how "Rosetti"
     resolves to the psych sheet's "Rossetti"). Zero or multiple candidates FAIL LOUDLY (printed and
     left with an empty, unmatchable hash) rather than guessing.

Run:  PYTHONPATH=. ./.venv312/bin/python scripts/build_wzag_relays.py
"""

from pathlib import Path
import json
import re

from swimtimeline.extract import (
    extract_text_pages,
    levenshtein,
    name_pairs,
    normalize_event_header_name,
    normalize_name_token,
    relay_hashes_for_swimmer,
)

ROOT = Path(__file__).resolve().parents[1]
PSYCH = ROOT / "meets/2026-wzag-championships-boise/input/wzag psych sheet v3.pdf"
OUT = ROOT / "data/internal_relay_sources/az-2026-wzag-relays.json"
SALT = "swimtimeline-az-wzag-2026-relays-v1"

# From the relay-assignment email: (leg, last_name, age, email_first_name_if_disambiguated).
ROSTER = {
    23: ("Mixed 10 & Under 200 LC Meter Freestyle Relay", [(1, "Applegate", 10, None), (2, "Folster", 10, None), (3, "Penry", 10, None), (4, "Yang", 10, "Roddy")]),
    24: ("Mixed 12 & Under 200 LC Meter Freestyle Relay", [(1, "Beltran", 12, None), (2, "Carter", 11, None), (3, "Horst", 12, None), (4, "Rosetti", 10, None)]),
    25: ("Mixed 14 & Under 200 LC Meter Freestyle Relay", [(1, "Stein", 13, None), (2, "Hoppes", 12, None), (3, "Cova", 12, None), (4, "Yarosz", 13, None)]),
    50: ("Girls 12 & Under 200 LC Meter Medley Relay", [(1, "Rosetti", 10, None), (2, "Cova", 12, None), (3, "Duddleston", 12, None), (4, "Carter", 11, None)]),
    51: ("Boys 12 & Under 200 LC Meter Medley Relay", [(1, "Horst", 12, None), (2, "Beltran", 12, None), (3, "Folster", 10, None), (4, "Hoppes", 12, None)]),
    52: ("Girls 14 & Under 200 LC Meter Medley Relay", [(1, "Stein", 13, None), (2, "Pena", 14, None), (3, "Penry", 10, None), (4, "Yang", 12, "Richelle")]),
    74: ("Mixed 10 & Under 200 LC Meter Medley Relay", [(1, "Folster", 10, None), (2, "Rosetti", 10, None), (3, "Penry", 10, None), (4, "Yang", 10, "Roddy")]),
    75: ("Mixed 12 & Under 200 LC Meter Medley Relay", [(1, "Horst", 12, None), (2, "Cova", 12, None), (3, "Duddleston", 12, None), (4, "Carter", 11, None)]),
    76: ("Mixed 14 & Under 200 LC Meter Medley Relay", [(1, "Stein", 13, None), (2, "Yarosz", 13, None), (3, "Pena", 14, None), (4, "Yang", 12, "Richelle")]),
    99: ("Girls 12 & Under 200 LC Meter Freestyle Relay", [(1, "Cova", 12, None), (2, "Rosetti", 10, None), (3, "Carter", 11, None), (4, "Duddleston", 12, None)]),
    100: ("Boys 12 & Under 200 LC Meter Freestyle Relay", [(1, "Horst", 12, None), (2, "Folster", 10, None), (3, "Beltran", 12, None), (4, "Hoppes", 12, None)]),
    101: ("Girls 14 & Under 200 LC Meter Freestyle Relay", [(1, "Stein", 13, None), (2, "Penry", 10, None), (3, "Pena", 14, None), (4, "Yang", 12, "Richelle")]),
}
# Relays with a swimmer who withdrew (Adrian Beltran); the replacement was not yet published.
PENDING_EVENTS = {24, 51, 100}

txt = "\n".join(extract_text_pages(PSYCH))

# Arizona individual entries -> map (last_norm, age) -> First, and per-age candidate slices.
first_by_key: dict[tuple[str, int], str] = {}
az_by_age: dict[int, list[tuple[str, str]]] = {}  # age -> [(last_norm, First)]
for line in txt.splitlines():
    m = re.match(r"^(AZ)\s+[\d:]+\.?\d*L?\s+(\d{1,2})([A-Z][a-zA-Z'\-]+,\s*[A-Z][a-zA-Z'\-]+)", line.strip())
    if not m:
        continue
    age = int(m.group(2))
    pairs = name_pairs(m.group(3).strip())
    if not pairs:
        continue
    first, last = pairs[0]
    first_by_key.setdefault((last, age), first.title())
    slice_ = az_by_age.setdefault(age, [])
    if (last, first.title()) not in slice_:
        slice_.append((last, first.title()))


def resolve_first(last_norm: str, age: int) -> tuple[str | None, str | None, str]:
    """(first_name, resolved_last_name, status). Exact, then scoped fuzzy (unique lev<=1 within the
    Arizona same-age slice). The RESOLVED last name (the psych sheet's authoritative spelling) is
    returned so the hash matches what a parent actually searches -- 'Rossetti', not the email's
    'Rosetti'."""
    if (last_norm, age) in first_by_key:
        return first_by_key[(last_norm, age)], last_norm, "exact"
    near = [(cand_last, cand_first) for cand_last, cand_first in az_by_age.get(age, [])
            if levenshtein(last_norm, cand_last) <= 1]
    if len(near) == 1:
        cand_last, cand_first = near[0]
        return cand_first, cand_last, f"fuzzy -> {cand_last.title()}, {cand_first}"
    if len(near) > 1:
        return None, None, f"AMBIGUOUS ({len(near)} candidates: {[c[0] for c in near]}) -- left unmatchable"
    return None, None, "no candidate in AZ same-age slice -- left unmatchable"


# Arizona Relay A entry time per relay event, from the psych rows "A <seed>Arizona<rank>".
entry_by_event: dict[int, str] = {}
cur = None
for line in txt.splitlines():
    s = line.strip()
    h = re.match(r"(?:#|Event)\s*(\d+)\s+(.+)$", s)
    if h:
        cur = int(h.group(1))
        continue
    r = re.match(r"^A\s+(\d+:\d{2}\.\d{2})Arizona\d+$", s)
    if r and cur is not None:
        entry_by_event.setdefault(cur, r.group(1))

entries = []
print("Name resolution (email -> psych sheet):")
for event_number, (fallback_name, legs) in ROSTER.items():
    swimmers = []
    for leg, last, age, email_first in legs:
        last_norm = normalize_name_token(last)
        if email_first:
            first, resolved_last, status = email_first, last_norm, "email-provided"
        else:
            first, resolved_last, status = resolve_first(last_norm, age)
        if first:
            hashes = sorted(relay_hashes_for_swimmer(SALT, f"{resolved_last}, {first}"))
            resolved = f"{resolved_last.title()}, {first}"
        else:
            hashes = []
            resolved = f"{last}({age})"
        flag = "" if hashes else "   <<< UNRESOLVED"
        print(f"  #{event_number:>3} leg {leg}: {last}({age}) -> {resolved:24} [{status}]{flag}")
        swimmers.append({"hashes": hashes, "leg": leg})
    entry = {
        "event_number": event_number,
        "event_name": normalize_event_header_name(fallback_name),
        "relay_label": "Relay A",
        "entry_time": entry_by_event.get(event_number, ""),
        "page": 1,
        "swimmers": swimmers,
    }
    if event_number in PENDING_EVENTS:
        entry["lineup_pending"] = True
    entries.append(entry)

doc = {
    "id": "az-2026-wzag-relays",
    "label": "Include Arizona relay lineup",
    "club": "AZ",
    "meet_ids": ["2026-wzag-championships-boise"],
    "source_label": "Arizona relay lineup add-on",
    "salt": SALT,
    "notes": [
        "Roster names are stored as salted hashes only; the website should not display relay swimmer names.",
        "Relay lineups can change; families should confirm assignments with their coach.",
        "Relays flagged lineup_pending include a swimmer who withdrew after entries; the coach's replacement was not yet published.",
        "Names were resolved against the psych sheet (exact, then a unique fuzzy match within the Arizona same-age slice, e.g. Rosetti -> Rossetti). A swimmer absent from the psych sheet (a withdrawal) is recorded with no matchable hash.",
    ],
    "entries": entries,
}
OUT.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
unresolved = sum(1 for e in entries for s in e["swimmers"] if not s["hashes"])
print(f"\nwrote {OUT}")
print(f"events: {len(entries)}  unresolved legs (no matchable hash): {unresolved}")
