const form = document.querySelector("#uploadForm");
const statusEl = document.querySelector("#status");
const resultEl = document.querySelector("#result");
const meetNameEl = document.querySelector("#meetName");
const summaryEl = document.querySelector("#summary");
const downloadsEl = document.querySelector("#downloads");
const warningsEl = document.querySelector("#warnings");
const eventsBody = document.querySelector("#eventsBody");
const resultStatsEl = document.querySelector("#resultStats");
const currentMeetList = document.querySelector("#currentMeetList");
const publishCurrentBtn = document.querySelector("#publishCurrent");
let lastPayload = null;

loadCurrentMeets();

publishCurrentBtn.addEventListener("click", publishCurrentMeet);

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  statusEl.textContent = "Processing PDFs...";
  resultEl.classList.add("hidden");

  const data = new FormData(form);
  if (!data.getAll("modes").length) {
    statusEl.textContent = "Select at least one calendar file.";
    return;
  }

  try {
    const response = await fetch("/api/analyze", {
      method: "POST",
      body: data,
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Upload failed.");
    }
    renderResult(payload);
    statusEl.textContent = "Ready";
  } catch (error) {
    statusEl.textContent = error.message;
  }
});

async function loadCurrentMeets() {
  try {
    const response = await fetch("/api/current-meets");
    const payload = await response.json();
    renderCurrentMeets(payload.current_meets || []);
  } catch (error) {
    currentMeetList.innerHTML = `<div class="empty-state">Current meets could not be loaded.</div>`;
  }
}

function renderCurrentMeets(meets) {
  currentMeetList.innerHTML = "";
  if (!meets.length) {
    currentMeetList.innerHTML = `<div class="empty-state">No hosted meets yet.</div>`;
    return;
  }
  for (const meet of meets) {
    const card = document.createElement("article");
    card.className = "meet-card";
    const docs = (meet.documents || []).map((doc) => `<span>${escapeHtml(doc)}</span>`).join("");
    card.innerHTML = `
      <div>
        <div class="meet-title-row">
          <h3>${escapeHtml(meet.name)}</h3>
          <span class="status-pill">${escapeHtml(meet.status || "ready")}</span>
        </div>
        <p class="muted">${escapeHtml(meet.dates || "")}</p>
        <div class="doc-tags">${docs}</div>
      </div>
      <button class="primary" type="button">Use Meet</button>
    `;
    card.querySelector("button").addEventListener("click", () => analyzeCurrentMeet(meet));
    currentMeetList.appendChild(card);
  }
}

async function analyzeCurrentMeet(meet) {
  const swimmerName = form.elements.swimmer_name.value.trim();
  const state = form.elements.state.value.trim() || meet.state || "AZ";
  const modes = new FormData(form).getAll("modes");
  if (!swimmerName) {
    statusEl.textContent = "Swimmer name is required.";
    return;
  }
  if (!modes.length) {
    statusEl.textContent = "Select at least one calendar file.";
    return;
  }
  statusEl.textContent = `Processing ${meet.short_name || meet.name}...`;
  resultEl.classList.add("hidden");
  try {
    const response = await fetch("/api/analyze-current", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        meet_id: meet.id,
        swimmer_name: swimmerName,
        state,
        modes,
      }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Current meet analysis failed.");
    }
    renderResult(payload);
    statusEl.textContent = "Ready";
  } catch (error) {
    statusEl.textContent = error.message;
  }
}

function renderResult(payload) {
  lastPayload = payload;
  meetNameEl.textContent = payload.meet.name;
  summaryEl.textContent = `${payload.verified_event_count} verified individual events and ${payload.verified_relay_count || 0} verified relays for ${payload.swimmer}`;
  downloadsEl.innerHTML = "";
  warningsEl.innerHTML = "";
  eventsBody.innerHTML = "";
  resultStatsEl.innerHTML = "";
  publishCurrentBtn.disabled = false;
  publishCurrentBtn.classList.toggle("hidden", !payload.can_publish_current || !payload.run_id);
  const calendarCount = ["daily_ics", "weekend_ics", "detailed_ics"].filter((key) => payload.downloads[key]).length;
  resultStatsEl.innerHTML = `
    <div><strong>${payload.verified_event_count}</strong><span>Individual events</span></div>
    <div><strong>${payload.verified_relay_count || 0}</strong><span>Relays</span></div>
    <div><strong>${calendarCount}</strong><span>Calendar files</span></div>
  `;

  const downloadLabels = {
    daily_ics: "Daily ICS",
    weekend_ics: "Whole Meet ICS",
    detailed_ics: "Swim-by-Swim ICS",
    audit: "Audit",
  };
  for (const [key, label] of Object.entries(downloadLabels)) {
    if (payload.downloads[key]) {
      const link = document.createElement("a");
      link.href = payload.downloads[key];
      link.textContent = label;
      if (key === "audit") {
        link.className = "secondary";
      }
      downloadsEl.appendChild(link);
    }
  }

  for (const warning of payload.warnings || []) {
    const item = document.createElement("div");
    item.className = "warning";
    item.textContent = warning;
    warningsEl.appendChild(item);
  }

  for (const swim of payload.items || payload.events) {
    const row = document.createElement("tr");
    const seedCell = swim.type === "relay"
      ? `${escapeHtml(swim.seed_time)}<br>${escapeHtml(swim.relay_label || "Relay")}, leg ${escapeHtml(swim.leg || "")}`
      : `${escapeHtml(swim.seed_time)}<br>seed place ${swim.seed_place}`;
    const sourceCell = swim.type === "relay"
      ? `page ${swim.page}<br>relay document`
      : `page ${swim.page}<br>${escapeHtml(swim.column)} column`;
    row.innerHTML = `
      <td>${escapeHtml(swim.day)}</td>
      <td><strong>#${swim.event_number}${swim.type === "relay" ? " Relay" : ""}</strong>${escapeHtml(swim.event_name)}</td>
      <td>${seedCell}</td>
      <td>${escapeHtml(swim.window)}</td>
      <td>${escapeHtml(swim.benchmarks.usa || "")}<br>${escapeHtml(swim.benchmarks.lsc || "")}${advancedLine(swim)}</td>
      <td>${sourceCell}</td>
    `;
    eventsBody.appendChild(row);
  }

  resultEl.classList.remove("hidden");
}

async function publishCurrentMeet() {
  if (!lastPayload?.run_id) {
    statusEl.textContent = "No uploaded meet is ready to save.";
    return;
  }
  publishCurrentBtn.disabled = true;
  statusEl.textContent = "Saving to Current Meets...";
  try {
    const response = await fetch("/api/publish-current", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: lastPayload.run_id }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Current meet save failed.");
    }
    publishCurrentBtn.classList.add("hidden");
    lastPayload.can_publish_current = false;
    await loadCurrentMeets();
    statusEl.textContent = payload.already_saved ? "Already saved to Current Meets" : "Saved to Current Meets";
  } catch (error) {
    statusEl.textContent = error.message;
    publishCurrentBtn.disabled = false;
  }
}

function advancedLine(swim) {
  if (!swim.benchmarks.advanced) return "";
  return `<br>${escapeHtml(swim.benchmarks.advanced)}`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
