(function () {
  "use strict";

  const runLine = document.getElementById("run-line");
  const finalBadge = document.getElementById("final-badge");
  const ledgerBody = document.getElementById("ledger-body");
  const scorePanel = document.getElementById("score-panel");
  const artifactsBtn = document.getElementById("artifacts-btn");
  const artifactsList = document.getElementById("artifacts-list");

  const HUNK_TITLES = {
    idempotency: "Idempotent event settlement",
    rounding: "Tier multipliers & rounding",
    redeem_check: "Redeem: insufficient-balance guard",
  };

  const STATUS_LABEL = {
    proven: "Verified \u2713 (proven)",
    claimed: "Claimed \u2014 could not defend",
    untested: "Untested",
  };

  function scoreCell(val, label) {
    const div = document.createElement("div");
    div.className = "score-cell";
    div.innerHTML = `<div class="val">${val}</div><div class="lab">${label}</div>`;
    return div;
  }

  async function bootstrap() {
    const res = await fetch("/api/verdict");
    const data = await res.json();

    runLine.textContent = `Run ${data.run_id}${data.rehearsal ? " (rehearsal)" : ""} \u2014 ${data.twins_summary.total} twins forged, ${data.twins_summary.killed} killed, ${data.twins_summary.survived} survived.`;

    const pass = data.score.symbiosis_index >= 82;
    finalBadge.className = `final-badge ${pass ? "pass" : "fail"}`;
    finalBadge.textContent = pass ? `\u2605 ${data.badge}` : data.badge;

    Object.keys(HUNK_TITLES).forEach((hunkId) => {
      const status = data.hunks[hunkId] || "untested";
      const elapsed = data.hunk_elapsed_ms[hunkId];
      const tr = document.createElement("tr");
      tr.innerHTML = `
        <td>${HUNK_TITLES[hunkId]}</td>
        <td>${elapsed != null ? `verified in ${Math.round(elapsed)}ms` : "never opened"}</td>
        <td><span class="status-tag ${status}">${STATUS_LABEL[status]}</span></td>
      `;
      ledgerBody.appendChild(tr);
    });

    scorePanel.appendChild(scoreCell(data.score.override_value, "Override Value (40 \u00d7 killed/valid)"));
    scorePanel.appendChild(scoreCell(data.score.probe_defense, "Probe Defense"));
    scorePanel.appendChild(scoreCell(data.score.baseline, "Baseline"));
    scorePanel.appendChild(scoreCell(data.score.symbiosis_index, "Symbiosis Index"));
  }

  artifactsBtn.addEventListener("click", async () => {
    const res = await fetch("/api/artifacts");
    const data = await res.json();
    artifactsList.innerHTML = `<div><strong>${data.run_dir}/</strong></div>` + data.paths.map((p) => `<div>${p}</div>`).join("");
  });

  bootstrap();
})();
