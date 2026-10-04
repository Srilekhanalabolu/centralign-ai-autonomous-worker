// CentrAlign AI dashboard — polls the task API and renders the agent's live state.
const $ = (id) => document.getElementById(id);
const ICON = { SUCCESS: "✓", FAILED: "⚠", RUNNING: "…", RECOVERED: "↻", WAITING_FOR_APPROVAL: "⏸", VERIFIED: "✓" };
const TERMINAL = ["COMPLETED", "FAILED"];
let taskId = null;
let pollTimer = null;
let knownRecordIds = new Set();

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const inr = (n) => (n === null || n === undefined || n === "" ? "—" : "₹" + Number(n).toLocaleString("en-IN"));
const prettyDate = (s) => {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s || "")) return s ?? "—";
  const d = new Date(s + "T00:00:00");
  return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
};
const time = (iso) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body;
}

// ------------------------------------------------------------------ boot
async function init() {
  try {
    const cfg = await api("/api/config");
    $("engine").textContent = cfg.mode === "llm" ? `LLM · ${cfg.model}` : "Demo mode · deterministic policy";
    $("threshold").textContent = inr(cfg.approval_threshold);
  } catch (e) { $("engine").textContent = "unavailable"; }
  document.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => { $("goal").value = c.dataset.goal; }));
  $("runBtn").addEventListener("click", runTask);
  $("approveBtn").addEventListener("click", () => decide(true));
  $("rejectBtn").addEventListener("click", () => decide(false));
  $("resetBtn").addEventListener("click", resetSandbox);
  $("refreshFinance").addEventListener("click", loadFinance);
  loadRepo();
  loadFinance(true);
}

async function runTask() {
  const goal = $("goal").value.trim();
  if (!goal) return;
  $("runBtn").disabled = true;
  $("resultCard").classList.add("hidden");
  $("timeline").innerHTML = '<li class="empty">Starting agent…</li>';
  try {
    const res = await api("/api/tasks", { method: "POST", body: JSON.stringify({ goal, mode: $("mode").value }) });
    taskId = res.task_id;
    clearInterval(pollTimer);
    pollTimer = setInterval(poll, 500);
    poll();
  } catch (e) {
    $("timeline").innerHTML = `<li class="empty">Could not start task: ${esc(e.message)}</li>`;
    $("runBtn").disabled = false;
  }
}

async function poll() {
  if (!taskId) return;
  try {
    const { state, events } = await api(`/api/tasks/${taskId}`);
    render(state, events);
    loadFinance();
    if (TERMINAL.includes(state.status)) {
      clearInterval(pollTimer);
      $("runBtn").disabled = false;
    }
  } catch (e) { console.error(e); }
}

async function decide(approved) {
  if (!taskId) return;
  $("approveBtn").disabled = $("rejectBtn").disabled = true;
  try {
    await api(`/api/tasks/${taskId}/approve`, { method: "POST", body: JSON.stringify({ approved, note: approved ? "Approved from dashboard" : "Rejected from dashboard" }) });
  } catch (e) { alert(e.message); }
  $("approveBtn").disabled = $("rejectBtn").disabled = false;
  poll();
}

async function resetSandbox() {
  if (!confirm("Clear all records from the sandbox finance system?")) return;
  await api("/api/sandbox/reset", { method: "POST" });
  knownRecordIds = new Set();
  loadFinance(true);
}

// ---------------------------------------------------------------- render
function render(state, events) {
  const badge = $("taskStatus");
  badge.textContent = state.status.replaceAll("_", " ");
  badge.className = "badge badge-" + state.status;
  $("taskId").textContent = `task ${state.task_id} · ${state.mode === "llm" ? state.model : "demo mode"}`;
  $("stepInfo").textContent = `${state.step_count} action(s) · ${state.retry_count} retr${state.retry_count === 1 ? "y" : "ies"}`;

  renderLoop(events, state);
  renderTimeline(events);
  renderState(state);
  renderApproval(state);
  renderResult(state);
}

function renderLoop(events, state) {
  const on = new Set();
  let adapted = false;
  for (const e of events) {
    if (e.title === "Goal received") on.add("goal");
    if (e.title === "Goal understood") on.add("understand");
    if (e.type === "plan") on.add("plan");
    if (e.type === "tool_call") { on.add("execute"); if (e.status !== "RUNNING") on.add("observe"); }
    if (e.type === "recovery" || e.status === "RECOVERED") { on.add("adapt"); adapted = true; }
    if (e.type === "verification" && e.status === "VERIFIED") on.add("verify");
    if (e.type === "complete" && e.status === "VERIFIED") on.add("complete");
  }
  document.querySelectorAll("#loop span").forEach((s) => {
    const p = s.dataset.phase;
    s.className = on.has(p) ? (p === "adapt" && adapted ? "warn" : "on") : "";
  });
}

function renderTimeline(events) {
  if (!events.length) return;
  const open = new Set([...document.querySelectorAll("#timeline details[open]")].map((d) => d.dataset.id));
  $("timeline").innerHTML = events.map((e) => {
    const details = e.details ? `<details data-id="${e.id}" ${open.has(String(e.id)) ? "open" : ""}><summary>Details</summary><pre>${esc(JSON.stringify(e.details, null, 2))}</pre></details>` : "";
    return `<li class="ev ev-${e.status} ev-type-${e.type}">
      <div class="ev-icon">${ICON[e.status] || "•"}</div>
      <div>
        <div class="ev-top">
          <span class="ev-title">${esc(e.title)}</span>
          <span class="ev-meta">${e.action ? `<span class="ev-tool">${esc(e.action)}</span>` : ""}<span class="badge badge-${e.status}">${e.status.replaceAll("_", " ")}</span><span>${time(e.timestamp)}</span></span>
        </div>
        ${e.summary ? `<div class="ev-summary">${esc(e.summary)}</div>` : ""}
        ${details}
      </div>
    </li>`;
  }).join("");
}

function renderState(state) {
  $("interpreted").textContent = state.interpreted_goal || "—";
  $("plan").innerHTML = state.plan.length
    ? state.plan.map((p, i) => `<li class="${p.done ? "done" : i === state.current_step && !TERMINAL.includes(state.status) ? "current" : ""}"><span class="tick">${p.done ? "✓" : ""}</span><span>${esc(p.description)} <span class="tool">${esc(p.tool)}</span></span></li>`).join("")
    : '<li class="muted">—</li>';

  const d = state.discovered_information || {};
  const facts = [];
  if (d.goal_entities?.company) facts.push(["Target company", d.goal_entities.company]);
  if (d.invoice_candidates) facts.push(["Invoices found", d.invoice_candidates.map((c) => `${c.invoice_number} (${c.issue_date})`).join(", ") || "none"]);
  if (d.source_invoice) {
    const s = d.source_invoice;
    facts.push(["Source invoice", s.invoice_number], ["Extracted amount", s.amount], ["Due date", prettyDate(s.due_date)]);
  }
  if (d.submitted_amount !== undefined) facts.push(["Submitted amount", String(d.submitted_amount)]);
  if (d.finance_record_id) facts.push(["Finance record", "#" + d.finance_record_id + (d.duplicate_detected ? " (pre-existing)" : "")]);
  if (state.approval_status !== "not_required") facts.push(["Approval", state.approval_status]);
  if (state.errors.length) facts.push(["Errors observed", String(state.errors.length)]);
  $("facts").innerHTML = facts.length ? facts.map(([k, v]) => `<div class="fk">${esc(k)}</div><div>${esc(v)}</div>`).join("") : '<span class="muted">—</span>';
}

function renderApproval(state) {
  const pending = state.approval_status === "pending";
  $("approval").classList.toggle("hidden", !pending);
  if (pending) $("approvalMsg").textContent = state.approval_request?.message || "Approve this action?";
}

function renderResult(state) {
  const card = $("resultCard");
  const r = state.final_result;
  if (!r) { card.classList.add("hidden"); return; }
  card.classList.remove("hidden");
  card.classList.toggle("failed", !r.verified);
  const ev = r.evidence;
  if (!ev) {
    card.innerHTML = `<div class="result-title">✕ ${esc(r.status)}</div><p>${esc(r.summary)}</p><p class="muted small">No success was reported because the result could not be verified.</p>`;
    return;
  }
  const source = state.discovered_information.source_invoice || {};
  const rows = ev.comparison.map((c) => `<tr><td>${esc(c.field)}</td><td class="mono">${esc(c.source)}</td><td class="mono">${esc(c.stored)}</td><td class="${c.match ? "ok" : "bad"}">${c.match ? "✓ match" : "✕ mismatch"}</td></tr>`).join("");
  card.innerHTML = `
    <div class="card-head"><div class="result-title">✓ ${esc(r.status)}</div><span class="badge badge-VERIFIED">VERIFIED</span></div>
    <div class="muted">${esc(r.summary)}</div>
    <div class="result-grid">
      <div><div class="k">Invoice</div><div class="val mono">${esc(ev.invoice_number)}</div></div>
      <div><div class="k">Company</div><div class="val">${esc(ev.company)}</div></div>
      <div><div class="k">Amount</div><div class="val">${esc(source.amount || ev.amount_display)}</div></div>
      <div><div class="k">Due date</div><div class="val">${prettyDate(ev.due_date)}</div></div>
      <div><div class="k">Finance record</div><div class="val mono">#${esc(ev.finance_record_id)}</div></div>
      <div><div class="k">Verification</div><div class="val ok">✓ VERIFIED</div></div>
    </div>
    <div class="k">Evidence · ${esc(ev.method)}</div>
    <table class="cmp"><thead><tr><th>Field</th><th>Source invoice</th><th>Finance record</th><th></th></tr></thead><tbody>${rows}</tbody></table>
    <div class="muted small" style="margin-top:8px">${r.steps_taken} actions · ${r.retries} recovered retr${r.retries === 1 ? "y" : "ies"} · source document <span class="mono">${esc(ev.source_document)}</span></div>`;
}

// ------------------------------------------------------------- tables
async function loadFinance(silent) {
  try {
    const { records } = await api("/finance/invoices");
    $("financeRows").innerHTML = records.length
      ? records.map((r) => `<tr class="${!silent && !knownRecordIds.has(r.id) ? "new" : ""}"><td class="mono">#${r.id}</td><td class="mono">${esc(r.invoice_number)}</td><td>${esc(r.company)}</td><td class="num">${inr(r.amount)}</td><td>${prettyDate(r.due_date)}</td><td class="muted">${esc((r.created_at || "").replace("T", " ").replace("+00:00", " UTC"))}</td></tr>`).join("")
      : '<tr><td colspan="6" class="muted">No records yet.</td></tr>';
    if (silent) records.forEach((r) => knownRecordIds.add(r.id));
  } catch (e) { console.error(e); }
}

async function loadRepo() {
  try {
    const { invoices } = await api("/api/invoices");
    $("repoRows").innerHTML = invoices.map((i) => `<tr><td class="mono">${esc(i.invoice_id)}.json</td><td class="mono">${esc(i.invoice_number)}</td><td>${esc(i.company)}</td><td class="num">${esc(i.amount)}</td><td>${prettyDate(i.issue_date)}</td><td>${prettyDate(i.due_date)}</td></tr>`).join("");
  } catch (e) { console.error(e); }
}

init();
