const byId = id => document.getElementById(id);
const status = byId("status");
const runButton = byId("run");
let report;
let selected = 0;

function element(tag, content, className) {
  const node = document.createElement(tag);
  node.textContent = content;
  if (className) node.className = className;
  return node;
}

function milliseconds(value) {
  return value.toFixed(2) + " ms";
}

function render() {
  const gate = byId("verdict");
  gate.textContent = report.passed ? "Release passes" : "Release blocked";
  gate.closest(".decision").dataset.pass = report.passed;
  const failures = report.cases.filter(row => !row.passed).length;
  byId("gate-summary").textContent = failures
    ? failures + " of " + report.cases.length + " endpoints triggered the gate."
    : "All " + report.cases.length + " endpoints met the gate.";
  byId("method").textContent = report.method;
  const cases = byId("cases");
  const focusedIndex = Array.from(cases.children).indexOf(document.activeElement);
  cases.replaceChildren();
  report.cases.forEach((row, index) => {
    const button = element("button", "", "case");
    const label = [row.slow ? "LATENCY" : "", row.contract_failures ? "RESPONSE" : ""].filter(Boolean).join(" + ");
    const tag = element("span", label || "PASS", "tag");
    tag.dataset.pass = row.passed;
    button.type = "button";
    button.setAttribute("aria-pressed", String(index === selected));
    button.append(element("strong", row.name), tag, element("code", "GET " + row.path));
    button.addEventListener("click", () => { selected = index; render(); });
    cases.append(button);
  });
  if (focusedIndex >= 0) cases.children[focusedIndex]?.focus();
  const row = report.cases[selected];
  const metric = (label, value) => {
    const block = element("div", "", "metric");
    block.append(element("span", label), element("strong", milliseconds(value)));
    return block;
  };
  byId("metrics").replaceChildren(
    metric("Baseline median", row.baseline_ms.median),
    metric("Candidate median", row.candidate_ms.median),
    metric("Paired median change", row.paired_delta_ms.median),
    metric("Latency margin", row.margin_ms),
  );
  const contract = byId("contract");
  contract.replaceChildren(
    element("p", "p95 · " + milliseconds(row.baseline_ms.p95) + " baseline / " + milliseconds(row.candidate_ms.p95) + " candidate"),
    element("p", row.contract_failures + " response mismatches across " + row.samples + " pairs."),
    ...row.differences.map(difference => element("p", difference)),
  );
  drawWaterfall(row.trace_ids);
}

function drawWaterfall(traceIds) {
  const waterfall = byId("waterfalls");
  waterfall.replaceChildren();
  const traces = traceIds.map(id => report.traces[id] || []);
  const axis = Math.max(1, ...traces.flat().map(span => span.start_ms + span.duration_ms));
  traces.forEach((spans, index) => {
    const panel = element("div", "", "trace-group");
    const title = index === 0 ? "Baseline" : "Candidate";
    const meta = element("div", "", "trace-meta");
    const id = element("span", traceIds[index].slice(0, 12));
    id.title = traceIds[index];
    meta.append(id, element("span", "0–" + milliseconds(axis)));
    panel.append(element("h3", title), meta);
    spans.forEach(span => {
      const row = element("div", "", "span-row");
      const track = element("div", "", "span-track");
      track.setAttribute("aria-hidden", "true");
      const bar = element("span", "", "span-bar");
      bar.dataset.kind = span.kind;
      bar.style.left = (100 * span.start_ms / axis) + "%";
      bar.style.width = (100 * span.duration_ms / axis) + "%";
      track.append(bar);
      const label = element("span", span.name, "span-name");
      label.style.paddingLeft = (span.kind === "CLIENT" ? 0 : span.kind === "SERVER" ? 8 : 16) + "px";
      row.title = span.kind + " starts at " + milliseconds(span.start_ms);
      row.append(label, track, element("span", milliseconds(span.duration_ms), "span-name"));
      panel.append(row);
    });
    if (!spans.length) panel.append(element("p", "No spans captured."));
    waterfall.append(panel);
  });
}

async function experiment(event) {
  event?.preventDefault();
  runButton.disabled = true;
  const scenario = byId("scenario").value;
  const repeats = byId("repeats").valueAsNumber;
  status.textContent = "Running " + repeats + " paired requests per endpoint…";
  status.dataset.error = "false";
  try {
    const response = await fetch("/api/experiment", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({scenario, repeats}),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : "Check the experiment settings.");
    report = body;
    selected = Math.max(0, body.cases.findIndex(row => !row.passed));
    byId("results").hidden = false;
    status.textContent = "Measured " + repeats + " pairs across " + body.cases.length + " endpoints.";
    render();
  } catch (error) {
    status.textContent = error.message;
    status.dataset.error = "true";
  } finally {
    runButton.disabled = false;
  }
}

byId("experiment").addEventListener("submit", experiment);
byId("export").addEventListener("click", () => {
  const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], {type: "application/json"}));
  const link = document.createElement("a");
  link.href = url;
  link.download = "releaselens-report.json";
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
experiment();
