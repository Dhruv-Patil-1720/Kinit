(function () {
  "use strict";

  const pageLoadTs = performance.now();
  const hunksEl = document.getElementById("hunks");
  const mergeBtn = document.getElementById("merge-btn");
  const rehearsalBtn = document.getElementById("rehearsal-btn");

  function lineHtml(line) {
    if (line.t === "gap") {
      return `<div class="line gap">${line.s}  unrelated code skipped  ${line.s}</div>`;
    }
    const cls = line.t === "add" ? "add" : "ctx";
    const escaped = line.s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    return `<div class="line ${cls}">${escaped || " "}</div>`;
  }

  function renderHunk(hunk) {
    const div = document.createElement("div");
    div.className = "hunk";
    div.id = `hunk-${hunk.id}`;
    div.innerHTML = `
      <div class="hunk-head">
        <h2>${hunk.title}</h2>
        <span class="hunk-file">${hunk.file}</span>
      </div>
      <div class="diff">
        <div class="line ctx">${hunk.hunk_header}</div>
        ${hunk.lines.map(lineHtml).join("")}
      </div>
      <div class="caption">${hunk.caption}</div>
      <div class="hunk-footer">
        <span class="elapsed" id="elapsed-${hunk.id}"></span>
        <button data-hunk="${hunk.id}" class="verify-btn">Verified &mdash; I understand this</button>
      </div>
    `;
    return div;
  }

  function markVerified(hunkId, elapsedMs) {
    const btn = document.querySelector(`button[data-hunk="${hunkId}"]`);
    if (btn) {
      btn.textContent = "\u2713 Verified";
      btn.classList.add("verified");
      btn.disabled = true;
    }
    const elapsedEl = document.getElementById(`elapsed-${hunkId}`);
    if (elapsedEl) {
      elapsedEl.textContent = `verified ${Math.round(elapsedMs)}ms after page load`;
    }
  }

  function updateMergeAvailability(allVerified) {
    mergeBtn.disabled = !allVerified;
    rehearsalBtn.disabled = !allVerified;
  }

  async function loadHunks() {
    const res = await fetch("/api/hunks");
    const data = await res.json();
    data.hunks.forEach((hunk) => hunksEl.appendChild(renderHunk(hunk)));

    hunksEl.querySelectorAll(".verify-btn").forEach((btn) => {
      btn.addEventListener("click", onVerifyClick);
    });
  }

  async function onVerifyClick(evt) {
    const hunkId = evt.currentTarget.dataset.hunk;
    const elapsedMs = performance.now() - pageLoadTs;
    markVerified(hunkId, elapsedMs);

    const res = await fetch("/api/verify", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hunk_id: hunkId, elapsed_ms: elapsedMs }),
    });
    const data = await res.json();
    updateMergeAvailability(data.all_verified);
  }

  async function startPipeline(rehearsal) {
    mergeBtn.disabled = true;
    rehearsalBtn.disabled = true;
    await fetch("/api/merge", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rehearsal }),
    });
    window.location.href = rehearsal ? "/forge?rehearsal=1" : "/forge";
  }

  mergeBtn.addEventListener("click", () => startPipeline(false));
  rehearsalBtn.addEventListener("click", () => startPipeline(true));

  loadHunks();
})();
