// TDflowMCP governance dashboard (Phase 2B, ADR-0005). A view and decision surface only: the component
// enforces every rule. External script (the CSP forbids inline script). Server data is rendered with
// textContent only. The CSRF token is kept in this tab's sessionStorage; a reload keeps the session while it is valid.
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
    $("pair-status").textContent = "In TouchDesigner's Browser sign-in dialog, press the button listed next to the code below. Without the dialog: Web page > Approve Pairing, if it shows the same code.";
    clearInterval(pairTimer);
    pairTimer = setInterval(pollPairing, 1500);
  }
  let startTab = null;
  function becomePaired(token) {
    clearInterval(pairTimer);
    csrf = token;
    try { sessionStorage.setItem("tdflow_csrf", token); } catch (e) { /* private mode: a reload means signing in again */ }
    $("pair-box").hidden = true;
    $("app").hidden = false;
    document.body.dataset.paired = "true";
    if (startTab) { const t = startTab; startTab = null; showTab(t); }
    refreshAll();
  }

  // Opened from TouchDesigner (Open Dashboard): the address carries no secret (owner D22). The page asks to pair at
  // once; TouchDesigner shows the same code in a dialog and the user approves there. Otherwise a still-valid session
  // of this tab is resumed.
  async function signIn() {
    // A link such as /ui/#capabilities (an agent may give it to the user) opens that tab after sign-in. It carries
    // no secret; signing in still needs the pairing approved in TouchDesigner.
    const m = /^#([a-z]+)$/.exec(location.hash);
    if (m && m[1] !== "pair" && document.querySelector('[role=tab][data-tab="' + m[1] + '"]')) {
      startTab = m[1];
      $("pair-status").textContent = "Pair this browser to open the " + m[1] + " page.";
    }
    if (location.hash === "#pair") {
      history.replaceState(null, "", location.pathname);
      await requestPairing();
      return;
    }
    // A reload of this tab keeps its session: the CSRF token is kept in this tab's sessionStorage (never in a
    // cookie or the URL), and the session cookie must still be valid (a 401 below ends it).
    let saved = null;
    try { saved = sessionStorage.getItem("tdflow_csrf"); } catch (e) { saved = null; }
    if (saved) {
      csrf = saved;
      const probe = await api("GET", "/setup/api/agents");
      if (probe.ok) { csrf = null; becomePaired(saved); } else { csrf = null; try { sessionStorage.removeItem("tdflow_csrf"); } catch (e) {} }
    }
  }

  async function pollPairing() {
    const r = await api("GET", "/setup/api/pair/status");
    const st = r.data && r.data.state;
    if (st === "approved" && r.data.csrf) {
      becomePaired(r.data.csrf);
    } else if (st === "rejected" || st === "expired" || st === "unknown") {
      clearInterval(pairTimer);
      $("pair-code").hidden = true;
      $("pair-status").textContent = st === "rejected" ? "Pairing was rejected in TouchDesigner." : "The pairing request expired. Request a new one.";
    }
  }
  function unpaired(msg) {
    csrf = null;
    try { sessionStorage.removeItem("tdflow_csrf"); } catch (e) { /* nothing stored */ }
    $("app").hidden = true;
    $("pair-box").hidden = false;
    $("pair-code").hidden = true;
    $("pair-status").textContent = msg || "";
    $("kill").hidden = $("resume").hidden = true;
    document.body.dataset.paired = "false";
  }

  // ---- tabs ----------------------------------------------------------------------------------------------
  function showTab(name) {
    if (!document.querySelector('[role=tab][data-tab="' + name + '"]')) name = "overview";
    tab = name;
    document.body.dataset.tab = name;
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
      card.append(el("pre", (a.targets || []).filter((t) => ["read", "create", "update", "delete", "execute"].includes(t[0]))
        .map((t) => t[0] + "  " + t[1]).join("\n") || "(none)"));
      card.append(el("p", "Complete arguments, exactly as the server stored them (" + String(a.args || "").length +
        " characters, sha256 " + String(a.args_sha256).slice(0, 16) + "…). Read all of it:"));
      card.append(el("pre", a.args || "(no longer kept)"));
      if (a.review) {
        const settings = el("details");
        settings.append(el("summary", "Current callback settings"), el("pre",
          JSON.stringify({ bypass: a.review.bypass, ...a.review.settings }, null, 2)));
        card.append(el("p", "Current active parameter · " + JSON.stringify(a.review.active)), settings);
        card.append(el("p", "Callback source"), el("pre", a.review.callback_source));
      } else if (a.review_invalid) {
        card.append(el("p", "The callback preview could not be verified. Request a fresh approval."));
      }
      if (a.note) card.append(el("p", "Your note: " + a.note));
      if (a.state === "pending") {
        const row = el("div", undefined, "row");
        const note = el("input"); note.maxLength = 500; note.placeholder = "Note to the agent (optional)";
        const yes = el("button", "Approve " + a.id.slice(-6) + " (" + String(a.args_sha256).slice(0, 8) + ")", "approve"); yes.type = "button";
        const no = el("button", "Deny", "secondary"); no.type = "button";
        yes.disabled = a.review_invalid;
        yes.addEventListener("click", () => decide(a, "approved", note.value, yes, no));
        no.addEventListener("click", () => decide(a, "denied", note.value, yes, no));
        row.append(yes, no, note);
        card.append(row);
      }
      box.append(card);
    }
  }
  async function decide(approval, decision, note, ...buttons) {
    for (const b of buttons) b.disabled = true;
    const r = await api("POST", "/api/approvals/decide", {
      id: approval.id, decision, note, args_sha256: approval.args_sha256, review_sha256: approval.review_sha256
    });
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
      const rot = el("button", "New token", "secondary small"); rot.type = "button";      // parity with TouchDesigner
      rot.addEventListener("click", async () => {
        const x = await api("POST", "/api/agents/rotate", { agent_id: a.id });
        if (!x.ok || !x.data || !x.data.token) { $("token-status").textContent = "Not done: " + why(x) + "."; return; }
        $("token-status").textContent = "New token for " + a.display_name + "; its older tokens were revoked.";
        $("token-once").hidden = false;
        $("token-value").textContent = x.data.token;
        refreshAgents();
      });
      act.append(rot);
      const cv = el("button", "Capabilities", "secondary small"); cv.type = "button";
      cv.addEventListener("click", () => { capAgent = a.id; draft = null; showTab("capabilities"); });
      act.append(cv);
      const caps = (a.capabilities || []).includes("memory:repo:write") ? " + repository memory" : "";
      const perms = el("td");
      for (const g of a.grants || []) {
        const line = el("div", (g.effect === "deny" ? "DENY " : "") + g.actions.join(",") + " on " + g.scope);
        const rm = el("button", "Remove", "secondary small"); rm.type = "button";
        rm.addEventListener("click", async () => {
          const x = await api("POST", "/api/grants/remove", { agent_id: a.id, grant_id: g.id });
          $("token-status").textContent = x.ok ? "Permission removed." : "Not done: " + why(x) + ".";
          refreshAgents();
        });
        line.append(rm);
        perms.append(line);
      }
      const add = el("div", undefined, "row");
      const scope = el("input"); scope.maxLength = 256; scope.placeholder = (r.data.project_root || "/project1") + "/fx/**";
      const acts = el("input"); acts.maxLength = 60; acts.placeholder = "read,update";
      const eff = el("select"); for (const v of ["allow", "deny"]) { const o = el("option", v); o.value = v; eff.append(o); }
      const go = el("button", "Add permission", "secondary small"); go.type = "button";
      go.addEventListener("click", async () => {
        // Owner D25: the same preview and explicit confirmation as the Capabilities tab (more access is confirmed).
        await changeViaPreview(a.id, { add_grants: [{ scope: scope.value.trim(), effect: eff.value,
          actions: acts.value.split(",").map((v) => v.trim()).filter(Boolean) }] }, $("token-status"));
        refreshAgents();
      });
      add.append(scope, acts, eff, go);
      perms.append(add);
      const seen = (a.last_seen_s === null || a.last_seen_s === undefined) ? "not seen yet" :
        (a.connected ? "connected (" + a.last_seen_s + " s ago)" : "last request " + a.last_seen_s + " s ago");
      tr.append(el("td", a.display_name), el("td", a.role + caps + (a.enabled ? "" : " (disabled)")), el("td", seen),
                perms, toks, act);
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

  // ---- capabilities (owner D25): one matrix; changes go through preview, confirmation and the one backend ----------
  const STATUS_CLASS = { available: "st-ok", approval: "st-apr", denied: "st-deny", not_implemented: "st-ni", protected: "st-prot" };
  let caps = null, capAgent = null, draft = null, lastPreview = null;
  function chip(status, label) {
    const s = el("span", label || (caps && caps.legend[status]) || status, "chip " + (STATUS_CLASS[status] || ""));
    s.dataset.status = status;
    return s;
  }
  function freshDraft(a) { return { role: a.role, remove: new Set(), add: [], repo: (a.capabilities || []).includes("memory:repo:write") }; }
  function draftChange(a) {
    const ch = {};
    if (draft.role !== a.role) ch.role = draft.role;
    if (draft.remove.size) ch.remove_grants = Array.from(draft.remove);
    if (draft.add.length) ch.add_grants = draft.add.map((g) => ({ scope: g.scope, actions: g.actions, effect: g.effect }));
    const had = (a.capabilities || []).includes("memory:repo:write");
    if (draft.repo !== had) ch.capabilities = { "memory:repo:write": draft.repo };
    return ch;
  }
  async function refreshCapabilities(keepDraft) {
    const r = await api("GET", "/api/capabilities");
    if (!r.ok || !r.data) { $("c-state").textContent = "Not available: " + why(r) + "."; return; }
    caps = r.data;
    const sel = $("c-agent");
    const want = capAgent || (sel.value) || (caps.agents[0] && caps.agents[0].id);
    sel.replaceChildren();
    for (const a of caps.agents) {
      const o = el("option", a.display_name + " · " + (caps.roles.find((x) => x.id === a.role) || {}).label + (a.enabled ? "" : " (disabled)"));
      o.value = a.id; sel.append(o);
    }
    if (!caps.agents.length) sel.append(Object.assign(el("option", "(no agents yet)"), { value: "" }));
    sel.value = caps.agents.some((a) => a.id === want) ? want : (caps.agents[0] ? caps.agents[0].id : "");
    capAgent = sel.value || null;
    const st = caps.state;
    $("c-state").textContent = (st.mcp ? "MCP on" : "MCP off") + (st.suspended ? " · all agents suspended" : "") +
      " · Code Mode " + (st.code_mode ? "on" : "off");
    // Review 22: a state that stops every agent is said plainly above the table, not only in grey text.
    $("c-banner").hidden = st.mcp && !st.suspended;
    $("c-banner").textContent = st.suspended ? "All agents are suspended: nothing in this table runs until you resume them." :
      !st.mcp ? "MCP is off: no agent can connect, so nothing in this table runs until you turn on Enable MCP." : "";
    const lg = $("c-legend"); lg.replaceChildren();
    const defs = { available: "can do it now, inside its scopes", approval: "may ask; each use waits for your approval",
                   denied: "not allowed now (role, scopes or a setting): you can change this",
                   not_implemented: "TDflowMCP has no such feature", protected: "blocked for every agent; nothing can enable it" };
    for (const s of ["available", "approval", "denied", "not_implemented", "protected"]) { const li = el("li"); li.append(chip(s), el("span", " " + defs[s], "def")); lg.append(li); }
    const a = currentAgent();
    if (!keepDraft || !draft || !a || draft.agent !== a.id) { draft = a ? Object.assign(freshDraft(a), { agent: a.id }) : null; lastPreview = null; $("c-result").hidden = true; }
    renderMatrix();
    renderEditor();
  }
  function currentAgent() { return caps && caps.agents.find((x) => x.id === capAgent) || null; }
  function renderMatrix() {
    const tb = $("caps").tBodies[0];
    tb.replaceChildren();
    const a = currentAgent();
    $("c-agent-col").textContent = a ? a.display_name : "Selected agent";
    const sel = document.querySelector("#caps tr.selected");
    const focusCap = (sel && sel.dataset.cap) || null;
    let first = true;
    const proposed = lastPreview && lastPreview.result ? lastPreview.result.matrix : null;
    const fixed = { protected: $("c-protected"), future: $("c-future") };
    fixed.protected.replaceChildren(); fixed.future.replaceChildren();
    const groups = caps.groups.filter((g) => !(g.id in fixed));
    for (const g of groups) {
      const rows = caps.catalog.filter((c) => c.group === g.id);
      rows.forEach((c, i) => {
        const tr = el("tr", undefined, "caprow");
        tr.tabIndex = (focusCap ? c.id === focusCap : first) ? 0 : -1;       // roving tabindex: one stop in the tab order
        first = false;
        tr.dataset.cap = c.id;
        if (i === 0) { const gh = el("th", g.title, "grp"); gh.scope = "rowgroup"; gh.rowSpan = rows.length; tr.append(gh); }
        const th = el("th", c.title); th.scope = "row"; tr.append(th);
        for (const col of [c.engine].concat(caps.roles.map((r) => c.roles[r.id]))) {
          const td = el("td"); td.append(chip(col.status)); td.title = col.why || "";
          if (col === c.engine && c.setting === "code_mode" && !caps.state.code_mode) td.append(el("span", " (off)", "where"));
          tr.append(td);
        }
        const td = el("td", undefined, "agentcell");
        if (a) {
          const m = a.matrix[c.id];
          td.append(chip(m.status));
          if (m.where && m.where.length && m.status !== "denied") td.append(el("span", " " + m.where.join(", "), "where"));
          if (m.status === "denied") td.append(el("span", " · " + reasonTag(m), "where"));
          td.title = m.why || "";
          const pn = proposed && proposed[c.id];
          if (pn && (pn.status !== m.status || (pn.status !== "denied" && String(pn.where) !== String(m.where)))) {
            const n = proposed[c.id];
            td.append(el("span", " → ", "arrow"), chip(n.status));
            if (n.where && n.where.length && n.status !== "denied") td.append(el("span", " " + n.where.join(", "), "where"));
            tr.classList.add("changed");
          }
        } else td.append(el("span", "–", "hint"));
        tr.append(td);
        const show = () => showCapDetail(c, a);
        tr.addEventListener("click", show);
        tr.addEventListener("keydown", (ev) => {
          if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); show(); }
          else if (ev.key === "ArrowDown" || ev.key === "ArrowUp") {
            ev.preventDefault();
            const all = Array.from(document.querySelectorAll("#caps tr.caprow"));
            const next = all[all.indexOf(tr) + (ev.key === "ArrowDown" ? 1 : -1)];
            if (next) { for (const x of all) x.tabIndex = -1; next.tabIndex = 0; next.focus(); }
          }
        });
        tb.append(tr);
      });
    }
    for (const key of Object.keys(fixed)) {
      for (const c of caps.catalog.filter((x) => x.group === key)) {
        const li = el("li");
        li.append(chip(c.engine.status), el("span", " " + c.title, "fixed-title"));
        if (c.engine.why) li.append(el("span", " " + c.engine.why, "hint"));
        fixed[key].append(li);
      }
    }
  }
  function reasonTag(m) {
    const f = m.fix || "";
    return f.startsWith("role:") ? "role" + (f.includes("setting:code_mode") ? " + Code Mode" : "") : f.startsWith("grant:") ? "no scope" :
      f === "setting:code_mode" ? "Code Mode off" : f.startsWith("capability:") ? "needs permission" : f === "enable" ? "agent disabled" : "policy";
  }
  function showCapDetail(c, a) {
    for (const x of document.querySelectorAll("#caps tr.selected")) x.classList.remove("selected");
    const tr = document.querySelector('#caps tr[data-cap="' + c.id + '"]');
    if (tr) tr.classList.add("selected");
    const m = a ? a.matrix[c.id] : null;
    const parts = [c.title + "."];
    if (c.tools.length) parts.push("Tools: " + c.tools.join(", ") + ".");
    if (c.needs.length) parts.push("Needs " + c.needs.join(" + ") + " on a scope" + (c.approval ? ", and your approval of each call." : "."));
    if (m) {
      parts.push((a.display_name + ": " + caps.legend[m.status]) + (m.where && m.where.length ? " on " + m.where.join(", ") : "") +
        (m.except && m.except.length ? " (except " + m.except.join(", ") + ")" : "") + ". " + (m.why || ""));
      const fix = { "role:operator": "Change its role to Operator to allow it.", "role:builder": "Change its role to Builder to allow it.",
                    "role:operator+setting:code_mode": "Change its role to Operator and turn on Code Mode in Settings.",
                    "capability:memory:repo:write": "Tick the repository-memory permission to allow it.",
                    "setting:code_mode": "Turn on Code Mode in Settings (each script still needs your approval).",
                    enable: "Enable the agent on the Agents tab." }[m.fix] || (m.fix && m.fix.startsWith("grant:") ? "Add a scope with " + m.fix.slice(6).replace(/,/g, " + ") + "." : "");
      if (fix) parts.push(fix);
    }
    $("c-detail").textContent = parts.join(" ");
  }
  function renderEditor() {
    const a = currentAgent();
    const form = $("c-form");
    for (const x of form.querySelectorAll("input, select, button")) x.disabled = !a;
    if (!a) { $("c-edit-who").textContent = "No agent selected."; return; }
    $("c-edit-who").textContent = a.display_name + " (" + a.id + ")" + (a.enabled ? "" : ", disabled") + ". Changes take effect at once and cancel this agent's open approvals.";
    const role = $("c-role"); role.replaceChildren();
    for (const r of caps.roles) { const o = el("option", r.label); o.value = r.id; role.append(o); }
    role.value = draft.role;
    const ra = caps.roles.find((r) => r.id === draft.role);
    $("c-role-acts").textContent = ra ? "Actions: " + ra.actions.join(", ") + (draft.role === "operator" ? " (code-running changes wait for your approval)" : "") : "";
    const ul = $("c-grants"); ul.replaceChildren();
    for (const g of a.grants) {
      const li = el("li", undefined, draft.remove.has(g.id) ? "removed" : "");
      li.append(el("span", (g.effect === "deny" ? "DENY " : "") + g.actions.join(", ") + " on " + g.scope));
      const b = el("button", draft.remove.has(g.id) ? "Keep" : "Remove", "secondary small"); b.type = "button";
      b.setAttribute("aria-label", (draft.remove.has(g.id) ? "Keep " : "Remove ") + g.scope);
      b.addEventListener("click", () => { draft.remove.has(g.id) ? draft.remove.delete(g.id) : draft.remove.add(g.id); invalidatePreview(); renderEditor(); });
      li.append(b); ul.append(li);
    }
    draft.add.forEach((g, i) => {
      const li = el("li", undefined, "added");
      li.append(el("span", "new: " + (g.effect === "deny" ? "DENY " : "") + g.actions.join(", ") + " on " + g.scope));
      const b = el("button", "Drop", "secondary small"); b.type = "button";
      b.addEventListener("click", () => { draft.add.splice(i, 1); invalidatePreview(); renderEditor(); });
      li.append(b); ul.append(li);
    });
    if (!ul.children.length) ul.append(el("li", "No scopes: the agent only sees its identity tools.", "hint"));
    const acts = $("c-acts");
    if (!acts.children.length) {
      for (const x of caps.actions) {
        const lab = el("label", undefined, "check"); const cb = el("input"); cb.type = "checkbox"; cb.value = x; cb.checked = x === "read";
        lab.append(cb, document.createTextNode(" " + x)); acts.append(lab);
      }
    }
    if (!$("c-scope").value) $("c-scope").value = (caps.project_root || "/project1") + "/**";
    $("c-cap-repo").checked = draft.repo;
  }
  function invalidatePreview() { lastPreview = null; $("c-result").hidden = true; $("c-status").textContent = ""; renderMatrix(); }
  async function previewChange(ev) {
    if (ev) ev.preventDefault();
    const a = currentAgent();
    if (!a) return;
    const change = draftChange(a);
    if (!Object.keys(change).length) {
      const ticked = Array.from($("c-acts").querySelectorAll("input:checked")).map((x) => x.value);
      const pending = $("c-scope").value.trim() !== (caps.project_root || "/project1") + "/**" || ticked.join() !== "read";
      $("c-status").textContent = pending ? "Nothing to change yet. Click Add scope to include " + $("c-scope").value.trim() + " (" +
        (ticked.join(", ") || "no action") + ") in the change." : "Nothing to change yet.";
      return;
    }
    const r = await api("POST", "/api/permissions/preview", { agent_id: a.id, change });
    if (!r.ok || !r.data) { lastPreview = null; $("c-result").hidden = true; $("c-status").textContent = "Not possible: " + why(r) + "."; renderMatrix(); return; }
    lastPreview = Object.assign(r.data, { change });
    const d = r.data, diff = $("c-diff"), notes = $("c-notes");
    diff.replaceChildren(); notes.replaceChildren();
    for (const x of d.diff) {
      const li = el("li", undefined, x.escalation ? "up" : "");
      li.append(el("span", x.title + ": "), chip(x.from), el("span", " → "), chip(x.to));
      if (x.to_where && x.to_where.length) li.append(el("span", " " + x.to_where.join(", "), "where"));
      diff.append(li);
    }
    if (!d.diff.length) diff.append(el("li", d.changes_anything ? "No capability changes status (scopes or grants change only)." : "This changes nothing.", "hint"));
    for (const n of d.notes || []) notes.append(el("li", n));
    // Review 22: the scopes after the change, with the actions it adds or drops (a role change rewrites them).
    const before = {};
    for (const g of a.grants) before[g.effect + " " + g.scope] = g.actions;
    const sc = $("c-scopes-after"); sc.replaceChildren();
    for (const g of d.result.grants) {
      const old = before[g.effect + " " + g.scope] || [];
      const plus = g.actions.filter((x) => !old.includes(x)), minus = old.filter((x) => !g.actions.includes(x));
      sc.append(el("li", (g.effect === "deny" ? "DENY " : "") + g.actions.join(", ") + " on " + g.scope +
        (plus.length ? "  (+ " + plus.join(", ") + ")" : "") + (minus.length ? "  (- " + minus.join(", ") + ")" : ""), plus.length ? "up" : ""));
    }
    if (!d.result.grants.length) sc.append(el("li", "No scopes", "hint"));
    $("c-escalation").hidden = !d.escalation;
    $("c-escalation").textContent = d.escalation ? "This gives the agent more access: " + d.escalation_reasons.join("; ") + "." : "";
    $("c-confirm-wrap").hidden = !d.escalation;
    $("c-confirm").checked = false;
    $("c-apply").disabled = !d.changes_anything || d.escalation;
    $("c-result").hidden = false;
    $("c-status").textContent = "Preview only: nothing has changed yet.";
    renderMatrix();
    $("c-apply").scrollIntoView({ block: "nearest" });          // the decision (confirm, Apply) stays on screen
  }
  async function applyChange() {
    const a = currentAgent();
    if (!a || !lastPreview) return;
    $("c-apply").disabled = true;
    const r = await api("POST", "/api/permissions/apply", { agent_id: a.id, change: lastPreview.change, digest: lastPreview.digest,
                                                            confirm_escalation: $("c-confirm").checked });
    $("c-status").textContent = r.ok ? "Applied. " + (r.data.approvals_cancelled ? r.data.approvals_cancelled + " open approval(s) of this agent were cancelled." : "No open approvals needed cancelling.")
      : "Not done: " + why(r) + ".";
    lastPreview = null; $("c-result").hidden = true;
    if (r.ok) draft = null;
    await refreshCapabilities(!r.ok);
  }
  // The Agents tab's "Add permission" goes through the same preview and confirmation.
  async function changeViaPreview(agentId, change, statusEl) {
    const p = await api("POST", "/api/permissions/preview", { agent_id: agentId, change });
    if (!p.ok || !p.data) { statusEl.textContent = "Not done: " + why(p) + "."; return false; }
    if (!p.data.changes_anything) { statusEl.textContent = "This changes nothing."; return false; }
    let confirmed = false;
    if (p.data.escalation) {
      const lines = p.data.diff.filter((x) => x.escalation).slice(0, 12).map((x) => "- " + x.title + ": " + caps_label(x.from) + " -> " + caps_label(x.to));
      confirmed = window.confirm("This gives the agent more access (" + p.data.escalation_reasons.join("; ") + "):\n" + lines.join("\n") + "\n\nApply it?");
      if (!confirmed) { statusEl.textContent = "Not applied."; return false; }
    }
    const r = await api("POST", "/api/permissions/apply", { agent_id: agentId, change, digest: p.data.digest, confirm_escalation: confirmed });
    statusEl.textContent = r.ok ? "Permission changed. Open approvals of this agent were cancelled." : "Not done: " + why(r) + ".";
    return r.ok;
  }
  function caps_label(s) { return ({ available: "Available", approval: "Approval Required", denied: "Denied", not_implemented: "Not Implemented", protected: "Protected" })[s] || s; }

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
    if (d.code_mode) { setText("s-cm-what", d.code_mode.what); $("s-cm").checked = !!d.code_mode.enabled; }
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
    else if (tab === "capabilities") await refreshCapabilities(false);
    else if (tab === "memory") await refreshMemory();
    else if (tab === "settings") await refreshSettings();
  }
  async function refreshAll() { await refreshOverview(); await refreshTab(); $("updated").textContent = new Date().toLocaleTimeString(); }
  let ticks = 0;
  async function tick() {
    if (csrf) {
      await refreshOverview();
      if (tab === "approvals" && ticks % 2 === 0) await refreshApprovals();
      // Native/web parity: changes made in TouchDesigner appear here, but never under an unsaved draft or the focus.
      if (tab === "capabilities" && ticks % 5 === 0 && !lastPreview && !document.querySelector(".caps-edit").contains(document.activeElement)
          && (!draft || (!draft.remove.size && !draft.add.length && currentAgent() && draft.role === currentAgent().role))) await refreshCapabilities(true);
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
    $("c-agent").addEventListener("change", () => {
      const a = currentAgent();
      if (a && draft && Object.keys(draftChange(a)).length && !window.confirm("Discard the unsaved change for " + a.display_name + "?")) {
        $("c-agent").value = a.id; return;
      }
      capAgent = $("c-agent").value; draft = null; refreshCapabilities(false);
    });
    $("c-jump").addEventListener("click", () => { $("c-edit-title").scrollIntoView({ block: "start" }); $("c-role").focus(); });
    $("c-scope").addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); $("c-add").click(); } });
    $("c-form").addEventListener("submit", previewChange);
    $("c-role").addEventListener("change", () => { if (draft) { draft.role = $("c-role").value; invalidatePreview(); } });
    $("c-cap-repo").addEventListener("change", () => { if (draft) { draft.repo = $("c-cap-repo").checked; invalidatePreview(); } });
    $("c-add").addEventListener("click", () => {
      if (!draft) return;
      const acts = Array.from($("c-acts").querySelectorAll("input:checked")).map((x) => x.value);
      const scope = $("c-scope").value.trim();
      if (!scope || !acts.length) { $("c-status").textContent = "A scope and at least one action are needed."; return; }
      draft.add.push({ scope, actions: acts, effect: $("c-effect").value });
      invalidatePreview(); renderEditor();
    });
    $("c-reset").addEventListener("click", () => { const a = currentAgent(); if (a) { draft = Object.assign(freshDraft(a), { agent: a.id }); invalidatePreview(); renderEditor(); } });
    $("c-confirm").addEventListener("change", () => { $("c-apply").disabled = !(lastPreview && lastPreview.changes_anything && $("c-confirm").checked); });
    $("c-apply").addEventListener("click", applyChange);
    $("report-form").addEventListener("submit", buildReport);
    $("report-copy").addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(lastReport.markdown); $("report-status").textContent += " Copied."; }
      catch (e) { $("report-status").textContent += " Copying is not available here; select the text instead."; }
    });
    $("s-diag").addEventListener("change", () => setSetting({ diagnostic_log: $("s-diag").checked }));
    $("s-cm").addEventListener("change", () => setSetting({ code_mode: $("s-cm").checked }));
    $("s-loc-apply").addEventListener("click", () => setSetting({ data_location: $("s-loc").value }));
    signIn();
    tick();
  });
})();
