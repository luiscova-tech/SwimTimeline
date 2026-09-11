// Badge-card page (/officials). Deliberately its own file rather than sharing app.js: app.js is
// wired end-to-end for the family calendar flow -- swimmer-name rows, relay opt-ins, benchmark
// tables, the download dock -- and runs that wiring against elements this page does not have. The
// two pages share only the stylesheet and a couple of tiny helpers (setStatus/escapeHtml), which
// are cheaper to restate here than to untangle app.js into a shared module for.

const statusEl = document.querySelector("#status");
const resultEl = document.querySelector("#result");
const meetSelect = document.querySelector("#meetSelect");
const loadMeetButton = document.querySelector("#loadMeet");
const loadUploadButton = document.querySelector("#loadUpload");
const timelineInput = document.querySelector("#timelineInput");
const psychInput = document.querySelector("#psychInput");
const sessionsBody = document.querySelector("#sessionsBody");
const meetNameEl = document.querySelector("#meetName");
const summaryEl = document.querySelector("#summary");
const downloadAll = document.querySelector("#downloadAll");
const downloadSheet = document.querySelector("#downloadSheet");
const highlightFilterRow = document.querySelector("#highlightFilterRow");
const highlightedOnly = document.querySelector("#highlightedOnly");
const swimmerList = document.querySelector("#swimmerList");
const addSwimmerButton = document.querySelector("#addSwimmer");
const warningsEl = document.querySelector("#warnings");
const highlightSummaryEl = document.querySelector("#highlightSummary");

// The most recently loaded sessions response plus the query base its download links are built
// from, kept so the highlight filter can re-apply client-side without refetching.
let loaded = null;

loadHostedMeets();
updateRemoveButtons();

highlightedOnly.addEventListener("change", applyHighlightFilter);

loadMeetButton.addEventListener("click", () => {
  const meetId = meetSelect.value;
  if (!meetId) {
    setStatus("Choose a hosted meet, or upload a Session Report below.", "error");
    return;
  }
  loadSessions({ meet_id: meetId, swimmer_names: getSwimmerNames() });
});

loadUploadButton.addEventListener("click", () => {
  const file = timelineInput.files && timelineInput.files[0];
  if (!file) {
    setStatus("Choose a Session Report PDF to upload.", "error");
    return;
  }
  const body = new FormData();
  body.append("timeline_pdf", file);
  const psych = psychInput.files && psychInput.files[0];
  if (psych) {
    body.append("psych_pdf", psych);
  }
  // Same repeated "swimmer_names" field the family upload form posts, so the server's existing
  // swimmer_names_from_form reads it with no special-casing.
  for (const name of getSwimmerNames()) {
    body.append("swimmer_names", name);
  }
  loadSessions(body);
});

// Same shape as the family page's swimmer rows (see app.js addSwimmerRow/updateRemoveButtons),
// restated here because this page is deliberately its own script.
addSwimmerButton.addEventListener("click", () => addSwimmerRow());

swimmerList.addEventListener("click", (event) => {
  const button = event.target.closest(".remove-swimmer");
  if (!button) return;
  button.closest(".swimmer-row").remove();
  if (!swimmerList.querySelector(".swimmer-row")) addSwimmerRow();
  updateRemoveButtons();
});

function getSwimmerNames() {
  const seen = new Set();
  const names = [];
  for (const input of swimmerList.querySelectorAll('input[name="swimmer_names"]')) {
    const value = input.value.trim();
    // De-duplicated case-insensitively, matching the server's unique_swimmer_names.
    if (value && !seen.has(value.toLowerCase())) {
      seen.add(value.toLowerCase());
      names.push(value);
    }
  }
  return names;
}

function addSwimmerRow(value = "") {
  const row = document.createElement("div");
  row.className = "swimmer-row";
  row.innerHTML = `
    <input name="swimmer_names" placeholder="Last, First" autocomplete="off" value="${escapeHtml(value)}">
    <button class="icon-button remove-swimmer" type="button" aria-label="Remove swimmer">&times;</button>
  `;
  swimmerList.appendChild(row);
  updateRemoveButtons();
  row.querySelector("input").focus();
}

function updateRemoveButtons() {
  const rows = swimmerList.querySelectorAll(".swimmer-row");
  for (const row of rows) {
    row.querySelector(".remove-swimmer").classList.toggle("hidden", rows.length === 1);
  }
}

async function loadHostedMeets() {
  try {
    const response = await fetch("/api/officials/meets");
    const payload = await response.json();
    const current = payload.current_meets || [];
    const past = payload.past_meets || [];
    if (!current.length && !past.length) {
      meetSelect.innerHTML = '<option value="">No hosted meets have a timeline yet</option>';
      return;
    }
    const options = ['<option value="">Select a meet&hellip;</option>'];
    // Same current/past split the family-facing meet list uses, kept as two labelled groups so an
    // official can still reach a meet that has just finished.
    if (current.length) {
      options.push(`<optgroup label="Current meets">${current.map(meetOption).join("")}</optgroup>`);
    }
    if (past.length) {
      options.push(`<optgroup label="Past meets">${past.map(meetOption).join("")}</optgroup>`);
    }
    meetSelect.innerHTML = options.join("");
  } catch (error) {
    meetSelect.innerHTML = '<option value="">Could not load hosted meets</option>';
    setStatus(`Could not load hosted meets: ${error.message}`, "error");
  }
}

function meetOption(meet) {
  const dates = meet.dates ? ` (${meet.dates})` : "";
  return `<option value="${escapeHtml(meet.id)}">${escapeHtml(meet.name)}${escapeHtml(dates)}</option>`;
}

async function loadSessions(request) {
  const isUpload = request instanceof FormData;
  setStatus(isUpload ? "Reading your Session Report…" : "Reading the meet timeline…", "busy");
  resultEl.classList.add("hidden");
  try {
    const response = await fetch("/api/officials/sessions", {
      method: "POST",
      ...(isUpload ? { body: request } : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(request) }),
    });
    const payload = await response.json();
    if (!response.ok || payload.error) {
      throw new Error(payload.error || `Request failed (${response.status})`);
    }
    renderSessions(payload);
    setStatus("", "idle");
  } catch (error) {
    setStatus(error.message, "error");
  }
}

function renderSessions(payload) {
  const sessions = payload.sessions || [];
  if (!sessions.length) {
    setStatus("No sessions were found in that timeline.", "error");
    return;
  }
  // The badge endpoint takes either a hosted meet id or an upload token -- whichever this load
  // came from -- plus the same swimmer names, so a downloaded PDF is starred identically to what
  // the table below reports.
  const parts = [
    payload.meet_id
      ? `meet_id=${encodeURIComponent(payload.meet_id)}`
      : `token=${encodeURIComponent(payload.token)}`,
    ...(payload.swimmer_names || []).map((name) => `swimmer_names=${encodeURIComponent(name)}`),
  ];
  const query = parts.join("&");

  meetNameEl.textContent = payload.meet_name || "Swim Meet";
  const totalEvents = sessions.reduce((sum, session) => sum + (session.event_count || 0), 0);
  const starred = (payload.highlighted_events || []).length;
  summaryEl.textContent =
    `${sessions.length} session${sessions.length === 1 ? "" : "s"} · ` +
    `${totalEvents} events · one 2″×3″ card per session` +
    (starred ? ` · ${starred} starred event${starred === 1 ? "" : "s"}` : "");

  renderSwimmerFeedback(payload);
  sessionsBody.innerHTML = sessions.map((session) => sessionRow(session, query)).join("");

  // Keep the loaded payload so the filter toggle can re-apply without another round trip -- the
  // per-session highlighted_events the table needs is already in this response.
  loaded = { payload, query };
  const anySwimmers = (payload.swimmer_names || []).length > 0;
  const anyHighlightedSession = sessions.some(
    (session) => (session.highlighted_events || []).length > 0
  );
  // The toggle is meaningless with no swimmer names, and would silently empty the table if no
  // session matched at all, so it only appears when it can actually do something.
  const filterUsable = anySwimmers && anyHighlightedSession;
  highlightFilterRow.classList.toggle("hidden", !filterUsable);
  if (!filterUsable) {
    highlightedOnly.checked = false;
  }
  applyHighlightFilter();
  resultEl.classList.remove("hidden");
}

// Client-side only: hides table rows whose highlighted_events is empty, using the field already
// present in the sessions response -- no refetch, and no second notion of "has a highlight" that
// could disagree with the server's. The download links get highlighted_only=1 so the PDFs are
// filtered by the same rule server-side.
function applyHighlightFilter() {
  if (!loaded) return;
  const on = filterUsable() && highlightedOnly.checked;
  const rows = [...sessionsBody.querySelectorAll("tr")];
  let shown = 0;
  for (const row of rows) {
    const hasHighlight = row.dataset.highlighted === "1";
    const hide = on && !hasHighlight;
    row.hidden = hide;
    if (!hide) shown += 1;
  }
  const suffix = on ? `&highlighted_only=1` : "";
  downloadAll.setAttribute("href", `/api/officials/badges?${loaded.query}${suffix}`);
  downloadSheet.setAttribute("href", `/api/officials/badges?${loaded.query}&layout=sheet${suffix}`);
  if (on) {
    setStatus(
      `Showing ${shown} session${shown === 1 ? "" : "s"} with your swimmer(s). Downloads are filtered to match.`,
      "idle",
    );
  } else {
    setStatus("", "idle");
  }
}

function filterUsable() {
  return !highlightFilterRow.classList.contains("hidden");
}

function renderSwimmerFeedback(payload) {
  // Same per-warning markup the family page uses (app.js: div.warning inside #warnings).
  warningsEl.innerHTML = "";
  for (const warning of payload.swimmer_warnings || []) {
    const item = document.createElement("div");
    item.className = "warning";
    item.textContent = warning.ambiguous
      ? `${warning.message} (no events were highlighted for that name)`
      : warning.message;
    warningsEl.appendChild(item);
  }

  const matched = payload.matched_swimmers || [];
  if (!matched.length) {
    highlightSummaryEl.classList.add("hidden");
    highlightSummaryEl.innerHTML = "";
    return;
  }
  highlightSummaryEl.innerHTML =
    `<strong>Highlighted on the cards:</strong> ` +
    matched
      .map(
        (entry) =>
          `${escapeHtml(entry.name)} <span class="muted">(${entry.event_numbers
            .map((number) => `#${escapeHtml(number)}`)
            .join(", ")})</span>`
      )
      .join(" · ");
  highlightSummaryEl.classList.remove("hidden");
}

function sessionRow(session, query) {
  const ageNote = session.age_qualifier
    ? escapeHtml(session.age_qualifier)
    : '<span class="muted">mixed ages</span>';
  const href = `/api/officials/badges?${query}&session=${encodeURIComponent(session.session_number)}`;
  const starredEvents = session.highlighted_events || [];
  const starredCell = starredEvents.length
    ? `<span class="starred-count">★ ${starredEvents.map((n) => `#${escapeHtml(n)}`).join(", ")}</span>`
    : '<span class="muted">—</span>';
  return `
    <tr${starredEvents.length ? ' class="has-starred"' : ""} data-highlighted="${starredEvents.length ? "1" : "0"}">
      <td data-col="session" data-label="Session">#${escapeHtml(session.session_number)} ${escapeHtml(session.session_name)}<br>${ageNote}</td>
      <td data-col="sdate" data-label="Day">${escapeHtml(session.date_label)}</td>
      <td data-col="swindow" data-label="Start - Est. finish">${escapeHtml(session.start_label)} &ndash; ${escapeHtml(session.finish_label)}</td>
      <td data-col="events" data-label="Events">${escapeHtml(session.event_count)}</td>
      <td data-col="starred" data-label="Starred">${starredCell}</td>
      <td data-col="interval" data-label="Heat interval">${escapeHtml(session.heat_interval || "—")}</td>
      <td data-col="card" data-label="Card"><a class="session-card-link" href="${href}">Download card</a></td>
    </tr>
  `;
}

function setStatus(message, state = "idle") {
  statusEl.className = "status";
  statusEl.setAttribute("aria-busy", state === "busy" ? "true" : "false");
  if (!message) {
    statusEl.textContent = "";
    statusEl.classList.add("hidden");
    return;
  }
  statusEl.textContent = message;
  statusEl.classList.add(`status-${state}`);
  if (state === "error") {
    statusEl.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
