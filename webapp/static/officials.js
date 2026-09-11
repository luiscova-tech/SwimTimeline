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
const sessionsBody = document.querySelector("#sessionsBody");
const meetNameEl = document.querySelector("#meetName");
const summaryEl = document.querySelector("#summary");
const downloadAll = document.querySelector("#downloadAll");

loadHostedMeets();

loadMeetButton.addEventListener("click", () => {
  const meetId = meetSelect.value;
  if (!meetId) {
    setStatus("Choose a hosted meet, or upload a Session Report below.", "error");
    return;
  }
  loadSessions({ meet_id: meetId });
});

loadUploadButton.addEventListener("click", () => {
  const file = timelineInput.files && timelineInput.files[0];
  if (!file) {
    setStatus("Choose a Session Report PDF to upload.", "error");
    return;
  }
  const body = new FormData();
  body.append("timeline_pdf", file);
  loadSessions(body);
});

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
  // came from -- so every download link below is built off the same query base.
  const query = payload.meet_id
    ? `meet_id=${encodeURIComponent(payload.meet_id)}`
    : `token=${encodeURIComponent(payload.token)}`;

  meetNameEl.textContent = payload.meet_name || "Swim Meet";
  const totalEvents = sessions.reduce((sum, session) => sum + (session.event_count || 0), 0);
  summaryEl.textContent =
    `${sessions.length} session${sessions.length === 1 ? "" : "s"} · ` +
    `${totalEvents} events · one 2″×3″ card per session`;
  downloadAll.setAttribute("href", `/api/officials/badges?${query}`);

  sessionsBody.innerHTML = sessions.map((session) => sessionRow(session, query)).join("");
  resultEl.classList.remove("hidden");
}

function sessionRow(session, query) {
  const ageNote = session.age_qualifier
    ? escapeHtml(session.age_qualifier)
    : '<span class="muted">mixed ages</span>';
  const href = `/api/officials/badges?${query}&session=${encodeURIComponent(session.session_number)}`;
  return `
    <tr>
      <td data-col="session" data-label="Session">#${escapeHtml(session.session_number)} ${escapeHtml(session.session_name)}<br>${ageNote}</td>
      <td data-col="sdate" data-label="Day">${escapeHtml(session.date_label)}</td>
      <td data-col="swindow" data-label="Start - Est. finish">${escapeHtml(session.start_label)} &ndash; ${escapeHtml(session.finish_label)}</td>
      <td data-col="events" data-label="Events">${escapeHtml(session.event_count)}</td>
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
