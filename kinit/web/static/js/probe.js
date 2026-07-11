(function () {
  "use strict";

  const probeBody = document.getElementById("probe-body");
  const noProbe = document.getElementById("no-probe");
  const twinLine = document.getElementById("twin-line");
  const eventsBox = document.getElementById("events-box");
  const optionsEl = document.getElementById("options");
  const resultEl = document.getElementById("probe-result");
  const nextRow = document.getElementById("next-row");
  const verdictBtn = document.getElementById("verdict-btn");

  function renderOption(label, output) {
    const btn = document.createElement("button");
    btn.className = "probe-option";
    btn.dataset.label = label;
    btn.innerHTML = `<div class="opt-label">Output ${label}</div><pre>${JSON.stringify(output, null, 2)}</pre>`;
    return btn;
  }

  function showResult(correct) {
    resultEl.textContent = correct
      ? "\u2713 Correct \u2014 you actually know your own code."
      : "\u2717 Wrong \u2014 you verified code you couldn't predict.";
    resultEl.className = `probe-result ${correct ? "correct" : "wrong"}`;
    nextRow.style.display = "flex";
  }

  function paintChoice(optA, optB, chosenLabel, correct) {
    optA.disabled = true;
    optB.disabled = true;
    const chosenBtn = chosenLabel === "A" ? optA : optB;
    const otherBtn = chosenLabel === "A" ? optB : optA;
    chosenBtn.classList.add(correct ? "correct" : "wrong");
    if (!correct) otherBtn.classList.add("correct");
  }

  async function onChoose(label, optA, optB) {
    const res = await fetch("/api/probe_choice", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ choice: label }),
    });
    const data = await res.json();
    paintChoice(optA, optB, label, data.correct);
    showResult(data.correct);
  }

  async function bootstrap() {
    const res = await fetch("/api/probe_data");
    const data = await res.json();

    if (!data.available) {
      probeBody.classList.add("hidden");
      noProbe.classList.remove("hidden");
      return;
    }

    twinLine.textContent = `Surviving twin ${data.twin_id}: ${data.description}`;
    eventsBox.textContent = JSON.stringify(data.input_events, null, 2);

    const optA = renderOption("A", data.option_a);
    const optB = renderOption("B", data.option_b);
    optionsEl.appendChild(optA);
    optionsEl.appendChild(optB);

    if (data.already_answered) {
      paintChoice(optA, optB, data.choice, data.correct);
      showResult(data.correct);
    } else {
      optA.addEventListener("click", () => onChoose("A", optA, optB));
      optB.addEventListener("click", () => onChoose("B", optA, optB));
    }
  }

  verdictBtn.addEventListener("click", () => {
    window.location.href = "/verdict";
  });

  bootstrap();
})();
