// S6 spike Setup Assistant script. External file (CSP default-src 'self' forbids inline script).
"use strict";
(async function () {
  const box = document.getElementById("status");
  try {
    const r = await fetch("/setup/api/status", { headers: { "X-TDflow-Request": "1" }, cache: "no-store" });
    const s = await r.json();
    document.getElementById("state").textContent = s.gate || ("HTTP " + r.status);
    document.getElementById("build").textContent = s.build || "–";
    document.getElementById("main").textContent = String(s.is_main);
    box.dataset.state = s.gate || "error";
    box.dataset.main = String(s.is_main);
  } catch (e) {
    box.dataset.state = "error";
    document.getElementById("state").textContent = "unreachable";
  }
})();
