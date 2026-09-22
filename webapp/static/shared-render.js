// Rendering code shared between index.html's app.js (the interactive form/upload flow) and
// timeline.js (the bookmarkable read-only "Next up" live view for a hosted Current Meet). Loaded
// as a plain classic script, before either app.js or timeline.js, on whichever page needs it --
// there is no bundler in this project, so these are plain globals, same as every other script here.

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function seedDetails(swim) {
  const details = [`${escapeHtml(swim.seed_time)}`];
  if (swim.heat && swim.lane) {
    const label = swim.heat_is_estimated ? "estimated heat" : "heat";
    details.push(`${label} ${escapeHtml(swim.heat)}, lane ${escapeHtml(swim.lane)}`);
  } else {
    details.push(`seed place ${escapeHtml(swim.seed_place)}`);
  }
  return details.join("<br>");
}

// Never present an estimated heat/lane as settled fact -- seedDetails() above already draws that
// line for an individual swim; this just adds the relay-vs-individual branch on top of it, reused
// identically by the results table and the "Next up" card so neither can drift from the other.
function swimSeedCell(swim) {
  return swim.type === "relay"
    ? (swim.is_team_entry
        ? `<em>Tentative</em><br>team entered — confirm with coach`
        : `${escapeHtml(swim.seed_time)}<br>${escapeHtml(swim.relay_label || "Relay")}, leg ${escapeHtml(swim.leg || "")}`)
    : seedDetails(swim);
}

function swimSourceCell(swim) {
  return swim.type === "relay"
    ? `page ${swim.page}<br>${escapeHtml(swim.source_document || "relay document")}`
    : `page ${swim.page}<br>${escapeHtml(swim.source_document || "entry sheet")}${swim.column ? `<br>${escapeHtml(swim.column)} column` : ""}`;
}

function swimmerChip(swim, payload) {
  if (!payload.family || !swim.swimmer) return "";
  return `<span class="swimmer-chip">${escapeHtml(swim.swimmer)}</span>`;
}

// Matches a motivational-tier token (TIER_ORDER in standards.py, B lowest to AAAA highest) as a
// whole word, so it catches "AAA" in "AA; next AAA 39.09", the standalone "AAAA" in "USA-S ...:
// AAAA", both "B"s in "below B; B target ...", and the achieved-tier "AAAA" inside "Beyond AAAA:
// next ...". \b on both ends is what keeps this correct regardless of alternation order here --
// e.g. matching just "A" inside "AAAA" fails its own trailing \b (the next character is still a
// word character), forcing the engine to backtrack to the full "AAAA" -- so this never needs the
// longest-first ordering that a plain (non-anchored) alternation would.
const TIER_TOKEN_RE = /\b(AAAA|AAA|AA|BB|B|A)\b/g;

// Wraps each tier token in a span colored by webapp/static/styles.css's .tier-* rules. Runs BEFORE
// the source-link substitution below (on plain escaped text, no markup yet), so a label can never
// collide with a token span -- confirmed against every real fixture in this repo that no
// usa/lsc/sectional/national/advanced source label ever contains a bare tier word itself.
function colorizeTiers(escapedText) {
  return escapedText.replace(TIER_TOKEN_RE, (token) => `<span class="tier-${token}">${token}</span>`);
}

// Turn each standard's label into a checkable link to its source document. The backend supplies,
// per benchmark line, a list of {label, url} where label is an exact substring of the line text;
// we escape everything, then swap the escaped label for an <a> so the number stays plain text and
// only the label (e.g. "USA-S 11-12 Girls LCM", "Four Corners...") becomes a link.
function linkifyBenchmark(text, sources) {
  let html = colorizeTiers(escapeHtml(text || ""));
  for (const source of sources || []) {
    if (!source.url || !source.label) continue;
    const escapedLabel = escapeHtml(source.label);
    const anchor = `<a href="${escapeHtml(source.url)}" target="_blank" rel="noopener noreferrer">${escapedLabel}</a>`;
    html = html.replace(escapedLabel, () => anchor); // function replacer: no $ special-casing
  }
  return html;
}

function benchmarkLine(text, swim, lineName) {
  const sources = (swim.benchmarks.sources || {})[lineName];
  return linkifyBenchmark(text, sources);
}

function lscLine(swim) {
  // An AIA-scored meet (see build_swim_events/aia_benchmarks in extract.py) leaves "lsc" empty
  // on purpose, so its single AIA line (in "usa") stands alone instead of gaining a meaningless
  // "<br>LSC: n/a" tail. Every other meet's "lsc" is always a real sentence, even its own "not
  // configured" gap message, so this is a no-op for them -- same pattern as sectionalNationalLines.
  return swim.benchmarks.lsc ? `<br>${benchmarkLine(swim.benchmarks.lsc, swim, "lsc")}` : "";
}

function sectionalNationalLines(swim) {
  let html = "";
  if (swim.benchmarks.sectional) {
    html += `<br>${benchmarkLine(swim.benchmarks.sectional, swim, "sectional")}`;
  }
  if (swim.benchmarks.national) {
    html += `<br>${benchmarkLine(swim.benchmarks.national, swim, "national")}`;
  }
  return html;
}

function confidenceLine(swim) {
  // The old "Beyond AAAA" advanced line is gone -- Sectional/National each already collapse to
  // their own single nearest-unmet-cut line (sectionalNationalLines above), so the advanced line
  // was a guaranteed verbatim duplicate of whichever of those was nearer. swim.benchmarks.advanced
  // is still computed server-side (it feeds the calendar .ics description text), just not shown
  // in this table anymore.
  return swim.benchmarks.confidence ? `<br>${escapeHtml(swim.benchmarks.confidence)}` : "";
}

function renderWarnings(payload, container) {
  container.innerHTML = "";
  for (const warning of payload.warnings || []) {
    const item = document.createElement("div");
    item.className = "warning";
    item.textContent = warning;
    container.appendChild(item);
  }
}

function seedLabelFor(items) {
  // "Seed Rank", not "Place": this app has no results-tracking at all, and seed_place (see
  // seedDetails() above) is the swimmer's rank by seed time ENTERING the event -- a pre-meet
  // concept, never a finish position. "Place" alone reads as a race result.
  return items.some((item) => item.heat && item.lane) ? "Seed / Heat-Lane" : "Seed / Seed Rank";
}

// Builds the results table rows into an explicit tbody/seedHeaderEl rather than reaching for
// module-scope globals, so both index.html's live form flow and the standalone timeline.html page
// can call it against their own DOM.
function renderResultsTable(payload, { tbody, seedHeaderEl }) {
  const items = payload.items || payload.events || [];
  const seedLabel = seedLabelFor(items);
  if (seedHeaderEl) {
    seedHeaderEl.textContent = seedLabel;
  }
  tbody.innerHTML = "";
  for (const swim of items) {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td data-col="day" data-label="Day">${escapeHtml(swim.day)}</td>
      <td data-col="event" data-label="Event">${swimmerChip(swim, payload)}<strong>#${swim.event_number}${swim.type === "relay" ? " Relay" : ""}</strong>${escapeHtml(swim.event_name)}<br>${escapeHtml(swim.event_format || "")}</td>
      <td data-col="seed" data-label="${escapeHtml(seedLabel)}">${swimSeedCell(swim)}</td>
      <td data-col="window" data-label="Est. Window">${escapeHtml(swim.window)}</td>
      <td data-col="benchmark" data-label="Benchmark">${benchmarkLine(swim.benchmarks.usa, swim, "usa")}${lscLine(swim)}${sectionalNationalLines(swim)}${confidenceLine(swim)}</td>
      <td data-col="source" data-label="Source">${swimSourceCell(swim)}</td>
    `;
    // start_at is the tz-aware sibling of sort_start (see summarize_swim/summarize_relay in
    // extract.py) -- safe to compare directly against the viewer's own Date.now(), regardless of
    // which timezone the venue or the viewer is in.
    if (swim.start_at) {
      row.dataset.startAt = swim.start_at;
      row.classList.toggle("swim-past", isPast(swim.start_at));
    }
    tbody.appendChild(row);
  }
}

function groupItemsBySwimmer(payload) {
  const items = payload.items || payload.events || [];
  const names = payload.family ? (payload.swimmers || []).map((swimmer) => swimmer.name) : [payload.swimmer];
  const bySwimmer = new Map(names.filter(Boolean).map((name) => [name, []]));
  for (const item of items) {
    const name = item.swimmer || payload.swimmer;
    if (!name) continue;
    if (!bySwimmer.has(name)) {
      bySwimmer.set(name, []);
    }
    bySwimmer.get(name).push(item);
  }
  return bySwimmer;
}

function isPast(startAtIso) {
  if (!startAtIso) return false;
  const target = new Date(startAtIso).getTime();
  return !Number.isNaN(target) && target <= Date.now();
}

// payload.items is already sorted ascending by (naive) sort_start server-side, and start_at is
// the same instant with a real offset attached, so a swimmer's own items stay in ascending order
// once filtered -- the first one that hasn't started yet is "next up".
function nextUpwardItem(items) {
  return items.find((item) => item.start_at && !isPast(item.start_at)) || null;
}

function formatCountdown(startAtIso) {
  const target = new Date(startAtIso).getTime();
  if (Number.isNaN(target)) return "";
  const diffMs = target - Date.now();
  if (diffMs <= 0) return "Underway or just finished";
  const minutes = Math.round(diffMs / 60000);
  if (minutes < 1) return "Next in under a minute";
  if (minutes < 60) return `Next in ~${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    const remainder = minutes % 60;
    return `Next in ~${hours} hr${hours === 1 ? "" : "s"}${remainder ? ` ${remainder} min` : ""}`;
  }
  const days = Math.floor(hours / 24);
  const remainderHours = hours % 24;
  return `Next in ~${days} day${days === 1 ? "" : "s"}${remainderHours ? ` ${remainderHours} hr` : ""}`;
}

function nextUpEventLabel(item) {
  return item.type === "relay" ? `Relay: ${escapeHtml(item.relay_label || item.event_name)}` : escapeHtml(item.event_name);
}

// One card per swimmer, stacked -- never merged, even when two swimmers' next events happen to be
// close together. Reuses swimSeedCell() so an estimated heat/lane reads exactly the same caveat
// here as it does in the results table below.
function renderNextUpCards(payload, container) {
  if (!container) return;
  container.innerHTML = "";
  const bySwimmer = groupItemsBySwimmer(payload);
  if (!bySwimmer.size) {
    container.classList.add("hidden");
    return;
  }
  container.classList.remove("hidden");
  const list = document.createElement("div");
  list.className = "next-up-list";
  for (const [name, items] of bySwimmer) {
    const card = document.createElement("article");
    card.className = "next-up-card";
    const next = items.length ? nextUpwardItem(items) : null;
    if (!items.length) {
      card.classList.add("next-up-card-done");
      card.innerHTML = `
        <div class="next-up-swimmer">${escapeHtml(name)}</div>
        <p class="next-up-done">No events found for ${escapeHtml(name)}.</p>
      `;
    } else if (!next) {
      card.classList.add("next-up-card-done");
      card.innerHTML = `
        <div class="next-up-swimmer">${escapeHtml(name)}</div>
        <p class="next-up-done">No more events today for ${escapeHtml(name)}.</p>
      `;
    } else {
      const startTime = new Date(next.start_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
      card.innerHTML = `
        <div class="next-up-swimmer">${escapeHtml(name)}</div>
        <div class="next-up-headline">
          <strong>#${next.event_number}${next.type === "relay" ? " Relay" : ""}</strong> ${nextUpEventLabel(next)}
        </div>
        <div class="next-up-countdown" data-start-at="${escapeHtml(next.start_at)}">${formatCountdown(next.start_at)}</div>
        <div class="next-up-meta">
          <span>Est. start ${escapeHtml(startTime)}</span>
          <div class="next-up-seed">${swimSeedCell(next)}</div>
        </div>
      `;
    }
    list.appendChild(card);
  }
  container.appendChild(list);
}

let nextUpTickerHandle = null;

// Re-derives "what's next" and refreshes the countdown every 30s off the viewer's own clock -- no
// backend polling, since the underlying schedule only changes on a manual reload. Also re-checks
// every table row already on the page so a swim that just started gets dimmed without a full
// table re-render. Clears any previous interval first, so re-analyzing on index.html without a
// page reload never stacks up multiple tickers.
function startNextUpTicker(payload, container) {
  if (nextUpTickerHandle) {
    clearInterval(nextUpTickerHandle);
  }
  const tick = () => {
    renderNextUpCards(payload, container);
    document.querySelectorAll("tr[data-start-at]").forEach((row) => {
      row.classList.toggle("swim-past", isPast(row.dataset.startAt));
    });
  };
  tick();
  nextUpTickerHandle = setInterval(tick, 30000);
}
