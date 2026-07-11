(function () {
  "use strict";

  const feedEl = document.getElementById("feed");
  const witnessBanner = document.getElementById("witness-banner");
  const continueRow = document.getElementById("continue-row");
  const continueBtn = document.getElementById("continue-btn");
  const modeLine = document.getElementById("mode-line");

  const counters = {
    forged: document.getElementById("c-forged"),
    killed: document.getElementById("c-killed"),
    survived: document.getElementById("c-survived"),
  };

  const rows = new Map();
  let probeReady = false;

  function setCounters(counts) {
    counters.forged.textContent = counts.forged;
    counters.killed.textContent = counts.killed;
    counters.survived.textContent = counts.survived;
  }

  function ensureRow(twinId) {
    let row = rows.get(twinId);
    if (row) return row;
    row = document.createElement("div");
    row.className = "twin-row status-pending";
    row.innerHTML = `
      <span class="twin-id">${twinId}</span>
      <span class="twin-desc">forging&hellip;</span>
      <span class="twin-family"></span>
      <span class="pill status-pending">pending</span>
    `;
    feedEl.appendChild(row);
    rows.set(twinId, row);
    return row;
  }

  function onTwinSpawned(ev) {
    const row = ensureRow(ev.twin_id);
    row.querySelector(".twin-desc").textContent = ev.description;
    row.querySelector(".twin-family").textContent = ev.bug_family || "";
  }

  const PILL_LABEL = { killed: "killed", survived: "survived", invalid_patch: "invalid" };

  function onTwinResult(ev) {
    const row = ensureRow(ev.twin_id);
    row.className = `twin-row status-${ev.status}`;
    const pill = row.querySelector(".pill");
    pill.className = `pill status-${ev.status}`;
    pill.textContent = PILL_LABEL[ev.status] || ev.status;
  }

  function onWitnessFound(ev) {
    const row = rows.get(ev.twin_id);
    if (row) {
      const desc = row.querySelector(".twin-desc");
      desc.textContent += `  \u2014 witness: ${ev.first_divergence}`;
    }
    witnessBanner.classList.add("show");
  }

  function onPipelineDone(ev) {
    setCounters(ev.counts);
    probeReady = !!ev.probe_twin_id;
    revealContinue();
  }

  function revealContinue() {
    continueRow.style.display = "flex";
    continueBtn.textContent = probeReady
      ? "Continue to Probe"
      : "No blind spot found this run \u2014 continue to Verdict";
    continueBtn.dataset.target = probeReady ? "/probe" : "/verdict";
  }

  function applyEvent(ev) {
    switch (ev.type) {
      case "twin_spawned":
        onTwinSpawned(ev);
        break;
      case "twin_result":
        onTwinResult(ev);
        break;
      case "witness_found":
        onWitnessFound(ev);
        break;
      case "pipeline_done":
        onPipelineDone(ev);
        break;
      default:
        break;
    }
  }

  async function bootstrap() {
    const params = new URLSearchParams(window.location.search);
    modeLine.textContent = params.get("rehearsal") === "1"
      ? "Replaying demo/rehearsal_twins/ through the harness + witness finder, live (no Gemini calls)."
      : "Generating adversarial twins with Gemini, then running them through your test suite live.";

    const statusRes = await fetch("/api/status");
    if (!statusRes.ok) return;
    const status = await statusRes.json();

    if (status.pipeline_status === "idle") {
      feedEl.innerHTML = '<p class="subtitle">No run in progress. Go back to <a href="/review">/review</a> and click Merge.</p>';
      return;
    }

    setCounters(status.counts);
    status.twins.forEach((t) => {
      onTwinSpawned({ twin_id: t.twin_id, description: t.description, bug_family: t.bug_family });
      onTwinResult({ twin_id: t.twin_id, status: t.status });
      if (t.has_witness) witnessBanner.classList.add("show");
    });
    if (status.pipeline_status === "done") {
      probeReady = status.probe_ready;
      revealContinue();
    }

    const source = new EventSource("/events");
    source.onmessage = (msg) => {
      const ev = JSON.parse(msg.data);
      applyEvent(ev);
      if (ev.type === "pipeline_done") source.close();
    };
  }

  continueBtn.addEventListener("click", () => {
    window.location.href = continueBtn.dataset.target || "/probe";
  });

  bootstrap();
})();
