// TDflowMCP governance dashboard (Phase 2B, ADR-0005). A view and decision surface only: the component
// enforces every rule. External script (the CSP forbids inline script). Server data is rendered with
// textContent only. The CSRF token is kept in memory; a reload means pairing again.
"use strict";
(function () {
  const $ = (id) => document.getElementById(id);
  let csrf = null, tab = "overview", lastSeq = 0, oldestSeq = null, filters = {}, pairTimer = null, lastReport = null;
  const POLL_MS = 1000;                                   // ADR-0005 gate: events appear within 1 s

  function el(tag, text, cls) { const e = document.createElement(tag); if (text !== undefined && text !== null) e.textContent = String(text); if (cls) e.className = cls; return e; }
  function setText(id, v) { $(id).textContent = (v === undefined || v === null || v === "") ? "–" : String(v); }

  async function api(method, path, body) {
    const headers = { "X-TDflow-Request": "1" };
    if (csrf) headers["X-TDflow-CSRF"] = csrf;
    if (method !== "GET") headers["Content-Type"] = "application/json";
    let r;
    try {
      r = await fetch(path, { method, headers, cache: "no-store", credentials: "same-origin",
                              body: method === "GET" ? undefined : JSON.stringify(body || {}) });
    } catch (e) { return { ok: false, status: 0, data: null }; }
    let data = null;
    try { data = await r.json(); } catch (e) { data = null; }
    if (r.status === 401 && csrf) unpaired("This browser is no longer paired.");
    return { ok: r.ok, status: r.status, data };
  }
  function why(r) { return (r.data && (r.data.message || r.data.code || r.data.error)) || ("HTTP " + r.status); }

  // ---- pairing (same reverse flow as the Setup page: approved inside TouchDesigner) -------------------------
  async function requestPairing() {
    const r = await api("POST", "/setup/api/pair");
    if (!r.ok) {
      $("pair-status").textContent = r.status === 429 ? "Too many pairing requests. Wait a minute." :
        r.status === 409 ? "Another pairing request is waiting. Reject it in TouchDesigner or wait two minutes." : "Pairing request failed.";
      return;
    }
    $("pair-code").hidden = false;
    $("pair-code").textContent = r.data.match_code;
    $("pair-status").textContent = "In TouchDesigner, check that Pairing Request shows " + r.data.match_code + ", then pulse Approve Pairing.";
    clearInterval(pairTimer);
    pairTimer = setInterval(pollPairing, 1500);
  }
  async function pollPairing() {
    const r = await api("GET", "/setup/api/pair/status");
    const st = r.data && r.data.state;
    if (st === "approved" && r.data.csrf) {
      clearInterval(pairTimer);
      csrf = r.data.csrf;
      $("pair-box").hidden = true;
      $("app").hidden = false;
      document.body.dataset.paired = "true";
      refreshAll();
    } else if (st === "rejected" || st === "expired" || st === "unknown") {
      clearInterval(pairTimer);
      $("pair-code").hidden = true;
      $("pair-status").textContent = st === "rejected" ? "Pairing was rejected in TouchDesigner." : "The pairing request expired. Request a new one.";
    }
  }
  function unpaired(msg) {
    csrf = null;
    $("app").hidden = true;
    $("pair-box").hidden = false;
    $("pair-code").hidden = true;
    $("pair-status").textContent = msg || "";
    $("kill").hidden = $("resume").hidden = true;
    document.body.dataset.paired = "false";
  }

  // ---- tabs ----------------------------------------------------------------------------------------------
  function showTab(name) {
    tab = name;
    for (const b of document.querySelectorAll("[role=tab]")) b.setAttribute("aria-selected", String(b.dataset.tab === name));
    for (const s of document.querySelectorAll("[data-panel]")) s.hidden = s.dataset.panel !== name;
    refreshTab();
  }

  // ---- overview + kill switch ------------------------------------------------------------------------------
  async function refreshOverview() {
    const r = await api("GET", "/api/overview");
    if (!r.ok || !r.data) return;
    const d = r.data;
    $("state").textContent = d.state || "–";
    $("state").dataset.state = d.state || "";
    $("paused").hidden = !!d.cooking;
    $("kill").hidden = !!d.suspended;
    $("resume").hidden = !d.suspended;
    setText("o-state", d.state + (d.suspended ? " · all agents suspended" : ""));
    setText("o-detail", d.detail);
    setText("o-endpoint", d.endpoint || (d.mcp ? "" : "MCP is off"));
    setText("o-root", d.project_root);
    setText("o-agents", d.agents);
    setText("o-pending", d.approvals_pending);
    const a = d.audit || {};
    setText("o-audit", a.events + " events · chain " + (a.chain_ok ? "intact" : "BROKEN") + " · checkpoints " +
      (a.checkpoints_ok ? "valid" : "INVALID") + (a.unverified_tail ? " · " + a.unverified_tail + " event(s) after the last checkpoint" : ""));
    $("o-audit").className = a.ok ? "r-ok" : "r-bad";
    setText("o-storage", (d.storage && (d.storage.location || d.storage.status)) || d.data_status);
    setText("o-version", d.version + " · TouchDesigner " + d.td_build);
    const badge = $("apr-badge");
    badge.hidden = !d.approvals_pending;
    badge.textContent = d.approvals_pending || 0;
    if (d.last_seq > lastSeq && tab === "activity" && $("f-live").checked) await refreshEvents(true);
  }
  async function kill(suspend) {
    $("kill-status").textContent = "";
    const r = await api("POST", "/api/kill", { suspend });
    $("kill-status").textContent = !r.ok ? "Not done: " + why(r) + "." :
      suspend ? ("All agents suspended" + (r.data.recorded ? "." : " (the audit log could not record it).")) : r.data.note;
    refreshOverview();
  }

  // ---- approvals -------------------------------------------------------------------------------------------
  async function refreshApprovals(force) {
    const box0 = $("approvals");
    // Review 13: never move cards under the pointer or the keyboard focus (a click could land on another request).
    if (!force && (box0.matches(":hover") || box0.contains(document.activeElement))) return;
    const r = await api("GET", "/api/approvals");
    if (!r.ok || !r.data) return;
    const box = $("approvals");
    box.replaceChildren();
    if (!r.data.approvals.length) { box.append(el("p", "Nothing is waiting.", "hint")); return; }
    for (const a of r.data.approvals) {
      const card = el("div", undefined, "approval");
      card.setAttribute("aria-label", "request " + a.id);
      card.dataset.state = a.state;
      card.dataset.id = a.id;
      card.append(el("h3", a.tool + " · " + a.state + (a.state === "pending" ? " · " + a.expires_in_s + " s left" : "")));
      card.append(el("p", "Agent: " + (a.agent_name || a.agent_id) + " (" + a.agent_id + "), requested " + a.created_at));
      const reasons = el("ul");
      for (const x of a.reasons || []) reasons.append(el("li", x));
      card.append(el("p", "Why it needs you:"), reasons);
      card.append(el("p", "Operators it would change (resolved by the server):"));
      card.append(el("pre", (a.targets || []).map((t) => t[0] + "  " + t[1]).join("\n") || "(none)"));
      card.append(el("p", "Complete arguments, exactly as the server stored them (" + String(a.args || "").length +
        " characters, sha256 " + String(a.args_sha256).slice(0, 16) + "…). Read all of it:"));
      card.append(el("pre", a.args || "(no longer kept)"));
      if (a.note) card.append(el("p", "Your note: " + a.note));
      if (a.state === "pending") {
        const row = el("div", undefined, "row");
        const note = el("input"); note.maxLength = 500; note.placeholder = "Note to the agent (optional)";
        const yes = el("button", "Approve " + a.id.slice(-6) + " (" + String(a.args_sha256).slice(0, 8) + ")", "approve"); yes.type = "button";
        const no = el("button", "Deny", "secondary"); no.type = "button";
        yes.addEventListener("click", () => decide(a.id, "approved", note.value, yes, no));
        no.addEventListener("click", () => decide(a.id, "denied", note.value, yes, no));
        row.append(yes, no, note);
        card.append(row);
      }
      box.append(card);
    }
  }
  async function decide(id, decision, note, ...buttons) {
    for (const b of buttons) b.disabled = true;
    const r = await api("POST", "/api/approvals/decide", { id, decision, note });
    $("apr-status").textContent = r.ok ? (decision === "approved" ? "Approved. It runs when the agent repeats the identical call." : "Denied.") : "Not done: " + why(r) + ".";
    refreshApprovals(true);
    refreshOverview();
  }

  // ---- activity: audit search + live tail ------------------------------------------------------------------
  function readFilters() {
    const f = {};
    for (const [k, id] of [["agent", "f-agent"], ["tool", "f-tool"], ["outcome", "f-outcome"], ["q", "f-q"]]) if ($(id).value.trim()) f[k] = $(id).value.trim();
    return f;
  }
  function eventRow(e) {
    const tr = el("tr", undefined, "clickable");
    tr.dataset.seq = e.seq;
    const res = e.kind === "deny" ? "denied" + (e.error_code ? " (" + e.error_code + ")" : "") :
      e.kind === "outcome" ? e.outcome + (e.error_code ? " (" + e.error_code + ")" : "") : e.kind + (e.outcome ? " " + e.outcome : "");
    const cls = (e.kind === "deny" || (e.kind === "outcome" && e.outcome !== "ok")) ? "r-bad" : e.kind === "outcome" ? "r-ok" : "";
    const tg = (e.targets || []).slice(0, 3).map((t) => t[0] + " " + t[1]).join(", ") + ((e.targets || []).length > 3 ? " …" : "");
    tr.append(el("td", e.seq), el("td", e.ts), el("td", e.agent_id || "–"), el("td", e.tool || "–"), el("td", res, cls), el("td", tg));
    tr.addEventListener("click", () => {
      for (const x of document.querySelectorAll("#events tr.selected")) x.classList.remove("selected");
      tr.classList.add("selected");
      $("event-detail").hidden = false;
      $("event-detail").textContent = JSON.stringify(e, null, 2);           // agent text shown as data
    });
    return tr;
  }
  async function refreshEvents(tail) {
    const body = Object.assign({ limit: 100 }, filters);
    if (tail && lastSeq) body.after = lastSeq;
    const r = await api("POST", "/api/audit/search", body);
    if (!r.ok || !r.data) { $("search-status").textContent = "Search failed: " + why(r) + "."; return; }
    const tb = $("events").tBodies[0];
    if (!tail) { tb.replaceChildren(); oldestSeq = null; }
    const rows = r.data.events;
    if (tail) { for (const e of rows.slice().reverse()) tb.prepend(eventRow(e)); }
    else for (const e of rows) tb.append(eventRow(e));
    if (rows.length) { lastSeq = Math.max(lastSeq, rows[0].seq); if (!tail || oldestSeq === null) oldestSeq = rows[rows.length - 1].seq; }
    if (!tail) { lastSeq = Math.max(r.data.last_seq || 0, lastSeq); $("more").hidden = !r.data.more; }
    $("search-status").textContent = tb.rows.length + " event(s) shown.";
  }
  async function olderEvents() {
    if (oldestSeq === null) return;
    const r = await api("POST", "/api/audit/search", Object.assign({ limit: 100, before: oldestSeq }, filters));
    if (!r.ok || !r.data) return;
    for (const e of r.data.events) $("events").tBodies[0].append(eventRow(e));
    if (r.data.events.length) oldestSeq = r.data.events[r.data.events.length - 1].seq;
    $("more").hidden = !r.data.more;
  }

  // ---- sessions, agents, memory ----------------------------------------------------------------------------
  async function refreshSessions() {
    const r = await api("GET", "/api/sessions");
    if (!r.ok || !r.data) return;
    const tb = $("sessions").tBodies[0];
    tb.replaceChildren();
    for (const s of r.data.sessions) {
      const tr = el("tr");
      const b = el("button", "Report", "secondary small"); b.type = "button";
      b.addEventListener("click", () => { $("r-session").value = s.session_id; $("r-from").value = $("r-to").value = ""; showTab("reports"); buildReport(); });
      const td = el("td"); td.append(b);
      tr.append(el("td", s.session_id), el("td", s.agent_id), el("td", s.started), el("td", s.last),
                el("td", s.events + " events · " + s.ok_calls + " ok · " + s.denied + " denied · " + s.notes + " note(s)"), td);
      tb.append(tr);
    }
  }
  async function refreshAgents() {
    const r = await api("GET", "/api/agents");
    if (!r.ok || !r.data) return;
    if (!$("t-scope").value) $("t-scope").value = (r.data.project_root || "/project1") + "/**";
    const tb = $("agents").tBodies[0];
    tb.replaceChildren();
    for (const a of r.data.agents) {
      const tr = el("tr");
      const toks = el("td");
      for (const t of a.tokens) {
        toks.append(el("span", t.token_id + (t.revoked ? " (revoked)" : ""), "tok"));
        if (!t.revoked) {
          const b = el("button", "Revoke", "secondary small"); b.type = "button";
          b.addEventListener("click", async () => { const x = await api("POST", "/api/tokens/revoke", { token_id: t.token_id }); $("token-status").textContent = x.ok ? "Token revoked." : "Not done: " + why(x) + "."; refreshAgents(); });
          toks.append(b);
        }
        toks.append(el("br"));
      }
      const act = el("td");
      const tg = el("button", a.enabled ? "Disable" : "Enable", "secondary small"); tg.type = "button";
      tg.addEventListener("click", async () => { await api("POST", "/api/agents/state", { agent_id: a.id, enabled: !a.enabled }); refreshAgents(); });
      act.append(tg);
      const caps = (a.capabilities || []).includes("memory:repo:write") ? " + repository memory" : "";
      tr.append(el("td", a.display_name), el("td", a.role + caps + (a.enabled ? "" : " (disabled)")), el("td", a.scopes.join(", ")), toks, act);
      tb.append(tr);
    }
  }
  async function createToken(ev) {
    ev.preventDefault();
    const body = { display_name: $("t-name").value, role: $("t-role").value, scope: $("t-scope").value,
                   capabilities: $("t-cap-repo").checked ? ["memory:repo:write"] : [] };
    const r = await api("POST", "/api/tokens", body);
    if (!r.ok) { $("token-status").textContent = "Could not create the token (" + why(r) + ")."; return; }
    $("token-status").textContent = "Token created for " + r.data.agent.display_name + ".";
    $("token-once").hidden = false;
    $("token-value").textContent = r.data.token;
    refreshAgents();
  }
  async function refreshMemory() {
    const r = await api("GET", "/api/memory");
    if (!r.ok || !r.data) return;
    const d = r.data, ready = d.status === "ready";
    setText("m-where", !ready ? "No project memory (" + d.status + ")" : d.authority === "repo" ? "Files in " + d.folder : "Inside the tdflowProject component");
    setText("m-records", ready && d.records !== undefined ? d.records + " record(s), " + d.tasks + " task(s)" : "–");
    setText("m-state", !ready ? d.reason : d.state === "ok" ? "Writable by agents" : d.state === "unavailable" ? "Not usable: " + (d.link_message || d.link) : "Read-only: " + (d.read_only_reason || ""));
  }

  // ---- reports -------------------------------------------------------------------------------------------
  async function buildReport(ev) {
    if (ev) ev.preventDefault();
    const body = {};
    if ($("r-session").value.trim()) body.session = $("r-session").value.trim();
    if ($("r-from").value.trim()) body.from_seq = $("r-from").value.trim();
    if ($("r-to").value.trim()) body.to_seq = $("r-to").value.trim();
    $("report-status").textContent = "Building…";
    const r = await api("POST", "/api/report", body);
    if (!r.ok || !r.data) { $("report-status").textContent = "Not possible: " + why(r) + "."; $("report").hidden = true; $("report-actions").hidden = true; return; }
    lastReport = r.data;
    const v = r.data.report.verification;
    $("report-status").textContent = (v.covered ? "Verified. " : "NOT fully verified. ") + v.statement;
    $("report-status").className = v.covered ? "r-ok" : "r-bad";
    $("report").hidden = false;
    $("report").textContent = r.data.markdown;
    for (const id of ["report-md", "report-json"]) if ($(id).href.startsWith("blob:")) URL.revokeObjectURL($(id).href);
    $("report-md").href = URL.createObjectURL(new Blob([r.data.markdown], { type: "text/markdown" }));
    $("report-json").href = URL.createObjectURL(new Blob([JSON.stringify(r.data.report, null, 2)], { type: "application/json" }));
    $("report-actions").hidden = false;
  }

  // ---- settings ------------------------------------------------------------------------------------------
  async function refreshSettings() {
    const r = await api("GET", "/api/settings");
    if (!r.ok || !r.data) return;
    const d = r.data;
    setText("s-audit", "Always on. " + d.security_audit.why);
    setText("s-diag-what", d.diagnostic_log.what + " File: " + d.diagnostic_log.file + ".");
    $("s-diag").checked = !!d.diagnostic_log.enabled;
    setText("s-loc-inuse", d.data_location.in_use);
    setText("s-loc-status", d.data_location.status);
    setText("s-loc-how", d.data_location.how);
    $("s-loc").value = d.data_location.choice || "project";
  }
  async function setSetting(body) {
    const r = await api("POST", "/api/settings", body);
    $("settings-status").textContent = r.ok ? (body.data_location ? "Requested. " + r.data.changed.data_location.then : "Saved.") : "Not done: " + why(r) + ".";
    refreshSettings();
  }

  // ---- refresh loop ----------------------------------------------------------------------------------------
  async function refreshTab() {
    if (!csrf) return;
    if (tab === "approvals") await refreshApprovals(true);
    else if (tab === "activity") await refreshEvents(false);
    else if (tab === "sessions") await refreshSessions();
    else if (tab === "agents") await refreshAgents();
    else if (tab === "memory") await refreshMemory();
    else if (tab === "settings") await refreshSettings();
  }
  async function refreshAll() { await refreshOverview(); await refreshTab(); $("updated").textContent = new Date().toLocaleTimeString(); }
  let ticks = 0;
  async function tick() {
    if (csrf) {
      await refreshOverview();
      if (tab === "approvals" && ticks % 2 === 0) await refreshApprovals();
      $("updated").textContent = new Date().toLocaleTimeString();
    }
    ticks++;
    setTimeout(tick, POLL_MS);
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("pair").addEventListener("click", requestPairing);
    $("logout").addEventListener("click", async () => { await api("POST", "/setup/api/logout"); unpaired("Unpaired."); });
    $("kill").addEventListener("click", () => kill(true));
    $("resume").addEventListener("click", () => kill(false));
    for (const b of document.querySelectorAll("[role=tab]")) b.addEventListener("click", () => showTab(b.dataset.tab));
    $("search").addEventListener("submit", (ev) => { ev.preventDefault(); filters = readFilters(); lastSeq = 0; refreshEvents(false); });
    $("more").addEventListener("click", olderEvents);
    $("token-form").addEventListener("submit", createToken);
    $("report-form").addEventListener("submit", buildReport);
    $("report-copy").addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(lastReport.markdown); $("report-status").textContent += " Copied."; }
      catch (e) { $("report-status").textContent += " Copying is not available here; select the text instead."; }
    });
    $("s-diag").addEventListener("change", () => setSetting({ diagnostic_log: $("s-diag").checked }));
    $("s-loc-apply").addEventListener("click", () => setSetting({ data_location: $("s-loc").value }));
    tick();
  });
})();
