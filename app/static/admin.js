/* BAGSIK PISOWIFI - admin live updates.
   Vanilla JS, no dependencies: polls one JSON endpoint and patches the DOM. */

(function () {
  "use strict";

  const root = document.querySelector("[data-admin-live]");
  if (!root) return;

  const fmt = (n) => String(n);

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function setStat(key, value) {
    root.querySelectorAll('[data-stat="' + key + '"]').forEach((el) => {
      el.textContent = value;
    });
  }

  function badge(status) {
    const cls = status === "active" ? "badge-ok" : status === "paused" ? "badge-warn" : "badge-muted";
    return '<span class="badge ' + cls + '">' + status + "</span>";
  }

  function renderSessions(rows) {
    const body = root.querySelector("#live-sessions");
    if (!body) return;
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="7" class="empty">No one is online right now.</td></tr>';
      return;
    }
    body.innerHTML = rows
      .map(function (row) {
        return (
          "<tr" + (row.status === "paused" ? ' class="is-paused"' : "") + ">" +
          '<td><span class="mono">' + esc(row.mac) + "</span><br><small>" + esc(row.ip) + "</small></td>" +
          "<td>" + esc(row.plan) + "</td>" +
          '<td class="num">' + esc(row.left) + "</td>" +
          '<td class="num">' + esc(row.used) + "</td>" +
          '<td class="num"><small>' + esc(row.cap) + "</small></td>" +
          '<td><small>' + esc(row.rate) + "</small></td>" +
          "<td>" + badge(esc(row.status)) + "</td>" +
          "</tr>"
        );
      })
      .join("");
  }
  
  function health(id, data) {
    const el = root.querySelector(id);
    if (!el) return;
    const state = (data && data.state) || "unknown";
    const cls = state === "ok" ? "dot-ok" : state === "warn" ? "dot-warn" : "dot-bad";
    el.textContent = "";
    const dot = document.createElement("span");
    dot.className = "dot " + cls;
    el.appendChild(dot);
    el.appendChild(document.createTextNode(esc(state)));
  }

  async function tick() {
    try {
      const response = await fetch("/admin/api/overview", { headers: { Accept: "application/json" } });
      if (!response.ok) return;
      const data = await response.json();
      Object.keys(data.stats).forEach((key) => setStat(key, fmt(data.stats[key])));
      renderSessions(data.sessions || []);
      health("#router-state", data.router);
      health("#coin-state", data.coin);
      const clock = root.querySelector('[data-stat="clock"]');
      if (clock) clock.textContent = data.clock;
    } catch (err) {
      /* offline is normal on a dead uplink - try again on the next tick */
    }
  }

  tick();
  window.setInterval(tick, 5000);

  document.querySelectorAll("[data-confirm]").forEach(function (el) {
    el.addEventListener("submit", function (event) {
      if (!window.confirm(el.getAttribute("data-confirm"))) event.preventDefault();
    });
  });
})();
