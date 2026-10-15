// LeadPilot dashboard. Vanilla JS, no build step. All lead data is untrusted (it comes from a
// public form), so everything rendered goes through esc() or textContent.
"use strict";

const $ = (sel) => document.querySelector(sel);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtTime = (ts) => ts ? new Date(ts * 1000).toLocaleString() : "—";
const fmtMin = (m) => m == null ? "—" : m < 1 ? `${Math.round(m * 60)} s` : m < 120 ? `${m.toFixed(1)} min` : `${(m / 60).toFixed(1)} h`;
const tier = (t) => t ? `<span class="tier tier-${esc(t)}">${esc(t)}</span>` : `<span class="muted">…</span>`;

let currentTab = "review";
let openLeadId = null;

function adminHeaders() {
  let token = "";
  try { token = localStorage.getItem("leadpilot_admin_token") || ""; } catch (_) { /* storage blocked */ }
  return token ? { "X-Admin-Token": token } : {};
}

async function api(path, opts = {}) {
  const res = await fetch(path, { ...opts, headers: { "Content-Type": "application/json", ...adminHeaders(), ...(opts.headers || {}) } });
  if (res.status === 401 && opts.method === "POST" && !path.startsWith("/webhooks")) {
    const token = prompt("This action needs the admin token (LEADPILOT_ADMIN_TOKEN):");
    if (token) {
      try { localStorage.setItem("leadpilot_admin_token", token); } catch (_) { /* ignore */ }
      return api(path, opts);
    }
  }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail ? (typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail)) : res.statusText);
  return body;
}

function toast(msg) {
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2600);
}

// ------------------------------------------------------------------ header + metrics
async function refreshHealth() {
  try {
    const h = await api("/healthz");
    $("#health").innerHTML = [
      `qualifier: <b>${esc(h.qualifier)}</b>`, `crm: <b>${esc(h.crm)}</b>`, `mail: <b>${esc(h.mailer)}</b>`,
      `queue: <b>${esc(h.queue_depth)}</b>`,
    ].map((s) => `<span class="pill">${s}</span>`).join("");
    $("#count-dead").textContent = h.dead_letter;
  } catch (e) { $("#health").textContent = "API unreachable"; }
}

function baCard(label, before, after, deltaText) {
  return `<div class="card"><div class="label">${esc(label)}</div>
    <div class="ba"><span class="before">${esc(before)}</span><span class="after">${esc(after)}</span></div>
    <div class="delta">${esc(deltaText || "")}</div></div>`;
}
function statCard(label, value) {
  return `<div class="card"><div class="label">${esc(label)}</div><div class="value">${esc(value)}</div></div>`;
}

async function refreshMetrics() {
  const m = await api("/api/metrics");
  const ba = m.before_after;
  const mpl = ba.manual_minutes_per_lead;
  const fr = ba.first_response_minutes_median;
  $("#ba-note").textContent = `${m.leads_completed} leads processed. "Before" = documented manual baseline (assumption, config/business.yaml); "after" = measured.`;
  $("#before-after").innerHTML = [
    baCard("Team minutes per lead", `${mpl.before} min`, mpl.after == null ? "—" : `${mpl.after} min`,
      mpl.after == null ? "" : `${Math.round((1 - mpl.after / mpl.before) * 100)}% less manual work`),
    baCard("Median time to first reply (sent replies)", fmtMin(fr.before), fmtMin(fr.after),
      fr.after == null ? "" : fr.after < 1 ? "auto-replies go out in seconds" : `${Math.round(fr.before / fr.after)}× faster`),
    baCard("Team time for these leads", `${(ba.team_minutes_for_these_leads.before / 60).toFixed(1)} h`,
      `${(ba.team_minutes_for_these_leads.after / 60).toFixed(1)} h`, `${ba.hours_saved} h saved`),
  ].join("");
  $("#ops").innerHTML = [
    statCard("Zero-touch rate", m.zero_touch_rate == null ? "—" : `${Math.round(m.zero_touch_rate * 100)}%`),
    statCard("Awaiting review", m.pending_review),
    statCard("Pipeline p50 / p95", m.processing_seconds_median == null ? "—" : `${m.processing_seconds_median}s / ${m.processing_seconds_p95}s`),
    statCard("Retries / dead letter", `${m.retries} / ${m.dead_letter}`),
    statCard("LLM cost per lead", m.llm_cost_usd_per_lead == null ? "—" : `$${m.llm_cost_usd_per_lead.toFixed(4)}`),
    `<div class="card wide"><div class="label">By tier</div><div class="value small-value">${
      Object.entries(m.by_tier).map(([k, v]) => `<span class="tier tier-${esc(k)}">${esc(k)} ${esc(v)}</span>`).join(" ") || "—"}</div></div>`,
  ].join("");
  $("#count-review").textContent = m.pending_review;
}

// ------------------------------------------------------------------ tabs
function leadTable(rows, emptyText) {
  if (!rows.length) return `<div class="empty">${esc(emptyText)}</div>`;
  return `<table><thead><tr><th>Received</th><th>Lead</th><th>Tier</th><th>Score</th><th>Status</th><th>Reply</th><th>Why</th></tr></thead><tbody>
    ${rows.map((r) => `<tr class="clickable" data-lead="${esc(r.id)}">
      <td class="small muted">${esc(fmtTime(r.received_at))}</td>
      <td><b>${esc(r.name || r.email)}</b><div class="small muted">${esc(r.company || r.email)} · ${esc(r.source)}</div></td>
      <td>${tier(r.tier)}</td><td class="num">${esc(r.score ?? "")}</td>
      <td class="status status-${esc(r.status)}">${esc(r.status.replace("_", " "))}</td>
      <td class="small">${esc(r.reply_status)}</td>
      <td class="small muted">${esc((r.review_reasons || []).join("; ") || r.error || "")}</td></tr>`).join("")}
  </tbody></table>`;
}

const tabs = {
  async review() { return leadTable(await api("/api/leads?status=needs_review"), "Nothing waiting for a human. 🎉"); },
  async leads() { return leadTable(await api("/api/leads?limit=200"), "No leads yet. Use “Send test lead” to load samples."); },
  async dead() { return leadTable(await api("/api/leads?status=dead"), "Dead-letter queue is empty."); },
  async crm() {
    const { contacts, deals } = await api("/api/crm");
    const byContact = Object.fromEntries(contacts.map((c) => [c.id, c]));
    return `<h3 style="margin:14px">Deals (${deals.length})</h3>${deals.length ? `<table><thead><tr><th>Deal</th><th>Contact</th><th>Amount</th><th>Stage</th><th>Created</th></tr></thead><tbody>
      ${deals.map((d) => `<tr><td>${esc(d.name)}</td><td>${esc(byContact[d.contact_id]?.email || d.contact_id)}</td><td class="num">${d.amount ? "$" + Number(d.amount).toLocaleString() : "—"}</td><td>${esc(d.stage)}</td><td class="small muted">${esc(fmtTime(d.created_at))}</td></tr>`).join("")}
      </tbody></table>` : `<div class="empty">No deals (local CRM). With LEADPILOT_CRM=hubspot they go to HubSpot.</div>`}
      <h3 style="margin:14px">Contacts (${contacts.length})</h3>${contacts.length ? `<table><thead><tr><th>Email</th><th>Name</th><th>Company</th><th>Updated</th></tr></thead><tbody>
      ${contacts.map((c) => `<tr><td>${esc(c.email)}</td><td>${esc(c.name)}</td><td>${esc(c.company)}</td><td class="small muted">${esc(fmtTime(c.updated_at))}</td></tr>`).join("")}
      </tbody></table>` : `<div class="empty">No contacts yet.</div>`}`;
  },
  async outbox() {
    const rows = await api("/api/outbox");
    if (!rows.length) return `<div class="empty">No email sent yet.</div>`;
    return `<table><thead><tr><th>Sent</th><th>To</th><th>Subject</th><th>Transport</th></tr></thead><tbody>
      ${rows.map((r) => `<tr class="clickable" data-lead="${esc(r.lead_id)}"><td class="small muted">${esc(fmtTime(r.sent_at))}</td><td>${esc(r.recipient)}</td><td>${esc(r.subject)}</td><td>${esc(r.transport)}</td></tr>`).join("")}
    </tbody></table>`;
  },
  async alerts() {
    const rows = await api("/api/notifications");
    if (!rows.length) return `<div class="empty">No notifications yet.</div>`;
    return `<table><thead><tr><th>When</th><th>Channel</th><th>Message</th><th>Slack</th></tr></thead><tbody>
      ${rows.map((r) => `<tr${r.lead_id ? ` class="clickable" data-lead="${esc(r.lead_id)}"` : ""}><td class="small muted">${esc(fmtTime(r.ts))}</td><td>${esc(r.channel)}</td><td class="message small">${esc(r.text)}</td><td>${r.delivered ? "sent" : "logged"}</td></tr>`).join("")}
    </tbody></table>`;
  },
  async submit() {
    return `<form class="form" id="lead-form">
      <div><label for="f-name">Name</label><input id="f-name" name="name" value="Dr. Priya Lee"></div>
      <div><label for="f-email">Email</label><input id="f-email" name="email" type="email" required value="priya@brightsmile-dental.example"></div>
      <div><label for="f-company">Company</label><input id="f-company" name="company" value="BrightSmile Dental"></div>
      <div><label for="f-website">Website</label><input id="f-website" name="website" value=""></div>
      <div class="full"><label for="f-message">Message</label><textarea id="f-message" name="message" required>I'm the owner of a 3-location dental practice (about 40 staff). Our front desk spends 15 hours a week copying appointment requests into our system by hand. Budget is around $8,000 and we want this automated within the next 3 weeks.</textarea></div>
      <div class="full actions">
        <button class="btn primary" type="submit">Submit to webhook</button>
        <button class="btn" type="button" id="load-samples">Load 12 sample leads</button>
        <span class="small muted">Posts to <code>POST /webhooks/lead</code>, exactly like a web form, n8n or Zapier would.</span>
      </div></form>`;
  },
};

async function renderTab() {
  document.querySelectorAll(".tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === currentTab));
  const body = $("#tab-body");
  try { body.innerHTML = await tabs[currentTab](); } catch (e) { body.innerHTML = `<div class="empty">${esc(e.message)}</div>`; }
  if (currentTab === "submit") wireForm();
}

function wireForm() {
  $("#lead-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const data = Object.fromEntries(new FormData(ev.target).entries());
    data.source = "dashboard";
    try {
      const r = await api("/webhooks/lead", { method: "POST", body: JSON.stringify(data) });
      toast(r.duplicate ? "Duplicate submission: already processed" : "Lead queued");
      openLead(r.lead_id);
    } catch (e) { toast("Rejected: " + e.message); }
  });
  $("#load-samples").addEventListener("click", async (ev) => {
    ev.target.disabled = true;
    try {
      const samples = await api("/api/samples?n=12");
      let ok = 0;
      for (const s of samples) {
        try { await api("/webhooks/lead", { method: "POST", body: JSON.stringify({ ...s, source: "sample" }) }); ok++; } catch (_) { /* dup */ }
      }
      toast(`Queued ${ok} sample leads`);
      currentTab = "leads"; renderTab();
    } finally { ev.target.disabled = false; }
  });
}

// ------------------------------------------------------------------ lead drawer
async function openLead(id) {
  openLeadId = id;
  $("#drawer").classList.add("open");
  $("#drawer").setAttribute("aria-hidden", "false");
  await renderLead();
}
function closeLead() {
  openLeadId = null;
  $("#drawer").classList.remove("open");
  $("#drawer").setAttribute("aria-hidden", "true");
}

async function renderLead() {
  if (!openLeadId) return;
  const editing = document.activeElement && document.activeElement.closest && document.activeElement.closest("#drawer-body form");
  if (editing) return; // don't clobber a reviewer mid-edit
  const { lead, events, steps, job } = await api(`/api/leads/${openLeadId}`);
  const q = lead.qualification || {};
  const p = lead.payload;
  const stepRows = steps.map((s) => `<tr><td>${esc(s.step)}</td><td class="num">${s.duration_ms.toFixed(0)} ms</td><td class="num">${s.attempt}</td></tr>`).join("");
  const review = lead.status === "needs_review";
  $("#drawer-body").innerHTML = `
    <h2>${esc(p.name || p.email)} ${tier(lead.tier)}</h2>
    <p class="muted small">${esc(p.email)}${p.company ? " · " + esc(p.company) : ""} · ${esc(p.source)} · ${esc(fmtTime(lead.received_at))}</p>
    <div class="message">${esc(p.message)}</div>
    ${review ? `<div class="review-box"><b>Needs a human:</b> ${esc((lead.review_reasons || []).join("; ") || "reply draft awaiting approval")}</div>` : ""}
    ${lead.status === "dead" ? `<div class="review-box"><b>Dead letter:</b> ${esc(lead.error)}<div class="actions"><button class="btn" id="btn-retry">Re-queue</button></div></div>` : ""}
    <h3>Qualification <span class="muted small">(${esc(lead.qualifier || "pending")})</span></h3>
    <dl class="kv">
      <dt>Status</dt><dd class="status status-${esc(lead.status)}">${esc(lead.status)}</dd>
      <dt>Score</dt><dd>${esc(lead.score ?? "—")} · confidence ${esc(q.confidence ?? "—")}</dd>
      <dt>Intent</dt><dd>${esc(q.intent || "—")}</dd>
      <dt>Summary</dt><dd>${esc(q.summary || "—")}</dd>
      <dt>Services</dt><dd>${esc((q.services || []).join(", ") || "—")}</dd>
      <dt>Budget / timeline</dt><dd>${q.budget_usd ? "$" + Number(q.budget_usd).toLocaleString() : "—"} · ${esc(q.timeline || "—")}</dd>
      <dt>Company</dt><dd>${esc(q.company_size || "—")} · ${esc(q.industry || "—")}${q.is_decision_maker ? " · decision maker" : ""}</dd>
      <dt>Reasons</dt><dd>${(q.reasons || []).length ? `<ul class="reasons">${q.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : "—"}</dd>
      <dt>Red flags</dt><dd>${(q.red_flags || []).map((f) => `<span class="flag">${esc(f)}</span>`).join("") || "—"}</dd>
      <dt>CRM</dt><dd>${esc(lead.crm_contact_id || "—")}${lead.crm_deal_id ? " · deal " + esc(lead.crm_deal_id) : ""}</dd>
      <dt>LLM cost</dt><dd>$${Number(lead.cost_usd || 0).toFixed(5)}</dd>
    </dl>
    ${lead.reply ? `<h3>Reply <span class="muted small">(${esc(lead.reply_status)})</span></h3>
      ${review ? `<form id="reply-form"><label for="r-subject">Subject</label><input id="r-subject" value="${esc(lead.reply.subject)}">
        <label for="r-body">Body</label><textarea id="r-body">${esc(lead.reply.body)}</textarea>
        <div class="actions"><button class="btn primary" type="submit">Approve &amp; send</button><button class="btn danger" type="button" id="btn-dismiss">Dismiss</button></div></form>`
        : `<div class="message">${esc(lead.reply.subject)}\n\n${esc(lead.reply.body)}</div>`}`
      : review ? `<div class="actions"><button class="btn primary" id="btn-approve-noreply">Mark reviewed</button><button class="btn danger" id="btn-dismiss">Dismiss</button></div>` : ""}
    <h3>Pipeline steps</h3>
    ${stepRows ? `<table><thead><tr><th>Step</th><th>Duration</th><th>Attempt</th></tr></thead><tbody>${stepRows}</tbody></table>` : `<p class="muted">Queued…</p>`}
    <p class="small muted">Job: ${esc(job?.status)} · attempts ${esc(job?.attempts)}${job?.last_error ? " · last error: " + esc(job.last_error) : ""}</p>
    <h3>Audit log</h3>
    <ul class="timeline">${events.map((e) => `<li class="${esc(e.level)}"><span class="t">${esc(new Date(e.ts * 1000).toLocaleTimeString())}</span>${esc(e.message)}</li>`).join("")}</ul>`;

  const act = async (path, body) => {
    try { await api(path, { method: "POST", body: JSON.stringify(body || {}) }); toast("Done"); refreshAll(); }
    catch (e) { toast(e.message); }
  };
  $("#reply-form")?.addEventListener("submit", (ev) => {
    ev.preventDefault();
    act(`/api/leads/${lead.id}/approve`, { subject: $("#r-subject").value, body: $("#r-body").value });
  });
  $("#btn-dismiss")?.addEventListener("click", () => act(`/api/leads/${lead.id}/dismiss`, {}));
  $("#btn-approve-noreply")?.addEventListener("click", () => act(`/api/leads/${lead.id}/approve`, {}));
  $("#btn-retry")?.addEventListener("click", () => act(`/api/leads/${lead.id}/retry`, {}));
}

// ------------------------------------------------------------------ wiring
document.querySelectorAll(".tabs button").forEach((b) => b.addEventListener("click", () => { currentTab = b.dataset.tab; renderTab(); }));
$("#tab-body").addEventListener("click", (ev) => {
  const row = ev.target.closest("tr[data-lead]");
  if (row) openLead(row.dataset.lead);
});
$("#drawer-close").addEventListener("click", closeLead);
$("#drawer").addEventListener("click", (ev) => { if (ev.target.id === "drawer") closeLead(); });
document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") closeLead(); });

async function refreshAll() {
  await Promise.allSettled([refreshHealth(), refreshMetrics(), currentTab !== "submit" ? renderTab() : null, renderLead()]);
}
// Deep links: /#lead=<id> opens that lead (Slack alerts link here), /#tab=<name> picks a tab.
function fromHash() {
  const params = new URLSearchParams(location.hash.slice(1));
  if (params.get("tab") && tabs[params.get("tab")]) currentTab = params.get("tab");
  if (params.get("lead")) openLead(params.get("lead"));
}
window.addEventListener("hashchange", fromHash);
fromHash();
refreshAll();
setInterval(refreshAll, 3000);
