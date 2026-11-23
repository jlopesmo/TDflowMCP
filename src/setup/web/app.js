// TDflowMCP Setup Assistant (Phase 1A read-only + Phase 1B pairing and agent tokens).
// External script: the CSP forbids inline script. Server data is rendered with textContent only.
// The admin CSRF token is kept in memory only (a reload means pairing again).
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
      $("admin").hidden = false;
      document.body.dataset.paired = "true";
      await refreshAdmin();
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
      tr.append(el("td", a.display_name), el("td", a.role + (a.enabled ? "" : " (disabled)")), el("td", a.scopes.join(", ")), toks);
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
    $("admin").hidden = true;
    $("pair-box").hidden = false;
    $("pair-code").hidden = true;
    $("pair-status").textContent = msg || "";
    document.body.dataset.paired = "false";
  }

  async function createToken(ev) {
    ev.preventDefault();
    const actions = Array.from($("t-actions").querySelectorAll("input:checked")).map((i) => i.value);
    const r = await api("POST", "/setup/api/tokens", { display_name: $("t-name").value, role: $("t-role").value,
                                                       scope: $("t-scope").value, actions });
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
    refresh();
    setInterval(refresh, 2000);
  });
})();
