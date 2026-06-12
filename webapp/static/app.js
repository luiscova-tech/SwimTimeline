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
const featuredMeetEl = document.querySelector("#featuredMeet");
const pastMeetsEl = document.querySelector("#pastMeets");
const pastMeetList = document.querySelector("#pastMeetList");
const publishCurrentBtn = document.querySelector("#publishCurrent");
const swimmerList = document.querySelector("#swimmerList");
const addSwimmerBtn = document.querySelector("#addSwimmer");
const downloadDock = document.querySelector("#downloadDock");
const downloadDockMessage = document.querySelector("#downloadDockMessage");
const downloadDockPrimary = document.querySelector("#downloadDockPrimary");
const jumpDownloadsBtn = document.querySelector("#jumpDownloads");
let lastPayload = null;

loadCurrentMeets();
updateRemoveButtons();

publishCurrentBtn.addEventListener("click", publishCurrentMeet);
addSwimmerBtn.addEventListener("click", () => addSwimmerRow());
downloadDockPrimary.addEventListener("click", () => {
  downloadDock.classList.add("hidden");
});
jumpDownloadsBtn.addEventListener("click", () => {
  downloadsEl.scrollIntoView({ behavior: "smooth", block: "start" });
  downloadDock.classList.add("hidden");
});
swimmerList.addEventListener("click", (event) => {
  if (event.target.classList.contains("remove-swimmer")) {
    event.target.closest(".swimmer-row").remove();
    updateRemoveButtons();
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  setStatus("Creating calendar files from uploaded PDFs...", "busy");
  resultEl.classList.add("hidden");
  hideDownloadDock();

  const data = new FormData(form);
  data.set("combine_family", form.elements.combine_family.checked ? "1" : "0");
  data.set("estimate_heat_lanes", form.elements.estimate_heat_lanes.checked ? "1" : "0");
  if (!getSwimmerNames().length) {
    setStatus("At least one swimmer name is required.", "error");
    return;
  }
  if (!data.getAll("modes").length) {
    setStatus("Select at least one calendar file.", "error");
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
    setStatus("Calendar files are ready.", "success");
  } catch (error) {
    setStatus(error.message, "error");
  }
});

async function loadCurrentMeets() {
  try {
    const response = await fetch("/api/current-meets");
    const payload = await response.json();
    renderCurrentMeets(payload.current_meets || [], payload.past_meets || []);
  } catch (error) {
    currentMeetList.innerHTML = `<div class="empty-state">Current meets could not be loaded.</div>`;
  }
}

function renderCurrentMeets(meets, pastMeets = []) {
  featuredMeetEl.innerHTML = "";
  currentMeetList.innerHTML = "";
  pastMeetList.innerHTML = "";
  const featuredMeet = meets.find((meet) => meet.is_featured);
  const regularMeets = meets.filter((meet) => meet !== featuredMeet);
  if (featuredMeet) {
    featuredMeetEl.appendChild(buildMeetCard(featuredMeet, { featured: true }));
  }
  if (!meets.length) {
    currentMeetList.innerHTML = `<div class="empty-state">No hosted meets yet.</div>`;
  }
  for (const meet of regularMeets) {
    currentMeetList.appendChild(buildMeetCard(meet));
  }
  if (featuredMeet && !regularMeets.length) {
    currentMeetList.classList.add("hidden");
  } else {
    currentMeetList.classList.remove("hidden");
  }
  pastMeetsEl.classList.toggle("hidden", !pastMeets.length);
  for (const meet of pastMeets) {
    pastMeetList.appendChild(buildMeetCard(meet, { past: true }));
  }
}

function buildMeetCard(meet, options = {}) {
  const card = document.createElement("article");
  card.className = options.featured ? "meet-card featured-meet-card" : "meet-card";
  if (options.past) {
    card.classList.add("past-meet-card");
  }
  const docs = (meet.documents || []).map((doc) => `<span>${escapeHtml(doc)}</span>`).join("");
  const featuredMeta = options.featured
    ? `<div class="featured-meta">
        <span>${escapeHtml(meet.featured_label || "Featured current meet")}</span>
        ${meet.featured_until_label ? `<span>Through ${escapeHtml(meet.featured_until_label)}</span>` : ""}
      </div>`
    : "";
  const note = options.featured && meet.featured_note ? `<p class="meet-note">${escapeHtml(meet.featured_note)}</p>` : "";
  card.innerHTML = `
    <div class="meet-card-main">
      ${featuredMeta}
      <div class="meet-title-row">
        <h3>${escapeHtml(meet.name)}</h3>
        <span class="status-pill">${escapeHtml(statusLabel(meet.status))}</span>
      </div>
      <p class="muted">${escapeHtml(meet.dates || "")}</p>
      ${note}
      <div class="meet-facts">
        ${meet.state ? `<span>${escapeHtml(meet.state)}</span>` : ""}
        ${meet.has_relay ? "<span>Relay doc</span>" : ""}
      </div>
      <div class="doc-tags">${docs}</div>
    </div>
    <button class="primary" type="button">${options.featured ? "Use featured meet" : "Use this meet"}</button>
  `;
  card.querySelector("button").addEventListener("click", () => analyzeCurrentMeet(meet));
  return card;
}

function statusLabel(status) {
  return String(status || "ready").replace("-", " ");
}

async function analyzeCurrentMeet(meet) {
  const swimmerNames = getSwimmerNames();
  const state = form.elements.state.value.trim() || meet.state || "";
  const modes = new FormData(form).getAll("modes");
  if (!swimmerNames.length) {
    setStatus("At least one swimmer name is required.", "error");
    return;
  }
  if (!modes.length) {
    setStatus("Select at least one calendar file.", "error");
    return;
  }
  setStatus(`Creating calendar files for ${meet.short_name || meet.name}...`, "busy");
  resultEl.classList.add("hidden");
  hideDownloadDock();
  try {
    const response = await fetch("/api/analyze-current", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        meet_id: meet.id,
        swimmer_names: swimmerNames,
        state,
        modes,
        combine_family: form.elements.combine_family.checked,
        estimate_heat_lanes: form.elements.estimate_heat_lanes.checked,
      }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "Current meet analysis failed.");
    }
    renderResult(payload);
    setStatus("Calendar files are ready.", "success");
  } catch (error) {
    setStatus(error.message, "error");
  }
}

function renderResult(payload) {
  lastPayload = payload;
  meetNameEl.textContent = payload.meet.name;
  const swimmerCount = payload.family ? (payload.swimmers || []).length : 1;
  summaryEl.textContent = payload.family
    ? `${payload.verified_event_count} verified individual events and ${payload.verified_relay_count || 0} verified relays for ${swimmerCount} swimmers`
    : `${payload.verified_event_count} verified individual events and ${payload.verified_relay_count || 0} verified relays for ${payload.swimmer}`;
  downloadsEl.innerHTML = "";
  warningsEl.innerHTML = "";
  eventsBody.innerHTML = "";
  resultStatsEl.innerHTML = "";
  publishCurrentBtn.disabled = false;
  publishCurrentBtn.classList.toggle("hidden", !payload.can_publish_current || !payload.run_id);
  const calendarCount = ["daily_ics", "weekend_ics", "detailed_ics"].filter((key) => payload.downloads[key]).length;
  const familyCalendarCount = Object.keys(payload.downloads || {}).filter((key) => key.endsWith("_ics")).length;
  const individualCalendarCount = (payload.swimmers || []).reduce(
    (count, swimmer) => count + Object.keys(swimmer.downloads || {}).filter((key) => key.endsWith("_ics")).length,
    0,
  );
  const resultCalendarCount = payload.family ? familyCalendarCount || individualCalendarCount : calendarCount;
  const resultCalendarLabel = payload.family
    ? (familyCalendarCount ? "Combined calendars" : "Individual calendars")
    : "Calendar files";
  resultStatsEl.innerHTML = `
    <div><strong>${swimmerCount}</strong><span>Swimmers</span></div>
    <div><strong>${payload.verified_event_count}</strong><span>Individual events</span></div>
    <div><strong>${payload.verified_relay_count || 0}</strong><span>Relays</span></div>
    <div><strong>${resultCalendarCount}</strong><span>${resultCalendarLabel}</span></div>
  `;

  const downloadLabels = {
    family_daily_ics: "Download Family Daily Calendar",
    family_weekend_ics: "Download Family Whole Meet Calendar",
    family_detailed_ics: "Download Family Swim-by-Swim Calendar",
    daily_ics: "Download Daily Calendar",
    weekend_ics: "Download Whole Meet Calendar",
    detailed_ics: "Download Swim-by-Swim Calendar",
    audit: "Download Audit",
  };
  const primaryLinks = [];
  for (const [key, label] of Object.entries(downloadLabels)) {
    if (payload.downloads[key]) {
      primaryLinks.push({ href: payload.downloads[key], label, secondary: key === "audit" });
    }
  }
  appendDownloadGroup(payload.family ? "Combined family calendars" : "Calendar files", primaryLinks);
  renderIndividualDownloads(payload);

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
      : seedDetails(swim);
    const sourceCell = swim.type === "relay"
      ? `page ${swim.page}<br>relay document`
      : `page ${swim.page}<br>${escapeHtml(swim.source_document || "entry sheet")}<br>${escapeHtml(swim.column)} column`;
    row.innerHTML = `
      <td>${escapeHtml(swim.day)}</td>
      <td>${swimmerChip(swim, payload)}<strong>#${swim.event_number}${swim.type === "relay" ? " Relay" : ""}</strong>${escapeHtml(swim.event_name)}<br>${escapeHtml(swim.event_format || "")}</td>
      <td>${seedCell}</td>
      <td>${escapeHtml(swim.window)}</td>
      <td>${escapeHtml(swim.benchmarks.usa || "")}<br>${escapeHtml(swim.benchmarks.lsc || "")}${advancedLine(swim)}</td>
      <td>${sourceCell}</td>
    `;
    eventsBody.appendChild(row);
  }

  resultEl.classList.remove("hidden");
  revealResultDownloads(payload);
}

function renderIndividualDownloads(payload) {
  if (!payload.family) return;
  const individualLinks = [];
  for (const swimmer of payload.swimmers || []) {
    const links = swimmer.downloads || {};
    const ordered = [
      ["daily_ics", `${swimmer.name} Daily`],
      ["weekend_ics", `${swimmer.name} Whole Meet`],
      ["detailed_ics", `${swimmer.name} Swim-by-Swim`],
      ["audit", `${swimmer.name} Audit`],
    ];
    for (const [key, label] of ordered) {
      if (!links[key]) continue;
      individualLinks.push({ href: links[key], label, secondary: true });
    }
  }
  appendDownloadGroup("Individual swimmer files", individualLinks);
}

function appendDownloadGroup(title, links) {
  if (!links.length) return;
  const group = document.createElement("section");
  group.className = "download-group";
  const heading = document.createElement("h3");
  heading.textContent = title;
  group.appendChild(heading);
  const list = document.createElement("div");
  list.className = "download-links";
  for (const item of links) {
    const link = document.createElement("a");
    link.href = item.href;
    link.textContent = item.label;
    if (item.secondary) {
      link.className = "secondary";
    }
    list.appendChild(link);
  }
  group.appendChild(list);
  downloadsEl.appendChild(group);
}

async function publishCurrentMeet() {
  if (!lastPayload?.run_id) {
    setStatus("No uploaded meet is ready to save.", "error");
    return;
  }
  publishCurrentBtn.disabled = true;
  setStatus("Saving to Current Meets...", "busy");
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
    setStatus(payload.already_saved ? "Already saved to Current Meets." : "Saved to Current Meets.", "success");
  } catch (error) {
    setStatus(error.message, "error");
    publishCurrentBtn.disabled = false;
  }
}

function advancedLine(swim) {
  if (!swim.benchmarks.advanced) return "";
  return `<br>${escapeHtml(swim.benchmarks.advanced)}`;
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

function swimmerChip(swim, payload) {
  if (!payload.family || !swim.swimmer) return "";
  return `<span class="swimmer-chip">${escapeHtml(swim.swimmer)}</span>`;
}

function getSwimmerNames() {
  return Array.from(form.querySelectorAll('input[name="swimmer_names"]'))
    .map((input) => input.value.trim())
    .filter(Boolean);
}

function addSwimmerRow(value = "") {
  const row = document.createElement("div");
  row.className = "swimmer-row";
  row.innerHTML = `
    <input name="swimmer_names" placeholder="First Last" autocomplete="off" value="${escapeHtml(value)}">
    <button class="icon-button remove-swimmer" type="button" aria-label="Remove swimmer">&times;</button>
  `;
  swimmerList.appendChild(row);
  updateRemoveButtons();
  row.querySelector("input").focus();
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
}

function hideDownloadDock() {
  downloadDock.classList.add("hidden");
  downloadDockPrimary.removeAttribute("href");
}

function revealResultDownloads(payload) {
  updateReadyDock(payload);
  if (window.matchMedia("(max-width: 860px)").matches) {
    requestAnimationFrame(() => {
      resultEl.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    setTimeout(() => {
      resultEl.scrollIntoView({ behavior: "smooth", block: "start" });
    }, 120);
    downloadDock.classList.remove("hidden");
  }
}

function updateReadyDock(payload) {
  const primary = primaryCalendarDownload(payload);
  if (!primary) {
    downloadDockPrimary.classList.add("hidden");
    downloadDockMessage.textContent = "Calendar files are ready.";
    return;
  }
  downloadDockPrimary.href = primary.href;
  downloadDockPrimary.textContent = primary.label;
  downloadDockPrimary.classList.remove("hidden");
  downloadDockMessage.textContent = primary.message;
}

function primaryCalendarDownload(payload) {
  const downloads = payload.downloads || {};
  if (downloads.family_daily_ics) {
    return {
      href: downloads.family_daily_ics,
      label: "Download Daily Calendar",
      message: "Your family daily calendar is ready to download.",
    };
  }
  if (downloads.daily_ics) {
    return {
      href: downloads.daily_ics,
      label: "Download Daily Calendar",
      message: "Your daily calendar is ready to download.",
    };
  }
  for (const swimmer of payload.swimmers || []) {
    const href = swimmer.downloads?.daily_ics;
    if (href) {
      return {
        href,
        label: `Download ${swimmer.name} Daily`,
        message: "A daily calendar is ready to download.",
      };
    }
  }
  const fallback = Object.entries(downloads).find(([key]) => key.endsWith("_ics"));
  if (!fallback) return null;
  return {
    href: fallback[1],
    label: "Download Calendar",
    message: "A calendar file is ready to download.",
  };
}

function updateRemoveButtons() {
  const rows = swimmerList.querySelectorAll(".swimmer-row");
  rows.forEach((row) => {
    row.querySelector(".remove-swimmer").classList.toggle("hidden", rows.length === 1);
    row.querySelector("input").required = rows.length === 1;
  });
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
