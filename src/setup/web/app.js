// TDflowMCP Setup Assistant (Phase 1A read-only + Phase 1B pairing and agent tokens).
// External script: the CSP forbids inline script. Server data is rendered with textContent only.
// The admin CSRF token is kept in memory only; a reload of the same tab keeps it (sessionStorage).
"use strict";
(function () {
  const $ = (id) => document.getElementById(id);
  let lastTell = "";
  let csrf = null;
  let projectRoot = "/project1";
  let pairTimer = null;

  function setText(id, v) { $(id).textContent = (v === undefined || v === null || v === "") ? "–" : String(v); }
  function el(tag, text, cls) { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; }

  async function api(method, path, body) {
    const headers = { "X-TDflow-Request": "1" };
    if (csrf) headers["X-TDflow-CSRF"] = csrf;                 // admin reads need it too (cookies span ports)
    if (method !== "GET") headers["Content-Type"] = "application/json";
    const r = await fetch(path, { method, headers, cache: "no-store", credentials: "same-origin",
                                  body: method === "GET" ? undefined : JSON.stringify(body || {}) });
    let data = null;
    try { data = await r.json(); } catch (e) { data = null; }
    return { ok: r.ok, status: r.status, data };
  }

  function renderChecks(checks) {
    const body = $("checks").tBodies[0];
    body.replaceChildren();
    for (const c of checks) {
      const tr = document.createElement("tr");
      tr.dataset.check = c.id;
      tr.dataset.result = c.result;
      tr.append(el("td", c.id), el("td", c.result, "res r-" + c.result), el("td", c.evidence || ""));
      body.append(tr);
    }
  }

  function renderTrouble(checks) {
    const box = $("trouble");
    box.replaceChildren();
    const bad = checks.filter((c) => c.result === "fail" || c.result === "warn");
    if (!bad.length) { box.append(el("p", "No problems reported.", "hint")); return; }
    for (const c of bad) {
      const d = el("div", undefined, "trouble-item" + (c.result === "fail" ? " fail" : ""));
      d.dataset.remediation = c.remediation_id || "";
      d.append(el("h3", (c.title || c.id) + " (" + c.result + ")"));
      for (const [label, text] of [["Cause", c.cause], ["Confirm", c.confirm]]) if (text) d.append(el("p", label + ": " + text));
      if (c.steps && c.steps.length) { const ol = el("ol"); for (const s of c.steps) ol.append(el("li", s)); d.append(ol); }
      for (const href of (c.docs || [])) {
        if (!/^https:\/\//.test(href)) continue;
        const a = el("a", href); a.href = href; a.rel = "noreferrer";
        const p = el("p"); p.append(a); d.append(p);
      }
      box.append(d);
    }
  }

  async function refresh() {
    try {
      const r = await api("GET", "/setup/api/status");
      if (r.status === 503 && r.data && r.data.error === "TDFLOW_NOT_COOKING") {
        // TouchDesigner runs no frames while its window is minimized, so the component cannot answer.
        $("status").dataset.state = "paused";
        setText("message", "TouchDesigner is not running frames right now. If its window is minimized, restore it. Agents get a clear error meanwhile, and nothing is executed.");
        return;
      }
      if (!r.ok) throw new Error("HTTP " + r.status);
      const s = r.data;
      $("status").dataset.state = s.state || "unknown";
      setText("message", s.message);
      setText("state", s.state + (s.error_code ? " (" + s.error_code + ")" : ""));
      setText("version", s.version);
      setText("td-build", s.td_build);
      setText("url", s.url);
      setText("port", s.port);
      setText("portstatus", s.portstatus);
      setText("gate", s.gate === "SERVING" ? "verified: this computer only" : s.gate);
      setText("mcp", s.mcp);
      setText("endpoint", s.endpoint);
      setText("summary", s.summary);
      renderChecks(s.checks || []);
      renderTrouble(s.checks || []);
      lastTell = s.tell_agent || "";
      $("tellagent").textContent = lastTell || "–";
      $("updated").textContent = new Date().toLocaleTimeString();
      document.body.dataset.loaded = "true";
      if (csrf) await refreshAdmin();
    } catch (e) {
      $("status").dataset.state = "unreachable";
      setText("message", "The component is not responding. In TouchDesigner, pulse Open Setup on the TDflowMCP component.");
    }
  }

  // ---- pairing ---------------------------------------------------------------------------------
  async function requestPairing() {
    const r = await api("POST", "/setup/api/pair");
    if (!r.ok) {
      $("pair-status").textContent = r.status === 429 ? "Too many pairing requests. Wait a minute." :
        r.status === 409 ? "Another pairing request is already waiting. Reject it in TouchDesigner (Reject Pairing) or wait two minutes." :
        "Pairing request failed.";
      return;
    }
    $("pair-code").hidden = false;
    $("pair-code").textContent = r.data.match_code;
    $("pair-status").textContent = "In TouchDesigner's Browser sign-in dialog, press the button listed next to the code below. Without the dialog: Web page > Approve Pairing, if it shows the same code.";
    clearInterval(pairTimer);
    pairTimer = setInterval(pollPairing, 1500);
  }

  async function becomePaired(token) {
    clearInterval(pairTimer);
    csrf = token;
    try { sessionStorage.setItem("tdflow_csrf", token); } catch (e) { /* private mode: a reload means signing in again */ }
    $("pair-box").hidden = true;
    $("admin").hidden = false;
    document.body.dataset.paired = "true";
    await refreshAdmin();
  }

  // Opened from TouchDesigner (Open Setup / Open Dashboard): the address carries no secret (owner D22; the rc2
  // ticket on the browser's command line is retired). The page asks to pair at once; TouchDesigner shows the same
  // code in a dialog and the user approves there. Otherwise a still-valid session of this tab is resumed.
  async function signIn() {
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
      if (probe.ok) { csrf = null; await becomePaired(saved); } else { csrf = null; try { sessionStorage.removeItem("tdflow_csrf"); } catch (e) {} }
    }
  }

  async function pollPairing() {
    const r = await api("GET", "/setup/api/pair/status");
    const st = r.data && r.data.state;
    if (st === "approved" && r.data.csrf) {
      await becomePaired(r.data.csrf);
    } else if (st === "rejected" || st === "expired" || st === "unknown") {
      clearInterval(pairTimer);
      $("pair-code").hidden = true;
      $("pair-status").textContent = st === "rejected" ? "Pairing was rejected in TouchDesigner." : "The pairing request expired. Request a new one.";
    }
  }

  // ---- admin -----------------------------------------------------------------------------------
  async function refreshAdmin() {
    const r = await api("GET", "/setup/api/agents");
    if (!r.ok) { if (r.status === 401) unpaired("This browser is no longer paired."); return; }
    const d = r.data;
    projectRoot = d.project_root || projectRoot;
    if (!$("t-scope").value) $("t-scope").value = projectRoot + "/**";
    const body = $("agents").tBodies[0];
    body.replaceChildren();
    for (const a of d.agents) {
      const tr = el("tr");
      tr.dataset.agent = a.id;
      const toks = el("td");
      for (const t of a.tokens) {
        const span = el("span", t.token_id + (t.revoked ? " (revoked)" : ""), "tok");
        toks.append(span);
        if (!t.revoked) {
          const b = el("button", "Revoke", "secondary small");
          b.type = "button";
          b.dataset.token = t.token_id;
          b.addEventListener("click", () => revoke(t.token_id));
          toks.append(b);
        }
      }
      const caps = (a.capabilities || []).includes("memory:repo:write") ? " + repository memory" : "";
      tr.append(el("td", a.display_name), el("td", a.role + caps + (a.enabled ? "" : " (disabled)")), el("td", a.scopes.join(", ")), toks);
      body.append(tr);
    }
    const sb = $("sessions").tBodies[0];
    sb.replaceChildren();
    for (const s of d.sessions) {
      const tr = el("tr");
      tr.append(el("td", s.session_id), el("td", s.agent_id), el("td", s.last), el("td", s.events + " events, " + s.denied + " denied"));
      sb.append(tr);
    }
    const au = d.audit || {};
    $("audit").textContent = "Audit log: " + au.events + " events, chain " + (au.chain_ok ? "intact" : "BROKEN") +
      ", checkpoints " + (au.checkpoints_ok ? "valid" : "INVALID") + ".";
    $("audit").dataset.ok = String(!!au.ok);
    if (!skillsPending) await refreshSkills();
    // The memory status reads every record, so it is refreshed every fifth poll (about 10 s), not every 2 s.
    if (!memPending && (memTick++ % 5 === 0)) await refreshMemory();
  }

  // ---- Project memory (status; preview -> confirm the exact preview digest) --------------------
  let memPending = null;
  let memTick = 0;

  async function refreshMemory() {
    const r = await api("GET", "/setup/api/memory");
    if (!r.ok || !r.data) return;
    const d = r.data;
    const ready = d.status === "ready";
    $("memory").dataset.authority = ready ? d.authority : "none";
    $("memory").dataset.state = ready ? (d.state || "") : d.status;
    setText("mem-authority", !ready ? "No project memory (" + d.status + ")" :
      d.authority === "repo" ? "Files in a folder of this project" : "Inside the tdflowProject component");
    setText("mem-folder", ready && d.authority === "repo" ? d.folder : "–");
    setText("mem-records", ready && d.records !== undefined ? d.records + " record(s), " + d.tasks + " task(s)" : "–");
    setText("mem-state", !ready ? (d.reason || "") : d.state === "ok" ? "Writable by agents" :
      d.state === "unavailable" ? "Not usable: " + (d.link_message || d.link) : "Read-only: " + (d.read_only_reason || "needs Claim Project Memory"));
    $("mem-to-repo").hidden = !(ready && d.authority === "embedded");
    const broken = ready && d.authority === "repo" && d.state === "unavailable";
    $("mem-to-embedded").hidden = !(ready && d.authority === "repo" && !broken);
    $("mem-relink").hidden = !broken;                       // the folder link was lost: offer to link it again
    if (ready && !d.project_saved && !$("mem-status").textContent) $("mem-status").textContent = "Save the TouchDesigner project before linking a folder.";
  }

  async function planMemory(to) {
    const req = to === "repo" ? { to, folder: $("mem-folder-input").value } :
      to === "relink" ? { to, folder: $("mem-relink-input").value } : { to };
    const r = await api("POST", "/setup/api/memory/plan", req);
    if (!r.ok || !r.data) {
      $("mem-status").textContent = "Not possible: " + ((r.data && (r.data.message || r.data.code || r.data.error)) || r.status) + ".";
      return;
    }
    const p = r.data;
    const lines = [to === "repo" ? "Move project memory into " + p.target + ":" :
      to === "relink" ? "Link project memory to " + p.target + " again:" :
      "Move project memory from " + p.source + " back into the component:"];
    if (to !== "embedded") lines.push("  tdflow-project.json  " + p.project_file);
    if (p.also_written && p.also_written.length) lines.push("  also written: " + p.also_written.join(", "));
    for (const a of p.actions) lines.push("  " + a.action.padEnd(18) + a.record + "  sha256 " + a.sha256.slice(0, 12) + "…");
    if (!p.actions.length) lines.push("  (no records yet)");
    if (p.conflicts && p.conflicts.length) lines.push("", "Stopped: the folder already holds different records: " + p.conflicts.join(", "));
    if (p.then) lines.push("", p.then);
    $("mem-plan").textContent = lines.join("\n");
    $("mem-confirm").disabled = !!(p.conflicts && p.conflicts.length);
    memPending = Object.assign({ digest: p.digest }, req);
    $("mem-preview").hidden = false;
    $("mem-status").textContent = "";
  }

  async function confirmMemory() {
    if (!memPending) return;
    const p = memPending;
    memPending = null;
    $("mem-preview").hidden = true;
    const r = await api("POST", "/setup/api/memory/migrate", p);
    $("mem-status").textContent = r.ok ? "Project memory moved: " + r.data.records + " record(s). Save the TouchDesigner project."
      : "Not done: " + ((r.data && (r.data.message || r.data.code || r.data.error)) || r.status) + ".";
    await refreshMemory();
  }

  // ---- Skills (preview -> confirm the exact preview digest) -----------------------------------
  let skillsPending = null;

  async function refreshSkills() {
    const r = await api("GET", "/setup/api/skills");
    if (!r.ok || !r.data) return;
    const body = $("skills").tBodies[0];
    body.replaceChildren();
    for (const t of r.data.targets) {
      const tr = el("tr");
      tr.dataset.client = t.client;
      tr.dataset.scope = t.scope;
      const act = el("td");
      if (t.error) {
        act.textContent = t.error === "PROJECT_UNSAVED" ? "Save the project first" : t.error;
      } else {
        const inst = el("button", t.installed_version ? (t.mismatch ? "Update" : "Reinstall") : "Install", "small");
        inst.type = "button";
        inst.addEventListener("click", () => showPlan(t, "install"));
        act.append(inst);
        if (t.installed_version) {
          const un = el("button", "Uninstall", "secondary small");
          un.type = "button";
          un.addEventListener("click", () => showPlan(t, "uninstall"));
          act.append(un);
        }
      }
      const status = t.error ? "–" : (t.installed_version || "not installed") + (t.mismatch ? " (differs from bundled)" : "");
      tr.append(el("td", t.client), el("td", (t.target || t.scope) + (t.in_git_repo ? " (inside a Git repository)" : "")),
                el("td", status, t.mismatch ? "warn" : ""), el("td", r.data.bundled_version), act);
      body.append(tr);
    }
  }

  function showPlan(t, op) {
    const plan = t[op];
    const lines = [(op === "install" ? "Install / update " : "Uninstall ") + "TDflowMCP Skills " + t.bundled_version +
                   " for " + t.client + " in " + t.target + ":"];
    for (const a of plan.actions) lines.push("  " + a.action.padEnd(14) + a.path + "  sha256 " + a.sha256.slice(0, 12) + "…");
    if (plan.conflicts && plan.conflicts.length) lines.push("", "Stopped: these files were changed by someone else and will not be overwritten: " + plan.conflicts.join(", "));
    if (t.in_git_repo) lines.push("", "Note: this folder is inside a Git repository, so the files will appear in git status.");
    $("skills-plan").textContent = lines.join("\n");
    $("skills-confirm").disabled = !!(plan.conflicts && plan.conflicts.length);
    skillsPending = { op, client: t.client, scope: t.scope, digest: plan.digest };
    $("skills-preview").hidden = false;
  }

  async function confirmSkills() {
    if (!skillsPending) return;
    const p = skillsPending;
    skillsPending = null;
    $("skills-preview").hidden = true;
    const r = await api("POST", "/setup/api/skills/" + p.op, { client: p.client, scope: p.scope, digest: p.digest });
    $("skills-status").textContent = r.ok ? (p.op === "install" ? "Skills installed: " + (r.data.written || []).length + " file(s) written."
                                                                : "Skills removed. Kept (changed by you): " + ((r.data.kept_modified || []).join(", ") || "none") + ".")
                                         : "Not done: " + ((r.data && (r.data.message || r.data.code || r.data.error)) || r.status) + ".";
    await refreshSkills();
  }

  function unpaired(msg) {
    csrf = null;
    try { sessionStorage.removeItem("tdflow_csrf"); } catch (e) { /* nothing stored */ }
    $("admin").hidden = true;
    $("pair-box").hidden = false;
    $("pair-code").hidden = true;
    $("pair-status").textContent = msg || "";
    document.body.dataset.paired = "false";
  }

  async function createToken(ev) {
    ev.preventDefault();
    const actions = Array.from($("t-actions").querySelectorAll("input:checked")).map((i) => i.value);
    const capabilities = $("t-cap-repo").checked ? ["memory:repo:write"] : [];
    const r = await api("POST", "/setup/api/tokens", { display_name: $("t-name").value, role: $("t-role").value,
                                                       scope: $("t-scope").value, actions, capabilities });
    if (!r.ok) { $("token-status").textContent = "Could not create the token (" + ((r.data && r.data.error) || r.status) + ")."; return; }
    $("token-status").textContent = "Token created for " + r.data.agent.display_name + ".";
    $("token-once").hidden = false;
    $("token-value").textContent = r.data.token;
    await refreshAdmin();
  }

  async function revoke(tokenId) {
    const r = await api("POST", "/setup/api/tokens/revoke", { token_id: tokenId });
    $("token-status").textContent = r.ok ? "Token " + tokenId + " revoked." : "Could not revoke the token.";
    await refreshAdmin();
  }

  async function copyText(text, statusId) {
    const status = $(statusId);
    try {
      if (!navigator.clipboard || !navigator.clipboard.writeText) throw new Error("no clipboard API");
      await navigator.clipboard.writeText(text);
      status.textContent = "Copied.";
      status.dataset.result = "ok";
    } catch (e) {
      status.textContent = "Copying is not available in this browser. Select the text and press Ctrl+C (Cmd+C on macOS).";
      status.dataset.result = "failed";
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("copy").addEventListener("click", () => copyText(lastTell, "copy-status"));
    $("pair").addEventListener("click", requestPairing);
    $("logout").addEventListener("click", async () => { await api("POST", "/setup/api/logout"); unpaired("Unpaired."); });
    $("token-form").addEventListener("submit", createToken);
    $("copy-token").addEventListener("click", () => copyText($("token-value").textContent, "copy-token-status"));
    $("skills-confirm").addEventListener("click", confirmSkills);
    $("skills-cancel").addEventListener("click", () => { skillsPending = null; $("skills-preview").hidden = true; });
    $("mem-plan-repo").addEventListener("click", () => planMemory("repo"));
    $("mem-plan-embedded").addEventListener("click", () => planMemory("embedded"));
    $("mem-plan-relink").addEventListener("click", () => planMemory("relink"));
    $("mem-confirm").addEventListener("click", confirmMemory);
    $("mem-cancel").addEventListener("click", () => { memPending = null; $("mem-preview").hidden = true; });
    signIn();
    refresh();
    setInterval(refresh, 2000);
  });
})();
