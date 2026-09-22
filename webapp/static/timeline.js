// Bookmarkable, read-only "Next up" live view for a hosted Current Meet -- reads meet_id and
// swimmer_b64 straight from its own URL (see app.js's buildTimelineUrl) and re-analyzes the
// meet's current documents fresh on every load, the same stateless pattern /subscribe.ics uses.
// No push, no polling: reopen or reload the page to pick up a newly posted heat sheet.

const statusEl = document.querySelector("#timelineStatus");
const resultEl = document.querySelector("#result");
const meetNameEl = document.querySelector("#timelineMeetName");
const summaryEl = document.querySelector("#timelineSummary");
const warningsEl = document.querySelector("#warnings");
const nextUpEl = document.querySelector("#nextUp");
const eventsBody = document.querySelector("#eventsBody");
const seedHeaderEl = document.querySelector("#seedHeader");

function setStatus(message, isError = false) {
  if (!message) {
    statusEl.classList.add("hidden");
    statusEl.textContent = "";
    return;
  }
  statusEl.classList.remove("hidden");
  statusEl.classList.toggle("status-error", isError);
  statusEl.textContent = message;
}

function render(payload) {
  document.title = `${payload.swimmer} — SwimTimeline Live View`;
  meetNameEl.textContent = payload.meet?.name || "SwimTimeline";
  summaryEl.textContent = `Live view for ${payload.swimmer}`;
  renderWarnings(payload, warningsEl);
  renderResultsTable(payload, { tbody: eventsBody, seedHeaderEl });
  startNextUpTicker(payload, nextUpEl);
  resultEl.classList.remove("hidden");
}

async function loadTimeline() {
  const params = new URLSearchParams(window.location.search);
  const hasSwimmer = params.get("swimmer_b64") || params.get("swimmer");
  if (!params.get("meet_id") || !hasSwimmer) {
    setStatus(
      "This link is missing its meet or swimmer information. Open “Open live view” again from SwimTimeline's results page.",
      true,
    );
    return;
  }
  setStatus("Loading the latest schedule...");
  try {
    const response = await fetch(`/api/timeline?${params.toString()}`);
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || "This meet's live view could not be loaded.");
    }
    render(payload);
    setStatus("");
  } catch (error) {
    resultEl.classList.add("hidden");
    setStatus(error.message, true);
  }
}

loadTimeline();
