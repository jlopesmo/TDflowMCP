// TDflowMCP Setup Assistant (Phase 1A, read-only). External script: the CSP forbids inline script.
// Everything is rendered with textContent; server data is never parsed as HTML.
"use strict";
(function () {
  const $ = (id) => document.getElementById(id);
  let lastTell = "";

  function setText(id, v) { $(id).textContent = (v === undefined || v === null || v === "") ? "–" : String(v); }

  function renderChecks(checks) {
    const body = $("checks").tBodies[0];
    body.replaceChildren();
    for (const c of checks) {
      const tr = document.createElement("tr");
      tr.dataset.check = c.id;
      tr.dataset.result = c.result;
      const id = document.createElement("td"); id.textContent = c.id;
      const res = document.createElement("td"); res.className = "res r-" + c.result; res.textContent = c.result;
      const ev = document.createElement("td"); ev.textContent = c.evidence || "";
      tr.append(id, res, ev);
      body.append(tr);
    }
  }

  function renderTrouble(checks) {
    const box = $("trouble");
    box.replaceChildren();
    const bad = checks.filter((c) => c.result === "fail" || c.result === "warn");
    if (!bad.length) {
      const p = document.createElement("p"); p.className = "hint"; p.textContent = "No problems reported.";
      box.append(p);
      return;
    }
    for (const c of bad) {
      const d = document.createElement("div");
      d.className = "trouble-item" + (c.result === "fail" ? " fail" : "");
      d.dataset.remediation = c.remediation_id || "";
      const h = document.createElement("h3"); h.textContent = (c.title || c.id) + " (" + c.result + ")";
      d.append(h);
      for (const [label, text] of [["Cause", c.cause], ["Confirm", c.confirm]]) {
        if (!text) continue;
        const p = document.createElement("p"); p.textContent = label + ": " + text; d.append(p);
      }
      if (c.steps && c.steps.length) {
        const ol = document.createElement("ol");
        for (const s of c.steps) { const li = document.createElement("li"); li.textContent = s; ol.append(li); }
        d.append(ol);
      }
      for (const href of (c.docs || [])) {
        if (!/^https:\/\//.test(href)) continue;          // only https documentation links
        const a = document.createElement("a"); a.href = href; a.textContent = href; a.rel = "noreferrer";
        const p = document.createElement("p"); p.append(a); d.append(p);
      }
      box.append(d);
    }
  }

  async function refresh() {
    try {
      const r = await fetch("/setup/api/status", { headers: { "X-TDflow-Request": "1" }, cache: "no-store" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const s = await r.json();
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
      setText("summary", s.summary);
      renderChecks(s.checks || []);
      renderTrouble(s.checks || []);
      lastTell = s.tell_agent || "";
      $("tellagent").textContent = lastTell || "–";
      $("updated").textContent = new Date().toLocaleTimeString();
      document.body.dataset.loaded = "true";
    } catch (e) {
      $("status").dataset.state = "unreachable";
      setText("message", "The component is not responding. In TouchDesigner, pulse Open Setup on the TDflowMCP component.");
    }
  }

  async function copy() {
    const status = $("copy-status");
    try {
      if (!navigator.clipboard || !navigator.clipboard.writeText) throw new Error("no clipboard API");
      await navigator.clipboard.writeText(lastTell);
      status.textContent = "Copied.";
      status.dataset.result = "ok";
    } catch (e) {
      const sel = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents($("tellagent"));
      sel.removeAllRanges(); sel.addRange(range);
      status.textContent = "Copying is not available in this browser. The text is selected: press Ctrl+C (Cmd+C on macOS), or pulse Copy Tell Agent on the component.";
      status.dataset.result = "failed";
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("copy").addEventListener("click", copy);
    refresh();
    setInterval(refresh, 2000);
  });
})();
